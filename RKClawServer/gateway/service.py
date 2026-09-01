from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .logging_utils import (
    LOG_DIR,
    detail_logs_enabled,
    ensure_detail_req_id,
    log_json,
    log_target_enabled,
    log_text,
    write_detail_log,
)
from .output import OutputDelta, ReasoningTransformer, ToolCallParser, make_parser
from .parsing import ParseConfig, build_parse_config, get_profile
from .runtime import InferenceEvent, RKNN3LiteBackend, RuntimeBackend
from .schemas import GenerationRequest, Usage
from .template import ChatTemplateEngine, load_tokenizer

logger = logging.getLogger("gateway")


class InferenceFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenComparison:
    input_tokens: int
    previous_input_tokens: int
    common_tokens: int
    rollback_tokens: int
    added_tokens: int


class GatewayService:
    def __init__(
        self,
        settings: Settings,
        tokenizer: Any | None = None,
        backend: RuntimeBackend | None = None,
    ):
        self.settings = settings
        self.tokenizer = tokenizer
        self.backend = backend
        self.templates: ChatTemplateEngine | None = None
        self.parse_config: ParseConfig | None = None
        self.created = int(time.time())
        self._previous_input_tokens: list[int] = []
        self._request_usage: dict[str, Usage] = {}
        self._request_model_traces: dict[str, dict[str, str]] = {}
        # One RKNN session maps to one NPU execution lane. Keep waiting
        # requests in asyncio so cancellation removes retries before they can
        # enter the backend's native FIFO.
        self._inference_lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return bool(self.backend and self.backend.ready and self.templates)

    async def start(self) -> None:
        if self.tokenizer is None:
            self.tokenizer = load_tokenizer(self.settings)
        self.templates = ChatTemplateEngine(self.tokenizer, self.settings)
        if self.backend is None:
            self.backend = RKNN3LiteBackend(self.settings, self.tokenizer)
        await self.backend.start()
        profile = get_profile(self.backend.model_type)
        logger.info(
            "Model profile selected backend_model_type=%r profile=%s rewrite_tool_format=%s logits_name=%s",
            self.backend.model_type,
            profile.model_type,
            profile.rewrite_tool_format,
            profile.logits_name,
        )
        self.parse_config = build_parse_config(
            profile,
            self.tokenizer,
            self.templates.tool_start,
            self.templates.tool_end,
        )
        self.templates.set_parse_config(self.parse_config)
        self.templates.validate()

    def take_usage(self, req_id: str) -> Usage | None:
        """Return and remove internal usage captured for one request."""
        if not req_id:
            return None
        return self._request_usage.pop(req_id, None)

    def take_model_trace(self, req_id: str) -> dict[str, str] | None:
        """Return and remove the rendered model input and raw model output."""
        if not req_id:
            return None
        return self._request_model_traces.pop(req_id, None)

    def _begin_model_trace(self, req_id: str, prompt: str) -> None:
        if req_id:
            self._request_model_traces[req_id] = {"input": prompt, "output": ""}

    def _finish_model_trace(self, req_id: str, raw_output: str) -> None:
        if req_id:
            trace = self._request_model_traces.setdefault(req_id, {"input": "", "output": ""})
            trace["output"] = raw_output

    async def close(self) -> None:
        if self.backend is not None:
            await self.backend.close()
        close_tokenizer = getattr(self.tokenizer, "close", None)
        if callable(close_tokenizer):
            close_tokenizer()

    async def _serialized_inference(
        self,
        prompt: str,
        prompt_tokens: list[int],
        request: GenerationRequest,
        system_prompt: str,
        req_id: str,
    ) -> AsyncIterator[InferenceEvent]:
        queued = self._inference_lock.locked()
        admitted = False
        wait_started = time.perf_counter()
        if queued:
            logger.info("Inference request queued%s", f" req_id={req_id}" if req_id else "")
        try:
            async with self._inference_lock:
                admitted = True
                wait_ms = (time.perf_counter() - wait_started) * 1000
                logger.info(
                    "Inference request admitted%s queued=%s wait_ms=%.1f",
                    f" req_id={req_id}" if req_id else "",
                    queued,
                    wait_ms,
                )
                assert self.backend is not None
                events = self.backend.generate(
                    prompt, prompt_tokens, request, system_prompt)
                try:
                    async for event in events:
                        yield event
                finally:
                    await events.aclose()
        except asyncio.CancelledError:
            if not admitted:
                logger.info(
                    "Queued inference request cancelled before RKNN admission%s",
                    f" req_id={req_id}" if req_id else "",
                )
            raise

    def _make_parser(self, request: GenerationRequest) -> ToolCallParser:
        assert self.parse_config is not None
        return make_parser(
            self.parse_config,
            bool(request.tools),
            self.settings.enable_tool_call_correction,
        )

    def _make_transformer(self) -> ReasoningTransformer:
        assert self.parse_config is not None
        return ReasoningTransformer(
            self.settings.separate_reasoning,
            self.settings.fallback_delimiter,
            reasoning_starts=self.parse_config.reasoning_starts,
            reasoning_ends=self.parse_config.reasoning_ends,
            skip_prefix_opens=self.parse_config.skip_prefix_opens,
        )

    def render_prompt(self, request: GenerationRequest) -> str:
        prompt, _, _ = self._render_prompt_tokens(request)
        return prompt

    def _render_prompt_tokens(self, request: GenerationRequest) -> tuple[str, list[int], str]:
        if self.templates is None:
            raise RuntimeError("Template engine is not ready")
        result = self.templates.render(request)
        prompt = result.prompt
        system_prompt = result.system_prompt
        tokens = self.tokenizer.encode(prompt, add_special_tokens=False)
        if len(tokens) + request.max_new_tokens > self.settings.max_context_tokens:
            raise ValueError(
                f"Prompt ({len(tokens)} tokens) and requested completion ({request.max_new_tokens} tokens) "
                f"exceed the {self.settings.max_context_tokens}-token context window"
            )
        return prompt, tokens, system_prompt

    async def complete(self, request: GenerationRequest, req_id: str = "") -> dict[str, Any]:
        req_id = ensure_detail_req_id(req_id, self.settings)
        prompt, prompt_tokens, system_prompt = self._render_prompt_tokens(request)
        self._begin_model_trace(req_id, prompt)
        self._log_input_token_stats(prompt_tokens, req_id)
        if log_target_enabled(self.settings.llm_input_log):
            log_text(
                "LLM input prompt",
                prompt,
                req_id,
                self.settings.llm_input_log,
                self.settings.logger_detail_log_max_chars,
            )
        finish_reason = "stop"
        usage = Usage()
        raw_parts: list[str] = []
        events = self._serialized_inference(
            prompt, prompt_tokens, request, system_prompt, req_id)
        try:
            async for event in events:
                if event.type == "text":
                    raw_parts.append(event.text)
                elif event.type == "error":
                    raise InferenceFailed(event.error or "RKNN3 inference failed")
                elif event.type == "done":
                    finish_reason = event.finish_reason or "stop"
                    usage = event.usage or Usage()
        finally:
            self._finish_model_trace(req_id, "".join(raw_parts))
            await events.aclose()
        raw_output = "".join(raw_parts)
        self._log_output_token_stats(raw_output, usage, req_id)
        if log_target_enabled(self.settings.llm_output_log):
            log_text(
                "raw LLM output",
                raw_output,
                req_id,
                self.settings.llm_output_log,
                self.settings.logger_detail_log_max_chars,
            )
        parser = self._make_parser(request)
        if raw_output:
            parser.raw = raw_output
        if parser.corrector and log_target_enabled(self.settings.llm_output_log):
            corrected_output, changed = parser.corrected_output_with_status()
            if changed:
                log_text(
                    "corrected LLM output",
                    corrected_output,
                    req_id,
                    self.settings.llm_output_log,
                    self.settings.logger_detail_log_max_chars,
                )
        parsed = parser.full()
        transformer = self._make_transformer()
        transformed = transformer.feed(parsed.content) + transformer.finish(pending_as_reasoning=bool(parsed.tool_calls))
        content = "".join(delta.content for delta in transformed)
        reasoning = "".join(delta.reasoning_content for delta in transformed)
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if reasoning:
            message["reasoning_content"] = reasoning
        if parsed.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id or f"call_{uuid.uuid4().hex}",
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in parsed.tool_calls
            ]
            finish_reason = "tool_calls"
        response = _chat_response(request.model, message, finish_reason, usage)
        if req_id:
            self._request_usage[req_id] = usage
        if log_target_enabled(self.settings.openai_response_log):
            log_json(
                "final OpenAI API response",
                response,
                req_id,
                self.settings.openai_response_log,
                self.settings.logger_detail_log_max_chars,
            )
        return response

    def stream(self, request: GenerationRequest, req_id: str = "") -> AsyncIterator[bytes]:
        req_id = ensure_detail_req_id(req_id, self.settings)
        prompt, prompt_tokens, system_prompt = self._render_prompt_tokens(request)
        self._begin_model_trace(req_id, prompt)
        self._log_input_token_stats(prompt_tokens, req_id)
        if log_target_enabled(self.settings.llm_input_log):
            log_text(
                "LLM input prompt",
                prompt,
                req_id,
                self.settings.llm_input_log,
                self.settings.logger_detail_log_max_chars,
            )
        events = self._serialized_inference(
            prompt, prompt_tokens, request, system_prompt, req_id)
        return self._stream_events(request, events, req_id)

    async def _stream_events(
        self,
        request: GenerationRequest,
        events: AsyncIterator[InferenceEvent],
        req_id: str = "",
    ) -> AsyncIterator[bytes]:
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        parser = self._make_parser(request)
        transformer = self._make_transformer()
        saw_tools = False
        usage = Usage()
        response_chunks: list[bytes] = []

        def emit(chunk: bytes) -> bytes:
            response_chunks.append(chunk)
            return chunk

        heartbeat_events = _with_heartbeat(
            events, self.settings.sse_heartbeat_interval_s)
        try:
            yield emit(_sse(_stream_chunk(
                completion_id,
                created,
                request.model,
                {"role": "assistant", "content": None},
                None,
            )))
            async for event in heartbeat_events:
                if event is None:
                    # Use a valid OpenAI chunk instead of an SSE comment. SDKs
                    # commonly swallow comments inside __anext__, which would
                    # not reset an application-level stream idle timeout.
                    yield emit(_sse(_stream_chunk(
                        completion_id, created, request.model, {}, None)))
                    continue
                if event.type == "text":
                    try:
                        text, tool_deltas = parser.feed(event.text)
                    except RuntimeError:
                        _dump_parser_state(parser, completion_id)
                        raise
                    for delta in transformer.feed(text):
                        if not delta.empty():
                            yield emit(_sse(_stream_chunk(completion_id, created, request.model, _delta_json(delta), None)))
                    if tool_deltas:
                        saw_tools = True
                        for delta in transformer.finish(pending_as_reasoning=True):
                            if not delta.empty():
                                yield emit(_sse(_stream_chunk(completion_id, created, request.model, _delta_json(delta), None)))
                        yield emit(_sse(_stream_chunk(completion_id, created, request.model, {"tool_calls": tool_deltas}, None)))
                elif event.type == "error":
                    yield emit(_sse({"error": {"message": event.error or "RKNN3 inference failed", "type": "server_error", "code": "inference_error"}}))
                    yield emit(b"data: [DONE]\n\n")
                    return
                elif event.type == "done":
                    usage = event.usage or Usage()
                    try:
                        trailing, tool_deltas = parser.finish()
                    except RuntimeError:
                        _dump_parser_state(parser, completion_id)
                        raise
                    for delta in transformer.feed(trailing) + transformer.finish():
                        if not delta.empty():
                            yield emit(_sse(_stream_chunk(completion_id, created, request.model, _delta_json(delta), None)))
                    if tool_deltas:
                        saw_tools = True
                        for delta in transformer.finish(pending_as_reasoning=True):
                            if not delta.empty():
                                yield emit(_sse(_stream_chunk(completion_id, created, request.model, _delta_json(delta), None)))
                        yield emit(_sse(_stream_chunk(completion_id, created, request.model, {"tool_calls": tool_deltas}, None)))
                    finish = "tool_calls" if saw_tools else (event.finish_reason or "stop")
                    yield emit(_sse(_stream_chunk(completion_id, created, request.model, {}, finish)))
            if request.include_usage:
                payload = _stream_chunk(completion_id, created, request.model, {}, None)
                payload["choices"] = []
                payload["usage"] = usage.to_openai()
                yield emit(_sse(payload))
            yield emit(b"data: [DONE]\n\n")
        finally:
            self._finish_model_trace(req_id, parser.raw)
            await heartbeat_events.aclose()
            await events.aclose()
            if req_id:
                self._request_usage[req_id] = usage
            self._log_output_token_stats(parser.raw, usage, req_id)
            if log_target_enabled(self.settings.llm_output_log):
                log_text(
                    "raw LLM output",
                    parser.raw,
                    req_id,
                    self.settings.llm_output_log,
                    self.settings.logger_detail_log_max_chars,
                )
                if parser.corrector:
                    corrected_output, changed = parser.corrected_output_with_status()
                    if changed:
                        log_text(
                            "corrected LLM output",
                            corrected_output,
                            req_id,
                            self.settings.llm_output_log,
                            self.settings.logger_detail_log_max_chars,
                        )
            if log_target_enabled(self.settings.openai_response_log):
                log_text(
                    "final OpenAI API response (SSE)",
                    b"".join(response_chunks).decode("utf-8", errors="replace"),
                    req_id,
                    self.settings.openai_response_log,
                    self.settings.logger_detail_log_max_chars,
                )

    def _log_input_token_stats(self, tokens: list[int], req_id: str = "") -> None:
        stats = _compare_tokens(self._previous_input_tokens, tokens)
        self._previous_input_tokens = list(tokens)
        _log_token_stats("LLM input token stats", stats, req_id)

    def _log_output_token_stats(self, raw_output: str, usage: Usage, req_id: str = "") -> None:
        # A client can disconnect before the first output token. Apart from being
        # cheaper, this keeps diagnostics away from zero-length native input.
        output_tokens = len(self.tokenizer.encode(raw_output, add_special_tokens=False)) if raw_output else 0
        logger.info(
            "LLM output token stats%s output_tokens=%d usage_completion_tokens=%d",
            f" req_id={req_id}" if req_id else "",
            output_tokens,
            usage.completion_tokens,
        )


def _chat_response(model: str, message: dict[str, Any], finish_reason: str, usage: Usage) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason, "logprobs": None}],
        "usage": usage.to_openai(),
    }


def _stream_chunk(completion_id: str, created: int, model: str, delta: dict[str, Any], finish: str | None) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish, "logprobs": None}],
    }


def _delta_json(delta: OutputDelta) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if delta.content:
        result["content"] = delta.content
    if delta.reasoning_content:
        result["reasoning_content"] = delta.reasoning_content
    if delta.tool_calls:
        result["tool_calls"] = delta.tool_calls
    return result


def _compare_tokens(previous: list[int], current: list[int]) -> TokenComparison:
    common = 0
    for left, right in zip(previous, current):
        if left != right:
            break
        common += 1
    return TokenComparison(
        input_tokens=len(current),
        previous_input_tokens=len(previous),
        common_tokens=common,
        rollback_tokens=len(previous) - common,
        added_tokens=len(current) - common,
    )


def _log_token_stats(label: str, stats: TokenComparison, req_id: str = "") -> None:
    logger.info(
        "%s%s input_tokens=%d previous_input_tokens=%d common_tokens=%d rollback_tokens=%d added_tokens=%d",
        label,
        f" req_id={req_id}" if req_id else "",
        stats.input_tokens,
        stats.previous_input_tokens,
        stats.common_tokens,
        stats.rollback_tokens,
        stats.added_tokens,
    )


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


async def _with_heartbeat(
    events: AsyncIterator[InferenceEvent],
    interval_s: float,
) -> AsyncIterator[InferenceEvent | None]:
    """Yield ``None`` while waiting without cancelling the pending event read."""
    pending: asyncio.Task[InferenceEvent] | None = None
    timeout = max(0.001, interval_s)
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(events))
            done, _ = await asyncio.wait({pending}, timeout=timeout)
            if not done:
                yield None
                continue
            completed = pending
            pending = None
            try:
                event = completed.result()
            except StopAsyncIteration:
                return
            yield event
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending


def _dump_parser_state(parser: ToolCallParser, completion_id: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = LOG_DIR / f"{ts}-{completion_id[-12:]}-output.json"
    data = {
        "completion_id": completion_id,
        "raw": parser.raw,
        "previous_content": parser.previous.content,
        "previous_tool_calls": [
            {"name": c.name, "arguments": c.arguments}
            for c in parser.previous.tool_calls
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.error("non-monotonic stream detected completion_id=%s raw_len=%d", completion_id, len(parser.raw))
