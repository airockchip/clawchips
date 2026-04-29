import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from .models import (
    DeviceDefinition,
    ServiceDefinition,
    ServiceStatus,
    SessionRecord,
    SessionStatus,
    Settings,
    UpstreamRequest,
    UpstreamResult,
)


@dataclass
class DeviceRuntime:
    definition: DeviceDefinition
    running_services: set[str] = field(default_factory=set)
    active_request_count: int = 0
    pending_restore_services: dict[str, list[str]] = field(default_factory=dict)
    services_waiting_for_reclaim: set[str] = field(default_factory=set)
    # External streaming leases that also hold one device concurrency slot (see reserve_device_slot).
    reserved_external_slots: dict[tuple[str, str], datetime] = field(default_factory=dict)


@dataclass
class ServiceRuntime:
    definition: ServiceDefinition
    status: ServiceStatus = ServiceStatus.STOPPED
    active_session_id: str | None = None
    queue_size: int = 0
    inflight_requests: int = 0
    last_used_at: datetime | None = None
    last_started_at: datetime | None = None
    last_stopped_at: datetime | None = None
    last_error: str | None = None
    stop_after_request: bool = False
    reclaimed_for_session_ids: set[str] = field(default_factory=set)
    external_leases: dict[str, datetime] = field(default_factory=dict)


RESERVE_DEVICE_SLOT_REQUIRES_DEVICE_TABLE = (
    "model_hub has no device record for this service's device; "
    "cannot reserve a slot. Load config with a top-level `devices` list "
    "(see model_hub_config.yaml) so device concurrency is tracked."
)

_LOG = logging.getLogger("model_hub_py.state_store")


def _reserved_slots_label(device_runtime: DeviceRuntime | None) -> str:
    if device_runtime is None:
        return "n/a"
    if not device_runtime.reserved_external_slots:
        return "none"
    return ",".join(
        f"{svc}:{lease_id[:8]}…"
        for svc, lease_id in device_runtime.reserved_external_slots
    )


class InMemoryStateStore:
    def __init__(self, settings: Settings | dict[str, ServiceDefinition]) -> None:
        if isinstance(settings, Settings):
            devices = settings.devices
            services = settings.services
        else:
            devices = {}
            services = settings

        self.devices = {
            name: DeviceRuntime(definition=device)
            for name, device in devices.items()
        }
        self.services = {
            name: ServiceRuntime(definition=service)
            for name, service in services.items()
        }
        self.sessions: dict[str, SessionRecord] = {}
        self.results: dict[str, UpstreamResult] = {}

    def debug_log_device_slot(self, context: str, device_name: str) -> None:
        """Log used/max and reserved-slot breakdown. Only emits when root logger is DEBUG."""
        if not _LOG.isEnabledFor(logging.DEBUG):
            return
        self._prune_device_reserved_slots(device_name)
        device_runtime = self.devices.get(device_name)
        if device_runtime is None:
            _LOG.debug(
                "%s: device=%s has_device_table=False effective_used=0 effective_max=1",
                context,
                device_name,
            )
            return
        used, max_c = self.device_slot_usage(device_name)
        _LOG.debug(
            "%s: device=%s used=%d max_concurrency=%d active_proxy_inflight=%d "
            "reserved_external_leases=%d reserved=%s",
            context,
            device_name,
            used,
            max_c,
            device_runtime.active_request_count,
            len(device_runtime.reserved_external_slots),
            _reserved_slots_label(device_runtime),
        )

    def create_session(self, service_name: str, request: UpstreamRequest) -> str:
        session_id = f"sess_{uuid4().hex}"
        self.sessions[session_id] = SessionRecord(
            session_id=session_id,
            service_name=service_name,
            status=SessionStatus.QUEUED,
            created_at=datetime.now(UTC),
            request=request,
        )
        self.services[service_name].queue_size += 1
        return session_id

    def get_session(self, session_id: str) -> SessionRecord:
        return self.sessions[session_id]

    def mark_service_started(
        self, service_name: str, *, started_at: datetime | None = None
    ) -> None:
        current = started_at or datetime.now(UTC)
        runtime = self.services[service_name]
        runtime.status = ServiceStatus.IDLE
        runtime.last_started_at = current

        device_runtime = self.devices.get(runtime.definition.device)
        if device_runtime is not None:
            device_runtime.running_services.add(service_name)

    def mark_service_stopped(
        self, service_name: str, *, stopped_at: datetime | None = None
    ) -> None:
        current = stopped_at or datetime.now(UTC)
        runtime = self.services[service_name]
        runtime.status = ServiceStatus.STOPPED
        runtime.active_session_id = None
        runtime.inflight_requests = 0
        runtime.stop_after_request = False
        runtime.last_stopped_at = current

        device_runtime = self.devices.get(runtime.definition.device)
        if device_runtime is not None:
            device_runtime.running_services.discard(service_name)

    def _sync_status_from_external_leases(self, service_name: str) -> None:
        """
        If any external lease is present, set status to ``external`` (unless proxy
        is starting / stopping / processing). If none remain, drop ``external`` to ``idle``.
        """
        runtime = self.services[service_name]
        if runtime.status in {
            ServiceStatus.PROCESSING,
            ServiceStatus.STARTING,
            ServiceStatus.STOPPING,
        }:
            return
        if runtime.external_leases:
            runtime.status = ServiceStatus.EXTERNAL
        elif runtime.status == ServiceStatus.EXTERNAL:
            runtime.status = ServiceStatus.IDLE

    def _prune_device_reserved_slots(self, device_name: str, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        device_runtime = self.devices.get(device_name)
        if device_runtime is None:
            return
        stale_keys = [
            key
            for key, exp in device_runtime.reserved_external_slots.items()
            if exp <= current
        ]
        for key in stale_keys:
            device_runtime.reserved_external_slots.pop(key, None)
        if stale_keys and _LOG.isEnabledFor(logging.DEBUG):
            for svc, lid in stale_keys:
                _LOG.debug(
                    "pruned expired reserved device slot: device=%s service=%s lease_id=%s…",
                    device_name,
                    svc,
                    lid[:8],
                )

    def _release_device_slot_for_lease(self, service_name: str, lease_id: str) -> None:
        device_name = self.services[service_name].definition.device
        device_runtime = self.devices.get(device_name)
        if device_runtime is None:
            return
        popped = device_runtime.reserved_external_slots.pop((service_name, lease_id), None)
        if popped is not None and _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "released reserved device slot: device=%s service=%s lease_id=%s…",
                device_name,
                service_name,
                lease_id[:8],
            )

    def device_slot_usage(
        self,
        device_name: str,
        *,
        now: datetime | None = None,
    ) -> tuple[int, int]:
        """Return (used_slots, max_concurrency) including proxy inflight + reserved external leases."""
        self._prune_device_reserved_slots(device_name, now=now)
        device_runtime = self.devices.get(device_name)
        if device_runtime is None:
            return 0, 1
        used = device_runtime.active_request_count + len(device_runtime.reserved_external_slots)
        return used, device_runtime.definition.max_concurrency

    def describe_device_occupant(self, device_name: str, *, now: datetime | None = None) -> str:
        """Human-oriented label for what is holding the device (single-slot messaging)."""
        self._prune_device_reserved_slots(device_name, now=now)
        device_runtime = self.devices.get(device_name)
        if device_runtime is None:
            return "unknown"
        if device_runtime.reserved_external_slots:
            holder_svc, _holder_lease = next(iter(device_runtime.reserved_external_slots))
            return holder_svc
        for name, runtime in self.services.items():
            if runtime.definition.device != device_name:
                continue
            if runtime.inflight_requests > 0:
                return name
        return "unknown"

    def can_accept_proxy_work(self, device_name: str, *, now: datetime | None = None) -> tuple[bool, str | None]:
        used, max_c = self.device_slot_usage(device_name, now=now)
        if used < max_c:
            self.debug_log_device_slot("can_accept_proxy_work accept", device_name)
            return True, None
        occupant = self.describe_device_occupant(device_name, now=now)
        self.debug_log_device_slot("can_accept_proxy_work reject", device_name)
        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "can_accept_proxy_work: device=%s occupant=%s",
                device_name,
                occupant,
            )
        return False, f"device is occupied by service {occupant}"

    def external_reserve_slot_ready(
        self,
        device_name: str,
        service_name: str,
        lease_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """
        True if ``touch_external_lease(..., reserve_device_slot=True)`` may proceed:
        this lease already holds a reserved slot, or the device has a free slot.
        """
        self._prune_device_reserved_slots(device_name)
        slot_key = (service_name, lease_id)
        device_runtime = self.devices.get(device_name)
        if device_runtime is not None and slot_key in device_runtime.reserved_external_slots:
            return True
        used, max_c = self.device_slot_usage(device_name, now=now)
        return used < max_c

    def touch_external_lease(
        self,
        service_name: str,
        lease_id: str,
        *,
        expires_at: datetime,
        reserve_device_slot: bool = False,
    ) -> str | None:
        """
        Record or renew an external activity lease.

        When ``reserve_device_slot`` is True, also consumes one device concurrency slot
        for the service's device. Returns an error message if the slot cannot be acquired;
        on error the caller must not record the lease.
        """
        runtime = self.services[service_name]
        device_name = runtime.definition.device
        device_runtime = self.devices.get(device_name)
        slot_key = (service_name, lease_id)

        if reserve_device_slot and device_runtime is None:
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug(
                    "touch_external_lease: cannot reserve (no device table) service=%s lease_id=%s…",
                    service_name,
                    lease_id[:8],
                )
            return RESERVE_DEVICE_SLOT_REQUIRES_DEVICE_TABLE

        if device_runtime is not None:
            self._prune_device_reserved_slots(device_name)

        # Keep device slot expiry aligned whenever this lease already holds a slot.
        if device_runtime is not None and slot_key in device_runtime.reserved_external_slots:
            device_runtime.reserved_external_slots[slot_key] = expires_at
            if reserve_device_slot and _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug(
                    "touch_external_lease: renewed reserved device slot service=%s lease_id=%s… expires=%s",
                    service_name,
                    lease_id[:8],
                    expires_at.isoformat(),
                )

        if reserve_device_slot and device_runtime is not None:
            if slot_key not in device_runtime.reserved_external_slots:
                used, max_c = self.device_slot_usage(device_name)
                if used >= max_c:
                    occupant = self.describe_device_occupant(device_name)
                    self.debug_log_device_slot(
                        "touch_external_lease reserve blocked",
                        device_name,
                    )
                    if _LOG.isEnabledFor(logging.DEBUG):
                        _LOG.debug(
                            "touch_external_lease: reserve blocked service=%s lease_id=%s… occupant=%s",
                            service_name,
                            lease_id[:8],
                            occupant,
                        )
                    return f"device is occupied by service {occupant}"
                device_runtime.reserved_external_slots[slot_key] = expires_at
                if _LOG.isEnabledFor(logging.DEBUG):
                    _LOG.debug(
                        "touch_external_lease: acquired reserved device slot service=%s lease_id=%s… expires=%s",
                        service_name,
                        lease_id[:8],
                        expires_at.isoformat(),
                    )

        runtime.external_leases[lease_id] = expires_at

        if (
            device_runtime is not None
            and device_runtime.definition.max_concurrency == 1
            and slot_key in device_runtime.reserved_external_slots
        ):
            self._fail_queued_sessions_on_device(device_name, blocking_service=service_name)
        self._sync_status_from_external_leases(service_name)
        return None

    def _fail_queued_sessions_on_device(self, device_name: str, *, blocking_service: str) -> None:
        now = datetime.now(UTC)
        msg = f"device is occupied by service {blocking_service}"
        for session_id, session in list(self.sessions.items()):
            if session.status != SessionStatus.QUEUED:
                continue
            svc = self.services.get(session.service_name)
            if svc is None or svc.definition.device != device_name:
                continue
            svc.queue_size = max(0, svc.queue_size - 1)
            session.status = SessionStatus.FAILED
            session.error = msg
            session.finished_at = now
            svc.last_error = msg

    def abort_proxy_session_device_busy(
        self,
        device_name: str,
        service_name: str,
        session_id: str,
    ) -> bool:
        """
        If the device has no free slot for a proxy session, mark the session failed.

        Returns True if the session was aborted (caller should skip processing).
        """
        session = self.sessions[session_id]
        if session.status != SessionStatus.QUEUED:
            return False
        ok, err = self.can_accept_proxy_work(device_name)
        if ok:
            return False
        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "abort_proxy_session_device_busy: session_id=%s service=%s device=%s error=%s",
                session_id,
                service_name,
                device_name,
                err,
            )
        now = datetime.now(UTC)
        runtime = self.services[service_name]
        session.status = SessionStatus.FAILED
        session.error = err
        session.finished_at = now
        runtime.last_error = err or "device busy"
        return True

    def release_external_lease(self, service_name: str, lease_id: str) -> None:
        self.services[service_name].external_leases.pop(lease_id, None)
        self._release_device_slot_for_lease(service_name, lease_id)
        self._sync_status_from_external_leases(service_name)
        if _LOG.isEnabledFor(logging.DEBUG):
            dname = self.services[service_name].definition.device
            self.debug_log_device_slot(
                f"release_external_lease service={service_name} lease_id={lease_id[:8]}…",
                dname,
            )

    def service_has_external_activity(
        self,
        service_name: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(UTC)
        runtime = self.services[service_name]
        stale_ids = [
            lease_id
            for lease_id, expires_at in runtime.external_leases.items()
            if expires_at <= current
        ]
        for lease_id in stale_ids:
            runtime.external_leases.pop(lease_id, None)
            self._release_device_slot_for_lease(service_name, lease_id)
        self._sync_status_from_external_leases(service_name)
        return bool(runtime.external_leases)

    def count_external_leases(
        self,
        service_name: str,
        *,
        now: datetime | None = None,
    ) -> int:
        self.service_has_external_activity(service_name, now=now)
        return len(self.services[service_name].external_leases)

    def cleanup_expired(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        for service_name in self.services:
            self.service_has_external_activity(service_name, now=current)
        for session_id, session in list(self.sessions.items()):
            if session.status not in {SessionStatus.FINISHED, SessionStatus.FAILED}:
                continue
            if session.finished_at is None:
                continue

            ttl_seconds = self.services[session.service_name].definition.result_ttl_seconds
            if (current - session.finished_at).total_seconds() < ttl_seconds:
                continue

            session.status = SessionStatus.EXPIRED
            self.results.pop(session_id, None)
