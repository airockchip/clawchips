from __future__ import annotations

import ctypes
import json

import numpy as np

from gateway.native_sampling import NativeSamplingResult
from gateway.schemas import GenerationRequest
from gateway.xgrammar_sampling import (
    SamplingPipeline,
    SamplingState,
    _tool_call_json_schema,
    _tool_call_structure_tag,
)


class FakeTokenizer:
    pieces = {0: "thinking ", 1: "<tool_", 2: "call>", 3: "</tool_", 4: "call>"}
    special_eos_ids = [4]

    def __len__(self):
        return 5

    def decode(self, tokens):
        raise AssertionError("incremental tool-call detection must not decode full history")

    def token_to_piece(self, token):
        return self.pieces[token]


class FakeNativeEngine:
    def __init__(self):
        self.session = object()
        self.structural_tag = None
        self.destroyed = []
        self.results = []

    def create_session(self, request, structural_tag_json):
        self.structural_tag = structural_tag_json
        return self.session

    def sample_fp16(self, logits_ptr, session):
        assert session is self.session
        if self.results:
            return self.results.pop(0)
        return native_result(0)

    def destroy_session(self, session):
        self.destroyed.append(session)


def native_result(
    token_id: int,
    *,
    active_before: bool = False,
    active_after: bool = False,
    completed: bool = False,
) -> NativeSamplingResult:
    return NativeSamplingResult(
        token_id=token_id,
        mask_applied=active_before,
        grammar_active_before=active_before,
        grammar_active_after=active_after,
        grammar_completed=completed,
        mask_ms=0.25 if active_before else 0.0,
        sampler_ms=0.75,
        accept_ms=0.05,
    )


def generation_request(*, tools=None) -> GenerationRequest:
    return GenerationRequest(
        model="model",
        messages=[],
        tools=tools or [],
        tool_choice="auto",
        parallel_tool_calls=True,
        stream=False,
        include_usage=False,
        max_new_tokens=16,
        temperature=0.7,
        top_p=0.9,
        top_k=40,
        repeat_penalty=1.1,
        stop=[],
        enable_thinking=True,
    )


def logits_buffer() -> ctypes.Array:
    values = np.zeros(5, dtype=np.float16).view(np.uint16)
    return (ctypes.c_uint16 * len(values))(*[int(value) for value in values])


def test_native_sampling_works_without_xgrammar_state() -> None:
    engine = FakeNativeEngine()
    sampler = SamplingPipeline(
        FakeTokenizer(),
        enable_xgrammar=False,
        enable_native_sampling=True,
        native_engine=engine,
    )
    state = sampler.create_state(generation_request())

    token_id = sampler.sample(logits_buffer(), state)

    assert token_id == 0
    assert state.native_session is engine.session
    assert state.sample_count == 1
    assert state.choose_total_ms == 0.75
    assert state.accept_total_ms == 0.05


def test_create_state_serializes_open_qwen35_structure_for_native_compiler() -> None:
    engine = FakeNativeEngine()
    sampler = SamplingPipeline(
        FakeTokenizer(),
        enable_xgrammar=True,
        enable_native_sampling=True,
        model_structure="qwen3.5",
        native_engine=engine,
    )
    tools = [{
        "type": "function",
        "function": {"name": "exec", "parameters": {"type": "object"}},
    }]

    sampler.create_state(generation_request(tools=tools))
    structure = json.loads(engine.structural_tag)

    assert structure["type"] == "structural_tag"
    elements = structure["format"]["elements"]
    assert structure["format"]["type"] == "sequence"
    assert elements[0] == {"type": "const_string", "value": "\n<function="}
    assert elements[1] == {"type": "regex", "pattern": "[A-Za-z0-9_-]{1,64}"}
    assert elements[3]["style"] == "qwen_xml"
    assert elements[3]["json_schema"]["additionalProperties"] is True
    assert "any_order" not in elements[3]


def test_active_native_matcher_timing_is_recorded() -> None:
    engine = FakeNativeEngine()
    engine.results.append(native_result(4, active_before=True, completed=True))
    sampler = SamplingPipeline(
        FakeTokenizer(),
        enable_xgrammar=True,
        native_engine=engine,
    )
    state = SamplingState(native_session=engine.session)

    token_id = sampler.sample(logits_buffer(), state)

    assert token_id == 4
    assert state.mask_count == 1
    assert state.mask_total_ms == 0.25
    assert state.masked_choose_count == 1
    assert state.masked_choose_total_ms == 0.75


def test_close_state_destroys_native_request_session() -> None:
    engine = FakeNativeEngine()
    sampler = SamplingPipeline(FakeTokenizer(), False, True, native_engine=engine)
    state = sampler.create_state(generation_request())

    sampler.close_state(state)

    assert engine.destroyed == [engine.session]
    assert state.native_session is None


def test_debug_sampling_log_cadence() -> None:
    assert SamplingPipeline._should_log_step(1) is True
    assert SamplingPipeline._should_log_step(32) is True
    assert SamplingPipeline._should_log_step(33) is False
    assert SamplingPipeline._should_log_step(50) is True


def test_qwen3_structure_keeps_json_name_and_arguments_schema() -> None:
    tools = [{
        "type": "function",
        "function": {
            "name": "exec",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }]

    schema = _tool_call_json_schema(tools)
    structure = _tool_call_structure_tag(tools, "qwen3")

    assert schema["properties"]["name"] == {"enum": ["exec"]}
    assert schema["properties"]["arguments"]["required"] == ["command"]
    assert structure["format"]["type"] == "sequence"
    assert structure["format"]["elements"][0] == {
        "type": "json_schema", "json_schema": schema
    }
    assert structure["format"]["elements"][1] == {
        "type": "const_string", "value": "</tool_call>"
    }


def test_qwen35_tool_call_tag_allows_an_open_function_name() -> None:
    structure = _tool_call_structure_tag([{
        "type": "function",
        "function": {
            "name": "exec",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }], "qwen3.5")

    sequence = structure["format"]
    elements = sequence["elements"]
    assert structure["type"] == "structural_tag"
    assert sequence["type"] == "sequence"
    assert elements == [
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
    ]


def test_qwen35_tool_call_structure_does_not_enumerate_request_tools() -> None:
    tools = [
        {"type": "function", "function": {
            "name": "exec", "parameters": {"type": "object"},
        }},
        {"type": "function", "function": {
            "name": "weather", "parameters": {
                "type": "object", "properties": {"city": {"type": "string"}},
            },
        }},
    ]

    structure = _tool_call_structure_tag(tools, "qwen3.5")

    serialized = json.dumps(structure, ensure_ascii=False)
    assert "exec" not in serialized
    assert "weather" not in serialized
    assert "any_order" not in serialized
    assert structure["format"]["elements"][1] == {
        "type": "regex",
        "pattern": "[A-Za-z0-9_-]{1,64}",
    }
