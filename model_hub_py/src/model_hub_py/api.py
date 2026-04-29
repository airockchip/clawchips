import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from .config import load_settings
from .device import Device
from .device_factory import build_devices
from .dispatcher import DeviceSlotUnavailableError, Dispatcher
from .models import ServiceDefinition, ServiceStatus, SessionRecord, Settings, UpstreamRequest
from .process_manager import ProcessManager
from .proxy import ProxyClient
from .state_store import RESERVE_DEVICE_SLOT_REQUIRES_DEVICE_TABLE, InMemoryStateStore

_LOG = logging.getLogger("model_hub_py.api")


def _device_reserve_lock(app: FastAPI, device_name: str) -> asyncio.Lock:
    locks: dict[str, asyncio.Lock] = app.state.device_reserve_locks
    if device_name not in locks:
        locks[device_name] = asyncio.Lock()
    return locks[device_name]


async def _wait_and_touch_external_lease_with_reserve(
    app: FastAPI,
    store: InMemoryStateStore,
    *,
    service_name: str,
    lease_id: str,
    ttl_seconds: int,
    wait_timeout_seconds: float,
) -> tuple[datetime, str | None]:
    """
    Wait until a device slot is available (or this lease already holds one), then touch.

    Returns ``(expires_at, lease_error)`` where ``lease_error`` is set only on failure
    after acquiring the per-device lock (should be rare).
    """
    device_name = store.services[service_name].definition.device
    if store.devices.get(device_name) is None:
        raise HTTPException(status_code=409, detail=RESERVE_DEVICE_SLOT_REQUIRES_DEVICE_TABLE)
    poll_s = 0.05
    deadline = time.monotonic() + wait_timeout_seconds if wait_timeout_seconds > 0 else None

    while True:
        while not store.external_reserve_slot_ready(device_name, service_name, lease_id):
            if deadline is not None and time.monotonic() >= deadline:
                occupant = store.describe_device_occupant(device_name)
                msg = (
                    "timeout waiting for a free device slot; "
                    f"device is occupied by service {occupant}"
                )
                raise HTTPException(status_code=408, detail=msg)
            await asyncio.sleep(poll_s)

        lock = _device_reserve_lock(app, device_name)
        async with lock:
            if not store.external_reserve_slot_ready(device_name, service_name, lease_id):
                continue
            expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
            lease_error = store.touch_external_lease(
                service_name,
                lease_id,
                expires_at=expires_at,
                reserve_device_slot=True,
            )
            if lease_error is None:
                return expires_at, None
            if lease_error == RESERVE_DEVICE_SLOT_REQUIRES_DEVICE_TABLE:
                raise HTTPException(status_code=409, detail=lease_error)
            _LOG.warning(
                "touch_external_lease failed after slot-ready (retrying): service=%s lease=%s err=%s",
                service_name,
                lease_id,
                lease_error,
            )


def create_app(
    settings: Settings | dict[str, ServiceDefinition],
    *,
    devices: dict[str, Device] | None = None,
    process_manager: ProcessManager | None = None,
    cleanup_interval_seconds: float = 5.0,
) -> FastAPI:
    if isinstance(settings, Settings) and devices is None:
        msg = "devices is required when settings is a Settings object"
        raise ValueError(msg)

    if isinstance(settings, Settings):
        store = InMemoryStateStore(settings)
        device_map = devices or {}
    else:
        store = InMemoryStateStore(settings)
        device_map = devices or {}

    shared_process_manager = process_manager or ProcessManager()

    async def cleanup_loop() -> None:
        while True:
            await asyncio.sleep(cleanup_interval_seconds)
            store.cleanup_expired()

    def ensure_runtime(app: FastAPI) -> Dispatcher:
        dispatcher = app.state.dispatcher
        if dispatcher is None:
            http_client = httpx.AsyncClient(timeout=300.0)
            proxy_client = ProxyClient(http_client)
            dispatcher = Dispatcher(
                store=store,
                devices=device_map,
                process_manager=shared_process_manager,
                proxy_forward=proxy_client.forward,
            )
            cleanup_task = asyncio.create_task(cleanup_loop())
            app.state.http_client = http_client
            app.state.proxy_client = proxy_client
            app.state.dispatcher = dispatcher
            app.state.cleanup_task = cleanup_task
        return dispatcher

    async def reconcile_services_on_startup() -> None:
        async def probe_one(name: str) -> tuple[str, bool]:
            definition = store.services[name].definition
            try:
                ok = await shared_process_manager.check_healthy_once(definition)
            except Exception as exc:
                _LOG.warning(
                    "Startup health probe failed: service=%s error=%r",
                    name,
                    exc,
                )
                ok = False
            return name, ok

        if not store.services:
            return
        _LOG.info("Startup: reconciling service state with one-shot health checks")
        probe_results = await asyncio.gather(
            *[probe_one(name) for name in store.services],
        )
        for name, ok in probe_results:
            runtime = store.services[name]
            if ok:
                store.mark_service_started(name)
                runtime.last_error = None
                _LOG.info(
                    "Startup health: service=%s reconciled=idle (healthcheck ok)",
                    name,
                )
            else:
                if runtime.status != ServiceStatus.STOPPED:
                    store.mark_service_stopped(name)
                runtime.last_error = None
                _LOG.info(
                    "Startup health: service=%s reconciled=stopped (healthcheck failed)",
                    name,
                )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        _LOG.info(
            "Application startup: loading runtime (services=%d devices=%d)",
            len(store.services),
            len(device_map),
        )
        ensure_runtime(app)
        await reconcile_services_on_startup()
        _LOG.info("Application ready: HTTP API is accepting requests")
        try:
            yield
        finally:
            _LOG.info("Application shutdown: stopping background tasks and closing clients")
            cleanup_task = app.state.cleanup_task
            if cleanup_task is not None:
                cleanup_task.cancel()
                await asyncio.gather(cleanup_task, return_exceptions=True)

            dispatcher = app.state.dispatcher
            if dispatcher is not None:
                for task in dispatcher.tasks.values():
                    task.cancel()
                await asyncio.gather(*dispatcher.tasks.values(), return_exceptions=True)

            http_client = app.state.http_client
            if http_client is not None:
                await http_client.aclose()
            _LOG.info("Application shutdown complete")

    app = FastAPI(title="model_hub_py", lifespan=lifespan)
    app.state.store = store
    app.state.devices = device_map
    app.state.dispatcher = None
    app.state.proxy_client = None
    app.state.http_client = None
    app.state.cleanup_task = None
    app.state.device_reserve_locks: dict[str, asyncio.Lock] = {}

    @app.middleware("http")
    async def log_http_requests(request: Request, call_next):
        start = time.perf_counter()
        client = request.client.host if request.client else "unknown"
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - start
            _LOG.exception(
                "HTTP request failed: %s %s from %s after %.3fs",
                request.method,
                request.url.path,
                client,
                duration,
            )
            raise
        duration = time.perf_counter() - start
        _LOG.info(
            "HTTP %s %s -> %s from %s in %.3fs",
            request.method,
            request.url.path,
            response.status_code,
            client,
            duration,
        )
        return response

    @app.post("/services/{service_name}")
    async def submit_task(service_name: str, request: UpstreamRequest) -> dict:
        if service_name not in store.services:
            raise HTTPException(status_code=404, detail="service not found")
        dispatcher = ensure_runtime(app)
        try:
            session_id = await dispatcher.submit(service_name, request)
        except DeviceSlotUnavailableError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc
        _LOG.info(
            "Task queued: service=%s session_id=%s upstream=%s %s",
            service_name,
            session_id,
            request.method,
            request.path,
        )
        return {
            "session_id": session_id,
            "service_name": service_name,
            "status": "queued",
        }

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> SessionRecord:
        session = store.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session

    @app.get("/sessions/{session_id}/result")
    async def get_result(session_id: str) -> dict:
        store.cleanup_expired()
        session = store.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        if session.status.value in {"queued", "starting", "processing"}:
            raise HTTPException(status_code=409, detail="result not ready")
        if session.status.value == "expired":
            raise HTTPException(status_code=410, detail="result expired")
        result = store.results.get(session_id)
        if result is None:
            _LOG.debug(
                "GET /sessions/.../result: no stored result (session_id=%s status=%s error=%r)",
                session_id,
                session.status.value,
                session.error,
            )
            raise HTTPException(status_code=404, detail="result not found")
        return {
            "session_id": session_id,
            "status": session.status,
            **result.model_dump(),
        }

    @app.get("/services")
    async def list_services() -> dict[str, dict]:
        store.cleanup_expired()
        return {
            name: {
                "status": runtime.status,
                "queue_size": runtime.queue_size,
                "active_session_id": runtime.active_session_id,
                "last_error": runtime.last_error,
            }
            for name, runtime in store.services.items()
        }

    @app.get("/services/{service_name}")
    async def get_service(service_name: str) -> dict:
        store.cleanup_expired()
        runtime = store.services.get(service_name)
        if runtime is None:
            raise HTTPException(status_code=404, detail="service not found")
        return {
            "service_name": service_name,
            "status": runtime.status,
            "queue_size": runtime.queue_size,
            "active_session_id": runtime.active_session_id,
            "last_error": runtime.last_error,
            "base_url": str(runtime.definition.base_url),
            "external_active": store.service_has_external_activity(service_name),
            "external_lease_count": store.count_external_leases(service_name),
        }

    @app.post("/services/{service_name}/leases")
    async def touch_service_lease(service_name: str, payload: dict | None = None) -> dict:
        runtime = store.services.get(service_name)
        if runtime is None:
            raise HTTPException(status_code=404, detail="service not found")
        body = payload or {}
        requested_id = body.get("lease_id")
        lease_id = requested_id if isinstance(requested_id, str) and requested_id else f"lease_{uuid4().hex}"
        ttl_raw = body.get("ttl_seconds", 30)
        try:
            ttl_seconds = max(1, int(ttl_raw))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="ttl_seconds must be an integer") from exc
        reserve_device_slot = bool(body.get("reserve_device_slot", False))
        device_name = runtime.definition.device
        _LOG.info(
            "Lease touch request: service=%s device=%s lease_id=%s… ttl_seconds=%d reserve_device_slot=%s",
            service_name,
            device_name,
            lease_id[:8],
            ttl_seconds,
            reserve_device_slot,
        )
        if reserve_device_slot:
            raw_wait = body.get("reserve_wait_timeout_seconds", 600)
            try:
                wait_timeout_seconds = float(raw_wait)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail="reserve_wait_timeout_seconds must be a number",
                ) from exc
            if wait_timeout_seconds < 0:
                raise HTTPException(
                    status_code=400,
                    detail="reserve_wait_timeout_seconds must be non-negative (0 = no timeout)",
                )
            wait_timeout_seconds = min(wait_timeout_seconds, 86400.0)
            _LOG.info(
                "Lease touch reserve: service=%s device=%s reserve_wait_timeout_seconds=%s",
                service_name,
                device_name,
                wait_timeout_seconds,
            )
            expires_at, lease_error = await _wait_and_touch_external_lease_with_reserve(
                app,
                store,
                service_name=service_name,
                lease_id=lease_id,
                ttl_seconds=ttl_seconds,
                wait_timeout_seconds=wait_timeout_seconds,
            )
        else:
            expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
            lease_error = store.touch_external_lease(
                service_name,
                lease_id,
                expires_at=expires_at,
                reserve_device_slot=False,
            )
        if lease_error is not None:
            _LOG.info(
                "Lease touch failed: service=%s device=%s lease_id=%s… reserve_device_slot=%s detail=%r",
                service_name,
                device_name,
                lease_id[:8],
                reserve_device_slot,
                lease_error,
            )
            raise HTTPException(status_code=409, detail=lease_error)
        response_body: dict = {
            "service_name": service_name,
            "lease_id": lease_id,
            "expires_at": expires_at.isoformat(),
            "external_active": True,
            "external_lease_count": store.count_external_leases(service_name),
            "reserve_device_slot": reserve_device_slot,
        }
        if reserve_device_slot:
            response_body["reserve_wait_timeout_seconds"] = wait_timeout_seconds
        response_body["status"] = store.services[service_name].status
        _LOG.info(
            "Lease touch ok: service=%s device=%s lease_id=%s… reserve_device_slot=%s "
            "expires_at=%s external_lease_count=%d status=%s pid=%s",
            service_name,
            device_name,
            lease_id[:8],
            reserve_device_slot,
            expires_at.isoformat(),
            response_body["external_lease_count"],
            response_body["status"],
            os.getpid(),
        )
        if reserve_device_slot and _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "touch_service_lease 200 (debug): service=%s device=%s lease_id=%s…",
                service_name,
                device_name,
                lease_id[:8],
            )
            store.debug_log_device_slot("touch_service_lease 200 (reserve)", device_name)
        return response_body

    @app.delete("/services/{service_name}/leases/{lease_id}")
    async def release_service_lease(service_name: str, lease_id: str) -> dict:
        runtime = store.services.get(service_name)
        if runtime is None:
            raise HTTPException(status_code=404, detail="service not found")
        rdevice = runtime.definition.device
        _LOG.info(
            "Lease release request: service=%s device=%s lease_id=%s… pid=%s",
            service_name,
            rdevice,
            lease_id[:8],
            os.getpid(),
        )
        store.release_external_lease(service_name, lease_id)
        ext = store.service_has_external_activity(service_name)
        ext_count = store.count_external_leases(service_name)
        _LOG.info(
            "Lease release ok: service=%s device=%s lease_id=%s… external_active=%s "
            "external_lease_count=%d status=%s",
            service_name,
            rdevice,
            lease_id[:8],
            ext,
            ext_count,
            store.services[service_name].status,
        )
        return {
            "service_name": service_name,
            "lease_id": lease_id,
            "status": store.services[service_name].status,
            "external_active": ext,
            "external_lease_count": ext_count,
        }

    @app.post("/services/{service_name}/start")
    async def start_service(service_name: str) -> dict:
        runtime = store.services.get(service_name)
        if runtime is None:
            raise HTTPException(status_code=404, detail="service not found")
        if runtime.status.value not in {"stopped", "error"}:
            _LOG.info(
                "Service start skipped (not stopped): service=%s status=%s",
                service_name,
                runtime.status.value,
            )
            return {"service_name": service_name, "status": runtime.status}
        device_name = runtime.definition.device
        dispatcher = ensure_runtime(app)
        scheduler = dispatcher.schedulers[device_name]
        async with scheduler.service_lock(service_name):
            runtime = store.services[service_name]
            if runtime.status.value not in {"stopped", "error"}:
                return {"service_name": service_name, "status": runtime.status}
            _LOG.info("Service start requested: service=%s", service_name)
            try:
                await scheduler.wait_memory_capacity_for_explicit_start(service_name)
            except TimeoutError as exc:
                _LOG.warning(
                    "Service start failed (device memory / scheduling): service=%s error=%s",
                    service_name,
                    exc,
                )
                raise HTTPException(
                    status_code=503,
                    detail="device memory capacity not available in time; try later or free peer services",
                ) from exc
            runtime.status = ServiceStatus.STARTING
            await shared_process_manager.start(runtime.definition)
            await shared_process_manager.wait_until_healthy(runtime.definition)
            store.mark_service_started(service_name)
        _LOG.info("Service started: service=%s status=%s", service_name, runtime.status.value)
        return {"service_name": service_name, "status": store.services[service_name].status}

    @app.post("/services/{service_name}/stop")
    async def stop_service(service_name: str) -> dict:
        runtime = store.services.get(service_name)
        if runtime is None:
            raise HTTPException(status_code=404, detail="service not found")
        if store.service_has_external_activity(service_name):
            _LOG.info("Service stop rejected (external clients active): service=%s", service_name)
            raise HTTPException(status_code=409, detail="service has active external clients")
        if runtime.active_session_id is not None:
            _LOG.info(
                "Service stop rejected (busy): service=%s active_session_id=%s",
                service_name,
                runtime.active_session_id,
            )
            raise HTTPException(status_code=409, detail="service is processing")
        if runtime.status.value == "stopped":
            _LOG.info("Service stop skipped (already stopped): service=%s", service_name)
            return {"service_name": service_name, "status": runtime.status}
        _LOG.info("Service stop requested: service=%s", service_name)
        runtime.status = ServiceStatus.STOPPING
        await shared_process_manager.stop(runtime.definition)
        store.mark_service_stopped(service_name)
        _LOG.info("Service stopped: service=%s", service_name)
        return {"service_name": service_name, "status": runtime.status}

    @app.post("/services/{service_name}/healthcheck")
    async def healthcheck_service(service_name: str) -> dict:
        runtime = store.services.get(service_name)
        if runtime is None:
            raise HTTPException(status_code=404, detail="service not found")
        await shared_process_manager.wait_until_healthy(runtime.definition)
        if runtime.status == ServiceStatus.STOPPED:
            store.mark_service_started(service_name)
        else:
            if store.service_has_external_activity(service_name):
                runtime.status = ServiceStatus.EXTERNAL
            else:
                runtime.status = ServiceStatus.IDLE
        return {"service_name": service_name, "status": runtime.status}

    return app


def create_app_from_config(config_path: str) -> FastAPI:
    settings = load_settings(config_path)
    return create_app(settings, devices=build_devices(settings))
