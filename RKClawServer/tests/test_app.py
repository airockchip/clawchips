from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

import gateway.app as app_module
import gateway.service as service_module
from gateway.app import create_app
from gateway.config import Settings
from gateway.runtime.base import InferenceEvent, RuntimeBackend
from gateway.schemas import ChatMessage, GenerationRequest, Usage
from gateway.service import GatewayService


QWEN_TEMPLATE = """{%- if tools %}<tools>{{ tools | tojson }}</tools>{# model emits <tool_call> #}{% endif -%}
{%- for message in messages %}<|im_start|>{{ message.role }}
{{ message.content or '' }}<|im_end|>
{% endfor -%}
{%- if add_generation_prompt %}<|im_start|>assistant
{% if not enable_thinking %}<think>

</think>

{% endif %}{% endif -%}"""


class FakeTokenizer:
    chat_template = QWEN_TEMPLATE
    chat_templates = {"default": QWEN_TEMPLATE, "tool_use": QWEN_TEMPLATE}
    special_bos_ids = [151643]
    special_eos_ids = [151645]
    bos_token_id = 151643
    eos_token_id = 151645
    linefeed_id = 198
    bos_token = "<|endoftext|>"
    eos_token = "<|im_end|>"

    def __init__(self) -> None:
        self.encode_calls = 0

    def encode(self, text, add_special_tokens=False):
        self.encode_calls += 1
        return text.split()


class FakeBackend(RuntimeBackend):
    def __init__(self, events: list[InferenceEvent], event_delay_s: float = 0.0):
        self.events = events
        self.event_delay_s = event_delay_s
        self._ready = False
        self.prompts: list[str] = []
        self.prompt_tokens: list[list[int]] = []
        self.requests: list[GenerationRequest] = []

    @property
    def ready(self) -> bool:
        return self._ready

    async def start(self) -> None:
        self._ready = True

    async def close(self) -> None:
        self._ready = False

    def generate(self, prompt: str, prompt_tokens: list[int], request: GenerationRequest, system_prompt: str = "") -> AsyncIterator[InferenceEvent]:
        self.prompts.append(prompt)
        self.prompt_tokens.append(list(prompt_tokens))
        self.requests.append(request)

        async def iterate():
            for event in self.events:
                if self.event_delay_s:
                    await asyncio.sleep(self.event_delay_s)
                yield event

        return iterate()


class BlockingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__([])
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.generate_calls = 0
        self.active = 0
        self.max_active = 0

    def generate(
        self,
        prompt: str,
        prompt_tokens: list[int],
        request: GenerationRequest,
        system_prompt: str = "",
    ) -> AsyncIterator[InferenceEvent]:
        self.prompts.append(prompt)
        self.prompt_tokens.append(list(prompt_tokens))
        self.requests.append(request)
        self.generate_calls += 1

        async def iterate():
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started.set()
            try:
                await self.release.wait()
                yield InferenceEvent("text", text="answer")
                yield InferenceEvent(
                    "done", finish_reason="stop", usage=Usage(3, 1))
            finally:
                self.active -= 1

        return iterate()


def settings(**overrides) -> Settings:
    values = {
        "model_id": "Qwen3-4B-Instruct",
        "rknn_path": "/model/model.rknn",
        "weight_path": "/model/model.weight",
        "tokenizer_path": "/model/tokenizer",
        "embed_path": "/model/embed.bin",
    }
    values.update(overrides)
    return Settings(**values)


def client_for(events: list[InferenceEvent], **setting_overrides):
    backend = FakeBackend(events)
    config = settings(**setting_overrides)
    service = GatewayService(config, tokenizer=FakeTokenizer(), backend=backend)
    return TestClient(create_app(settings=config, service=service)), backend


def request_body(**overrides):
    body = {"model": "Qwen3-4B-Instruct", "messages": [{"role": "user", "content": "hello"}]}
    body.update(overrides)
    return body


async def test_non_streaming_completion_is_cancelled_on_disconnect() -> None:
    disconnect_messages: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    service_started = asyncio.Event()
    service_cancelled = asyncio.Event()

    class DisconnectingRequest:
        async def receive(self):
            return await disconnect_messages.get()

    class BlockingService:
        async def complete(self, parsed, req_id):
            service_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                service_cancelled.set()

    completion = asyncio.create_task(app_module._complete_until_disconnect(
        DisconnectingRequest(), BlockingService(), object(), "queued-request"))
    await asyncio.wait_for(service_started.wait(), timeout=1)
    await disconnect_messages.put({"type": "http.disconnect"})

    with pytest.raises(app_module.ClientDisconnected):
        await asyncio.wait_for(completion, timeout=1)
    assert service_cancelled.is_set()


async def test_service_passes_rendered_prompt_tokens_to_backend() -> None:
    backend = FakeBackend([InferenceEvent("done", finish_reason="stop", usage=Usage(3, 0))])
    tokenizer = FakeTokenizer()
    service = GatewayService(settings(), tokenizer=tokenizer, backend=backend)
    await service.start()
    try:
        request = GenerationRequest(
            model="Qwen3-4B-Instruct",
            messages=[ChatMessage("user", "hello")],
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

        await service.complete(request)
    finally:
        await service.close()

    assert backend.prompts
    assert backend.prompt_tokens == [backend.prompts[0].split()]
    assert tokenizer.encode_calls == 1



async def test_service_captures_rendered_model_input_and_raw_output() -> None:
    raw_output = "<think>raw reason</think>raw answer"
    backend = FakeBackend([
        InferenceEvent("text", text=raw_output),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 2)),
    ])
    service = GatewayService(settings(), tokenizer=FakeTokenizer(), backend=backend)
    await service.start()
    try:
        request = GenerationRequest(
            model="Qwen3-4B-Instruct",
            messages=[ChatMessage("user", "hello")],
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
        await service.complete(request, "model-trace")
        trace = service.take_model_trace("model-trace")
    finally:
        await service.close()

    assert trace == {"input": backend.prompts[0], "output": raw_output}
    assert service.take_model_trace("model-trace") is None


async def test_streaming_service_captures_raw_model_output() -> None:
    raw_output = "streamed raw output"
    backend = FakeBackend([
        InferenceEvent("text", text=raw_output),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 2)),
    ])
    service = GatewayService(settings(), tokenizer=FakeTokenizer(), backend=backend)
    await service.start()
    try:
        request = GenerationRequest(
            model="Qwen3-4B-Instruct",
            messages=[ChatMessage("user", "hello")],
            tools=[],
            tool_choice="auto",
            parallel_tool_calls=True,
            stream=True,
            include_usage=True,
            max_new_tokens=16,
            temperature=0.7,
            top_p=0.9,
            top_k=1,
            repeat_penalty=1.0,
            stop=[],
            enable_thinking=True,
        )
        chunks = [chunk async for chunk in service.stream(request, "stream-model-trace")]
        trace = service.take_model_trace("stream-model-trace")
    finally:
        await service.close()

    assert chunks[-1] == b"data: [DONE]\n\n"
    assert trace == {"input": backend.prompts[0], "output": raw_output}

def test_chat_completion_separates_reasoning() -> None:
    client, backend = client_for([
        InferenceEvent("text", text="<thi"),
        InferenceEvent("text", text="nk>reason</think>answer"),
        InferenceEvent("done", finish_reason="stop", usage=Usage(8, 4)),
    ])
    with client:
        response = client.post("/v1/chat/completions", json=request_body())

    assert response.status_code == 200
    payload = response.json()
    assert payload["choices"][0]["message"] == {
        "role": "assistant", "content": "answer", "reasoning_content": "reason"
    }
    assert payload["usage"] == {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}
    assert backend.requests[0].enable_thinking is True


def test_reasoning_separation_can_be_disabled() -> None:
    client, _ = client_for([
        InferenceEvent("text", text="<think>reason</think>answer"),
        InferenceEvent("done", finish_reason="stop", usage=Usage()),
    ], separate_reasoning=False)
    with client:
        response = client.post("/v1/chat/completions", json=request_body())

    message = response.json()["choices"][0]["message"]
    assert message["content"] == "<think>reason</think>answer"
    assert "reasoning_content" not in message


def test_streaming_chat_and_usage() -> None:
    client, _ = client_for([
        InferenceEvent("text", text="<think>why"),
        InferenceEvent("text", text="</think>yes"),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 2)),
    ])
    with client:
        with client.stream("POST", "/v1/chat/completions", json=request_body(
            stream=True, stream_options={"include_usage": True}
        )) as response:
            body = response.read().decode()

    assert response.status_code == 200
    assert '"role":"assistant"' in body
    assert '"reasoning_content":"why"' in body
    assert '"content":"yes"' in body
    assert '"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}' in body
    assert body.endswith("data: [DONE]\n\n")


def test_streaming_emits_openai_heartbeat_while_waiting_for_prefill() -> None:
    backend = FakeBackend(
        [InferenceEvent("done", finish_reason="stop", usage=Usage(3, 0))],
        event_delay_s=0.03,
    )
    config = settings(sse_heartbeat_interval_s=0.01)
    service = GatewayService(config, tokenizer=FakeTokenizer(), backend=backend)
    client = TestClient(create_app(settings=config, service=service))

    with client:
        with client.stream(
            "POST", "/v1/chat/completions", json=request_body(stream=True)
        ) as response:
            body = response.read().decode()

    assert response.status_code == 200
    assert response.headers["x-accel-buffering"] == "no"
    assert '"delta":{},"finish_reason":null' in body
    assert body.endswith("data: [DONE]\n\n")


async def test_service_serializes_inference_and_drops_cancelled_waiter() -> None:
    backend = BlockingBackend()
    config = settings()
    service = GatewayService(config, tokenizer=FakeTokenizer(), backend=backend)
    await service.start()
    request = GenerationRequest(
        model="Qwen3-4B-Instruct",
        messages=[ChatMessage("user", "hello")],
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
    first = asyncio.create_task(service.complete(request, "first"))
    second: asyncio.Task | None = None
    try:
        await asyncio.wait_for(backend.started.wait(), timeout=1)
        second = asyncio.create_task(service.complete(request, "retry"))
        await asyncio.sleep(0)

        assert backend.generate_calls == 1
        assert backend.max_active == 1

        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second

        backend.release.set()
        await first
        await service.complete(request, "next")

        assert backend.generate_calls == 2
        assert backend.max_active == 1
    finally:
        backend.release.set()
        tasks = [first] + ([second] if second is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()


def test_streaming_can_be_disabled_by_server_config() -> None:
    client, _ = client_for([
        InferenceEvent("text", text="<think>why</think>yes"),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 2)),
    ], enable_streaming=False)
    with client:
        response = client.post(
            "/v1/chat/completions", json=request_body(stream=True)
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    message = response.json()["choices"][0]["message"]
    assert message == {
        "role": "assistant", "content": "yes", "reasoning_content": "why"
    }


def test_debug_logs_cover_complete_request_lifecycle(caplog, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("gateway.logging_utils.LOG_DIR", tmp_path)
    monkeypatch.setattr("gateway.logging_utils.LOG_DIR", tmp_path)
    client, _ = client_for([
        InferenceEvent("text", text="<think>why</think>yes"),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 2)),
    ], debug_logs=True)
    with caplog.at_level(logging.DEBUG, logger="gateway"):
        with client:
            response = client.post("/v1/chat/completions", json=request_body())

    assert response.status_code == 200
    logs = caplog.text
    assert "OpenAI API request" in logs
    assert "BEGIN OpenAI API request" in logs
    assert "END OpenAI API request" in logs
    assert "LLM input prompt" in logs
    assert "BEGIN LLM input prompt" in logs
    assert "END LLM input prompt" in logs
    assert "raw LLM output" in logs
    assert "BEGIN raw LLM output" in logs
    assert "END raw LLM output" in logs
    assert "<think>why</think>yes" in logs
    assert "final OpenAI API response" in logs
    assert "BEGIN final OpenAI API response" in logs
    assert "END final OpenAI API response" in logs
    assert '"reasoning_content": "why"' in logs
    assert "LLM input token stats" in logs
    assert "LLM output token stats" in logs
    assert "output_tokens=1" in logs
    assert "usage_completion_tokens=2" in logs


def test_raw_llm_output_logger_renders_escaped_newlines(caplog) -> None:
    output = "first\\n\\n\\nsecond"
    client, _ = client_for([
        InferenceEvent("text", text=output),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 1)),
    ], debug_logs=True)

    with caplog.at_level(logging.DEBUG, logger="gateway"):
        with client:
            response = client.post("/v1/chat/completions", json=request_body())

    assert response.status_code == 200
    assert "BEGIN raw LLM output\nfirst\n\n\nsecond\n" in caplog.text
    assert "first\\n\\n\\nsecond" not in caplog.text


def test_non_streaming_raw_llm_output_logs_before_tool_call_correction(monkeypatch) -> None:
    order: list[str] = []

    def fake_log_text(label, value, req_id, target, logger_max_chars=0):
        if label == "raw LLM output":
            order.append("raw_log")

    def fake_correct(self, original):
        order.append("correction")
        return original

    monkeypatch.setattr(service_module, "log_text", fake_log_text)
    monkeypatch.setattr("gateway.output.ToolCallCorrector.correct", fake_correct)

    output = '<tool_call>\n{"name":"weather","arguments":{"city":"SZ"}}\n</tool_call>'
    client, _ = client_for([
        InferenceEvent("text", text=output),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 1)),
    ], llm_output_log="logger", enable_tool_call_correction=True)

    with client:
        response = client.post(
            "/v1/chat/completions",
            json=request_body(tools=[{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}]),
        )

    assert response.status_code == 200
    assert order[:2] == ["raw_log", "correction"]


def test_corrected_llm_output_is_logged_when_tool_call_correction_enabled(caplog) -> None:
    output = '<tool_call>\n{name:"weather",arguments:{city:"SZ"}}\n</tool_call>'
    client, _ = client_for([
        InferenceEvent("text", text=output),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 1)),
    ], llm_output_log="logger", enable_tool_call_correction=True)

    with caplog.at_level(logging.DEBUG, logger="gateway"):
        with client:
            response = client.post(
                "/v1/chat/completions",
                json=request_body(tools=[{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}]),
            )

    assert response.status_code == 200
    assert "BEGIN raw LLM output" in caplog.text
    assert '{name:"weather",arguments:{city:"SZ"}}' in caplog.text
    assert "BEGIN corrected LLM output" in caplog.text
    assert '{"name":"weather","arguments":{"city":"SZ"}}' in caplog.text


def test_corrected_llm_output_is_not_logged_without_actual_correction(caplog) -> None:
    output = '<tool_call>\n{"name":"weather","arguments":{"city":"SZ"}}\n</tool_call>'
    client, _ = client_for([
        InferenceEvent("text", text=output),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 1)),
    ], llm_output_log="logger", enable_tool_call_correction=True)

    with caplog.at_level(logging.DEBUG, logger="gateway"):
        with client:
            response = client.post(
                "/v1/chat/completions",
                json=request_body(tools=[{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}]),
            )

    assert response.status_code == 200
    assert "BEGIN raw LLM output" in caplog.text
    assert "BEGIN corrected LLM output" not in caplog.text


def test_input_token_stats_compare_against_previous_request(caplog) -> None:
    client, _ = client_for([
        InferenceEvent("text", text="answer"),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 1)),
    ])
    with caplog.at_level(logging.INFO, logger="gateway"):
        with client:
            first = client.post("/v1/chat/completions", json=request_body())
            second = client.post("/v1/chat/completions", json=request_body())

    assert first.status_code == 200
    assert second.status_code == 200
    lines = [line for line in caplog.text.splitlines() if "LLM input token stats" in line]
    assert len(lines) == 2
    assert "previous_input_tokens=0" in lines[0]
    assert "common_tokens=0" in lines[0]
    assert "rollback_tokens=0" in lines[0]
    assert "added_tokens=0" not in lines[0]
    assert "rollback_tokens=0" in lines[1]
    assert "added_tokens=0" in lines[1]


def test_token_comparison_counts_common_rollback_and_added_tokens() -> None:
    stats = service_module._compare_tokens([1, 2, 3, 4], [1, 2, 9, 10, 11])

    assert stats.input_tokens == 5
    assert stats.previous_input_tokens == 4
    assert stats.common_tokens == 2
    assert stats.rollback_tokens == 2
    assert stats.added_tokens == 3


def test_empty_output_token_stats_do_not_call_tokenizer(caplog) -> None:
    tokenizer = FakeTokenizer()
    service = GatewayService(settings(), tokenizer=tokenizer)

    with caplog.at_level(logging.INFO, logger="gateway"):
        service._log_output_token_stats("", Usage(), "cancelled-request")

    assert tokenizer.encode_calls == 0
    assert "output_tokens=0" in caplog.text
    assert "usage_completion_tokens=0" in caplog.text


def test_detail_logs_can_be_written_to_files(caplog, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("gateway.logging_utils.LOG_DIR", tmp_path)
    monkeypatch.setattr("gateway.logging_utils.LOG_DIR", tmp_path)
    client, _ = client_for([
        InferenceEvent("text", text="<think>why</think>yes"),
        InferenceEvent("done", finish_reason="stop", usage=Usage(3, 2)),
    ], openai_request_log="file", llm_input_log="file", llm_output_log="file", openai_response_log="file")

    with caplog.at_level(logging.INFO, logger="gateway"):
        with client:
            response = client.post("/v1/chat/completions", json=request_body())

    assert response.status_code == 200
    logs = caplog.text
    assert "OpenAI API request log written to" in logs
    assert "LLM input prompt log written to" in logs
    assert "raw LLM output log written to" in logs
    assert "final OpenAI API response log written to" in logs

    trace_files = list(tmp_path.glob("*-trace.log"))
    assert len(trace_files) == 1
    trace = trace_files[0].read_text(encoding="utf-8")
    assert "BEGIN OpenAI API request" in trace
    assert "BEGIN LLM input prompt" in trace
    assert "BEGIN raw LLM output" in trace
    assert "BEGIN final OpenAI API response" in trace
    assert '"messages"' in trace
    assert "<|im_start|>assistant" in trace
    assert "<think>why</think>yes" in trace
    assert '"reasoning_content": "why"' in trace


def test_logger_detail_logs_can_be_truncated_without_truncating_files(caplog, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("gateway.logging_utils.LOG_DIR", tmp_path)
    output = "abcdefghij0123456789"
    client, _ = client_for(
        [
            InferenceEvent("text", text=output),
            InferenceEvent("done", finish_reason="stop", usage=Usage(3, 1)),
        ],
        llm_output_log="both",
        logger_detail_log_max_chars=10,
    )

    with caplog.at_level(logging.DEBUG, logger="gateway"):
        with client:
            response = client.post("/v1/chat/completions", json=request_body())

    assert response.status_code == 200
    logs = caplog.text
    assert "abcde\n...\n56789" in logs
    assert output not in logs

    trace_files = list(tmp_path.glob("*-trace.log"))
    assert len(trace_files) == 1
    trace = trace_files[0].read_text(encoding="utf-8")
    assert output in trace


def test_debug_logs_cover_streaming_final_response(caplog, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("gateway.logging_utils.LOG_DIR", tmp_path)
    monkeypatch.setattr("gateway.logging_utils.LOG_DIR", tmp_path)
    client, _ = client_for([
        InferenceEvent("text", text="<think>why</think>yes"),
        InferenceEvent("done", finish_reason="stop", usage=Usage()),
    ], debug_logs=True)
    with caplog.at_level(logging.DEBUG, logger="gateway"):
        with client:
            with client.stream(
                "POST", "/v1/chat/completions", json=request_body(stream=True)
            ) as response:
                response.read()

    assert response.status_code == 200
    logs = caplog.text
    assert "raw LLM output" in logs
    assert "final OpenAI API response (SSE)" in logs
    assert '"reasoning_content":"why"' in logs
    assert "data: [DONE]" in logs
    assert "LLM input token stats" in logs
    assert "LLM output token stats" in logs


def test_tool_call_is_structured() -> None:
    output = '<think>checking</think><tool_call>\n{"name":"weather","arguments":{"city":"SZ"}}\n</tool_call>'
    client, _ = client_for([InferenceEvent("text", text=output), InferenceEvent("done", finish_reason="stop", usage=Usage())])
    tools = [{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}]
    with client:
        response = client.post("/v1/chat/completions", json=request_body(tools=tools))

    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["reasoning_content"] == "checking"
    call = choice["message"]["tool_calls"][0]
    assert call["function"] == {"name": "weather", "arguments": '{"city":"SZ"}'}
    assert call["id"].startswith("call_")


def test_tool_call_without_fallback_delimiter_treats_text_as_reasoning() -> None:
    output = 'checking weather<tool_call>\n{"name":"weather","arguments":{"city":"SZ"}}\n</tool_call>'
    client, _ = client_for([
        InferenceEvent("text", text=output),
        InferenceEvent("done", finish_reason="stop", usage=Usage()),
    ], fallback_delimiter="\n\n\n")
    tools = [{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}]
    with client:
        response = client.post("/v1/chat/completions", json=request_body(tools=tools))

    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] == ""
    assert choice["message"]["reasoning_content"] == "checking weather"


def test_tool_call_with_fallback_delimiter_splits_reasoning_and_content() -> None:
    output = 'checking\n\n\nvisible<tool_call>\n{"name":"weather","arguments":{"city":"SZ"}}\n</tool_call>'
    client, _ = client_for([
        InferenceEvent("text", text=output),
        InferenceEvent("done", finish_reason="stop", usage=Usage()),
    ], fallback_delimiter="\n\n\n")
    tools = [{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}]
    with client:
        response = client.post("/v1/chat/completions", json=request_body(tools=tools))

    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["reasoning_content"] == "checking"
    assert choice["message"]["content"] == "visible"


def test_streaming_tool_call_without_fallback_delimiter_emits_reasoning_before_tool() -> None:
    client, _ = client_for([
        InferenceEvent("text", text='checking weather<tool_call>\n{"name":"weather","arguments":{"city":"SZ"}}\n</tool_call>'),
        InferenceEvent("done", finish_reason="stop", usage=Usage()),
    ], fallback_delimiter="\n\n\n")
    tools = [{"type": "function", "function": {"name": "weather", "parameters": {"type": "object"}}}]
    with client:
        with client.stream("POST", "/v1/chat/completions", json=request_body(
            tools=tools, stream=True
        )) as response:
            body = response.read().decode()

    assert response.status_code == 200
    reasoning_index = body.index('"reasoning_content":"checking weather"')
    tool_index = body.index('"tool_calls"')
    assert reasoning_index < tool_index
    assert '"content":"checking weather"' not in body
    assert '"finish_reason":"tool_calls"' in body


def test_completion_without_tool_call_or_fallback_delimiter_treats_text_as_content() -> None:
    client, _ = client_for([
        InferenceEvent("text", text="plain answer"),
        InferenceEvent("done", finish_reason="stop", usage=Usage()),
    ], fallback_delimiter="\n\n\n")
    with client:
        response = client.post("/v1/chat/completions", json=request_body())

    message = response.json()["choices"][0]["message"]
    assert message["content"] == "plain answer"
    assert "reasoning_content" not in message


def test_request_validation_and_removed_completions_endpoint() -> None:
    client, _ = client_for([InferenceEvent("done", finish_reason="stop", usage=Usage())])
    with client:
        unknown = client.post("/v1/chat/completions", json=request_body(seed=1))
        wrong_model = client.post("/v1/chat/completions", json=request_body(model="other"))
        old_completion = client.post("/v1/completions", json={"model": "Qwen3-4B-Instruct", "prompt": "hi"})
    assert unknown.status_code == 400
    assert unknown.json()["error"]["type"] == "invalid_request_error"
    # Compatibility behavior: callers may use an alias or stale model name.
    # The gateway logs a warning and continues with the configured model.
    assert wrong_model.status_code == 200
    assert old_completion.status_code == 404


@pytest.mark.parametrize("field", ["frequency_penalty", "presence_penalty"])
def test_non_finite_penalty_request_is_rejected(field) -> None:
    client, _ = client_for([])
    body = request_body(**{field: float("nan")})
    with client:
        response = client.post(
            "/v1/chat/completions",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == f"'{field}' must be finite"


def test_health_ready_and_models() -> None:
    client, _ = client_for([])
    with client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ok"}
        assert client.get("/openapi.json").json()["info"]["title"] == "RKClawServer"
        models = client.get("/v1/models").json()
    assert models["data"][0]["id"] == "Qwen3-4B-Instruct"
    assert models["data"][0]["owned_by"] == "rknn"


def test_request_overrides_are_forwarded() -> None:
    client, backend = client_for([InferenceEvent("done", finish_reason="stop", usage=Usage())])
    with client:
        response = client.post("/v1/chat/completions", json=request_body(
            max_tokens=32,
            temperature=0.2,
            top_p=0.8,
            top_k=4,
            repeat_penalty=1.1,
            frequency_penalty=0.25,
            presence_penalty=0.5,
            chat_template_kwargs={"enable_thinking": False},
        ))
    assert response.status_code == 200
    request = backend.requests[0]
    assert (request.max_new_tokens, request.temperature, request.top_p, request.top_k) == (32, 0.2, 0.8, 4)
    assert request.repeat_penalty == 1.1
    assert request.frequency_penalty == 0.25
    assert request.presence_penalty == 0.5
    assert request.enable_thinking is False
    assert backend.prompts[0].endswith("<think>\n\n</think>\n\n")
