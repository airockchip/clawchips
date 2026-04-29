from pathlib import Path

import yaml

from .models import DeviceDefinition, ServiceDefinition, Settings


def load_settings(path: str | Path) -> Settings:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    device_rows = raw.get("devices", [])
    if not isinstance(device_rows, list):
        raise ValueError("devices must be a list")

    service_rows = raw.get("services")
    if not isinstance(service_rows, list) or not service_rows:
        raise ValueError("services must be a non-empty list")

    devices: dict[str, DeviceDefinition] = {}
    for row in device_rows:
        device = DeviceDefinition.model_validate(row)
        if device.name in devices:
            raise ValueError(f"duplicate device name: {device.name}")
        devices[device.name] = device

    services: dict[str, ServiceDefinition] = {}
    for row in service_rows:
        service = ServiceDefinition.model_validate(row)
        if service.device not in devices:
            raise ValueError(f"unknown device reference: {service.device}")
        if service.name in services:
            raise ValueError(f"duplicate service name: {service.name}")
        services[service.name] = service

    return Settings(devices=devices, services=services)
