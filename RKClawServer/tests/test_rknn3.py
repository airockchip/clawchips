from __future__ import annotations

import asyncio
import ctypes
import logging
import threading
from dataclasses import replace

import anyio

from gateway.config import Settings
from gateway.runtime.rknn3 import RKNN3LiteBackend, _Job
from gateway.schemas import GenerationRequest


class FakeTokenizer:
    special_eos_ids = [2]
    special_bos_ids = [1]
    linefeed_id = 198

    def __len__(self):
        return 8

    def encode(self, text, add_special_tokens=False):
        return [len(part) for part in text.split()]

    def decode(self, tokens):
        values = {
            (1,): "",
            (2,): "标",
            (1, 2): "星标",
        }
        return values[tuple(tokens)]


class FakeRKNN:
    def __init__(self):
        self.session_runs = []
        self.saved_kvcache_paths = []

    def session_run(self, **kwargs):
        self.session_runs.append(kwargs)
        tokens = kwargs.get("tokens") or []
        return 0, [0, len(tokens), 0.0, 0.0]

    def save_kvcache(self, path):
        self.saved_kvcache_paths.append(path)
        with open(path, "wb") as handle:
            handle.write(b"cache")
        return 0

    def clear_kvcache(self):
        return 0


class _FakeSamplingParam(ctypes.Structure):
    _fields_ = [
        ("top_k", ctypes.c_int32),
        ("top_p", ctypes.c_float),
        ("temperature", ctypes.c_float),
        ("repeat_penalty", ctypes.c_float),
        ("frequency_penalty", ctypes.c_float),
        ("presence_penalty", ctypes.c_float),
    ]


class _FakeVocabInfo(ctypes.Structure):
    _fields_ = [
        ("vocab_size", ctypes.c_int32),
        ("special_eos_id", ctypes.c_int32 * 64),
        ("n_special_eos_id", ctypes.c_int32),
        ("special_bos_id", ctypes.c_int32 * 64),
        ("n_special_bos_id", ctypes.c_int32),
        ("linefeed_id", ctypes.c_int32),
        ("ignore_eos_token", ctypes.c_bool),
        ("skip_special_token", ctypes.c_bool),
    ]


class _FakeLLMParam(ctypes.Structure):
    _fields_ = [
        ("logits_name", ctypes.c_char_p),
        ("max_context_len", ctypes.c_int32),
        ("sampling_param", _FakeSamplingParam),
        ("vocab_info", _FakeVocabInfo),
    ]


class _FakeTypes:
    RKNN3LLMParam = _FakeLLMParam


class FakeSetParamRKNN:
    def __init__(self):
        self.sampling_calls = []

    def set_llm_param(self, params, n_params):
        sampling = params.sampling_param
        self.sampling_calls.append((
            sampling.top_k,
            sampling.top_p,
            sampling.temperature,
            sampling.repeat_penalty,
            sampling.frequency_penalty,
            sampling.presence_penalty,
        ))
        return 0


class ImmediateLoop:
    def call_soon_threadsafe(self, callback, *args):
        callback(*args)


class FakeCustomSampler:
    def __init__(self):
        self.calls = []

    def sample(self, logits_ptr, state):
        self.calls.append((logits_ptr, state))
        return 7


class FakeLifecycleSampler:
    def __init__(self):
        self.created = []
        self.closed = []

    def create_state(self, sampling_request):
        state = object()
        self.created.append((sampling_request, state))
        return state

    def close_state(self, state):
        self.closed.append(state)


class FakeStopRKNN:
    def __init__(self):
        self.stop_calls = 0

    def session_stop(self):
        self.stop_calls += 1


class BlockingStopRKNN:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def session_stop(self):
        self.started.set()
        self.release.wait(timeout=1)
        self.finished.set()


class Result(ctypes.Structure):
    _fields_ = [
        ("num_tokens", ctypes.c_int32),
        ("token_ids", ctypes.POINTER(ctypes.c_int32)),
    ]


def test_rknn_callback_decodes_accumulated_tokens_to_preserve_split_utf8() -> None:
    backend = RKNN3LiteBackend(Settings(), FakeTokenizer())
    backend._loop = ImmediateLoop()
    request = GenerationRequest(
        model="model",
        messages=[],
        tools=[],
        tool_choice="auto",
        parallel_tool_calls=True,
        stream=False,
        include_usage=False,
        max_new_tokens=16,
        temperature=0.7,
        top_p=0.9,
        top_k=1,
        repeat_penalty=1.0,
        stop=[],
        enable_thinking=True,
    )
    job = _Job("", [], request, asyncio.Queue())
    backend._active = job

    first_tokens = (ctypes.c_int32 * 1)(1)
    first_result = Result(1, first_tokens)
    backend._result_callback(None, ctypes.pointer(first_result), 0)

    second_tokens = (ctypes.c_int32 * 1)(2)
    second_result = Result(1, second_tokens)
    backend._result_callback(None, ctypes.pointer(second_result), 0)

    event = job.events.get_nowait()
    assert event.type == "text"
    assert event.text == "星标"
    assert job.decoded == "星标"


def test_sampling_callback_uses_the_job_sampling_state() -> None:
    backend = RKNN3LiteBackend(Settings(), FakeTokenizer())
    sampler = FakeCustomSampler()
    state = object()
    job = _Job("", [], request(), asyncio.Queue(), sampling_state=state)
    backend._custom_sampler = sampler
    backend._active = job

    assert backend._sampling_callback(None, None, None) == 7
    assert sampler.calls == [(None, state)]
    assert job.sampling_callback_count == 1


async def test_iterate_cancellation_stops_active_session(caplog) -> None:
    backend = RKNN3LiteBackend(Settings(), FakeTokenizer())
    backend._rknn = FakeStopRKNN()
    job = _Job("", [], request(), asyncio.Queue())
    backend._active = job
    iterator = backend._iterate(job)

    with caplog.at_level(logging.INFO, logger="gateway.rknn3"):
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        pending.cancel()

        try:
            await pending
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("iteration cancellation did not propagate")

    assert job.cancelled.is_set()
    assert backend._rknn.stop_calls == 1
    assert "RKNN request cancellation received: active=True" in caplog.text



async def test_anyio_cancellation_waits_for_session_stop_cleanup() -> None:
    backend = RKNN3LiteBackend(Settings(), FakeTokenizer())
    stop = BlockingStopRKNN()
    backend._rknn = stop
    job = _Job("", [], request(), asyncio.Queue())
    backend._active = job
    iterator = backend._iterate(job)

    scope_ready = anyio.Event()
    consumer_done = anyio.Event()
    scopes = []

    async def consume() -> None:
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            scope_ready.set()
            await anext(iterator)
        consumer_done.set()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(consume)
        await scope_ready.wait()
        scopes[0].cancel()
        try:
            with anyio.fail_after(1):
                await anyio.to_thread.run_sync(stop.started.wait)
            await anyio.sleep(0)
            assert not consumer_done.is_set()
        finally:
            stop.release.set()
        await consumer_done.wait()

    assert stop.finished.is_set()

def request() -> GenerationRequest:
    return GenerationRequest(
        model="model",
        messages=[],
        tools=[],
        tool_choice="auto",
        parallel_tool_calls=True,
        stream=False,
        include_usage=False,
        max_new_tokens=16,
        temperature=0.7,
        top_p=0.9,
        top_k=1,
        repeat_penalty=1.0,
        stop=[],
        enable_thinking=True,
    )


def test_worker_passes_prompt_tokens_directly_to_session_run() -> None:
    backend = RKNN3LiteBackend(Settings(), FakeTokenizer())
    backend._loop = ImmediateLoop()
    backend._rknn = FakeRKNN()
    backend._applied_sampling = backend._sampling_parameters(backend.settings)
    sampling_updates = []
    backend._set_sampling = sampling_updates.append
    job = _Job("prompt text", [10, 11, 12], request(), asyncio.Queue())
    backend._jobs.put(job)
    backend._jobs.put(None)

    backend._worker_main()

    assert backend._rknn.session_runs == [{
        "tokens": [10, 11, 12],
        "keep_history": False,
        "max_new_tokens": 16,
        "enable_thinking": True,
    }]
    assert sampling_updates == [job.request]
    event = job.events.get_nowait()
    assert event.type == "done"
    assert event.usage.prompt_tokens == 3


def test_system_prompt_prefill_uses_tokens(tmp_path) -> None:
    settings = Settings(
        kv_cache_dir=str(tmp_path),
        kv_cache_system_marker="MARK",
    )
    backend = RKNN3LiteBackend(settings, FakeTokenizer())
    backend._loop = ImmediateLoop()
    backend._rknn = FakeRKNN()
    backend._applied_sampling = backend._sampling_parameters(settings)
    prompt = "<|im_start|>system\nMARK ready<|im_end|>\n<|im_start|>user\nhello<|im_end|>\n<|im_start|>assistant\n"
    system_prompt = "<|im_start|>system\nMARK ready<|im_end|>\n"
    job = _Job(prompt, [101, 102, 103], request(), asyncio.Queue(), system_prompt=system_prompt)
    backend._jobs.put(job)
    backend._jobs.put(None)

    backend._worker_main()

    assert len(backend._rknn.session_runs) == 2
    prefill = backend._rknn.session_runs[0]
    assert "prompt" not in prefill
    assert prefill["tokens"] == [len(part) for part in "<|im_start|>system\nMARK ready<|im_end|>\n".split()]
    assert prefill["keep_history"] is False
    assert prefill["max_new_tokens"] == 1
    assert prefill["enable_thinking"] is False
    assert backend._rknn.session_runs[1]["tokens"] == [101, 102, 103]


def test_custom_sampler_uses_separate_prefill_and_generation_states(tmp_path) -> None:
    settings = Settings(
        kv_cache_dir=str(tmp_path),
        kv_cache_system_marker="MARK",
    )
    backend = RKNN3LiteBackend(settings, FakeTokenizer())
    backend._loop = ImmediateLoop()
    backend._rknn = FakeRKNN()
    backend._applied_sampling = backend._sampling_parameters(settings)
    sampler = FakeLifecycleSampler()
    backend._custom_sampler = sampler
    sampling_updates = []
    backend._set_sampling = sampling_updates.append
    prompt = "<|im_start|>system\nMARK ready<|im_end|>\n<|im_start|>user\nhello<|im_end|>\n"
    system_prompt = "<|im_start|>system\nMARK ready<|im_end|>\n"
    sampling_request = replace(
        request(),
        frequency_penalty=0.75,
        presence_penalty=0.5,
    )
    job = _Job(prompt, [101, 102, 103], sampling_request, asyncio.Queue(), system_prompt=system_prompt)
    backend._jobs.put(job)
    backend._jobs.put(None)

    backend._worker_main()

    assert len(sampler.created) == 2
    assert sampling_updates == []
    assert all(created_request is sampling_request for created_request, _ in sampler.created)
    assert sampler.created[0][1] is not sampler.created[1][1]
    assert sampler.closed == [sampler.created[0][1], sampler.created[1][1]]
    assert job.sampling_state is None

def test_is_gemma4_matches_model_type_case_insensitive() -> None:
    backend = RKNN3LiteBackend(Settings(), FakeTokenizer())
    assert backend._is_gemma4() is False
    backend._model_type = "Gemma4"
    assert backend._is_gemma4() is True
    backend._model_type = "GEMMA4"
    assert backend._is_gemma4() is True


def test_model_type_property_exposes_internal_state() -> None:
    backend = RKNN3LiteBackend(Settings(), FakeTokenizer())
    assert backend.model_type == ""
    backend._model_type = "qwen3"
    assert backend.model_type == "qwen3"
    backend._model_type = "gemma4"
    assert backend.model_type == "gemma4"


def test_initial_llm_args_uses_logits_gathered_for_gemma4() -> None:
    backend = RKNN3LiteBackend(Settings(), FakeTokenizer())
    args = backend._initial_llm_args(vocab_size=8)
    assert args["logits_name"] == b"output"
    backend._model_type = "gemma4"
    args = backend._initial_llm_args(vocab_size=8)
    assert args["logits_name"] == b"logits_gathered"


def test_set_sampling_restores_defaults_after_request_override() -> None:
    settings = Settings()
    backend = RKNN3LiteBackend(settings, FakeTokenizer())
    backend._types = _FakeTypes
    backend._rknn = FakeSetParamRKNN()
    backend._applied_sampling = backend._sampling_parameters(settings)

    overridden = replace(
        request(),
        frequency_penalty=0.75,
        presence_penalty=0.5,
    )
    backend._set_sampling(overridden)
    backend._set_sampling(request())
    backend._set_sampling(request())

    assert len(backend._rknn.sampling_calls) == 2
    assert backend._rknn.sampling_calls[0][-2:] == (
        ctypes.c_float(0.75).value,
        ctypes.c_float(0.5).value,
    )
    assert backend._rknn.sampling_calls[1][-2:] == (0.0, 0.0)
    assert backend._applied_sampling == backend._sampling_parameters(settings)


class _Attr:
    def __init__(self, name):
        self.name = name


def test_tensor_name_handles_bytes_and_str() -> None:
    assert RKNN3LiteBackend._tensor_name(_Attr(b"per_layer_inputs\0junk")) == "per_layer_inputs"
    assert RKNN3LiteBackend._tensor_name(_Attr("rope_cos_cache_0")) == "rope_cos_cache_0"
