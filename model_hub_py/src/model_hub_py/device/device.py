from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryInfo:
    total_bytes: int
    available_bytes: int
    used_bytes: int


class Device(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def device_id(self) -> str | int:
        raise NotImplementedError

    @abstractmethod
    def get_core_count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_memory_info(self) -> MemoryInfo:
        raise NotImplementedError
