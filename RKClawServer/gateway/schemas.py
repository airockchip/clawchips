from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from .config import Settings


logger = logging.getLogger("gateway")


class InvalidRequest(ValueError):
    pass


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_template_dict(self) -> dict[str, Any]:
        # llama.cpp renders a normalized message object. Supplying stable keys
        # keeps the rendered prompt predictable across templates that access
        # optional message fields directly.
        result: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
            "tool_calls": self.tool_calls,
            "reasoning_content": self.reasoning_content,
            "tool_call_id": self.tool_call_id,
            "name": self.name,
        }
        return result


@dataclass(frozen=True)
class GenerationRequest:
    model: str
    messages: list[ChatMessage]
    tools: list[dict[str, Any]]
    tool_choice: str
    parallel_tool_calls: bool
    stream: bool
    include_usage: bool
    max_new_tokens: int
    temperature: float
    top_p: float
    top_k: int
    repeat_penalty: float
    stop: list[str]
    enable_thinking: bool
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    session_id: str | None = None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_openai(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }


ALLOWED_FIELDS = {
    "model", "messages", "tools", "tool_choice", "parallel_tool_calls", "stream",
    "stream_options", "max_completion_tokens", "max_tokens", "temperature", "top_p",
    "top_k", "repeat_penalty", "frequency_penalty", "presence_penalty", "stop",
    "chat_template_kwargs", "session_id",
    "reasoning_effort",
}


def parse_chat_request(body: Any, settings: Settings) -> GenerationRequest:
    if not isinstance(body, dict):
        raise InvalidRequest("Request body must be a JSON object")
    unknown = sorted(set(body) - ALLOWED_FIELDS)
    if unknown:
        raise InvalidRequest(f"Unsupported request field(s): {', '.join(unknown)}")

    model = body.get("model")
    if model != settings.model_id:
        logger.warning(
            "Requested model does not match configured model: requested=%r configured=%r",
            model,
            settings.model_id,
        )

    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise InvalidRequest("'messages' must be a non-empty array")
    messages = [_parse_message(value, index) for index, value in enumerate(raw_messages)]

    tools = body.get("tools", [])
    if tools is None:
        tools = []
    if not isinstance(tools, list):
        raise InvalidRequest("'tools' must be an array")
    for index, tool in enumerate(tools):
        _validate_tool(tool, index)

    tool_choice = body.get("tool_choice", "auto")
    if tool_choice not in {"auto", "none", "required"}:
        raise InvalidRequest("'tool_choice' must be one of: auto, none, required")

    stream = _bool(body.get("stream", False), "stream")
    stream_options = body.get("stream_options", {})
    if not isinstance(stream_options, dict):
        raise InvalidRequest("'stream_options' must be an object")
    unknown_stream = set(stream_options) - {"include_usage"}
    if unknown_stream:
        raise InvalidRequest("Unsupported stream_options field(s): " + ", ".join(sorted(unknown_stream)))

    kwargs = body.get("chat_template_kwargs", {})
    if not isinstance(kwargs, dict):
        raise InvalidRequest("'chat_template_kwargs' must be an object")
    unknown_kwargs = set(kwargs) - {"enable_thinking"}
    if unknown_kwargs:
        raise InvalidRequest("Unsupported chat_template_kwargs field(s): " + ", ".join(sorted(unknown_kwargs)))

    max_tokens = body.get("max_completion_tokens", body.get("max_tokens", settings.max_new_tokens))
    max_tokens = _integer(max_tokens, "max_completion_tokens", minimum=1)
    max_tokens = min(max_tokens, settings.max_new_tokens)
    temperature = _number(body.get("temperature", settings.temperature), "temperature", minimum=0)
    top_p = _number(body.get("top_p", settings.top_p), "top_p", minimum=0, maximum=1)
    top_k = _integer(body.get("top_k", settings.top_k), "top_k", minimum=0)
    repeat_penalty = _number(body.get("repeat_penalty", settings.repeat_penalty), "repeat_penalty", minimum=0)
    frequency_penalty = _number(
        body.get("frequency_penalty", settings.frequency_penalty),
        "frequency_penalty",
        minimum=-2,
        maximum=2,
    )
    presence_penalty = _number(
        body.get("presence_penalty", settings.presence_penalty),
        "presence_penalty",
        minimum=-2,
        maximum=2,
    )

    raw_stop = body.get("stop", [])
    if isinstance(raw_stop, str):
        stop = [raw_stop]
    elif isinstance(raw_stop, list) and all(isinstance(item, str) for item in raw_stop):
        stop = raw_stop
    else:
        raise InvalidRequest("'stop' must be a string or an array of strings")
    if any(not item for item in stop):
        raise InvalidRequest("'stop' strings must not be empty")

    enable_thinking = kwargs.get("enable_thinking", settings.enable_thinking)
    enable_thinking = _bool(enable_thinking, "chat_template_kwargs.enable_thinking")

    session_id = body.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise InvalidRequest("'session_id' must be a string")

    return GenerationRequest(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        parallel_tool_calls=_bool(body.get("parallel_tool_calls", True), "parallel_tool_calls"),
        stream=stream,
        include_usage=_bool(stream_options.get("include_usage", False), "stream_options.include_usage"),
        max_new_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        repeat_penalty=repeat_penalty,
        stop=stop,
        enable_thinking=enable_thinking,
        frequency_penalty=frequency_penalty,
        presence_penalty=presence_penalty,
        session_id=session_id,
    )


def _parse_message(value: Any, index: int) -> ChatMessage:
    if not isinstance(value, dict):
        raise InvalidRequest(f"messages[{index}] must be an object")
    role = value.get("role")
    if role not in {"system", "user", "assistant", "tool"}:
        raise InvalidRequest(f"messages[{index}].role is invalid")
    content = value.get("content")
    tool_calls = value.get("tool_calls", [])
    if content is not None and not isinstance(content, str):
        raise InvalidRequest(f"messages[{index}].content must be a string or null")
    if role != "assistant" and content is None:
        raise InvalidRequest(f"messages[{index}].content is required")
    if role == "assistant" and content is None and not tool_calls:
        raise InvalidRequest(f"messages[{index}] must contain content or tool_calls")
    if not isinstance(tool_calls, list):
        raise InvalidRequest(f"messages[{index}].tool_calls must be an array")
    for tc_index, tool_call in enumerate(tool_calls):
        _validate_tool_call(tool_call, f"messages[{index}].tool_calls[{tc_index}]")
    reasoning = value.get("reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        raise InvalidRequest(f"messages[{index}].reasoning_content must be a string")
    tool_call_id = value.get("tool_call_id")
    if role == "tool" and not isinstance(tool_call_id, str):
        raise InvalidRequest(f"messages[{index}].tool_call_id is required")
    return ChatMessage(role, content, reasoning, tool_calls, tool_call_id, value.get("name"))


def _validate_tool(tool: Any, index: int) -> None:
    if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
        raise InvalidRequest(f"tools[{index}] must be a function tool")
    function = tool["function"]
    if not isinstance(function.get("name"), str) or not function["name"]:
        raise InvalidRequest(f"tools[{index}].function.name is required")
    if "parameters" in function and not isinstance(function["parameters"], dict):
        raise InvalidRequest(f"tools[{index}].function.parameters must be an object")


def _validate_tool_call(value: Any, field_name: str) -> None:
    if not isinstance(value, dict) or value.get("type") != "function" or not isinstance(value.get("function"), dict):
        raise InvalidRequest(f"{field_name} must be a function tool call")
    function = value["function"]
    if not isinstance(function.get("name"), str):
        raise InvalidRequest(f"{field_name}.function.name is required")
    if not isinstance(function.get("arguments"), str):
        raise InvalidRequest(f"{field_name}.function.arguments must be a JSON string")


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidRequest(f"'{name}' must be a boolean")
    return value


def _integer(value: Any, name: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidRequest(f"'{name}' must be an integer")
    if minimum is not None and value < minimum:
        raise InvalidRequest(f"'{name}' must be >= {minimum}")
    return value


def _number(value: Any, name: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRequest(f"'{name}' must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise InvalidRequest(f"'{name}' must be finite")
    if minimum is not None and parsed < minimum:
        raise InvalidRequest(f"'{name}' must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise InvalidRequest(f"'{name}' must be <= {maximum}")
    return parsed
