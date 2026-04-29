from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, HttpUrl

from .data_size import parse_memory_bytes


class ServiceStatus(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    IDLE = "idle"
    EXTERNAL = "external"
    PROCESSING = "processing"
    STOPPING = "stopping"
    ERROR = "error"


class SessionStatus(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    PROCESSING = "processing"
    FINISHED = "finished"
    FAILED = "failed"
    EXPIRED = "expired"


class StartupMode(StrEnum):
    ONCE = "once"
    ALWAYS = "always"


def _coerce_device_memory_usage(v: object) -> int:
    if isinstance(v, bool):
        msg = "device_memory_usage must not be a boolean"
        raise TypeError(msg)
    if isinstance(v, int):
        if v <= 0:
            msg = "device_memory_usage must be a positive size in bytes"
            raise ValueError(msg)
        return v
    if isinstance(v, str):
        return parse_memory_bytes(v)
    msg = f"device_memory_usage must be int or str, got {type(v).__name__}"
    raise TypeError(msg)


class DeviceDefinition(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    device_index: int = Field(default=0, ge=0)
    max_concurrency: int = Field(gt=0)
    scheduling_timeout_seconds: int = Field(gt=0)
    resume_window_seconds: int = Field(ge=0)


class ServiceDefinition(BaseModel):
    name: str = Field(min_length=1)
    device: str = Field(min_length=1)
    startup_mode: StartupMode = StartupMode.ALWAYS
    device_memory_usage: Annotated[
        int, BeforeValidator(_coerce_device_memory_usage), Field(gt=0)
    ]
    base_url: HttpUrl
    start_command: str = Field(min_length=1)
    stop_command: str = Field(min_length=1)
    healthcheck_command: str = Field(min_length=1)
    workdir: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    startup_timeout_seconds: int = Field(gt=0)
    result_ttl_seconds: int = Field(gt=0)


class Settings(BaseModel):
    devices: dict[str, DeviceDefinition]
    services: dict[str, ServiceDefinition]


class UpstreamRequest(BaseModel):
    method: str
    path: str
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: dict[str, Any] | list[Any] | None = None
    body_base64: str | None = None


class UpstreamResult(BaseModel):
    upstream_status_code: int
    upstream_headers: dict[str, str]
    upstream_body: Any | None = None
    body_base64: str | None = None
    duration_ms: int


class SessionRecord(BaseModel):
    session_id: str
    service_name: str
    status: SessionStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    request: UpstreamRequest
