from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

from ..schemas import GenerationRequest, Usage


class RuntimeBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class InferenceEvent:
    type: str
    text: str = ""
    finish_reason: str | None = None
    usage: Usage | None = None
    error: str | None = None


class RuntimeBackend(ABC):
    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def generate(
        self,
        prompt: str,
        prompt_tokens: list[int],
        request: GenerationRequest,
        system_prompt: str = "",
    ) -> AsyncIterator[InferenceEvent]:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @property
    @abstractmethod
    def ready(self) -> bool:
        raise NotImplementedError

    @property
    def model_type(self) -> str:
        """Model type string (e.g. ``"qwen3"``, ``"gemma4"``).

        Empty string until the runtime queries the model config.
        """
        return ""
