from __future__ import annotations

from model_hub_py.device import Device, RK182XDevice, RKNPUDevice
from model_hub_py.models import DeviceDefinition, Settings


def build_devices(settings: Settings) -> dict[str, Device]:
    devices: dict[str, Device] = {}
    for name, definition in settings.devices.items():
        if name in devices:
            msg = f"duplicate device name: {name}"
            raise ValueError(msg)
        key = definition.type.strip().upper()
        if key in {"RKNPU"}:
            devices[name] = RKNPUDevice(
                name=definition.name,
                device_id=definition.name,
            )
        elif key in {"RK182X", "RK181X", "RK1820"}:
            devices[name] = RK182XDevice(device_index=definition.device_index)
        else:
            msg = f"unsupported device type: {definition.type}"
            raise ValueError(msg)
    return devices
