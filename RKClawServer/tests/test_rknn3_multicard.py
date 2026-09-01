from __future__ import annotations

import asyncio
import ctypes
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import numpy as np

from gateway.config import Settings
from gateway.runtime.rknn3 import RKNN3LiteBackend, _Job
from gateway.runtime.rknn3_multicard import (
    MulticardKVCacheManager,
    MulticardRKNN3Pipeline,
    PipelineRunStats,
)


class FakeTokenizer:
    special_eos_ids = [2]
    special_bos_ids = [1]
    linefeed_id = 198

    def __len__(self):
        return 8


def make_pipeline() -> MulticardRKNN3Pipeline:
    pipeline = MulticardRKNN3Pipeline(
        Settings(),
        FakeTokenizer(),
        None,
        lambda *_: 0,
        lambda *_: 0,
    )
    pipeline._stages = [object(), object()]
    pipeline._eos_ids = set(FakeTokenizer.special_eos_ids)
    pipeline._start_worker_pool()
    return pipeline


def test_multicard_initial_llm_args_use_penalty_settings() -> None:
    settings = Settings(frequency_penalty=0.25, presence_penalty=0.5)
    pipeline = MulticardRKNN3Pipeline(
        settings,
        FakeTokenizer(),
        None,
        lambda *_: 0,
        lambda *_: 0,
    )

    args = pipeline._initial_llm_args("qwen3")

    assert args["frequency_penalty"] == 0.25
    assert args["presence_penalty"] == 0.5


def test_multicard_native_sampler_settings_are_mapped_by_name(monkeypatch) -> None:
    captured = {}

    class RecordingSamplingPipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.started = False

        def start(self):
            self.started = True

    monkeypatch.setattr(
        "gateway.xgrammar_sampling.SamplingPipeline",
        RecordingSamplingPipeline,
    )
    settings = Settings(
        enable_xgrammar=True,
        xgrammar_model_structure="qwen3.5",
        xgrammar_debug=True,
        enable_native_sampling=True,
        native_sampling_seed=123,
        native_repeat_last_n=37,
        native_penalize_newline=True,
        sampling_library="/tmp/libclaw_native.so",
    )
    backend = RKNN3LiteBackend(settings, FakeTokenizer())

    backend._initialize_custom_sampler()

    assert captured == {
        "tokenizer": backend.tokenizer,
        "enable_xgrammar": True,
        "enable_native_sampling": True,
        "native_seed": 123,
        "repeat_last_n": 37,
        "penalize_newline": True,
        "debug": True,
        "model_structure": "qwen3.5",
        "sampling_library": "/tmp/libclaw_native.so",
    }
    assert backend._custom_sampler.started is True


def test_multicard_request_reuses_one_sampling_state_for_all_tokens() -> None:
    class ImmediateLoop:
        def call_soon_threadsafe(self, callback, *args):
            callback(*args)

    class RecordingSampler:
        def __init__(self):
            self.created = []
            self.sampled = []
            self.closed = []

        def create_state(self, sampling_request):
            state = object()
            self.created.append((sampling_request, state))
            return state

        def sample(self, logits_ptr, state):
            self.sampled.append(state)
            return 7

        def close_state(self, state):
            self.closed.append(state)

    settings = Settings(enable_native_sampling=True)
    backend = RKNN3LiteBackend(settings, FakeTokenizer())
    sampler = RecordingSampler()

    class SamplingPipeline:
        stage_count = 2

        def clear_kvcache(self):
            return None

        def generate(self, *args, **kwargs):
            for _ in range(3):
                assert backend._sampling_callback(None, None, None) == 7
            return PipelineRunStats(
                generated_tokens=3,
                prompt_tokens=1,
                started_at=0.0,
                ended_at=0.1,
                stage_ms=[0.04, 0.06],
                reached_limit=False,
            )

    request = SimpleNamespace(
        max_new_tokens=3,
        enable_thinking=False,
        top_k=settings.top_k,
        top_p=settings.top_p,
        temperature=settings.temperature,
        repeat_penalty=settings.repeat_penalty,
        frequency_penalty=0.75,
        presence_penalty=0.5,
        stop=[],
    )
    job = _Job("prompt", [11], request, asyncio.Queue())
    backend._loop = ImmediateLoop()
    backend._custom_sampler = sampler
    backend._multicard = SamplingPipeline()
    backend._applied_sampling = backend._sampling_parameters(settings)
    sampling_updates = []
    backend._set_sampling = sampling_updates.append
    backend._active = job

    backend._worker_multicard(job)

    assert len(sampler.created) == 1
    assert sampling_updates == []
    assert sampler.created[0][0] is request
    state = sampler.created[0][1]
    assert sampler.sampled == [state, state, state]
    assert sampler.closed == [state]
    assert job.sampling_state is None


def test_generate_routes_each_sampled_token_through_all_stages_until_eos() -> None:
    pipeline = make_pipeline()
    sampled = iter([101, 102, 2])
    calls = []

    def run_once(tokens, **kwargs):
        calls.append((list(tokens), kwargs))
        return next(sampled), [1.0, 2.0]

    pipeline.run_once = run_once

    stats = pipeline.generate(
        [10, 11, 12],
        max_new_tokens=8,
        enable_thinking=True,
        keep_history=False,
        cancelled=threading.Event(),
        stop_requested=lambda: False,
    )

    assert [call[0] for call in calls] == [[10, 11, 12], [101], [102]]
    assert calls[0][1]["keep_history"] is False
    assert all(call[1]["keep_history"] is True for call in calls[1:])
    assert calls[0][1]["enable_thinking"] is True
    assert stats.generated_tokens == 3
    assert stats.reached_limit is False
    assert stats.stage_ms == [3.0, 6.0]


def test_generate_stops_at_requested_token_limit() -> None:
    pipeline = make_pipeline()
    sampled = iter([101, 102])
    pipeline.run_once = lambda *_args, **_kwargs: (next(sampled), [0.0, 0.0])

    stats = pipeline.generate(
        [10],
        max_new_tokens=2,
        enable_thinking=False,
        keep_history=False,
        cancelled=threading.Event(),
        stop_requested=lambda: False,
    )

    assert stats.generated_tokens == 2
    assert stats.reached_limit is True


def test_run_once_samples_only_the_last_bucket_and_preserves_prior_bucket_history() -> None:
    pipeline = make_pipeline()
    stage_one_calls = []

    class StageZero:
        def session_run(self, **kwargs):
            slot = pipeline._step().slots[0]
            with slot.condition:
                slot.batches.extend([
                    type("Batch", (), {"data": [[0.0], [0.0]], "n_tokens": 2})(),
                    type("Batch", (), {"data": [[0.0]], "n_tokens": 1})(),
                ])
                slot.condition.notify_all()
            return 0, []

    class StageOne:
        def session_run(self, **kwargs):
            stage_one_calls.append(kwargs)
            if not kwargs["disable_sampling"]:
                pipeline._step().next_token = 77
            return 0, []

    pipeline._stages[0] = type("Stage", (), {"rknn": StageZero()})()
    pipeline._stages[1] = type("Stage", (), {"rknn": StageOne()})()

    token, _ = pipeline.run_once(
        [10, 11, 12],
        keep_history=False,
        enable_thinking=False,
        sample=True,
    )

    assert token == 77
    assert len(stage_one_calls) == 2
    assert stage_one_calls[0]["disable_sampling"] is True
    assert stage_one_calls[1]["disable_sampling"] is False
    assert stage_one_calls[0]["keep_history"] is False
    assert stage_one_calls[1]["keep_history"] is True


class OldToolkitRuntime:
    def session_run(self, inputs=None, prompt=None, embeds=None, keep_history=None):
        return 0, []

    def create_output_tensors(self):
        return None, 0


def test_pipeline_rejects_toolkit_without_token_pipeline_inputs() -> None:
    with pytest.raises(RuntimeError, match="token/embed"):
        MulticardRKNN3Pipeline._validate_session_run_api(OldToolkitRuntime(), 0)


def test_b8_session_run_uses_reserved_controls_and_embedding_dim_first_view() -> None:
    calls = []
    infer_param = SimpleNamespace(reserved=[0] * 128)

    class LiteB8Runtime:
        llm = SimpleNamespace(rknn_session=SimpleNamespace(infer_param=infer_param))

        def session_run(
            self,
            inputs=None,
            prompt=None,
            embeds=None,
            tokens=None,
            keep_history=None,
            max_new_tokens=None,
            enable_thinking=False,
            session_index=0,
        ):
            calls.append(
                {
                    "shape": embeds.shape,
                    "flat": embeds.reshape(-1).copy(),
                    "controls": list(infer_param.reserved[:2]),
                }
            )
            return 0, []

    pipeline = make_pipeline()
    runtime = LiteB8Runtime()
    stage = SimpleNamespace(rknn=runtime, embedding_dim=4)
    data = np.arange(8, dtype=np.float16).reshape(2, 4)

    result = pipeline._session_run(
        stage,
        embeds=data,
        n_tokens=2,
        keep_history=False,
        max_new_tokens=1,
        enable_thinking=False,
        prefill_only=True,
        disable_sampling=True,
    )

    assert result == (0, [])
    assert calls[0]["shape"] == (4, 2)
    assert calls[0]["flat"].tolist() == data.reshape(-1).tolist()
    assert calls[0]["controls"] == [1, 1]
    assert infer_param.reserved[:2] == [0, 0]


def test_release_destroys_session_before_output_memory_and_runtime() -> None:
    events = []
    tensor = SimpleNamespace(mem=object())

    class Runtime:
        def release(self, session_index=None):
            events.append("session" if session_index == 0 else "runtime")
            return 0

        def destroy_mem(self, mem):
            events.append("memory")
            return 0

    pipeline = make_pipeline()
    pipeline._stages = [
        SimpleNamespace(
            index=0,
            rknn=Runtime(),
            output_tensors=[tensor],
            n_output_tensors=1,
        )
    ]

    pipeline.release()

    assert events == ["session", "memory", "runtime"]
    assert tensor.mem is None


def test_kv_checkpoint_policy_is_bounded_by_stage_context() -> None:
    class SaveCheckpoint(ctypes.Structure):
        _fields_ = [
            ("checkpoint_start_pos", ctypes.c_int),
            ("checkpoint_interval", ctypes.c_int),
            ("max_checkpoint_count", ctypes.c_int),
        ]

    class PolicyParam(ctypes.Structure):
        _fields_ = [("save_checkpoint", SaveCheckpoint)]

    class Policy:
        RKNN3_KVCACHE_POLICY_NORMAL = 0
        RKNN3_KVCACHE_POLICY_SAVE_CHECKPOINT = 1

    calls = []

    class Runtime:
        def set_kvcache_policy(self, policy, param=None):
            if param is None:
                calls.append((policy, None))
            else:
                checkpoint = param.save_checkpoint
                calls.append(
                    (
                        policy,
                        checkpoint.checkpoint_start_pos,
                        checkpoint.checkpoint_interval,
                        checkpoint.max_checkpoint_count,
                    )
                )
            return 0

    pipeline = make_pipeline()
    pipeline._types = SimpleNamespace(
        RKNN3KVCachePolicy=Policy,
        RKNN3KVCachePolicyParam=PolicyParam,
    )
    pipeline._stages = [
        SimpleNamespace(index=0, rknn=Runtime(), max_context_len=4096)
    ]

    pipeline.configure_kv_cache()

    assert calls == [(0, None), (1, 1024, 1024, 3)]


class FakeCachePipeline:
    stage_count = 2
    stage_identities = [
        {"device_id": "device-0", "rknn": {"size": 1}, "weight": {"size": 2}},
        {"device_id": "device-1", "rknn": {"size": 3}, "weight": {"size": 4}},
    ]

    def __init__(self):
        self.loaded_paths: list[Path] = []
        self.clear_calls = 0

    def save_cache_files(self, paths):
        for index, path in enumerate(paths):
            path.write_bytes(f"cache-{index}".encode())

    def load_cache_files(self, paths):
        self.loaded_paths = list(paths)

    def clear_kvcache(self):
        self.clear_calls += 1


def test_cache_manager_saves_and_loads_complete_stage_group(tmp_path) -> None:
    pipeline = FakeCachePipeline()
    manager = MulticardKVCacheManager(pipeline, tmp_path)

    assert manager.save("system", {"system_prompt": "system", "tokens": [1, 2]}) is True
    payload = manager.load("system")

    assert payload == {"system_prompt": "system", "tokens": [1, 2]}
    assert [path.name for path in pipeline.loaded_paths] == [
        "stage-000.cache",
        "stage-001.cache",
    ]
    pointer = json.loads((tmp_path / "multicard/system/current.json").read_text())
    assert pointer["version"] == 1


def test_cache_manager_invalidates_whole_group_when_one_stage_file_is_corrupt(tmp_path) -> None:
    pipeline = FakeCachePipeline()
    manager = MulticardKVCacheManager(pipeline, tmp_path)
    assert manager.save("session", {"history_tokens": [3, 4]}) is True
    cache_file = next((tmp_path / "multicard/session/generations").glob("*/stage-001.cache"))
    cache_file.write_bytes(b"broken-size")

    assert manager.load("session") is None
    assert pipeline.clear_calls == 1
    assert not (tmp_path / "multicard/session/current.json").exists()


def test_cache_manager_clears_all_stages_after_partial_native_load_failure(tmp_path) -> None:
    class FailingPipeline(FakeCachePipeline):
        def load_cache_files(self, paths):
            raise RuntimeError("stage 1 failed")

    pipeline = FailingPipeline()
    manager = MulticardKVCacheManager(pipeline, tmp_path)
    assert manager.save("system", {"system_prompt": "system", "tokens": [1]}) is True

    assert manager.load("system") is None
    assert pipeline.clear_calls == 1


class FakeBackendPipeline:
    def __init__(self):
        self.clear_calls = 0

    def clear_kvcache(self):
        self.clear_calls += 1


class FakeBackendCache:
    def __init__(self):
        self.saved = []
        self.session_payload = {"history_tokens": [10, 20]}

    def save(self, kind, payload):
        self.saved.append((kind, payload))
        return True

    def load(self, kind):
        assert kind == "session"
        return self.session_payload


def test_backend_keeps_session_cache_across_consecutive_tool_jobs_and_restores_prefix() -> None:
    backend = RKNN3LiteBackend(
        Settings(kv_cache_system_marker="MARK", kv_cache_dir="/cache"),
        FakeTokenizer(),
    )
    pipeline = FakeBackendPipeline()
    cache = FakeBackendCache()
    backend._multicard = pipeline
    backend._multicard_cache = cache
    backend._multicard_native_history_tokens = [10, 20]
    backend._multicard_native_is_main = True

    first_tool = _Job(
        "tool",
        [70],
        SimpleNamespace(),
        asyncio.Queue(),
        system_prompt="tool system",
    )
    tokens, keep_history, is_main = backend._prepare_multicard_input(first_tool)
    assert (tokens, keep_history, is_main) == ([70], False, False)
    assert backend._in_tool_call is True
    assert cache.saved == [("session", {"history_tokens": [10, 20]})]

    second_tool = _Job(
        "tool 2",
        [80],
        SimpleNamespace(),
        asyncio.Queue(),
        system_prompt="another tool",
    )
    backend._prepare_multicard_input(second_tool)
    assert backend._in_tool_call is True

    main = _Job(
        "main",
        [10, 20, 30],
        SimpleNamespace(),
        asyncio.Queue(),
        system_prompt="MARK main",
    )
    tokens, keep_history, is_main = backend._prepare_multicard_input(main)
    assert (tokens, keep_history, is_main) == ([30], True, True)
    assert backend._in_tool_call is False
    assert pipeline.clear_calls == 2
