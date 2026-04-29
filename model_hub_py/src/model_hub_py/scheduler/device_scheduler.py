import asyncio
import logging
import math
import time
from datetime import UTC, datetime
from typing import Awaitable, Callable

from model_hub_py.models import (
    ServiceStatus,
    SessionStatus,
    StartupMode,
    UpstreamRequest,
    UpstreamResult,
)

_LOG = logging.getLogger("model_hub_py.scheduler")


class DeviceScheduler:
    def __init__(
        self,
        device_name: str,
        store,
        process_manager,
        proxy_forward: Callable[[str, UpstreamRequest], Awaitable[dict | UpstreamResult]],
        *,
        device=None,
    ) -> None:
        self.device_name = device_name
        self.device = device
        self.store = store
        self.process_manager = process_manager
        self.proxy_forward = proxy_forward
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._service_locks = {
            name: asyncio.Lock()
            for name, runtime in store.services.items()
            if runtime.definition.device == device_name
        }

        device_runtime = store.devices.get(device_name)
        max_concurrency = (
            device_runtime.definition.max_concurrency if device_runtime is not None else 1
        )
        self.tasks = [
            asyncio.create_task(self._worker())
            for _ in range(max_concurrency)
        ]

    async def submit(self, service_name: str, session_id: str) -> None:
        await self.queue.put((service_name, session_id))

    async def wait_until_idle(self) -> None:
        await self.queue.join()

    async def _worker(self) -> None:
        while True:
            service_name, session_id = await self.queue.get()
            try:
                await self._run_one(service_name, session_id)
            finally:
                self.queue.task_done()

    async def _ensure_service_started(
        self, service_name: str, session_id: str
    ) -> None:
        runtime = self.store.services[service_name]
        session = self.store.sessions[session_id]
        async with self._service_locks[service_name]:
            if runtime.status not in {ServiceStatus.STOPPED, ServiceStatus.ERROR}:
                return

            await self._wait_for_capacity(service_name, session_id)
            previous_status = runtime.status
            _LOG.info(
                "Auto-starting service for request: service=%s session=%s previous_status=%s",
                service_name,
                session_id,
                previous_status.value,
            )
            runtime.status = ServiceStatus.STARTING
            session.status = SessionStatus.STARTING
            await self.process_manager.start(runtime.definition)
            await self.process_manager.wait_until_healthy(runtime.definition)
            self.store.mark_service_started(service_name)

    def _available_bytes(self) -> float:
        if self.device is None:
            return math.inf
        return float(self.device.get_memory_info().available_bytes)

    def _same_device_runtimes(self) -> list[tuple[str, object]]:
        return [
            (name, runtime)
            for name, runtime in self.store.services.items()
            if runtime.definition.device == self.device_name
        ]

    def _select_reclaim_candidates(self, target_service_name: str) -> list[tuple[str, object]]:
        device_runtime = self.store.devices.get(self.device_name)
        if device_runtime is None:
            return []
        candidates: list[tuple[str, object]] = []
        for service_name in device_runtime.running_services:
            if service_name == target_service_name:
                continue
            runtime = self.store.services[service_name]
            if runtime.inflight_requests > 0:
                continue
            if self.store.service_has_external_activity(service_name):
                continue
            if runtime.status not in {ServiceStatus.IDLE, ServiceStatus.ERROR}:
                continue
            candidates.append((service_name, runtime))
        candidates.sort(
            key=lambda item: (
                0 if item[1].definition.startup_mode == StartupMode.ONCE else 1,
                item[1].last_used_at or datetime.min.replace(tzinfo=UTC),
            )
        )
        return candidates

    def _has_busy_reclaim_blocker(self, target_service_name: str) -> bool:
        for service_name, runtime in self._same_device_runtimes():
            if service_name == target_service_name:
                continue
            if runtime.inflight_requests > 0:
                return True
        return False

    async def _ensure_memory_capacity(
        self,
        target_service_name: str,
        context_id: str,
        *,
        track_reclaim_for_session_restore: bool = True,
    ) -> bool:
        target_runtime = self.store.services[target_service_name]
        required_bytes = target_runtime.definition.device_memory_usage
        projected_available = self._available_bytes()
        _LOG.debug(
            "memory capacity check: device=%s context=%s target_service=%s "
            "projected_available=%s required=%s",
            self.device_name,
            context_id,
            target_service_name,
            projected_available,
            required_bytes,
        )
        if projected_available >= required_bytes:
            return True

        reclaimed_names: list[str] = []
        for service_name, runtime in self._select_reclaim_candidates(target_service_name):
            _LOG.info(
                "Stopping service for memory reclaim: target=%s reclaim=%s context=%s",
                target_service_name,
                service_name,
                context_id,
            )
            await self.process_manager.stop(runtime.definition)
            self.store.mark_service_stopped(service_name)
            if track_reclaim_for_session_restore:
                runtime.reclaimed_for_session_ids.add(context_id)
            reclaimed_names.append(service_name)
            projected_available += runtime.definition.device_memory_usage
            if projected_available >= required_bytes:
                if track_reclaim_for_session_restore:
                    device_runtime = self.store.devices.get(self.device_name)
                    if device_runtime is not None and reclaimed_names:
                        device_runtime.pending_restore_services[context_id] = reclaimed_names
                return True

        if self._has_busy_reclaim_blocker(target_service_name):
            return False
        raise RuntimeError("insufficient device memory after reclaim")

    async def _wait_for_capacity(self, service_name: str, session_id: str) -> None:
        device_runtime = self.store.devices.get(self.device_name)
        if device_runtime is None or self.device is None:
            return

        deadline = time.monotonic() + device_runtime.definition.scheduling_timeout_seconds
        last_error = "device scheduling timeout"
        device_runtime.services_waiting_for_reclaim.add(service_name)
        try:
            while time.monotonic() < deadline:
                try:
                    if await self._ensure_memory_capacity(
                        service_name, session_id, track_reclaim_for_session_restore=True
                    ):
                        return
                except RuntimeError as exc:
                    last_error = str(exc)
                await asyncio.sleep(0.01)
        finally:
            device_runtime.services_waiting_for_reclaim.discard(service_name)

        raise TimeoutError(last_error)

    def service_lock(self, service_name: str) -> asyncio.Lock:
        return self._service_locks[service_name]

    async def wait_memory_capacity_for_explicit_start(self, service_name: str) -> None:
        """
        与代理请求自动拉起的 `_wait_for_capacity` 使用相同的内存判断与按候选序回收
        空闲服务，但不记录 ``pending_restore_services`` / ``reclaimed_for_session_ids``，
        供 HTTP 显式 ``POST /services/{name}/start`` 使用。
        """
        device_runtime = self.store.devices.get(self.device_name)
        if device_runtime is None or self.device is None:
            return

        deadline = time.monotonic() + device_runtime.definition.scheduling_timeout_seconds
        last_error = "device scheduling timeout"
        context = "explicit_start"
        device_runtime.services_waiting_for_reclaim.add(service_name)
        try:
            while time.monotonic() < deadline:
                try:
                    if await self._ensure_memory_capacity(
                        service_name,
                        context,
                        track_reclaim_for_session_restore=False,
                    ):
                        return
                except RuntimeError as exc:
                    last_error = str(exc)
                await asyncio.sleep(0.01)
        finally:
            device_runtime.services_waiting_for_reclaim.discard(service_name)

        raise TimeoutError(last_error)

    async def _restore_reclaimed_services(self, session_id: str) -> None:
        device_runtime = self.store.devices.get(self.device_name)
        if device_runtime is None:
            return
        reclaimed_names = device_runtime.pending_restore_services.pop(session_id, [])
        if not reclaimed_names:
            return

        now = datetime.now(UTC)
        restore_candidates: list[tuple[str, object]] = []
        for service_name in reclaimed_names:
            runtime = self.store.services[service_name]
            if runtime.status != ServiceStatus.STOPPED:
                continue
            if runtime.last_used_at is None:
                continue
            age_seconds = (now - runtime.last_used_at).total_seconds()
            if age_seconds > device_runtime.definition.resume_window_seconds:
                continue
            restore_candidates.append((service_name, runtime))

        restore_candidates.sort(
            key=lambda item: (
                -(item[1].last_used_at or datetime.min.replace(tzinfo=UTC)).timestamp(),
                0 if item[1].definition.startup_mode == StartupMode.ALWAYS else 1,
            )
        )

        for service_name, runtime in restore_candidates:
            if self._available_bytes() < runtime.definition.device_memory_usage:
                break
            _LOG.info(
                "Restoring reclaimed service after session: service=%s session=%s",
                service_name,
                session_id,
            )
            await self.process_manager.start(runtime.definition)
            await self.process_manager.wait_until_healthy(runtime.definition)
            self.store.mark_service_started(service_name)

    async def _run_one(self, service_name: str, session_id: str) -> None:
        runtime = self.store.services[service_name]
        session = self.store.sessions[session_id]
        device_runtime = self.store.devices.get(self.device_name)
        runtime.queue_size -= 1
        req = session.request
        _LOG.info(
            "Processing session: session_id=%s service=%s device=%s upstream=%s %s",
            session_id,
            service_name,
            self.device_name,
            req.method,
            req.path,
        )

        try:
            if self.store.abort_proxy_session_device_busy(self.device_name, service_name, session_id):
                return
            await self._ensure_service_started(service_name, session_id)
            if self.store.abort_proxy_session_device_busy(self.device_name, service_name, session_id):
                return

            runtime.inflight_requests += 1
            runtime.active_session_id = session_id
            runtime.status = ServiceStatus.PROCESSING
            if device_runtime is not None:
                device_runtime.active_request_count += 1
                if _LOG.isEnabledFor(logging.DEBUG):
                    _LOG.debug(
                        "device active_proxy_inflight +1: device=%s value=%d session=%s service=%s",
                        self.device_name,
                        device_runtime.active_request_count,
                        session_id,
                        service_name,
                    )

            session.status = SessionStatus.PROCESSING
            session.started_at = datetime.now(UTC)
            result = await self.proxy_forward(
                str(runtime.definition.base_url),
                session.request,
            )
            result_obj = (
                result
                if isinstance(result, UpstreamResult)
                else UpstreamResult.model_validate(result)
            )
            finished_at = datetime.now(UTC)
            self.store.results[session_id] = result_obj
            session.status = SessionStatus.FINISHED
            session.finished_at = finished_at
            runtime.last_error = None
            runtime.last_used_at = finished_at
        except Exception as exc:  # noqa: BLE001
            finished_at = datetime.now(UTC)
            session.status = SessionStatus.FAILED
            session.error = str(exc)
            session.finished_at = finished_at
            runtime.last_error = str(exc)
            runtime.last_used_at = finished_at
            _LOG.warning(
                "Session failed: session_id=%s service=%s error=%s",
                session_id,
                service_name,
                exc,
            )
            _LOG.debug(
                "Session failed detail: session_id=%s service=%s device=%s",
                session_id,
                service_name,
                self.device_name,
                exc_info=True,
            )
        finally:
            if runtime.inflight_requests > 0:
                runtime.inflight_requests -= 1
            if device_runtime is not None and device_runtime.active_request_count > 0:
                device_runtime.active_request_count -= 1
                if _LOG.isEnabledFor(logging.DEBUG):
                    _LOG.debug(
                        "device active_proxy_inflight -1: device=%s value=%d session=%s service=%s",
                        self.device_name,
                        device_runtime.active_request_count,
                        session_id,
                        service_name,
                    )
            if runtime.inflight_requests == 0:
                runtime.active_session_id = None
                if session.status == SessionStatus.FAILED:
                    runtime.status = ServiceStatus.ERROR
                elif self.store.service_has_external_activity(service_name):
                    runtime.status = ServiceStatus.EXTERNAL
                else:
                    runtime.status = ServiceStatus.IDLE

        if session.status == SessionStatus.FINISHED:
            _LOG.info(
                "Session finished: session_id=%s service=%s",
                session_id,
                service_name,
            )

        if runtime.definition.startup_mode == StartupMode.ONCE:
            if self.store.service_has_external_activity(service_name):
                _LOG.info(
                    "Keeping ONCE startup_mode service running due to external activity: service=%s session=%s",
                    service_name,
                    session_id,
                )
                await self._restore_reclaimed_services(session_id)
                return
            _LOG.info(
                "Stopping ONCE startup_mode service after session: service=%s session=%s",
                service_name,
                session_id,
            )
            await self.process_manager.stop(runtime.definition)
            self.store.mark_service_stopped(service_name)
            await self._restore_reclaimed_services(session_id)
