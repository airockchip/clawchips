from __future__ import annotations

import ctypes
import inspect
import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import MulticardStageSettings, Settings
from ..parsing import get_profile


LOGGER = logging.getLogger("gateway.rknn3.multicard")


@dataclass
class PipelineRunStats:
    generated_tokens: int
    prompt_tokens: int
    started_at: float
    ended_at: float
    stage_ms: list[float]
    reached_limit: bool = False


@dataclass
class _TensorBatch:
    data: Any
    n_tokens: int
    tensor_name: str


@dataclass
class _StageSlot:
    condition: threading.Condition = field(default_factory=threading.Condition)
    batches: deque[_TensorBatch] = field(default_factory=deque)
    expected_tokens: int = 0
    emitted_tokens: int = 0
    active_input_tokens: int = 0
    producer_done: bool = False
    failed: bool = False

    def wait(self) -> _TensorBatch | None:
        with self.condition:
            self.condition.wait_for(
                lambda: bool(self.batches) or self.producer_done or self.failed
            )
            if self.failed or not self.batches:
                return None
            return self.batches.popleft()

    def close(self) -> None:
        with self.condition:
            self.producer_done = True
            self.condition.notify_all()

    def fail(self) -> None:
        with self.condition:
            self.failed = True
            self.condition.notify_all()

    def reset(self) -> None:
        """Reset transient state for reuse across run_once calls."""
        with self.condition:
            self.batches.clear()
            self.expected_tokens = 0
            self.emitted_tokens = 0
            self.active_input_tokens = 0
            self.producer_done = False
            self.failed = False


class _StepState:
    def __init__(self, boundary_count: int):
        self.slots = [_StageSlot() for _ in range(boundary_count)]
        self.next_token: int | None = None
        self.error: BaseException | None = None
        self.lock = threading.Lock()
        self.stage_ms = [0.0] * (boundary_count + 1)

    def fail(self, error: BaseException) -> None:
        with self.lock:
            if self.error is None:
                self.error = error
        for slot in self.slots:
            slot.fail()

    def reset(self) -> None:
        """Reset transient state for reuse across run_once calls."""
        self.next_token = None
        self.error = None
        for i in range(len(self.stage_ms)):
            self.stage_ms[i] = 0.0
        for slot in self.slots:
            slot.reset()


@dataclass
class _StageRuntime:
    index: int
    spec: MulticardStageSettings
    rknn: Any
    embedding_dim: int
    vocab_size: int
    max_context_len: int
    model_type: str
    output_tensors: Any = None
    n_output_tensors: int = 0
    output_index: int = -1
    output_name: str = ""
    callback_refs: list[Any] = field(default_factory=list)


class MulticardRKNN3Pipeline:
    """Host-mediated pipeline for one segmented LLM spread across RKNN devices."""

    _OUTPUT_NAME_HINTS = ("hidden_states", "last_hidden", "hidden", "output")

    def __init__(
        self,
        settings: Settings,
        tokenizer: Any,
        embeddings: Any,
        result_handler: Callable[..., int],
        embed_handler: Callable[..., int],
        sampling_handler: Callable[..., int] | None = None,
    ):
        self.settings = settings
        self.tokenizer = tokenizer
        self.embeddings = embeddings
        self._result_handler = result_handler
        self._embed_handler = embed_handler
        self._sampling_handler = sampling_handler
        self._stages: list[_StageRuntime] = []
        self._types: Any = None
        self._callback_types: dict[str, Any] = {}
        self._current_step: _StepState | None = None
        self._current_step_lock = threading.Lock()
        self._closed = False
        self.model_type = ""
        # P0: reusable step state (avoids per-run_once allocation)
        self._reusable_step: _StepState | None = None
        # P0: persistent worker threads (avoids per-run_once thread create/destroy)
        self._worker_threads: list[threading.Thread] = []
        self._worker_wakeup: threading.Event = threading.Event()
        self._worker_done_count: int = 0
        self._worker_done_lock: threading.Lock = threading.Lock()
        self._worker_done_event: threading.Event = threading.Event()
        self._worker_args: tuple[Any, ...] = ()
        # P1: cached eos token ids
        self._eos_ids: set[int] = set()

    @property
    def stage_count(self) -> int:
        return len(self._stages)

    @property
    def last_runtime(self) -> Any:
        if not self._stages:
            raise RuntimeError("Multicard RKNN3 pipeline is not initialized")
        return self._stages[-1].rknn

    @property
    def stage_identities(self) -> list[dict[str, Any]]:
        identities: list[dict[str, Any]] = []
        for stage in self.settings.multicard_stages:
            identity: dict[str, Any] = {"device_id": stage.device_id}
            for key, raw_path in (("rknn", stage.rknn_path), ("weight", stage.weight_path)):
                path = Path(raw_path).resolve()
                stat = path.stat()
                identity[key] = {
                    "path": str(path),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            identities.append(identity)
        return identities

    def initialize(self) -> None:
        try:
            from rknn3lite.api import (
                LLMGetEmbedCallback,
                LLMOutputCallback,
                LLMResultCallback,
                LLMSamplingCallback,
                RKLLMCallback,
                RKNN3Lite,
                rknn3_types,
            )
        except ImportError as exc:
            hint = (
                f" Install {self.settings.toolkit_lite_wheel}."
                if self.settings.toolkit_lite_wheel
                else ""
            )
            raise RuntimeError(
                "rknn3-toolkit-lite with multicard LLM support is not installed." + hint
            ) from exc

        self._types = rknn3_types
        self._callback_types = {
            "callback": RKLLMCallback,
            "embed": LLMGetEmbedCallback,
            "output": LLMOutputCallback,
            "result": LLMResultCallback,
            "sampling": LLMSamplingCallback,
        }
        self._validate_artifacts()
        self._validate_devices(RKNN3Lite)

        try:
            for index, spec in enumerate(self.settings.multicard_stages):
                self._stages.append(self._initialize_stage(RKNN3Lite, index, spec))
            self._validate_stage_chain()
            self.model_type = self._stages[-1].model_type or self._stages[0].model_type
        except Exception:
            self.release()
            raise

        # P1: cache eos ids once
        self._eos_ids = set(int(token) for token in self.tokenizer.special_eos_ids)
        # P0: create reusable step state
        self._reusable_step = _StepState(len(self._stages) - 1)
        # P0: start persistent worker threads for stages 1..N-1
        self._start_worker_pool()

    def _validate_artifacts(self) -> None:
        for index, stage in enumerate(self.settings.multicard_stages):
            for label, value in (("RKNN model", stage.rknn_path), ("RKNN weight", stage.weight_path)):
                if not Path(value).is_file():
                    raise RuntimeError(f"Multicard stage {index} {label} file not found: {value}")

    def _validate_devices(self, rknn_class: Any) -> None:
        probe = rknn_class(llm_mode=True, verbose=self.settings.debug_logs)
        try:
            raw_devices = probe.get_devices_id()
        except Exception as exc:
            raise RuntimeError("Failed to enumerate RKNN3 devices for multicard mode") from exc
        finally:
            try:
                probe.release()
            except Exception:
                pass

        if isinstance(raw_devices, tuple) and len(raw_devices) == 2 and isinstance(raw_devices[1], (list, tuple)):
            raw_devices = raw_devices[1]
        available = {
            item.decode("utf-8", errors="replace") if isinstance(item, (bytes, bytearray)) else str(item)
            for item in (raw_devices or [])
        }
        configured = {stage.device_id for stage in self.settings.multicard_stages}
        missing = sorted(configured - available)
        if missing:
            raise RuntimeError(
                f"Configured RKNN3 devices not found: {', '.join(missing)}; "
                f"available={sorted(available)}"
            )

    def _initialize_stage(
        self,
        rknn_class: Any,
        index: int,
        spec: MulticardStageSettings,
    ) -> _StageRuntime:
        rknn = rknn_class(llm_mode=True, verbose=self.settings.debug_logs)
        try:
            return self._initialize_stage_runtime(rknn, index, spec)
        except Exception:
            try:
                rknn.release()
            except Exception:
                LOGGER.exception("Failed to release partially initialized stage %d", index)
            raise

    def _initialize_stage_runtime(self, rknn: Any, index: int, spec: MulticardStageSettings) -> _StageRuntime:
        if rknn.load_rknn(spec.rknn_path, spec.weight_path) != 0:
            raise RuntimeError(f"stage {index}: failed to load RKNN model and weight")
        if rknn.init_runtime(
            target=self.settings.runtime_target,
            core_mask=self.settings.core_mask,
            device_id=spec.device_id.encode("utf-8"),
        ) != 0:
            raise RuntimeError(f"stage {index}: failed to initialize RKNN3 runtime on {spec.device_id}")
        self._validate_session_run_api(rknn, index)

        config = rknn.rknn3_query(self._types.RKNN3QueryCmd.RKNN3_QUERY_LLM_CONFIG)
        if config is None:
            raise RuntimeError(f"stage {index}: failed to query RKNN3 LLM config")
        raw_model_type = getattr(config, "model_type", None)
        model_type = (
            raw_model_type.decode("utf-8", errors="replace")
            if isinstance(raw_model_type, (bytes, bytearray))
            else str(raw_model_type or "")
        )
        stage = _StageRuntime(
            index=index,
            spec=spec,
            rknn=rknn,
            embedding_dim=int(getattr(config, "embedding_dim", 0)),
            vocab_size=int(getattr(config, "vocab_size", 0)),
            max_context_len=int(getattr(config, "max_ctx_len", 0)),
            model_type=model_type,
        )

        is_last = index == len(self.settings.multicard_stages) - 1
        callback = self._callback_types["callback"]()
        callback_userdata = ctypes.c_uint8(index)
        callback_userdata_ptr = ctypes.cast(
            ctypes.pointer(callback_userdata), ctypes.c_void_p
        )
        embed_callback = self._callback_types["embed"](self._embed_handler)
        callback.embed_callback = embed_callback
        callback.embed_userdata = callback_userdata_ptr
        stage.callback_refs.extend(
            [callback, callback_userdata, callback_userdata_ptr, embed_callback]
        )

        def stage_result(userdata: Any, result_ptr: Any, state: Any, stage_index: int = index) -> int:
            return self._handle_result(stage_index, userdata, result_ptr, state)

        result_callback = self._callback_types["result"](stage_result)
        callback.result_callback = result_callback
        callback.result_userdata = callback_userdata_ptr
        stage.callback_refs.append(result_callback)

        if not is_last:
            output_tensors, n_output_tensors = rknn.create_output_tensors()
            if output_tensors is None or n_output_tensors <= 0:
                raise RuntimeError(f"stage {index}: failed to allocate output tensors")
            stage.output_tensors = output_tensors
            stage.n_output_tensors = int(n_output_tensors)
            stage.output_index, stage.output_name = self._select_output_tensor(
                rknn, spec.output_tensor_name, stage.n_output_tensors, index, stage.embedding_dim
            )

            def stage_output(
                userdata: Any,
                output_ptr: Any,
                count: Any,
                state: Any,
                stage_index: int = index,
            ) -> int:
                return self._handle_output(stage_index, output_ptr, count, state)

            output_callback = self._callback_types["output"](stage_output)
            callback.output_callback = output_callback
            callback.output_userdata = callback_userdata_ptr
            callback.output_tensors = ctypes.cast(
                output_tensors, ctypes.POINTER(self._types.RKNN3Tensor)
            )
            callback.n_output_tensors = stage.n_output_tensors
            stage.callback_refs.extend([output_callback, output_tensors])
        elif self._sampling_handler is not None:
            sampling_callback = self._callback_types["sampling"](self._sampling_handler)
            callback.sampling_callback = sampling_callback
            callback.sampling_userdata = callback_userdata_ptr
            stage.callback_refs.append(sampling_callback)

        if rknn.init_llm_session(
            llm_args=[self._initial_llm_args(stage.model_type)],
            llm_callback=callback,
        ) != 0:
            raise RuntimeError(f"stage {index}: failed to initialize RKNN3 LLM session")
        self._validate_pipeline_controls(rknn, index)
        if rknn.set_chat_template("", "", "") != 0:
            raise RuntimeError(f"stage {index}: failed to clear RKNN3 chat template")

        LOGGER.info(
            "Initialized multicard stage=%d device=%s model=%s weight=%s "
            "embedding_dim=%d vocab_size=%d max_context=%d output=%s",
            index,
            spec.device_id,
            spec.rknn_path,
            spec.weight_path,
            stage.embedding_dim,
            stage.vocab_size,
            stage.max_context_len,
            stage.output_name or "<last-stage>",
        )
        return stage

    @staticmethod
    def _validate_session_run_api(rknn: Any, stage_index: int) -> None:
        required = {"tokens", "embeds"}
        try:
            parameters = inspect.signature(rknn.session_run).parameters
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"stage {stage_index}: cannot inspect Toolkit Lite session_run API"
            ) from exc
        has_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        missing = sorted(required - set(parameters)) if not has_kwargs else []
        if missing or not callable(getattr(rknn, "create_output_tensors", None)):
            details = ", ".join(missing or ["create_output_tensors"])
            raise RuntimeError(
                "Multicard mode requires RKNN3 Toolkit Lite with token/embed "
                f"pipeline support; missing: {details}"
            )

    @staticmethod
    def _supports_explicit_pipeline_controls(rknn: Any) -> bool:
        try:
            parameters = inspect.signature(rknn.session_run).parameters
        except (TypeError, ValueError):
            return False
        if any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            return True
        return {"prefill_only", "disable_sampling"} <= set(parameters)

    def _validate_pipeline_controls(self, rknn: Any, stage_index: int) -> None:
        if self._supports_explicit_pipeline_controls(rknn):
            return
        infer_param = getattr(
            getattr(getattr(rknn, "llm", None), "rknn_session", None),
            "infer_param",
            None,
        )
        reserved = getattr(infer_param, "reserved", None)
        if reserved is None or len(reserved) < 2:
            raise RuntimeError(
                f"stage {stage_index}: Toolkit Lite exposes neither explicit "
                "prefill controls nor the compatible infer_param reserved fields"
            )
        LOGGER.info(
            "stage=%d uses Toolkit Lite reserved infer controls "
            "(reserved[0]=prefill_only, reserved[1]=disable_sampling)",
            stage_index,
        )

    def _session_run(
        self,
        stage: _StageRuntime,
        *,
        tokens: list[int] | None = None,
        embeds: Any = None,
        n_tokens: int = 0,
        keep_history: bool,
        max_new_tokens: int,
        enable_thinking: bool,
        prefill_only: bool,
        disable_sampling: bool,
    ) -> Any:
        if embeds is not None and n_tokens > 0:
            # Toolkit Lite derives n_tokens from embeds.shape[1]. Preserve the
            # token-major flat memory while exposing (embedding_dim, N).
            reshape = getattr(embeds, "reshape", None)
            if callable(reshape):
                embeds = reshape(stage.embedding_dim, n_tokens)

        kwargs = {
            "tokens": tokens,
            "embeds": embeds,
            "keep_history": keep_history,
            "max_new_tokens": max_new_tokens,
            "enable_thinking": enable_thinking,
        }
        if self._supports_explicit_pipeline_controls(stage.rknn):
            return stage.rknn.session_run(
                **kwargs,
                prefill_only=prefill_only,
                disable_sampling=disable_sampling,
            )

        session = stage.rknn.llm.rknn_session
        reserved = session.infer_param.reserved
        reserved[0] = int(prefill_only)
        reserved[1] = int(disable_sampling)
        try:
            return stage.rknn.session_run(**kwargs)
        finally:
            reserved[0] = 0
            reserved[1] = 0

    def _select_output_tensor(
        self,
        rknn: Any,
        configured_name: str,
        count: int,
        stage_index: int,
        embedding_dim: int,
    ) -> tuple[int, str]:
        attrs = [
            rknn.rknn3_query(self._types.RKNN3QueryCmd.RKNN3_QUERY_OUTPUT_ATTR, index=index)
            for index in range(count)
        ]
        if any(attr is None for attr in attrs):
            raise RuntimeError(f"stage {stage_index}: failed to query output tensor attributes")
        names = [self._tensor_name(attr) for attr in attrs]
        if configured_name:
            matches = [index for index, name in enumerate(names) if name == configured_name]
        elif len(names) == 1:
            matches = [0]
        else:
            matches = [
                index
                for index, name in enumerate(names)
                if any(hint in name.lower() for hint in self._OUTPUT_NAME_HINTS)
            ]
        if len(matches) != 1:
            raise RuntimeError(
                f"stage {stage_index}: cannot uniquely select pipeline output tensor; "
                f"configured={configured_name!r}, available={names}"
            )
        selected = matches[0]
        if int(attrs[selected].dtype) != 1:
            raise RuntimeError(
                f"stage {stage_index}: pipeline output {names[selected]!r} must be FLOAT16, "
                f"got dtype={int(attrs[selected].dtype)}"
            )
        n_elems = int(attrs[selected].n_elems)
        if n_elems and embedding_dim > 0 and n_elems % embedding_dim:
            raise RuntimeError(
                f"stage {stage_index}: pipeline output {names[selected]!r} has n_elems={n_elems}, "
                f"which is not divisible by embedding_dim={embedding_dim}"
            )
        return selected, names[selected]

    @staticmethod
    def _tensor_name(attr: Any) -> str:
        raw = attr.name
        if isinstance(raw, str):
            return raw.split("\0", 1)[0]
        return bytes(raw).split(b"\0", 1)[0].decode("utf-8", errors="replace")

    def _validate_stage_chain(self) -> None:
        vocab_size = len(self.tokenizer)
        dimensions = {stage.embedding_dim for stage in self._stages if stage.embedding_dim > 0}
        if len(dimensions) != 1:
            raise RuntimeError(
                f"Multicard stage embedding dimensions do not match: {sorted(dimensions)}"
            )
        embedding_dim = next(iter(dimensions))
        if embedding_dim != int(self.embeddings.shape[1]):
            raise RuntimeError(
                f"Runtime embedding_dim={embedding_dim} does not match embedding file "
                f"dimension={int(self.embeddings.shape[1])}"
            )
        for stage in self._stages:
            if stage.vocab_size and stage.vocab_size != vocab_size:
                raise RuntimeError(
                    f"stage {stage.index}: runtime vocab_size={stage.vocab_size} "
                    f"does not match tokenizer vocab_size={vocab_size}"
                )
            if stage.max_context_len and self.settings.max_context_tokens > stage.max_context_len:
                raise RuntimeError(
                    f"stage {stage.index}: configured context {self.settings.max_context_tokens} "
                    f"exceeds model maximum {stage.max_context_len}"
                )
            if stage.model_type.lower() == "gemma4":
                raise RuntimeError("Gemma4 multicard pipeline is not supported yet")

    def _initial_llm_args(self, model_type: str) -> dict[str, Any]:
        return {
            "max_new_tokens": 1,
            "top_k": self.settings.top_k,
            "top_p": self.settings.top_p,
            "temperature": self.settings.temperature,
            "repeat_penalty": self.settings.repeat_penalty,
            "frequency_penalty": self.settings.frequency_penalty,
            "presence_penalty": self.settings.presence_penalty,
            "vocab_size": len(self.tokenizer),
            "special_eos_id": self.tokenizer.special_eos_ids,
            "special_bos_id": self.tokenizer.special_bos_ids,
            "linefeed_id": self.tokenizer.linefeed_id,
            "max_context_len": self.settings.max_context_tokens,
            "keep_history": 1,
            "logits_name": get_profile(model_type).logits_name.encode("utf-8"),
        }

    def _step(self) -> _StepState | None:
        with self._current_step_lock:
            return self._current_step

    def _handle_result(self, stage_index: int, userdata: Any, result_ptr: Any, state: Any) -> int:
        if stage_index != len(self._stages) - 1:
            return 0
        step = self._step()
        if step is not None and int(state) == 0 and result_ptr:
            result = result_ptr.contents
            if int(result.num_tokens) > 0:
                with step.lock:
                    step.next_token = int(result.token_ids[int(result.num_tokens) - 1])
        return self._result_handler(userdata, result_ptr, state)

    def _handle_output(self, stage_index: int, output_tensors: Any, count: Any, state: Any) -> int:
        step = self._step()
        if step is None:
            return -1
        try:
            import numpy as np

            stage = self._stages[stage_index]
            if stage.output_index >= int(count):
                raise RuntimeError(
                    f"stage {stage_index}: output callback count={int(count)} "
                    f"does not include selected index={stage.output_index}"
                )
            tensor = output_tensors[stage.output_index]
            attr = tensor.attr.contents
            mem = tensor.mem.contents
            address = mem.virt_addr.value if hasattr(mem.virt_addr, "value") else mem.virt_addr
            if not address:
                raise RuntimeError(f"stage {stage_index}: output tensor has no virtual address")

            slot = step.slots[stage_index]
            with slot.condition:
                remaining = max(0, slot.expected_tokens - slot.emitted_tokens)
                if slot.active_input_tokens > 0:
                    n_tokens = slot.active_input_tokens
                elif int(state) == 1:
                    n_tokens = remaining
                else:
                    n_tokens = min(remaining, self.settings.multicard_bucket_size)
                if n_tokens <= 0 and stage.embedding_dim > 0:
                    n_tokens = int(attr.n_elems) // stage.embedding_dim
                if n_tokens <= 0:
                    raise RuntimeError(f"stage {stage_index}: cannot determine output token count")
                valid_bytes = n_tokens * stage.embedding_dim * np.dtype(np.float16).itemsize
                if valid_bytes > int(attr.aligned_size):
                    raise RuntimeError(
                        f"stage {stage_index}: output buffer too small; "
                        f"need={valid_bytes}, aligned_size={int(attr.aligned_size)}"
                    )
                raw = ctypes.string_at(address, valid_bytes)
                # P1: avoid .copy() - frombuffer returns a read-only array that
                # is safe to pass to the consumer thread; the underlying bytes
                # buffer is independent of the RKNN output tensor.
                data = np.frombuffer(raw, dtype=np.float16).reshape(
                    n_tokens, stage.embedding_dim
                )
                slot.emitted_tokens += n_tokens
                slot.batches.append(_TensorBatch(data, n_tokens, stage.output_name))
                slot.condition.notify()
            return 0
        except BaseException as exc:
            step.fail(exc)
            LOGGER.exception("Multicard stage %d output callback failed", stage_index)
            return -1

    @staticmethod
    def _run_return_code(result: Any) -> int:
        if isinstance(result, tuple):
            return int(result[0])
        return int(result)

    def _start_worker_pool(self) -> None:
        """Create persistent worker threads for stages 1..N-1.

        Workers loop waiting for a wakeup signal, execute one run_once step,
        then signal completion and go back to sleep. This avoids creating
        and destroying N-1 threads on every run_once call (especially
        costly during the decode loop where each token = one run_once).
        """
        for index in range(1, len(self._stages)):
            t = threading.Thread(
                target=self._worker_loop,
                args=(index,),
                name=f"rknn3-stage-{index}",
                daemon=True,
            )
            t.start()
            self._worker_threads.append(t)

    def _stop_worker_pool(self) -> None:
        """Signal all worker threads to exit and join them."""
        if not self._worker_threads:
            return
        self._closed = True
        self._worker_wakeup.set()
        for t in self._worker_threads:
            t.join(timeout=5)
        self._worker_threads.clear()
        self._worker_wakeup.clear()

    def _worker_loop(self, stage_index: int) -> None:
        """Persistent worker loop: wake -> run one step -> signal done -> sleep."""
        while True:
            self._worker_wakeup.wait()
            self._worker_wakeup.clear()
            if self._closed:
                return
            step, keep_history, sample, cancelled = self._worker_args
            self._run_stage_worker(stage_index, step, keep_history, sample, cancelled)
            # Signal completion: all workers must finish before main thread proceeds
            with self._worker_done_lock:
                self._worker_done_count += 1
                if self._worker_done_count >= len(self._worker_threads):
                    self._worker_done_event.set()

    def _run_stage_worker(
        self,
        stage_index: int,
        step: _StepState,
        keep_history: bool,
        sample: bool,
        cancelled: threading.Event | None,
    ) -> None:
        stage = self._stages[stage_index]
        input_slot = step.slots[stage_index - 1]
        output_slot = step.slots[stage_index] if stage_index < len(self._stages) - 1 else None
        consumed_tokens = 0
        try:
            while True:
                if cancelled is not None and cancelled.is_set():
                    raise RuntimeError("Request was cancelled")
                batch = input_slot.wait()
                if batch is None:
                    if input_slot.failed:
                        return
                    break
                if output_slot is not None:
                    with output_slot.condition:
                        output_slot.expected_tokens += batch.n_tokens
                        output_slot.active_input_tokens = batch.n_tokens

                total_tokens = step.slots[0].expected_tokens
                is_last = stage_index == len(self._stages) - 1
                final_batch = consumed_tokens + batch.n_tokens >= total_tokens
                disable_sampling = not (is_last and sample and final_batch)
                batch_keep_history = keep_history or consumed_tokens > 0
                started = time.perf_counter()
                result = self._session_run(
                    stage,
                    embeds=batch.data,
                    n_tokens=batch.n_tokens,
                    keep_history=batch_keep_history,
                    max_new_tokens=1,
                    enable_thinking=False,
                    prefill_only=True,
                    disable_sampling=disable_sampling,
                )
                step.stage_ms[stage_index] += (time.perf_counter() - started) * 1000.0
                consumed_tokens += batch.n_tokens
                if output_slot is not None:
                    with output_slot.condition:
                        output_slot.active_input_tokens = 0
                ret = self._run_return_code(result)
                if ret != 0:
                    raise RuntimeError(f"stage {stage_index}: session_run failed with ret={ret}")
            if output_slot is not None:
                output_slot.close()
        except BaseException as exc:
            step.fail(exc)

    def run_once(
        self,
        tokens: list[int],
        *,
        keep_history: bool,
        enable_thinking: bool,
        sample: bool,
        cancelled: threading.Event | None = None,
    ) -> tuple[int | None, list[float]]:
        if not tokens:
            raise RuntimeError("Multicard pipeline input tokens must not be empty")

        # P0: reuse _StepState instead of creating a new one each call
        step = self._reusable_step
        if step is None:
            step = _StepState(len(self._stages) - 1)
            self._reusable_step = step
        step.reset()
        step.slots[0].expected_tokens = len(tokens)

        with self._current_step_lock:
            if self._current_step is not None:
                raise RuntimeError("Multicard pipeline already has an active step")
            self._current_step = step

        # P0: dispatch to persistent worker threads instead of creating new ones
        self._worker_args = (step, keep_history, sample, cancelled)
        with self._worker_done_lock:
            self._worker_done_count = 0
        self._worker_done_event.clear()
        self._worker_wakeup.set()

        try:
            started = time.perf_counter()
            result = self._session_run(
                self._stages[0],
                tokens=tokens,
                keep_history=keep_history,
                max_new_tokens=1,
                enable_thinking=enable_thinking,
                prefill_only=True,
                disable_sampling=True,
            )
            step.stage_ms[0] += (time.perf_counter() - started) * 1000.0
            ret = self._run_return_code(result)
            if ret != 0:
                step.fail(RuntimeError(f"stage 0: session_run failed with ret={ret}"))
            step.slots[0].close()

            # Wait for all worker threads to finish this step
            self._worker_done_event.wait()

            if step.error is not None:
                raise step.error
            if sample and step.next_token is None:
                raise RuntimeError("Last multicard stage did not return a sampled token")
            return step.next_token, step.stage_ms
        finally:
            step.slots[0].close()
            with self._current_step_lock:
                self._current_step = None

    def prefill(
        self,
        tokens: list[int],
        *,
        keep_history: bool,
        cancelled: threading.Event | None = None,
    ) -> list[float]:
        _, stage_ms = self.run_once(
            tokens,
            keep_history=keep_history,
            enable_thinking=False,
            sample=False,
            cancelled=cancelled,
        )
        return stage_ms

    def generate(
        self,
        tokens: list[int],
        *,
        max_new_tokens: int,
        enable_thinking: bool,
        keep_history: bool,
        cancelled: threading.Event,
        stop_requested: Callable[[], bool],
    ) -> PipelineRunStats:
        started_at = time.perf_counter()
        stage_ms = [0.0] * len(self._stages)
        next_token, first_stage_ms = self.run_once(
            tokens,
            keep_history=keep_history,
            enable_thinking=enable_thinking,
            sample=True,
            cancelled=cancelled,
        )
        # P1: in-place accumulation instead of zip + list comprehension
        for i in range(len(stage_ms)):
            stage_ms[i] += first_stage_ms[i]
        generated = 1
        # P1: use cached eos_ids
        eos_ids = self._eos_ids

        while generated < max_new_tokens:
            if cancelled.is_set():
                raise RuntimeError("Request was cancelled")
            if stop_requested() or next_token in eos_ids:
                break
            assert next_token is not None
            next_token, decode_stage_ms = self.run_once(
                [next_token],
                keep_history=True,
                enable_thinking=False,
                sample=True,
                cancelled=cancelled,
            )
            # P1: in-place accumulation
            for i in range(len(stage_ms)):
                stage_ms[i] += decode_stage_ms[i]
            generated += 1

        ended_at = time.perf_counter()
        return PipelineRunStats(
            generated_tokens=generated,
            prompt_tokens=len(tokens),
            started_at=started_at,
            ended_at=ended_at,
            stage_ms=stage_ms,
            reached_limit=generated >= max_new_tokens,
        )

    def configure_kv_cache(self) -> None:
        policy = getattr(self._types, "RKNN3KVCachePolicy", None)
        param_type = getattr(self._types, "RKNN3KVCachePolicyParam", None)
        if policy is None or param_type is None:
            raise RuntimeError("Toolkit Lite does not expose RKNN3 KV cache policy types")
        normal = getattr(policy, "RKNN3_KVCACHE_POLICY_NORMAL")
        checkpoint = getattr(policy, "RKNN3_KVCACHE_POLICY_SAVE_CHECKPOINT", None)
        if self.settings.checkpoint_enabled and checkpoint is None:
            raise RuntimeError("Toolkit Lite does not expose SAVE_CHECKPOINT KV policy")

        for stage in self._stages:
            ret = stage.rknn.set_kvcache_policy(normal)
            if ret != 0:
                raise RuntimeError(f"stage {stage.index}: failed to set NORMAL KV cache policy: ret={ret}")
            if not self.settings.checkpoint_enabled:
                LOGGER.info("stage=%d KV cache checkpoint policy disabled", stage.index)
                continue

            max_context = stage.max_context_len or self.settings.max_context_tokens
            start_pos, interval, max_count = self.settings.checkpoint_policy_values(max_context)
            param = param_type()
            ctypes.memset(ctypes.byref(param), 0, ctypes.sizeof(param))
            param.save_checkpoint.checkpoint_start_pos = start_pos
            param.save_checkpoint.checkpoint_interval = interval
            param.save_checkpoint.max_checkpoint_count = max_count
            ret = stage.rknn.set_kvcache_policy(checkpoint, param=param)
            if ret != 0:
                raise RuntimeError(
                    f"stage {stage.index}: failed to set SAVE_CHECKPOINT KV policy: ret={ret}"
                )
            LOGGER.info(
                "stage=%d KV cache policy set: SAVE_CHECKPOINT "
                "start_pos=%d interval=%d max_count=%d",
                stage.index,
                start_pos,
                interval,
                max_count,
            )

    def clear_kvcache(self) -> None:
        failures: list[str] = []
        for stage in self._stages:
            try:
                ret = stage.rknn.clear_kvcache()
            except Exception as exc:
                failures.append(f"stage {stage.index}: {exc}")
            else:
                if ret != 0:
                    failures.append(f"stage {stage.index}: ret={ret}")
        if failures:
            raise RuntimeError("Failed to clear multicard KV cache: " + "; ".join(failures))

    def save_cache_files(self, paths: list[Path]) -> None:
        if len(paths) != len(self._stages):
            raise ValueError("Cache path count does not match multicard stage count")
        for stage, path in zip(self._stages, paths):
            ret = stage.rknn.save_kvcache(str(path))
            if ret != 0:
                raise RuntimeError(f"stage {stage.index}: save_kvcache failed with ret={ret}")

    def load_cache_files(self, paths: list[Path]) -> None:
        if len(paths) != len(self._stages):
            raise ValueError("Cache path count does not match multicard stage count")
        try:
            for stage, path in zip(self._stages, paths):
                ret = stage.rknn.load_kvcache(kvcache_path=str(path))
                if ret != 0:
                    raise RuntimeError(f"stage {stage.index}: load_kvcache failed with ret={ret}")
        except Exception:
            try:
                self.clear_kvcache()
            except Exception:
                LOGGER.exception("Failed to clear stage KV caches after partial group load")
            raise

    def stop_all(self) -> None:
        step = self._step()
        if step is not None:
            step.fail(RuntimeError("Request was cancelled"))

        def stop(stage: _StageRuntime) -> None:
            try:
                stage.rknn.session_stop()
            except Exception:
                LOGGER.exception("Failed to stop multicard stage %d", stage.index)

        threads = [threading.Thread(target=stop, args=(stage,)) for stage in self._stages]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def release(self) -> None:
        if self._closed:
            return
        # P0: stop persistent worker threads before releasing stages
        self._stop_worker_pool()
        self._closed = True
        for stage in reversed(self._stages):
            session_released = False
            if stage.output_tensors is not None:
                try:
                    session_released = stage.rknn.release(session_index=0) == 0
                except Exception:
                    LOGGER.exception(
                        "Failed to release multicard stage %d session before output memory",
                        stage.index,
                    )
            if stage.output_tensors is not None:
                if session_released:
                    for index in range(stage.n_output_tensors):
                        tensor = stage.output_tensors[index]
                        if tensor.mem:
                            try:
                                stage.rknn.destroy_mem(tensor.mem)
                                tensor.mem = None
                            except Exception:
                                LOGGER.exception(
                                    "Failed to destroy output memory stage=%d tensor=%d",
                                    stage.index,
                                    index,
                                )
                else:
                    LOGGER.warning(
                        "Skipping explicit output memory release for stage=%d because "
                        "its session is still active; context release will reclaim it",
                        stage.index,
                    )
            try:
                stage.rknn.release()
            except Exception:
                LOGGER.exception("Failed to release multicard stage %d", stage.index)
        self._stages.clear()


class MulticardKVCacheManager:
    """Atomically publishes and validates stage-aligned KV cache groups."""

    def __init__(self, pipeline: MulticardRKNN3Pipeline, cache_dir: str | Path):
        self.pipeline = pipeline
        self.root = Path(cache_dir) / "multicard"

    def save(self, kind: str, payload: dict[str, Any]) -> bool:
        kind_dir = self._kind_dir(kind)
        generations = kind_dir / "generations"
        generations.mkdir(parents=True, exist_ok=True)
        generation = uuid.uuid4().hex
        temporary = generations / f".tmp-{generation}"
        final = generations / generation
        temporary.mkdir()
        try:
            cache_paths = [
                temporary / f"stage-{index:03d}.cache"
                for index in range(self.pipeline.stage_count)
            ]
            self.pipeline.save_cache_files(cache_paths)
            caches = [
                {"file": path.name, "size": path.stat().st_size}
                for path in cache_paths
            ]
            if any(cache["size"] <= 0 for cache in caches):
                raise RuntimeError("one or more stage KV cache files are empty")
            manifest = {
                "version": 1,
                "kind": kind,
                "generation": generation,
                "stage_count": self.pipeline.stage_count,
                "stage_identities": self.pipeline.stage_identities,
                "caches": caches,
                "payload": payload,
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, final)
            pointer_tmp = kind_dir / f".current-{generation}.tmp"
            pointer_tmp.write_text(
                json.dumps({"version": 1, "generation": generation}, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(pointer_tmp, kind_dir / "current.json")
            self._remove_old_generations(generations, generation)
            LOGGER.info("Saved multicard %s KV cache generation=%s", kind, generation)
            return True
        except Exception:
            LOGGER.exception("Failed to save multicard %s KV cache group", kind)
            shutil.rmtree(temporary, ignore_errors=True)
            return False

    def load(self, kind: str) -> dict[str, Any] | None:
        kind_dir = self._kind_dir(kind)
        pointer_path = kind_dir / "current.json"
        if not pointer_path.is_file():
            return None
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            if pointer.get("version") != 1 or not isinstance(pointer.get("generation"), str):
                raise ValueError("invalid current pointer")
            generation_dir = kind_dir / "generations" / pointer["generation"]
            manifest = json.loads((generation_dir / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("version") != 1 or manifest.get("kind") != kind:
                raise ValueError("invalid cache manifest")
            if manifest.get("stage_count") != self.pipeline.stage_count:
                raise ValueError("stage count changed")
            if manifest.get("stage_identities") != self.pipeline.stage_identities:
                raise ValueError("stage model identity changed")
            raw_caches = manifest.get("caches")
            if not isinstance(raw_caches, list) or len(raw_caches) != self.pipeline.stage_count:
                raise ValueError("invalid stage cache list")
            cache_paths: list[Path] = []
            for entry in raw_caches:
                if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
                    raise ValueError("invalid stage cache entry")
                path = generation_dir / entry["file"]
                if path.stat().st_size != entry.get("size"):
                    raise ValueError(f"cache size mismatch: {path.name}")
                cache_paths.append(path)
            payload = manifest.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("invalid cache payload")
            self.pipeline.load_cache_files(cache_paths)
            LOGGER.info(
                "Loaded multicard %s KV cache generation=%s",
                kind,
                pointer["generation"],
            )
            return payload
        except Exception:
            LOGGER.exception("Failed to load multicard %s KV cache group", kind)
            self.invalidate(kind)
            try:
                self.pipeline.clear_kvcache()
            except Exception:
                LOGGER.exception("Failed to clear KV cache after invalid cache group")
            return None

    def invalidate(self, kind: str) -> None:
        try:
            (self._kind_dir(kind) / "current.json").unlink()
        except FileNotFoundError:
            pass

    def _kind_dir(self, kind: str) -> Path:
        if kind not in {"system", "session"}:
            raise ValueError(f"Unsupported KV cache group kind: {kind}")
        return self.root / kind

    @staticmethod
    def _remove_old_generations(generations: Path, current: str) -> None:
        try:
            for path in generations.iterdir():
                if path.name == current:
                    continue
                if path.name.startswith(".tmp-") or path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
        except OSError:
            LOGGER.exception("Failed to clean old multicard KV cache generations")
