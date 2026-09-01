from __future__ import annotations

import ctypes

import numpy as np

from gateway.native_sampling import NativeSamplingEngine, NativeSamplingResultStruct
from gateway.schemas import GenerationRequest


class FakeFunction:
    def __init__(self, implementation):
        self.implementation = implementation
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.implementation(*args)


class FakeTokenizer:
    special_eos_ids = [2]
    linefeed_id = 1

    def __len__(self):
        return 3

    def token_to_piece(self, token_id):
        return ["a", "b", "<eos>"][token_id]


class FakeLibrary:
    def __init__(self):
        self.destroyed_sessions = []
        self.params = None
        self.structure = None
        self.claw_sampling_engine_create = FakeFunction(lambda *args: 0x1000)
        self.claw_sampling_engine_destroy = FakeFunction(lambda handle: None)
        self.claw_sampling_session_create = FakeFunction(self._create_session)
        self.claw_sampling_session_destroy = FakeFunction(self.destroyed_sessions.append)
        self.claw_sampling_session_sample_f16 = FakeFunction(self._sample_f16)
        self.claw_sampling_session_sample_f32 = FakeFunction(self._sample_f32)
        self.claw_sampling_last_error = FakeFunction(lambda: b"")

    def _create_session(self, engine, structure, params):
        self.structure = structure
        self.params = params._obj
        return 0x2000

    @staticmethod
    def _set_result(result_ptr, token_id):
        result = ctypes.cast(
            result_ptr,
            ctypes.POINTER(NativeSamplingResultStruct),
        ).contents
        result.token_id = token_id
        result.mask_applied = 1
        result.grammar_active_before = 1
        result.grammar_active_after = 1
        result.mask_ms = 0.25
        result.sampler_ms = 0.75
        result.accept_ms = 0.05
        return 0

    def _sample_f16(self, session, logits_ptr, result_ptr):
        values = np.ctypeslib.as_array(
            ctypes.cast(logits_ptr, ctypes.POINTER(ctypes.c_uint16)),
            shape=(3,),
        ).view(np.float16)
        return self._set_result(result_ptr, int(np.argmax(values)))

    def _sample_f32(self, session, logits_ptr, result_ptr):
        values = np.ctypeslib.as_array(
            ctypes.cast(logits_ptr, ctypes.POINTER(ctypes.c_float)),
            shape=(3,),
        )
        return self._set_result(result_ptr, int(np.argmax(values)))


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
        top_k=40,
        repeat_penalty=1.1,
        frequency_penalty=0.25,
        presence_penalty=0.5,
        stop=[],
        enable_thinking=True,
    )


def test_native_engine_wraps_model_and_request_lifecycle() -> None:
    library = FakeLibrary()
    engine = NativeSamplingEngine(FakeTokenizer(), seed=1234, repeat_last_n=32, library=library)
    session = engine.create_session(request(), '{"type":"structural_tag"}')

    fp32_result = engine.sample_fp32(np.array([-1.0, 4.0, 2.0], dtype=np.float32), session)
    fp16 = np.array([5.0, 1.0, 3.0], dtype=np.float16)
    fp16_result = engine.sample_fp16(ctypes.c_void_p(fp16.ctypes.data), session)
    engine.destroy_session(session)

    assert fp32_result.token_id == 1
    assert fp32_result.mask_applied is True
    assert fp32_result.sampler_ms == 0.75
    assert fp16_result.token_id == 0
    assert library.params.temperature == np.float32(0.7)
    assert library.params.top_k == 40
    assert library.params.repeat_last_n == 32
    assert library.params.frequency_penalty == np.float32(0.25)
    assert library.params.presence_penalty == np.float32(0.5)
    assert library.params.newline_token_id == 1
    assert library.params.penalize_newline == 0
    assert library.params.seed == 1234
    assert library.destroyed_sessions == [session]


def test_native_engine_rejects_non_contiguous_or_wrong_dtype_logits() -> None:
    engine = NativeSamplingEngine(FakeTokenizer(), library=FakeLibrary())
    session = engine.create_session(request(), None)

    try:
        engine.sample_fp32(np.array([1.0, 2.0, 3.0], dtype=np.float64), session)
    except RuntimeError as exc:
        assert "contiguous float32" in str(exc)
    else:
        raise AssertionError("float64 logits should be rejected")
