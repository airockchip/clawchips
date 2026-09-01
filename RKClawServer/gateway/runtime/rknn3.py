from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import mmap
import os
import queue
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import anyio

from ..config import Settings
from ..parsing import get_profile
from ..schemas import GenerationRequest, Usage
from .base import InferenceEvent, RuntimeBackend, RuntimeBusyError
from .rknn3_multicard import MulticardKVCacheManager, MulticardRKNN3Pipeline, PipelineRunStats


LOGGER = logging.getLogger("gateway.rknn3")


@dataclass
class _Job:
    prompt: str
    prompt_tokens: list[int]
    request: GenerationRequest
    events: asyncio.Queue[InferenceEvent]
    system_prompt: str = ""
    cancelled: threading.Event = field(default_factory=threading.Event)
    finished: bool = False
    tokens: list[int] = field(default_factory=list)
    decoded: str = ""
    sent_length: int = 0
    terminal_state: int = 2
    stopped_by_word: bool = False
    first_token_time: float | None = None
    sampling_state: Any | None = None
    sampling_callback_count: int = 0
    sampling_callback_total_ms: float = 0.0
    sampling_callback_max_ms: float = 0.0


@dataclass(frozen=True)
class _MemorySnapshot:
    rss_kb: int
    pss_kb: int
    anon_kb: int
    file_kb: int


@dataclass(frozen=True)
class _SamplingParameters:
    top_k: int
    top_p: float
    temperature: float
    repeat_penalty: float
    frequency_penalty: float
    presence_penalty: float


class RKNN3LiteBackend(RuntimeBackend):
    """Single-session RKNN3Lite backend owned by a FIFO worker thread."""

    def __init__(self, settings: Settings, tokenizer: Any):
        self.settings = settings
        self.tokenizer = tokenizer
        self._ready = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._jobs: queue.Queue[_Job | None] = queue.Queue(maxsize=settings.queue_size)
        self._worker: threading.Thread | None = None
        self._active: _Job | None = None
        self._rknn: Any = None
        self._embeddings: Any = None
        self._callback_refs: list[Any] = []
        self._types: Any = None
        self._closing = False
        self._system_prompt_text: str | None = None
        self._system_prompt_tokens: list[int] = []
        self._system_prompt_loaded: bool = False
        self._kv_clear_all: Any = None
        self._in_tool_call: bool = False
        self._model_type: str = ""
        self._per_layer_embeds: Any = None
        self._rope_caches: dict[str, dict[str, Any]] = {}
        self._rope_mmap: Any = None
        self._rope_mmap_addr: int = 0
        self._rope_mmap_base_obj: Any = None
        self._rope_file: Any = None
        self._memory_baseline: _MemorySnapshot | None = None
        self._memory_previous: _MemorySnapshot | None = None
        self._custom_sampler: Any = None
        self._sampling_affinity_checked = False
        self._applied_sampling: _SamplingParameters | None = None
        self._multicard: MulticardRKNN3Pipeline | None = None
        self._multicard_cache: MulticardKVCacheManager | None = None
        self._multicard_native_history_tokens: list[int] = []
        self._multicard_native_is_main: bool = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def model_type(self) -> str:
        return self._model_type

    def _is_gemma4(self) -> bool:
        return self.model_type.lower() == "gemma4"

    def _logits_name(self) -> bytes:
        """Resolve the RKNN output tensor name from the model profile."""
        return get_profile(self.model_type).logits_name.encode("utf-8")

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._initialize)
        self._worker = threading.Thread(target=self._worker_main, name="rknn3-inference", daemon=True)
        self._worker.start()
        self._ready = True

    async def close(self) -> None:
        self._log_memory("before runtime close")
        self._ready = False
        self._closing = True
        active = self._active
        if active is not None:
            active.cancelled.set()
            if self._multicard is not None:
                await asyncio.to_thread(self._multicard.stop_all)
            elif self._rknn is not None:
                await asyncio.to_thread(self._rknn.session_stop)
        while True:
            try:
                pending = self._jobs.get_nowait()
            except queue.Empty:
                break
            if pending is not None:
                pending.cancelled.set()
                self._emit(pending, InferenceEvent("error", error="Server is shutting down"))
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            await asyncio.to_thread(self._jobs.put, None)
        if self._worker is not None:
            await asyncio.to_thread(self._worker.join, 10)
        if self._custom_sampler is not None:
            self._custom_sampler.close()
            self._custom_sampler = None
        if self._multicard is not None:
            await asyncio.to_thread(self._multicard.release)
            self._multicard = None
            self._multicard_cache = None
        if self._rknn is not None:
            await asyncio.to_thread(self._rknn.release)
            self._rknn = None
        self._rope_mmap_base_obj = None
        if self._rope_mmap is not None:
            self._rope_mmap.close()
            self._rope_mmap = None
        if self._rope_file is not None:
            self._rope_file.close()
            self._rope_file = None
        self._log_memory("after runtime release")

    async def _iterate(self, job: _Job) -> AsyncIterator[InferenceEvent]:
        try:
            while True:
                event = await job.events.get()
                yield event
                if event.type in {"done", "error"}:
                    job.finished = True
                    break
        finally:
            if not job.finished:
                LOGGER.info(
                    "RKNN request cancellation received: active=%s prompt_chars=%d generated_tokens=%d",
                    self._active is job,
                    len(job.prompt),
                    len(job.tokens),
                )
                job.cancelled.set()
                if self._active is job and (self._rknn is not None or self._multicard is not None):
                    # AnyIO uses level cancellation, so every await in an
                    # already-cancelled scope is cancelled again. Shield this
                    # cleanup so session_stop completes; the original request
                    # cancellation still propagates after this finally block.
                    with anyio.CancelScope(shield=True):
                        if self._multicard is not None:
                            await asyncio.to_thread(self._multicard.stop_all)
                        else:
                            await asyncio.to_thread(self._rknn.session_stop)

    def generate(
        self,
        prompt: str,
        prompt_tokens: list[int],
        request: GenerationRequest,
        system_prompt: str = "",
    ) -> AsyncIterator[InferenceEvent]:
        if not self._ready or self._loop is None:
            raise RuntimeError("RKNN3 runtime is not ready")
        job = _Job(prompt, list(prompt_tokens), request, asyncio.Queue(), system_prompt=system_prompt)
        try:
            self._jobs.put_nowait(job)
        except queue.Full as exc:
            raise RuntimeBusyError("The inference queue is full") from exc
        return self._iterate(job)

    def _initialize(self) -> None:
        if self.settings.multicard_enabled:
            self._initialize_multicard()
            return
        self._log_memory("initialization start")
        for name, value in {
            "RKNN model": self.settings.rknn_path,
            "RKNN weight": self.settings.weight_path,
            "token embedding": self.settings.embed_path,
        }.items():
            if not Path(value).is_file():
                raise RuntimeError(f"{name} file not found: {value}")
        try:
            import numpy as np
            from rknn3lite.api import RKLLMCallback, RKNN3Lite, LLMGetEmbedCallback, LLMResultCallback
            from rknn3lite.api import rknn3_types
        except ImportError as exc:
            hint = f" Install {self.settings.toolkit_lite_wheel}." if self.settings.toolkit_lite_wheel else ""
            raise RuntimeError("rknn3-toolkit-lite is not installed for this Python/aarch64 environment." + hint) from exc

        self._types = rknn3_types
        vocab_size = len(self.tokenizer)
        raw = np.fromfile(self.settings.embed_path, dtype=np.float16)
        if raw.size == 0 or raw.size % vocab_size:
            raise RuntimeError(f"Embedding size is incompatible with tokenizer vocab_size={vocab_size}")
        self._embeddings = raw.reshape(vocab_size, raw.size // vocab_size)

        if self.settings.enable_xgrammar and not self.settings.enable_native_sampling:
            raise RuntimeError("xgrammar.enabled requires native_sampling.enabled")
        enable_custom_sampling = self.settings.enable_native_sampling
        self._rknn = RKNN3Lite(llm_mode=True, verbose=self.settings.debug_logs)
        try:
            if self._rknn.load_rknn(self.settings.rknn_path, self.settings.weight_path) != 0:
                raise RuntimeError("Failed to load RKNN model and weight")
            # llm_args/callback are omitted here so the LLM session stays
            # uninitialized -- init_llm_session below creates it after querying
            # model config. Mirrors the C++ split init flow and keeps a single
            # uniform path for all model types.
            self._log_memory("after load_rknn paths")
            if self._rknn.init_runtime(
                target=self.settings.runtime_target,
                core_mask=self.settings.core_mask,
                device_id=self.settings.device_id.encode("utf-8") if self.settings.device_id else None,
            ) != 0:
                raise RuntimeError("Failed to initialize RKNN3 LLM runtime")
            self._log_memory("after init_runtime")
            runtime_context = 0
            model_config = self._rknn.rknn3_query(rknn3_types.RKNN3QueryCmd.RKNN3_QUERY_LLM_CONFIG)
            if model_config is not None:
                runtime_vocab = int(getattr(model_config, "vocab_size", 0))
                runtime_embed = int(getattr(model_config, "embedding_dim", 0))
                runtime_context = int(getattr(model_config, "max_ctx_len", 0))
                raw_model_type = getattr(model_config, "model_type", None)
                if raw_model_type:
                    self._model_type = raw_model_type.decode("utf-8", errors="replace") if isinstance(raw_model_type, (bytes, bytearray)) else str(raw_model_type)
                LOGGER.info("RKNN3 LLM model_type=%s vocab_size=%d embedding_dim=%d max_ctx_len=%d",
                            self._model_type or "<unknown>", runtime_vocab, runtime_embed, runtime_context)
                if runtime_vocab and runtime_vocab != vocab_size:
                    raise RuntimeError(f"Runtime vocab_size={runtime_vocab} does not match tokenizer vocab_size={vocab_size}")
                if runtime_embed and runtime_embed != self._embeddings.shape[1]:
                    raise RuntimeError(
                        f"Runtime embedding_dim={runtime_embed} does not match embedding file dimension={self._embeddings.shape[1]}"
                    )
                if runtime_context and self.settings.max_context_tokens > runtime_context:
                    raise RuntimeError(
                        f"Configured context size {self.settings.max_context_tokens} exceeds model maximum {runtime_context}"
                    )
            if enable_custom_sampling:
                self._initialize_custom_sampler()
            result_cb = LLMResultCallback(self._result_callback)
            embed_cb = LLMGetEmbedCallback(self._embed_callback)
            callback_userdata = ctypes.c_uint8(0)
            callback_userdata_ptr = ctypes.cast(ctypes.pointer(callback_userdata), ctypes.c_void_p)
            callback = RKLLMCallback()
            callback.result_callback = result_cb
            callback.result_userdata = callback_userdata_ptr
            callback.embed_callback = embed_cb
            callback.embed_userdata = callback_userdata_ptr
            self._callback_refs = [result_cb, embed_cb, callback_userdata, callback_userdata_ptr, callback]
            if enable_custom_sampling:
                from rknn3lite.api import LLMSamplingCallback

                sampling_cb = LLMSamplingCallback(self._sampling_callback)
                callback.sampling_callback = sampling_cb
                callback.sampling_userdata = callback_userdata_ptr
                self._callback_refs.append(sampling_cb)
            if self._is_gemma4():
                self._setup_gemma4_inputs(callback, model_config, vocab_size)
            if self._rknn.init_llm_session(
                llm_args=[self._initial_llm_args(vocab_size)],
                llm_callback=callback,
            ) != 0:
                raise RuntimeError("Failed to initialize RKNN3 LLM session")
            self._applied_sampling = self._sampling_parameters(self.settings)
            # set_chat_template requires the session to be initialized.
            if self._rknn.set_chat_template("", "", "") != 0:
                raise RuntimeError("Failed to clear RKNN3 chat template")
            LOGGER.info("RKNN3 chat template cleared")
            self._init_kv_cache(runtime_context or self.settings.max_context_tokens)
            self._log_memory("initialization complete")
        except Exception:
            try:
                self._rknn.release()
            finally:
                self._rknn = None
                if self._custom_sampler is not None:
                    self._custom_sampler.close()
                    self._custom_sampler = None
            raise

    def _initialize_custom_sampler(self) -> None:
        """Create the native sampler owner shared by single/multicard paths."""
        from ..xgrammar_sampling import SamplingPipeline

        self._custom_sampler = SamplingPipeline(
            tokenizer=self.tokenizer,
            enable_xgrammar=self.settings.enable_xgrammar,
            enable_native_sampling=self.settings.enable_native_sampling,
            native_seed=self.settings.native_sampling_seed,
            repeat_last_n=self.settings.native_repeat_last_n,
            penalize_newline=self.settings.native_penalize_newline,
            debug=self.settings.xgrammar_debug,
            model_structure=self.settings.xgrammar_model_structure,
            sampling_library=self.settings.sampling_library,
        )
        self._custom_sampler.start()

    def _initialize_multicard(self) -> None:
        self._log_memory("multicard initialization start")
        if not Path(self.settings.embed_path).is_file():
            raise RuntimeError(f"token embedding file not found: {self.settings.embed_path}")
        try:
            import numpy as np
            from rknn3lite.api import rknn3_types
        except ImportError as exc:
            hint = (
                f" Install {self.settings.toolkit_lite_wheel}."
                if self.settings.toolkit_lite_wheel
                else ""
            )
            raise RuntimeError(
                "rknn3-toolkit-lite is not installed for this Python/aarch64 environment." + hint
            ) from exc

        self._types = rknn3_types
        vocab_size = len(self.tokenizer)
        raw = np.fromfile(self.settings.embed_path, dtype=np.float16)
        if raw.size == 0 or raw.size % vocab_size:
            raise RuntimeError(f"Embedding size is incompatible with tokenizer vocab_size={vocab_size}")
        self._embeddings = raw.reshape(vocab_size, raw.size // vocab_size)
        if self.settings.enable_xgrammar and not self.settings.enable_native_sampling:
            raise RuntimeError("xgrammar.enabled requires native_sampling.enabled")

        try:
            if self.settings.enable_native_sampling:
                self._initialize_custom_sampler()
            self._multicard = MulticardRKNN3Pipeline(
                self.settings,
                self.tokenizer,
                self._embeddings,
                self._result_callback,
                self._embed_callback,
                self._sampling_callback if self._custom_sampler is not None else None,
            )
            self._multicard.initialize()
            self._applied_sampling = self._sampling_parameters(self.settings)
            self._model_type = self._multicard.model_type
            if self.settings.kv_cache_dir:
                self._multicard.configure_kv_cache()
                self._multicard_cache = MulticardKVCacheManager(
                    self._multicard,
                    self.settings.kv_cache_dir,
                )
                payload = self._multicard_cache.load("system")
                if self._restore_multicard_system_metadata(payload):
                    self._multicard_native_history_tokens = list(self._system_prompt_tokens)
                    self._multicard_native_is_main = True
            self._log_memory("multicard initialization complete")
        except Exception:
            if self._multicard is not None:
                self._multicard.release()
                self._multicard = None
            if self._custom_sampler is not None:
                self._custom_sampler.close()
                self._custom_sampler = None
            raise

    def _restore_multicard_system_metadata(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        system_prompt = payload.get("system_prompt")
        tokens = payload.get("tokens")
        if not isinstance(system_prompt, str):
            return False
        if not isinstance(tokens, list) or not all(isinstance(token, int) for token in tokens):
            return False
        self._system_prompt_text = system_prompt
        self._system_prompt_tokens = list(tokens)
        self._system_prompt_loaded = True
        return True

    def _initial_llm_args(self, vocab_size: int) -> dict[str, Any]:
        eos = self.tokenizer.special_eos_ids
        bos = self.tokenizer.special_bos_ids
        return {
            "max_new_tokens": self.settings.max_new_tokens,
            "top_k": self.settings.top_k,
            "top_p": self.settings.top_p,
            "temperature": self.settings.temperature,
            "repeat_penalty": self.settings.repeat_penalty,
            "frequency_penalty": self.settings.frequency_penalty,
            "presence_penalty": self.settings.presence_penalty,
            "vocab_size": vocab_size,
            "special_eos_id": eos,
            "special_bos_id": bos,
            "linefeed_id": self.tokenizer.linefeed_id,
            "max_context_len": self.settings.max_context_tokens,
            "keep_history": 0,
            "logits_name": self._logits_name(),
        }

    # ------------------------------------------------------------------
    # Gemma4 linear-attention extras
    # ------------------------------------------------------------------

    _ROPE_CACHE_NAMES = (
        "rope_cos_cache_0", "rope_sin_cache_0",
        "rope_cos_cache_1", "rope_sin_cache_1",
    )

    _DTYPE_ELEM_SIZE = {
        0: 4,   # FLOAT32
        1: 2,   # FLOAT16
        2: 1,   # INT8
        3: 1,   # UINT8
        4: 2,   # INT16
        5: 2,   # UINT16
        6: 4,   # INT32
        7: 4,   # UINT32
        8: 8,   # INT64
        9: 8,   # UINT64
        10: 1,  # BOOL
        11: 1,  # INT4
        12: 1,  # FLOAT8E4M3FN
        13: 2,  # BFLOAT16
        14: 1,  # FLOAT8E8M0
        15: 1,  # FLOAT4E2M1
    }

    def _setup_gemma4_inputs(self, callback: Any, model_config: Any, vocab_size: int) -> None:
        """Wire up Gemma4's per_layer_inputs and rope cache host storage."""
        import numpy as np
        from rknn3lite.api import LLMInputCallback, LLMOutputCallback

        if not self.settings.per_layer_embed_path:
            raise RuntimeError("Gemma4 requires per_layer_embed_path to be configured")
        if not Path(self.settings.per_layer_embed_path).is_file():
            raise RuntimeError(f"Gemma4 per_layer_embed file not found: {self.settings.per_layer_embed_path}")

        ple_raw = np.memmap(self.settings.per_layer_embed_path, dtype=np.float16, mode="r")
        if ple_raw.size == 0 or ple_raw.size % vocab_size:
            raise RuntimeError(
                f"Per-layer embedding size is incompatible with vocab_size={vocab_size}"
            )
        self._per_layer_embeds = ple_raw.reshape(vocab_size, ple_raw.size // vocab_size)

        need_rope = bool(getattr(model_config, "rope_cache_host_storage", 0))
        if need_rope:
            self._load_rope_cache()

        ext_indices = self._query_ext_input_indices(need_rope)
        if not ext_indices:
            raise RuntimeError("Gemma4 model exposes no per_layer_inputs/rope cache input tensors")

        input_cb = LLMInputCallback(self._input_callback)
        output_cb = LLMOutputCallback(self._output_callback)
        callback.input_callback = input_cb
        callback.input_userdata = callback.result_userdata
        callback.output_callback = output_cb
        callback.output_userdata = callback.result_userdata
        ext_index_array = (ctypes.c_int32 * len(ext_indices))(*ext_indices)
        callback.input_tensors_index = ext_index_array
        callback.n_input_tensors = len(ext_indices)
        self._callback_refs.extend([input_cb, output_cb, ext_index_array])

    def _load_rope_cache(self) -> None:
        if not self.settings.rope_cache_path:
            raise RuntimeError("Gemma4 model requires rope_cache_path (rope_cache_host_storage=1)")
        if not Path(self.settings.rope_cache_path).is_file():
            raise RuntimeError(f"Gemma4 rope cache file not found: {self.settings.rope_cache_path}")

        self._rope_file = open(self.settings.rope_cache_path, "rb")
        # ACCESS_COPY lets ctypes.from_buffer reach the underlying address
        # without pulling the whole file into the Python heap.
        self._rope_mmap = mmap.mmap(self._rope_file.fileno(), 0, access=mmap.ACCESS_COPY)
        self._rope_mmap_base_obj = ctypes.c_char.from_buffer(self._rope_mmap)
        self._rope_mmap_addr = ctypes.addressof(self._rope_mmap_base_obj)

        header_size = struct.unpack("<Q", self._rope_mmap[:8])[0]
        header = json.loads(self._rope_mmap[8:8 + header_size].decode("utf-8"))
        meta_index = json.loads(header["__metadata__"]["index"])
        data_base = 8 + header_size

        for name in self._ROPE_CACHE_NAMES:
            meta_t = meta_index[name]
            tensor = header[name]
            shape = tensor["shape"]
            offsets = tensor["data_offsets"]
            if len(shape) != 5:
                raise RuntimeError(f"Rope tensor {name}: expected 5-D NC1HWC2, got shape={shape}")
            self._rope_caches[name] = {
                "dtype": int(meta_t["dtype"]),
                "layout": int(meta_t["layout"]),
                "shape": shape,
                "offset": data_base + int(offsets[0]),
            }
            LOGGER.info(
                "Loaded %-18s dtype=%-2d shape=%s",
                name, self._rope_caches[name]["dtype"], shape,
            )

    def _query_ext_input_indices(self, need_rope: bool) -> list[int]:
        rknn3_types = self._types
        io_num = self._rknn.rknn3_query(rknn3_types.RKNN3QueryCmd.RKNN3_QUERY_IN_OUT_NUM)
        if io_num is None:
            raise RuntimeError("Failed to query RKNN3 IO number for Gemma4 inputs")
        indices: list[int] = []
        for i in range(int(io_num.n_input)):
            attr = self._rknn.rknn3_query(rknn3_types.RKNN3QueryCmd.RKNN3_QUERY_INPUT_ATTR, index=i)
            if attr is None:
                raise RuntimeError(f"Failed to query input attr index={i}")
            name = self._tensor_name(attr)
            if name == "per_layer_inputs":
                indices.append(i)
            elif need_rope and ("rope_cos_cache" in name or "rope_sin_cache" in name):
                indices.append(i)
        return indices

    @staticmethod
    def _tensor_name(attr: Any) -> str:
        name = attr.name
        if isinstance(name, (bytes, bytearray)):
            return name.split(b"\0", 1)[0].decode("utf-8", errors="ignore")
        if isinstance(name, str):
            return name.split("\0", 1)[0]
        try:
            return ctypes.string_at(name).split(b"\0", 1)[0].decode("utf-8", errors="ignore")
        except Exception:
            return bytes(name).split(b"\0", 1)[0].decode("utf-8", errors="ignore")

    def _input_callback(self, userdata, input_tensors, n_input_tensors, param) -> int:
        try:
            import numpy as np
            p = param.contents if hasattr(param, "contents") else param
            num_tokens = int(p.num_tokens)
            pos = int(p.pos)
            tokens = [int(p.tokens[i]) for i in range(num_tokens)]
            embedding_dim = self._per_layer_embeds.shape[1]

            for i in range(int(n_input_tensors)):
                tensor = input_tensors[i]
                attr = tensor.attr.contents if hasattr(tensor.attr, "contents") else tensor.attr
                mem = tensor.mem.contents if hasattr(tensor.mem, "contents") else tensor.mem
                name = self._tensor_name(attr)

                if name in self._rope_caches:
                    self._fill_rope_cache(name, attr, mem, pos)
                    continue

                if name != "per_layer_inputs":
                    continue

                addr = mem.virt_addr
                if hasattr(addr, "value"):
                    addr = addr.value
                dst = np.ctypeslib.as_array(
                    ctypes.cast(ctypes.c_void_p(addr), ctypes.POINTER(ctypes.c_uint16)),
                    shape=(num_tokens * embedding_dim,),
                ).view(np.float16)
                for t, token_id in enumerate(tokens):
                    begin = t * embedding_dim
                    end = begin + embedding_dim
                    if 0 <= token_id < self._per_layer_embeds.shape[0]:
                        dst[begin:end] = self._per_layer_embeds[token_id]
                    else:
                        dst[begin:end] = 0
        except Exception:
            LOGGER.exception("Gemma4 input_callback failed")
            return -1
        return 0

    def _fill_rope_cache(self, name: str, attr: Any, mem: Any, pos: int) -> None:
        cache = self._rope_caches[name]
        elem_sz = self._DTYPE_ELEM_SIZE.get(int(cache["dtype"]), 1)
        c1 = int(cache["shape"][1])
        c2_bytes = int(cache["shape"][4]) * elem_sz
        src_stride = int(cache["shape"][3]) * c2_bytes
        dst_stride = int(attr.shape[3]) * c2_bytes
        src_base = int(cache["offset"]) + pos * c2_bytes

        addr = mem.virt_addr
        if hasattr(addr, "value"):
            addr = addr.value
        # Address arithmetic on the mmap base avoids per-step bytes copies.
        for c in range(c1):
            src_addr = self._rope_mmap_addr + src_base + c * src_stride
            dst_addr = addr + c * dst_stride
            ctypes.memmove(dst_addr, src_addr, dst_stride)

    def _output_callback(self, userdata, output_tensors, n_output_tensors, state) -> int:
        return 0

    # ------------------------------------------------------------------
    # System prompt KV cache
    # ------------------------------------------------------------------

    @property
    def _system_prompt_file(self) -> Path | None:
        if not self.settings.kv_cache_dir:
            return None
        return Path(self.settings.kv_cache_dir) / "system_prompt.cache"

    @property
    def _system_prompt_metadata_file(self) -> Path | None:
        if not self._system_prompt_file:
            return None
        return Path(str(self._system_prompt_file) + ".meta.json")

    def _init_kv_cache(self, max_context: int) -> None:
        """Initialise KV cache directory, checkpoint policy, and cached system prompt."""
        if not self.settings.kv_cache_dir:
            return
        from rknn3lite.api.rknn3_types import (
            RKNN3KVCacheClearPolicy,
            RKNN3KVCachePolicy,
            RKNN3KVCachePolicyParam,
        )

        self._kv_clear_all = RKNN3KVCacheClearPolicy.RKNN3_KVCACHE_CLEAR_ALL
        Path(self.settings.kv_cache_dir).mkdir(parents=True, exist_ok=True)

        ret = self._rknn.set_kvcache_policy(RKNN3KVCachePolicy.RKNN3_KVCACHE_POLICY_NORMAL)
        if ret != 0:
            raise RuntimeError(f"Failed to set KV cache policy: ret={ret}")
        LOGGER.info("KV cache policy set: NORMAL")

        if self.settings.checkpoint_enabled:
            start_pos, interval, max_count = self.settings.checkpoint_policy_values(max_context)
            kvcache_policy_param = RKNN3KVCachePolicyParam()
            ctypes.memset(
                ctypes.byref(kvcache_policy_param),
                0,
                ctypes.sizeof(kvcache_policy_param),
            )
            kvcache_policy_param.save_checkpoint.checkpoint_start_pos = start_pos
            kvcache_policy_param.save_checkpoint.checkpoint_interval = interval
            kvcache_policy_param.save_checkpoint.max_checkpoint_count = max_count
            ret = self._rknn.set_kvcache_policy(
                RKNN3KVCachePolicy.RKNN3_KVCACHE_POLICY_SAVE_CHECKPOINT,
                param=kvcache_policy_param,
            )
            if ret != 0:
                raise RuntimeError(f"Failed to set KV cache checkpoint policy: ret={ret}")
            LOGGER.info(
                "KV cache policy set: SAVE_CHECKPOINT start_pos=%d interval=%d max_count=%d",
                start_pos,
                interval,
                max_count,
            )
        else:
            LOGGER.info("KV cache checkpoint policy disabled by configuration")

        if self._system_prompt_file and self._system_prompt_file.exists():
            self._load_system_prompt_kv_cache()

    def _load_system_prompt_kv_cache(self) -> None:
        cache_path = self._system_prompt_file
        metadata_path = self._system_prompt_metadata_file
        if cache_path is None or metadata_path is None:
            return
        if not metadata_path.is_file():
            try:
                with cache_path.open("rb") as handle:
                    legacy_magic = handle.read(4)
            except OSError:
                legacy_magic = b""
            if legacy_magic == b"RKVC":
                LOGGER.warning(
                    "Legacy system prompt KV cache found; skipping it and rebuilding in path-load format on the next main request"
                )
            else:
                LOGGER.warning("System prompt KV cache metadata missing: %s", metadata_path)
            return

        self._log_memory("before system KV cache path load")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or metadata.get("version") != 2:
                raise ValueError("unsupported metadata version")
            system_prompt = metadata["system_prompt"]
            tokens = metadata["tokens"]
            cache_size = metadata["cache_size"]
            if not isinstance(system_prompt, str):
                raise ValueError("system_prompt must be a string")
            if not isinstance(tokens, list) or not all(isinstance(token, int) for token in tokens):
                raise ValueError("tokens must be an integer array")
            if not isinstance(cache_size, int) or cache_size != cache_path.stat().st_size:
                raise ValueError("cache file size does not match metadata")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.error("Invalid system prompt KV cache metadata %s: %s", metadata_path, exc)
            return
        ret = self._rknn.load_kvcache(kvcache_path=str(cache_path))
        if ret != 0:
            LOGGER.error("Failed to load system prompt KV cache: ret=%d", ret)
            return
        self._log_memory("after system KV cache path load")

        self._system_prompt_text = system_prompt
        self._system_prompt_tokens = tokens
        self._system_prompt_loaded = True
        LOGGER.info(
            "System prompt KV cache loaded: tokens=%d kv_size=%d",
            len(tokens), cache_size,
        )

    def _save_system_prompt_kv_cache(self, system_prompt: str) -> None:
        tokens = self.tokenizer.encode(system_prompt, add_special_tokens=False)
        cache_path = self._system_prompt_file
        metadata_path = self._system_prompt_metadata_file
        if cache_path is None or metadata_path is None:
            return

        self._log_memory("before native system KV cache save")
        ret = self._rknn.save_kvcache(str(cache_path))
        if ret != 0:
            LOGGER.error("Failed to save system prompt KV cache: ret=%d", ret)
            return

        metadata_tmp_path = Path(str(metadata_path) + ".tmp")
        try:
            metadata = {
                "version": 2,
                "system_prompt": system_prompt,
                "tokens": tokens,
                "cache_size": cache_path.stat().st_size,
            }
            metadata_tmp_path.write_text(
                json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(metadata_tmp_path, metadata_path)

            self._system_prompt_tokens = tokens
            LOGGER.info(
                "System prompt KV cache saved: tokens=%d kv_size=%d",
                len(tokens), metadata["cache_size"],
            )
        finally:
            try:
                os.unlink(metadata_tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _worker_main(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                break
            if self._closing or job.cancelled.is_set():
                self._emit(job, InferenceEvent("error", error="Request was cancelled"))
                continue
            self._active = job
            if self._multicard is not None:
                self._worker_multicard(job)
                continue
            try:
                LOGGER.info("Starting RKNN request: prompt_chars=%d max_new_tokens=%d thinking=%s", len(job.prompt), job.request.max_new_tokens, job.request.enable_thinking)
                if self._custom_sampler is None:
                    self._set_sampling(job.request)

                # --- System prompt KV cache ---------------------------------
                system_prompt = job.system_prompt
                is_main_session = self.settings.kv_cache_system_marker and self.settings.kv_cache_system_marker in system_prompt
                LOGGER.info("is_main_session=%s prompt_chars=%d system_prompt_chars=%d", is_main_session, len(job.prompt), len(system_prompt))

                if is_main_session:
                    # Restore session cache when returning from tool call
                    if self._in_tool_call and self._system_prompt_file:
                        session_cache = os.path.join(self.settings.kv_cache_dir, "session.cache")
                        if os.path.isfile(session_cache):
                            try:
                                self._log_memory("before session KV cache restore")
                                ret = self._rknn.load_kvcache(kvcache_path=session_cache)
                                if ret != 0:
                                    raise RuntimeError(f"Failed to load session KV cache: ret={ret}")
                                self._log_memory("after session KV cache path load")
                                LOGGER.info("Restored session KV cache after tool call")
                            except Exception:
                                LOGGER.exception("Failed to restore session KV cache")
                            finally:
                                try:
                                    os.unlink(session_cache)
                                except OSError:
                                    pass
                            self._log_memory("after session KV cache file removal")
                        self._in_tool_call = False

                    if system_prompt and self._system_prompt_file:
                        if not self._system_prompt_loaded or system_prompt != self._system_prompt_text:
                            LOGGER.info("System prompt changed or first run – pre-filling")
                            # self._rknn.clear_kvcache(self._kv_clear_all)
                            saved_tokens = list(job.tokens)
                            saved_decoded = job.decoded
                            saved_sent = job.sent_length

                            system_prompt_tokens = self.tokenizer.encode(system_prompt, add_special_tokens=False)
                            self._log_memory("before system prompt prefill")
                            if self._custom_sampler is not None:
                                job.sampling_state = self._custom_sampler.create_state(job.request)
                            self._rknn.session_run(
                                tokens=system_prompt_tokens,
                                keep_history=False,
                                max_new_tokens=1,
                                enable_thinking=False,
                            )
                            if self._custom_sampler is not None:
                                self._custom_sampler.close_state(job.sampling_state)
                                job.sampling_state = None
                            self._log_memory("after system prompt prefill")

                            job.tokens = saved_tokens
                            job.decoded = saved_decoded
                            job.sent_length = saved_sent
                            job.terminal_state = 2

                            self._save_system_prompt_kv_cache(system_prompt)
                            LOGGER.info("System prompt save finish!")
                            
                            self._log_memory("after system KV cache save returned")
                            self._system_prompt_text = system_prompt
                            self._system_prompt_loaded = True
                else:
                    # Tool call / sub-agent: save session KV cache before running
                    if self._system_prompt_loaded and self._system_prompt_file:
                        session_cache = os.path.join(self.settings.kv_cache_dir, "session.cache")
                        if not os.path.isfile(session_cache):
                            try:
                                self._log_memory("before session KV cache save")
                                self._rknn.save_kvcache(session_cache)
                                self._log_memory("after session KV cache save")
                                LOGGER.info("Saved session KV cache before tool call")
                            except Exception:
                                LOGGER.exception("Failed to save session KV cache")
                        self._in_tool_call = True
                # ------------------------------------------------------------

                if self._custom_sampler is not None:
                    job.sampling_state = self._custom_sampler.create_state(job.request)
                job.first_token_time = None
                self._log_memory("before session_run")
                ret, perf = self._rknn.session_run(
                    tokens=job.prompt_tokens,
                    keep_history=False,
                    max_new_tokens=job.request.max_new_tokens,
                    enable_thinking=job.request.enable_thinking,
                )
                self._log_memory("after session_run")
                self._log_perf(perf, job.first_token_time)
                self._flush_decoded(job)
                self._log_sampling_timing(job)
                if ret != 0 or job.terminal_state == 5:
                    self._emit(job, InferenceEvent("error", error="RKNN3 inference failed"))
                else:
                    prompt_tokens = int(perf[1]) if len(perf) > 1 else len(job.prompt_tokens)
                    completion_tokens = int(perf[0]) if perf else len(job.tokens)
                    finish = "length" if job.terminal_state == 4 else "stop"
                    self._emit(job, InferenceEvent("done", finish_reason=finish, usage=Usage(prompt_tokens, completion_tokens)))
            except Exception as exc:
                self._emit(job, InferenceEvent("error", error=str(exc)))
            finally:
                if self._custom_sampler is not None:
                    self._custom_sampler.close_state(job.sampling_state)
                    job.sampling_state = None
                if self.settings.clear_kv_cache:
                    try:
                        self._rknn.clear_kvcache()
                        self._log_memory("after clear_kvcache")
                    except Exception:
                        pass
                self._active = None

    def _worker_multicard(self, job: _Job) -> None:
        assert self._multicard is not None
        is_main_session = False
        try:
            LOGGER.info(
                "Starting multicard RKNN request: prompt_chars=%d max_new_tokens=%d thinking=%s stages=%d",
                len(job.prompt),
                job.request.max_new_tokens,
                job.request.enable_thinking,
                self._multicard.stage_count,
            )
            if self._custom_sampler is None:
                self._set_sampling(job.request)
            input_tokens, keep_history, is_main_session = self._prepare_multicard_input(job)
            if self._custom_sampler is not None:
                job.sampling_state = self._custom_sampler.create_state(job.request)
            job.first_token_time = None
            self._log_memory("before multicard generation")
            stats = self._multicard.generate(
                input_tokens,
                max_new_tokens=job.request.max_new_tokens,
                enable_thinking=job.request.enable_thinking,
                keep_history=keep_history,
                cancelled=job.cancelled,
                stop_requested=lambda: job.stopped_by_word,
            )
            self._log_memory("after multicard generation")
            self._flush_decoded(job)
            self._log_sampling_timing(job)
            self._log_multicard_perf(stats, job.first_token_time)
            if job.terminal_state == 5:
                raise RuntimeError("RKNN3 multicard inference failed")

            if is_main_session:
                self._multicard_native_history_tokens = list(job.prompt_tokens) + list(job.tokens)
                self._multicard_native_is_main = True
            finish = "length" if stats.reached_limit else "stop"
            self._emit(
                job,
                InferenceEvent(
                    "done",
                    finish_reason=finish,
                    usage=Usage(len(job.prompt_tokens), len(job.tokens)),
                ),
            )
        except Exception as exc:
            try:
                self._multicard.clear_kvcache()
            except Exception:
                LOGGER.exception("Failed to clear multicard KV cache after request error")
            self._multicard_native_history_tokens = []
            self._multicard_native_is_main = False
            self._emit(job, InferenceEvent("error", error=str(exc)))
        finally:
            if self._custom_sampler is not None:
                self._custom_sampler.close_state(job.sampling_state)
                job.sampling_state = None
            if self.settings.clear_kv_cache:
                try:
                    self._multicard.clear_kvcache()
                except Exception:
                    LOGGER.exception("Failed to clear multicard KV cache after request")
                self._multicard_native_history_tokens = []
                self._multicard_native_is_main = False
            self._active = None

    def _prepare_multicard_input(self, job: _Job) -> tuple[list[int], bool, bool]:
        assert self._multicard is not None
        marker = self.settings.kv_cache_system_marker
        is_main_session = bool(marker and marker in job.system_prompt)
        LOGGER.info(
            "multicard is_main_session=%s prompt_tokens=%d system_prompt_chars=%d native_prefix_tokens=%d",
            is_main_session,
            len(job.prompt_tokens),
            len(job.system_prompt),
            len(self._multicard_native_history_tokens),
        )

        if not is_main_session:
            if (
                self._multicard_cache is not None
                and self._multicard_native_is_main
                and self._multicard_native_history_tokens
            ):
                self._in_tool_call = self._multicard_cache.save(
                    "session",
                    {"history_tokens": self._multicard_native_history_tokens},
                )
            self._reset_multicard_native_cache()
            return list(job.prompt_tokens), False, False

        if self._multicard_cache is None:
            self._reset_multicard_native_cache()
            return list(job.prompt_tokens), False, True

        if self._in_tool_call:
            payload = self._multicard_cache.load("session")
            history = self._history_tokens_from_payload(payload)
            if history and self._tokens_start_with(job.prompt_tokens, history):
                self._multicard_native_history_tokens = history
                self._multicard_native_is_main = True
            else:
                self._reset_multicard_native_cache()
            self._in_tool_call = False

        if (
            self._multicard_native_is_main
            and self._multicard_native_history_tokens
            and self._tokens_start_with(job.prompt_tokens, self._multicard_native_history_tokens)
        ):
            suffix = job.prompt_tokens[len(self._multicard_native_history_tokens):]
            if suffix:
                return list(suffix), True, True

        if job.system_prompt:
            system_tokens = self.tokenizer.encode(job.system_prompt, add_special_tokens=False)
            payload = self._multicard_cache.load("system")
            loaded = self._restore_multicard_system_metadata(payload)
            if not loaded or self._system_prompt_text != job.system_prompt or self._system_prompt_tokens != system_tokens:
                self._reset_multicard_native_cache()
                if system_tokens:
                    self._multicard.prefill(
                        list(system_tokens),
                        keep_history=False,
                        cancelled=job.cancelled,
                    )
                    saved = self._multicard_cache.save(
                        "system",
                        {"system_prompt": job.system_prompt, "tokens": list(system_tokens)},
                    )
                    if saved:
                        self._system_prompt_text = job.system_prompt
                        self._system_prompt_tokens = list(system_tokens)
                        self._system_prompt_loaded = True
            self._multicard_native_history_tokens = list(system_tokens)
            self._multicard_native_is_main = bool(system_tokens)
            if system_tokens and self._tokens_start_with(job.prompt_tokens, system_tokens):
                suffix = job.prompt_tokens[len(system_tokens):]
                if suffix:
                    return list(suffix), True, True

        self._reset_multicard_native_cache()
        return list(job.prompt_tokens), False, True

    def _reset_multicard_native_cache(self) -> None:
        assert self._multicard is not None
        self._multicard.clear_kvcache()
        self._multicard_native_history_tokens = []
        self._multicard_native_is_main = False

    @staticmethod
    def _tokens_start_with(tokens: list[int], prefix: list[int]) -> bool:
        return len(tokens) >= len(prefix) and tokens[:len(prefix)] == prefix

    @staticmethod
    def _history_tokens_from_payload(payload: Any) -> list[int]:
        if not isinstance(payload, dict):
            return []
        tokens = payload.get("history_tokens")
        if not isinstance(tokens, list) or not all(isinstance(token, int) for token in tokens):
            return []
        return list(tokens)

    @staticmethod
    def _log_multicard_perf(stats: PipelineRunStats, first_token_time: float | None) -> None:
        total_ms = (stats.ended_at - stats.started_at) * 1000.0
        if first_token_time is not None:
            prefill_ms = max(0.0, (first_token_time - stats.started_at) * 1000.0)
            decode_ms = max(0.0, total_ms - prefill_ms)
        else:
            prefill_ms = total_ms
            decode_ms = 0.0
        n_prefill = stats.prompt_tokens
        n_decode = max(0, stats.generated_tokens - 1)
        prefill_tpt = prefill_ms / n_prefill if n_prefill else 0.0
        prefill_tps = (n_prefill * 1000.0) / prefill_ms if prefill_ms else 0.0
        decode_tpt = decode_ms / n_decode if n_decode else 0.0
        decode_tps = (n_decode * 1000.0) / decode_ms if decode_ms else 0.0
        LOGGER.info(
            "\n"
            "-----------------------------------------------------------------------------------------\n"
            " %-10s | %-16s | %-8s | %-20s | %-20s\n"
            "-----------------------------------------------------------------------------------------\n"
            " %-10s | %-16.2f | %-8d | %-20.2f | %-20.2f\n"
            " %-10s | %-16.2f | %-8d | %-20.2f | %-20.2f\n"
            "-----------------------------------------------------------------------------------------"
            + (("\n stage_ms=" + ",".join(f"{v:.2f}" for v in stats.stage_ms)) if stats.stage_ms else ""),
            "Stage", "Total Time (ms)", "Tokens", "Time per Token (ms)", "Tokens per Second",
            "Prefill", prefill_ms, n_prefill, prefill_tpt, prefill_tps,
            "Generate", decode_ms, n_decode, decode_tpt, decode_tps,
        )

    def _log_memory(self, stage: str) -> None:
        snapshot = _read_memory_snapshot()
        if snapshot is None:
            LOGGER.info("Process memory stage=%s unavailable", stage)
            return
        if self._memory_baseline is None:
            self._memory_baseline = snapshot
        previous = self._memory_previous or snapshot
        baseline = self._memory_baseline
        self._memory_previous = snapshot
        LOGGER.info(
            "Process memory stage=%s rss=%.1fMiB rss_delta=%+.1fMiB rss_total_delta=%+.1fMiB "
            "pss=%.1fMiB anon=%.1fMiB file=%.1fMiB",
            stage,
            snapshot.rss_kb / 1024,
            (snapshot.rss_kb - previous.rss_kb) / 1024,
            (snapshot.rss_kb - baseline.rss_kb) / 1024,
            snapshot.pss_kb / 1024,
            snapshot.anon_kb / 1024,
            snapshot.file_kb / 1024,
        )

    @staticmethod
    def _log_perf(perf: list, first_token_time: float | None) -> None:
        n_decode = int(perf[0]) if perf else 0
        n_prefill = int(perf[1]) if len(perf) > 1 else 0
        t_start = perf[2] if len(perf) > 2 else 0.0
        t_end = perf[3] if len(perf) > 3 else 0.0

        if first_token_time is not None:
            prefill_ms = (first_token_time - t_start) * 1000.0
            decode_ms = (t_end - first_token_time) * 1000.0
        else:
            total_ms = (t_end - t_start) * 1000.0
            prefill_ms = total_ms
            decode_ms = 0.0

        prefill_tpt = prefill_ms / n_prefill if n_prefill else 0.0
        prefill_tps = (n_prefill * 1000.0) / prefill_ms if prefill_ms else 0.0
        decode_tpt = decode_ms / n_decode if n_decode else 0.0
        decode_tps = (n_decode * 1000.0) / decode_ms if decode_ms else 0.0

        LOGGER.info(
            "\n"
            "-----------------------------------------------------------------------------------------\n"
            " %-10s | %-16s | %-8s | %-20s | %-20s\n"
            "-----------------------------------------------------------------------------------------\n"
            " %-10s | %-16.2f | %-8d | %-20.2f | %-20.2f\n"
            " %-10s | %-16.2f | %-8d | %-20.2f | %-20.2f\n"
            "-----------------------------------------------------------------------------------------",
            "Stage", "Total Time (ms)", "Tokens", "Time per Token (ms)", "Tokens per Second",
            "Prefill", prefill_ms, n_prefill, prefill_tpt, prefill_tps,
            "Generate", decode_ms, n_decode, decode_tpt, decode_tps,
        )

    def _set_sampling(self, request: GenerationRequest) -> None:
        # Older Toolkit Lite releases can block indefinitely in
        # rknn3_session_set_llm_param() even when re-applying the parameters
        # currently applied to the shared session. Compare against the actual
        # session state rather than configuration defaults so a request that
        # returns to defaults restores an earlier per-request override.
        requested = self._sampling_parameters(request)
        if requested == self._applied_sampling:
            return
        t = self._types
        params = t.RKNN3LLMParam()
        ctypes.memset(ctypes.byref(params), 0, ctypes.sizeof(params))
        params.logits_name = self._logits_name()
        params.max_context_len = self.settings.max_context_tokens
        params.sampling_param.top_k = request.top_k
        params.sampling_param.top_p = request.top_p
        params.sampling_param.temperature = request.temperature
        params.sampling_param.repeat_penalty = request.repeat_penalty
        params.sampling_param.frequency_penalty = request.frequency_penalty
        params.sampling_param.presence_penalty = request.presence_penalty
        params.vocab_info.vocab_size = len(self.tokenizer)
        eos = self.tokenizer.special_eos_ids[:64]
        bos = self.tokenizer.special_bos_ids[:64]
        for index, token in enumerate(eos):
            params.vocab_info.special_eos_id[index] = token
        params.vocab_info.n_special_eos_id = len(eos)
        for index, token in enumerate(bos):
            params.vocab_info.special_bos_id[index] = token
        params.vocab_info.n_special_bos_id = len(bos)
        params.vocab_info.linefeed_id = self.tokenizer.linefeed_id
        params.vocab_info.ignore_eos_token = False
        params.vocab_info.skip_special_token = True
        runtime = self._multicard.last_runtime if self._multicard is not None else self._rknn
        if runtime.set_llm_param(params, n_params=1) != 0:
            raise RuntimeError("Failed to set per-request RKNN3 sampling parameters")
        self._applied_sampling = requested

    @staticmethod
    def _sampling_parameters(source: Settings | GenerationRequest) -> _SamplingParameters:
        return _SamplingParameters(
            top_k=source.top_k,
            top_p=source.top_p,
            temperature=source.temperature,
            repeat_penalty=source.repeat_penalty,
            frequency_penalty=source.frequency_penalty,
            presence_penalty=source.presence_penalty,
        )

    def _result_callback(self, userdata, result_ptr, state) -> int:
        job = self._active
        if job is None:
            return 0
        state = int(state)
        job.terminal_state = state
        if state == 0:
            if job.first_token_time is None:
                job.first_token_time = time.perf_counter()
            count = int(result_ptr.contents.num_tokens)
            new_tokens = [int(result_ptr.contents.token_ids[index]) for index in range(count)]
            job.tokens.extend(new_tokens)
            job.decoded = self.tokenizer.decode(job.tokens)
            self._flush_decoded(job, terminal=False)
        elif state in {2, 3, 4, 5}:
            pass
        return 0

    def _sampling_callback(self, userdata, logits_ptr, logits_name_ptr) -> int:
        job = self._active
        if self._custom_sampler is None or job is None or job.sampling_state is None:
            return -1
        self._ensure_sampling_callback_affinity()
        start = time.perf_counter()
        try:
            return self._custom_sampler.sample(logits_ptr, job.sampling_state)
        except Exception:
            LOGGER.exception("Custom sampling callback failed")
            return -1
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            job.sampling_callback_count += 1
            job.sampling_callback_total_ms += elapsed_ms
            job.sampling_callback_max_ms = max(job.sampling_callback_max_ms, elapsed_ms)

    @staticmethod
    def _detect_big_cpus() -> tuple[set[int], set[int]]:
        """Return all big CPUs and the highest-capacity big CPU cluster."""
        capacities: dict[int, int] = {}
        for cpu_path in Path("/sys/devices/system/cpu").glob("cpu[0-9]*"):
            suffix = cpu_path.name[3:]
            if not suffix.isdigit():
                continue
            try:
                capacities[int(suffix)] = int(
                    (cpu_path / "cpu_capacity").read_text().strip())
            except (OSError, ValueError):
                continue
        distinct_capacities = set(capacities.values())
        if len(distinct_capacities) < 2:
            return set(), set()
        minimum_capacity = min(distinct_capacities)
        maximum_capacity = max(distinct_capacities)
        big_cpus = {
            cpu for cpu, capacity in capacities.items()
            if capacity > minimum_capacity
        }
        preferred_cpus = {
            cpu for cpu, capacity in capacities.items()
            if capacity == maximum_capacity
        }
        return big_cpus, preferred_cpus

    def _ensure_sampling_callback_affinity(self) -> None:
        """Pin the native callback thread to the fastest big-core cluster once."""
        if self._sampling_affinity_checked:
            return
        self._sampling_affinity_checked = True
        if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
            LOGGER.debug("Sampling callback CPU affinity is unsupported on this platform")
            return
        try:
            current_cpus = set(os.sched_getaffinity(0))
            big_cpus, preferred_cpus = self._detect_big_cpus()
            if not big_cpus or not preferred_cpus:
                LOGGER.warning(
                    "Sampling callback CPU affinity unchanged: CPU capacity topology unavailable")
                return
            if current_cpus and current_cpus.issubset(big_cpus):
                LOGGER.info(
                    "Sampling callback already bound to big CPUs: cpus=%s tid=%d",
                    sorted(current_cpus),
                    threading.get_native_id(),
                )
                return
            os.sched_setaffinity(0, preferred_cpus)
            actual_cpus = set(os.sched_getaffinity(0))
            LOGGER.info(
                "Sampling callback bound to highest-capacity big CPUs: previous=%s cpus=%s tid=%d",
                sorted(current_cpus),
                sorted(actual_cpus),
                threading.get_native_id(),
            )
        except (OSError, ValueError) as exc:
            LOGGER.warning(
                "Sampling callback CPU affinity setup failed; continuing unpinned: %s", exc)

    def _log_sampling_timing(self, job: _Job) -> None:
        count = job.sampling_callback_count
        if count <= 0:
            return
        state = job.sampling_state
        message = "RKNN3 sampling timing: callbacks=%d callback_total_ms=%.3f callback_max_ms=%.3f"
        args: tuple[Any, ...] = (
            count,
            job.sampling_callback_total_ms,
            job.sampling_callback_max_ms,
        )
        if state is not None and getattr(state, "sample_count", 0) > 0:
            message += (
                " sample_total_ms=%.3f"
                " xgrammar_mask_total_ms=%.3f xgrammar_mask_count=%d"
                " xgrammar_token_select_total_ms=%.3f"
                " xgrammar_token_select_count=%d"
                " token_select_total_ms=%.3f matcher_accept_total_ms=%.3f"
                " native_sampler_total_ms=%.3f sample_max_ms=%.3f"
            )
            args += (
                state.sample_total_ms,
                state.mask_total_ms,
                state.mask_count,
                state.masked_choose_total_ms,
                state.masked_choose_count,
                state.choose_total_ms,
                state.accept_total_ms,
                state.choose_total_ms,
                state.sample_max_ms,
            )
        LOGGER.info(message, *args)

    def _flush_decoded(self, job: _Job, terminal: bool = True) -> None:
        cutoff = len(job.decoded)
        found_stop = False
        if job.request.stop:
            positions = [job.decoded.find(word, job.sent_length) for word in job.request.stop]
            positions = [position for position in positions if position >= 0]
            if positions:
                cutoff = min(positions)
                found_stop = True
            elif not terminal:
                cutoff = max(job.sent_length, cutoff - max(len(word) for word in job.request.stop) + 1)
        if cutoff > job.sent_length:
            self._emit(job, InferenceEvent("text", text=job.decoded[job.sent_length:cutoff]))
            job.sent_length = cutoff
        if found_stop and not job.stopped_by_word:
            job.stopped_by_word = True
            if self._multicard is None:
                self._rknn.session_stop()

    def _embed_callback(self, userdata, tokens_ptr, num_tokens, embed, length) -> int:
        try:
            import numpy as np
            count = int(num_tokens)
            dimension = self._embeddings.shape[1]
            if int(length) != count * dimension * np.dtype(np.float16).itemsize:
                return -1
            token_ids = [int(tokens_ptr[index]) for index in range(count)]
            if any(token < 0 or token >= self._embeddings.shape[0] for token in token_ids):
                return -1
            destination = np.ctypeslib.as_array(
                ctypes.cast(embed, ctypes.POINTER(ctypes.c_uint16)), shape=(count * dimension,)
            ).view(np.float16)
            destination[:] = self._embeddings[token_ids].reshape(-1)
            return 0
        except Exception:
            return -1

    def _emit(self, job: _Job, event: InferenceEvent) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(job.events.put_nowait, event)


def _read_memory_snapshot(
    smaps_path: Path = Path("/proc/self/smaps_rollup"),
    status_path: Path = Path("/proc/self/status"),
) -> _MemorySnapshot | None:
    try:
        values = _read_proc_kb_values(smaps_path)
        return _MemorySnapshot(
            rss_kb=values["Rss"],
            pss_kb=values.get("Pss", values["Rss"]),
            anon_kb=values.get("Pss_Anon", values.get("Anonymous", 0)),
            file_kb=values.get("Pss_File", 0),
        )
    except (OSError, KeyError, ValueError):
        pass

    try:
        values = _read_proc_kb_values(status_path)
        rss_kb = values["VmRSS"]
        return _MemorySnapshot(
            rss_kb=rss_kb,
            pss_kb=rss_kb,
            anon_kb=values.get("RssAnon", 0),
            file_kb=values.get("RssFile", 0),
        )
    except (OSError, KeyError, ValueError):
        return None


def _read_proc_kb_values(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            name, separator, raw_value = line.partition(":")
            if not separator:
                continue
            fields = raw_value.split()
            if fields:
                try:
                    values[name] = int(fields[0])
                except ValueError:
                    continue
    return values
