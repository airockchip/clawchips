from __future__ import annotations

import ctypes
import ctypes.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .native_library import PACKAGED_NATIVE_LIBRARY, SYSTEM_NATIVE_LIBRARY
from .schemas import GenerationRequest


class NativeSamplingParams(ctypes.Structure):
    _fields_ = [
        ("temperature", ctypes.c_float),
        ("top_p", ctypes.c_float),
        ("top_k", ctypes.c_int32),
        ("repeat_penalty", ctypes.c_float),
        ("frequency_penalty", ctypes.c_float),
        ("presence_penalty", ctypes.c_float),
        ("repeat_last_n", ctypes.c_int32),
        ("newline_token_id", ctypes.c_int32),
        ("penalize_newline", ctypes.c_int32),
        ("seed", ctypes.c_int64),
    ]


class NativeSamplingResultStruct(ctypes.Structure):
    _fields_ = [
        ("token_id", ctypes.c_int32),
        ("mask_applied", ctypes.c_uint8),
        ("grammar_active_before", ctypes.c_uint8),
        ("grammar_active_after", ctypes.c_uint8),
        ("grammar_completed", ctypes.c_uint8),
        ("mask_ms", ctypes.c_double),
        ("sampler_ms", ctypes.c_double),
        ("accept_ms", ctypes.c_double),
    ]


@dataclass(frozen=True)
class NativeSamplingResult:
    token_id: int
    mask_applied: bool
    grammar_active_before: bool
    grammar_active_after: bool
    grammar_completed: bool
    mask_ms: float
    sampler_ms: float
    accept_ms: float


class NativeSamplingEngine:
    """Own the model-level XGrammar compiler and native sampler library."""

    def __init__(
        self,
        tokenizer: Any,
        *,
        seed: int = -1,
        repeat_last_n: int = 64,
        penalize_newline: bool = False,
        library_path: str = "",
        library: Any | None = None,
    ) -> None:
        self.vocab_size = len(tokenizer)
        self.seed = seed
        self.repeat_last_n = repeat_last_n
        self.penalize_newline = penalize_newline
        self.newline_token_id = int(getattr(tokenizer, "linefeed_id", -1))
        self._lib = library if library is not None else _load_library(library_path)
        _configure_library(self._lib)

        piece_bytes = getattr(tokenizer, "token_to_piece_bytes", None)
        pieces = [
            piece_bytes(token_id)
            if callable(piece_bytes)
            else tokenizer.token_to_piece(token_id).encode("utf-8")
            for token_id in range(self.vocab_size)
        ]
        piece_array = (ctypes.c_char_p * self.vocab_size)(*pieces)
        length_array = (ctypes.c_uint32 * self.vocab_size)(*[len(piece) for piece in pieces])
        stop_ids = list(getattr(tokenizer, "special_eos_ids", []) or [])
        stop_array = (ctypes.c_int32 * len(stop_ids))(*stop_ids) if stop_ids else None
        self._handle = self._lib.claw_sampling_engine_create(
            self.vocab_size,
            piece_array,
            length_array,
            stop_array,
            len(stop_ids),
        )
        if not self._handle:
            raise RuntimeError(f"native sampling engine initialization failed: {self._last_error()}")

    def create_session(
        self,
        request: GenerationRequest,
        structural_tag_json: str | None,
    ) -> Any:
        params = NativeSamplingParams(
            temperature=request.temperature,
            top_p=request.top_p,
            top_k=request.top_k,
            repeat_penalty=request.repeat_penalty,
            frequency_penalty=request.frequency_penalty,
            presence_penalty=request.presence_penalty,
            repeat_last_n=self.repeat_last_n,
            newline_token_id=self.newline_token_id,
            penalize_newline=int(self.penalize_newline),
            seed=self.seed,
        )
        encoded_structure = structural_tag_json.encode("utf-8") if structural_tag_json else None
        handle = self._lib.claw_sampling_session_create(
            self._handle,
            encoded_structure,
            ctypes.byref(params),
        )
        if not handle:
            raise RuntimeError(f"native sampling session initialization failed: {self._last_error()}")
        return handle

    def sample_fp16(self, logits_ptr: Any, session: Any) -> NativeSamplingResult:
        return self._sample(self._lib.claw_sampling_session_sample_f16, logits_ptr, session)

    def sample_fp32(self, logits: np.ndarray, session: Any) -> NativeSamplingResult:
        if logits.dtype != np.float32 or not logits.flags.c_contiguous:
            raise RuntimeError("native sampling requires contiguous float32 logits")
        if logits.size != self.vocab_size:
            raise RuntimeError(
                f"RKNN3 logits size {logits.size} does not match vocab_size={self.vocab_size}"
            )
        return self._sample(
            self._lib.claw_sampling_session_sample_f32,
            ctypes.c_void_p(logits.ctypes.data),
            session,
        )

    def destroy_session(self, session: Any) -> None:
        if session:
            self._lib.claw_sampling_session_destroy(session)

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._lib.claw_sampling_engine_destroy(self._handle)
            self._handle = None

    def __del__(self):  # pragma: no cover - best effort during interpreter shutdown
        try:
            self.close()
        except Exception:
            pass

    def _sample(self, function: Any, logits_ptr: Any, session: Any) -> NativeSamplingResult:
        result = NativeSamplingResultStruct()
        status = function(
            session,
            ctypes.cast(logits_ptr, ctypes.c_void_p),
            ctypes.byref(result),
        )
        if status != 0:
            raise RuntimeError(f"native sampling failed: {self._last_error()}")
        return NativeSamplingResult(
            token_id=result.token_id,
            mask_applied=bool(result.mask_applied),
            grammar_active_before=bool(result.grammar_active_before),
            grammar_active_after=bool(result.grammar_active_after),
            grammar_completed=bool(result.grammar_completed),
            mask_ms=result.mask_ms,
            sampler_ms=result.sampler_ms,
            accept_ms=result.accept_ms,
        )

    def _last_error(self) -> str:
        value = self._lib.claw_sampling_last_error()
        return value.decode("utf-8", errors="replace") if value else "unknown native error"


def _load_library(explicit_path: str = "") -> Any:
    override = os.environ.get("RKCLAW_NATIVE_LIB")
    root = Path(__file__).resolve().parent.parent
    candidates = [
        Path(explicit_path) if explicit_path else None,
        Path(override) if override else None,
        PACKAGED_NATIVE_LIBRARY,
        SYSTEM_NATIVE_LIBRARY,
        Path.cwd() / "lib" / "librkclaw_native.so",
        root / "lib" / "librkclaw_native.so",
        root / "dist" / "native" / "lib" / "librkclaw_native.so",
    ]
    for library_name in ("rkclaw_native",):
        discovered = ctypes.util.find_library(library_name)
        if discovered:
            candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate is not None and (candidate.is_file() or not candidate.is_absolute()):
            return ctypes.CDLL(str(candidate))
    checked = ", ".join(str(path) for path in candidates if path is not None)
    raise RuntimeError(
        "native XGrammar sampler is enabled but librkclaw_native.so was not found; "
        f"run scripts/build_native.sh (checked: {checked})"
    )


def _configure_library(library: Any) -> None:
    char_pointer_array = ctypes.POINTER(ctypes.c_char_p)
    library.claw_sampling_engine_create.argtypes = [
        ctypes.c_size_t,
        char_pointer_array,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_int32),
        ctypes.c_size_t,
    ]
    library.claw_sampling_engine_create.restype = ctypes.c_void_p
    library.claw_sampling_engine_destroy.argtypes = [ctypes.c_void_p]
    library.claw_sampling_engine_destroy.restype = None
    library.claw_sampling_session_create.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(NativeSamplingParams),
    ]
    library.claw_sampling_session_create.restype = ctypes.c_void_p
    library.claw_sampling_session_destroy.argtypes = [ctypes.c_void_p]
    library.claw_sampling_session_destroy.restype = None
    for name in ("claw_sampling_session_sample_f16", "claw_sampling_session_sample_f32"):
        function = getattr(library, name)
        function.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(NativeSamplingResultStruct),
        ]
        function.restype = ctypes.c_int
    library.claw_sampling_last_error.argtypes = []
    library.claw_sampling_last_error.restype = ctypes.c_char_p
