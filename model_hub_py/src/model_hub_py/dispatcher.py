import logging
from typing import Awaitable, Callable

from .models import UpstreamRequest, UpstreamResult
from .scheduler import DeviceScheduler

_LOG = logging.getLogger("model_hub_py.dispatcher")


class DeviceSlotUnavailableError(Exception):
    """Raised when a proxy session cannot be queued because the device has no free slot."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class Dispatcher:
    def __init__(
        self,
        store,
        devices: dict[str, object],
        process_manager,
        proxy_forward: Callable[[str, UpstreamRequest], Awaitable[dict | UpstreamResult]],
    ) -> None:
        self.store = store
        self.process_manager = process_manager
        self.proxy_forward = proxy_forward
        self.schedulers = {
            device_name: DeviceScheduler(
                device_name=device_name,
                device=devices.get(device_name),
                store=store,
                process_manager=process_manager,
                proxy_forward=proxy_forward,
            )
            for device_name in {
                runtime.definition.device for runtime in store.services.values()
            }
        }
        self.tasks = {
            f"{device_name}:{index}": task
            for device_name, scheduler in self.schedulers.items()
            for index, task in enumerate(scheduler.tasks)
        }

    async def submit(self, service_name: str, request: UpstreamRequest) -> str:
        device_name = self.store.services[service_name].definition.device
        ok, detail = self.store.can_accept_proxy_work(device_name)
        if not ok:
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug(
                    "submit rejected (device slot): service=%s device=%s detail=%r upstream=%s %s",
                    service_name,
                    device_name,
                    detail,
                    request.method,
                    request.path,
                )
            raise DeviceSlotUnavailableError(detail or "device busy")
        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "submit accepted: service=%s device=%s upstream=%s %s",
                service_name,
                device_name,
                request.method,
                request.path,
            )
        session_id = self.store.create_session(service_name, request)
        await self.schedulers[device_name].submit(service_name, session_id)
        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug(
                "submit queued: service=%s device=%s session_id=%s",
                service_name,
                device_name,
                session_id,
            )
        return session_id

    async def wait_until_idle(self, device_name: str) -> None:
        await self.schedulers[device_name].wait_until_idle()
