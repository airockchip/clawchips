from __future__ import annotations

import re
from pathlib import Path

from model_hub_py.device.device import Device, MemoryInfo

_CORE_LOAD_RE = re.compile(r"Core(\d+):\s*(\d+)\s*%")

DEFAULT_LOAD_PATHS: tuple[Path, ...] = (
    Path("/sys/kernel/debug/rknpu/load"),
    Path("/proc/rknpu/load"),
)


def parse_rknpu_load(text: str) -> list[tuple[int, int]]:
    matches = [(int(m.group(1)), int(m.group(2))) for m in _CORE_LOAD_RE.finditer(text)]
    matches.sort(key=lambda item: item[0])
    return matches


def _parse_meminfo_kib(text: str) -> tuple[int, int]:
    total_k: int | None = None
    avail_k: int | None = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0]
        if key == "MemTotal:":
            total_k = int(parts[1])
        elif key == "MemAvailable:":
            avail_k = int(parts[1])
    if total_k is None or avail_k is None:
        msg = "MemTotal or MemAvailable not found in meminfo"
        raise ValueError(msg)
    return total_k, avail_k


def _resolve_load_path(load_path: str | Path | None) -> Path:
    if load_path is not None:
        path = Path(load_path)
        if not path.is_file():
            raise FileNotFoundError(f"rknpu load file not found: {path}")
        return path
    for path in DEFAULT_LOAD_PATHS:
        if path.is_file():
            return path
    tried = ", ".join(str(p) for p in DEFAULT_LOAD_PATHS)
    raise FileNotFoundError(f"rknpu load file not found; tried: {tried}")


class RKNPUDevice(Device):
    def __init__(
        self,
        *,
        name: str = "rknpu",
        device_id: str | int = "rknpu",
        load_path: str | Path | None = None,
        meminfo_path: str | Path = "/proc/meminfo",
    ) -> None:
        self._name = name
        self._device_id = device_id
        self._load_path = load_path
        self._meminfo_path = Path(meminfo_path)

    @property
    def name(self) -> str:
        return self._name

    @property
    def device_id(self) -> str | int:
        return self._device_id

    def _read_load_text(self) -> str:
        path = _resolve_load_path(self._load_path)
        return path.read_text(encoding="utf-8")

    def _parsed_load(self) -> list[tuple[int, int]]:
        parsed = parse_rknpu_load(self._read_load_text())
        if not parsed:
            msg = "no rknpu core load entries found in load file"
            raise ValueError(msg)
        return parsed

    def get_core_count(self) -> int:
        return len(self._parsed_load())

    def get_core_load_percentages(self) -> list[float]:
        return [float(percent) for _, percent in self._parsed_load()]

    def get_memory_info(self) -> MemoryInfo:
        text = self._meminfo_path.read_text(encoding="utf-8")
        total_k, avail_k = _parse_meminfo_kib(text)
        used_k = max(total_k - avail_k, 0)
        unit = 1024
        return MemoryInfo(
            total_bytes=total_k * unit,
            available_bytes=avail_k * unit,
            used_bytes=used_k * unit,
        )
