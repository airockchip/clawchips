from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from .native_sampling import NativeSamplingEngine
from .schemas import GenerationRequest


LOGGER = logging.getLogger("gateway.xgrammar")


@dataclass
class SamplingState:
    native_session: Any | None = None
    sample_count: int = 0
    sample_total_ms: float = 0.0
    sample_max_ms: float = 0.0
    mask_total_ms: float = 0.0
    mask_count: int = 0
    choose_total_ms: float = 0.0
    masked_choose_total_ms: float = 0.0
    masked_choose_count: int = 0
    accept_total_ms: float = 0.0


class SamplingPipeline:
    """One native XGrammar and sampling session per RKNN generation request."""

    def __init__(
        self,
        tokenizer: Any,
        enable_xgrammar: bool,
        enable_native_sampling: bool = False,
        native_seed: int = -1,
        repeat_last_n: int = 64,
        penalize_newline: bool = False,
        debug: bool = False,
        *,
        model_structure: str = "qwen3",
        sampling_library: str = "",
        native_engine: NativeSamplingEngine | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.enable_xgrammar = enable_xgrammar
        self.enable_native_sampling = enable_native_sampling
        self.native_seed = native_seed
        self.repeat_last_n = repeat_last_n
        self.penalize_newline = penalize_newline
        self.debug = debug
        self.model_structure = model_structure
        self.sampling_library = sampling_library
        self.vocab_size = len(tokenizer)
        self._native_engine = native_engine

    def start(self) -> None:
        if self._native_engine is None:
            self._native_engine = NativeSamplingEngine(
                self.tokenizer,
                seed=self.native_seed,
                repeat_last_n=self.repeat_last_n,
                penalize_newline=self.penalize_newline,
                library_path=self.sampling_library,
            )
        LOGGER.info(
            "Native sampler initialized: xgrammar_cpp=%s native_sampling=%s "
            "vocab_size=%d debug=%s",
            self.enable_xgrammar,
            self.enable_native_sampling,
            self.vocab_size,
            self.debug,
        )

    def create_state(self, request: GenerationRequest) -> SamplingState:
        if self._native_engine is None:
            raise RuntimeError("native sampling engine is not initialized")
        state = SamplingState()
        compiled_structure = None
        if self.enable_xgrammar and request.tools and request.tool_choice != "none":
            compiled_structure = (
                _QWEN35_OPEN_STRUCTURE_JSON
                if self.model_structure == "qwen3.5"
                else json.dumps(
                    _tool_call_structure_tag(request.tools, self.model_structure),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        state.native_session = self._native_engine.create_session(
            request,
            compiled_structure,
        )
        return state

    def sample(self, logits_ptr: Any, state: SamplingState) -> int:
        if self._native_engine is None or state.native_session is None:
            raise RuntimeError("native sampling session is not initialized")
        sample_start = time.perf_counter()
        result = self._native_engine.sample_fp16(logits_ptr, state.native_session)
        state.mask_total_ms += result.mask_ms
        state.choose_total_ms += result.sampler_ms
        state.accept_total_ms += result.accept_ms
        if result.grammar_active_before:
            state.mask_count += 1
        if result.mask_applied:
            state.masked_choose_total_ms += result.sampler_ms
            state.masked_choose_count += 1

        token_id = result.token_id
        state.sample_count += 1
        elapsed_ms = (time.perf_counter() - sample_start) * 1000.0
        state.sample_total_ms += elapsed_ms
        state.sample_max_ms = max(state.sample_max_ms, elapsed_ms)
        if self.debug and self._should_log_step(state.sample_count):
            LOGGER.info(
                "Native sample step=%d token_id=%d mask_applied=%s "
                "grammar_active=%s grammar_completed=%s mask_ms=%.3f "
                "sampler_ms=%.3f accept_ms=%.3f",
                state.sample_count,
                token_id,
                result.mask_applied,
                result.grammar_active_after,
                result.grammar_completed,
                result.mask_ms,
                result.sampler_ms,
                result.accept_ms,
            )
        return token_id

    def close_state(self, state: SamplingState | None) -> None:
        if (
            state is not None
            and state.native_session is not None
            and self._native_engine is not None
        ):
            self._native_engine.destroy_session(state.native_session)
            state.native_session = None

    def close(self) -> None:
        if self._native_engine is not None:
            self._native_engine.close()
            self._native_engine = None

    @staticmethod
    def _should_log_step(step: int) -> bool:
        return step <= 32 or step % 50 == 0


def _tool_call_json_schema(tools: list[dict[str, Any]]) -> dict[str, Any]:
    variants = []
    for tool in tools:
        function = tool.get("function", {}) if isinstance(tool, dict) else {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        parameters = function.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object"}
        variants.append(
            {
                "type": "object",
                "properties": {
                    "name": {"enum": [name]},
                    "arguments": parameters,
                },
                "required": ["name", "arguments"],
                "additionalProperties": False,
            }
        )
    if not variants:
        return {"type": "object"}
    if len(variants) == 1:
        return variants[0]
    return {"oneOf": variants}


def _tool_call_qwen3_structure_tag(tools: list[dict[str, Any]]) -> dict[str, Any]:
    schema = _tool_call_json_schema(tools)
    return {
        "type": "structural_tag",
        "format": {
            "type": "sequence",
            "elements": [
                {"type": "json_schema", "json_schema": schema},
                {"type": "const_string", "value": "</tool_call>"},
            ],
        },
    }


def _tool_call_qwen35_structure_tag() -> dict[str, Any]:
    """Build a body-only Qwen3.5 XML grammar with an open function name."""
    return {
        "type": "structural_tag",
        "format": {
            "type": "sequence",
            "elements": [
                {"type": "const_string", "value": "\n<function="},
                {"type": "regex", "pattern": "[A-Za-z0-9_-]{1,64}"},
                {"type": "const_string", "value": ">\n"},
                {
                    "type": "json_schema",
                    "json_schema": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "style": "qwen_xml",
                },
                {"type": "const_string", "value": "\n</function>\n</tool_call>"},
            ],
        },
    }


def _tool_call_structure_tag(
    tools: list[dict[str, Any]],
    model_structure: str = "qwen3",
) -> dict[str, Any]:
    if model_structure == "qwen3.5":
        return _tool_call_qwen35_structure_tag()
    if model_structure == "qwen3":
        return _tool_call_qwen3_structure_tag(tools)
    raise ValueError(f"Unsupported XGrammar model structure: {model_structure}")


_QWEN35_OPEN_STRUCTURE_JSON = json.dumps(
    _tool_call_qwen35_structure_tag(),
    ensure_ascii=False,
    separators=(",", ":"),
)
