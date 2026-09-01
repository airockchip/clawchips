from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from .parsing import (
    DEFAULT_HERMES_FUNC_CLOSE,
    DEFAULT_HERMES_FUNC_PREFIX,
    DEFAULT_HERMES_PARAM_CLOSE,
    DEFAULT_HERMES_PARAM_PREFIX,
    DEFAULT_TOOL_END,
    DEFAULT_TOOL_START,
    ParseConfig,
)
from .tool_call_correction import ToolCallCorrector

logger = logging.getLogger("gateway")

# Reasoning block markers. Qwen models wrap reasoning in <think></think>,
# Gemma4 (AgentModel-V3) wraps each "channel" with <|channel>NAME\n...\n<channel|>.
# Both sets are always checked -- the tokens are distinct so there is no
# ambiguity, and a single request never mixes the two formats.
_REASONING_START_MARKERS = ("<" + "think" + ">", "<|channel>")
_REASONING_END_MARKERS = ("<" + "/think" + ">", "<channel|>")
# When the Gemma4 opening marker is matched, the channel name (e.g. "thought")
# and its trailing newline are not part of the reasoning text -- skip them.
_CHANNEL_NAME_SKIP_OPENS = ("<|channel>",)


@dataclass(frozen=True)
class ParsedToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ParsedOutput:
    content: str
    tool_calls: list[ParsedToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class OutputDelta:
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[dict] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.content and not self.reasoning_content and not self.tool_calls


class ToolCallParser:
    """Base tool-call parser.

    Finds ``tool_start``/``tool_end`` markers in raw model output and
    delegates the body between them to :meth:`_parse_body`.  Subclasses
    implement model-specific body parsing.  Provides streaming
    ``feed``/``finish`` that emit monotonic OpenAI deltas.
    """

    def __init__(
        self,
        tool_start: str = DEFAULT_TOOL_START,
        tool_end: str = DEFAULT_TOOL_END,
        parse_tool_calls: bool = True,
        correct_tool_calls: bool = False,
    ):
        self.tool_start = tool_start
        self.tool_end = tool_end
        self.parse_tool_calls = parse_tool_calls
        self.raw = ""
        self.previous = ParsedOutput("")
        self.call_ids: list[str] = []
        self.corrector = ToolCallCorrector() if correct_tool_calls else None

    def feed(self, text: str) -> tuple[str, list[dict]]:
        self.raw += text
        parsed = self._parse(self.raw)
        content_delta = _suffix(self.previous.content, parsed.content)
        tool_deltas = self._build_tool_deltas(parsed)
        self.previous = parsed
        return content_delta, tool_deltas

    def finish(self) -> tuple[str, list[dict]]:
        parsed = self._parse(self.raw, final=True)
        content_delta = _suffix(self.previous.content, parsed.content)
        self.previous = parsed
        return content_delta, []

    def full(self) -> ParsedOutput:
        return self._parse(self.raw, final=True)

    def corrected_output(self) -> str:
        corrected, _ = self.corrected_output_with_status()
        return corrected

    def corrected_output_with_status(self) -> tuple[str, bool]:
        if not self.corrector:
            return self.raw, False
        return _correct_tool_blocks(self.raw, self.corrector, self.tool_start, self.tool_end)

    def _build_tool_deltas(self, parsed: ParsedOutput) -> list[dict]:
        tool_deltas: list[dict] = []
        for index, call in enumerate(parsed.tool_calls):
            while len(self.call_ids) <= index:
                self.call_ids.append(f"call_{uuid.uuid4().hex}")
            previous = self.previous.tool_calls[index] if index < len(self.previous.tool_calls) else None
            delta: dict = {"index": index}
            if previous is None:
                delta.update({"id": self.call_ids[index], "type": "function"})
            function: dict[str, str] = {}
            if previous is None or call.name != previous.name:
                function["name"] = call.name
            args_delta = call.arguments if previous is None else _suffix(previous.arguments, call.arguments)
            if args_delta:
                function["arguments"] = args_delta
            if function:
                delta["function"] = function
            if len(delta) > 1:
                tool_deltas.append(delta)
        return tool_deltas

    def _parse(self, text: str, final: bool = False) -> ParsedOutput:
        """Find tool_start/tool_end markers, extract body, delegate to _parse_body."""
        content: list[str] = []
        calls: list[ParsedToolCall] = []
        cursor = 0
        while cursor < len(text):
            start_pos = text.find(self.tool_start, cursor) if self.parse_tool_calls else -1
            if start_pos < 0:
                tail = text[cursor:]
                if not final and self.parse_tool_calls:
                    tail = _without_partial_marker(tail, self.tool_start)
                content.append(tail)
                break
            content.append(text[cursor:start_pos])
            body_start = start_pos + len(self.tool_start)
            end = text.find(self.tool_end, body_start)
            if end < 0:
                partial = None if self.corrector else self._parse_body(text[body_start:], len(calls))
                if partial:
                    calls.append(partial)
                break
            parsed = self._parse_body(text[body_start:end], len(calls))
            if parsed:
                calls.append(parsed)
            else:
                content.append(text[start_pos:end + len(self.tool_end)])
            cursor = end + len(self.tool_end)
        return ParsedOutput("".join(content), calls)

    def _parse_body(self, body: str, index: int) -> ParsedToolCall | None:
        raise NotImplementedError


class QwenToolParser(ToolCallParser):
    """Qwen3/Qwen3.5 tool-call parser.

    Handles two body formats:
    - Qwen JSON: ``{"name":"...","arguments":{...}}``
    - Hermes XML (Qwen3.5 native): ``<function=NAME>...</function>``

    Hermes markers are also searched at the top level (not wrapped in
    tool_start/tool_end) because Qwen3.5 emits them directly.
    """

    def __init__(
        self,
        correct_tool_calls: bool = False,
        tool_start: str = DEFAULT_TOOL_START,
        tool_end: str = DEFAULT_TOOL_END,
        parse_tool_calls: bool = True,
        hermes_func_prefix: str = DEFAULT_HERMES_FUNC_PREFIX,
        hermes_func_close: str = DEFAULT_HERMES_FUNC_CLOSE,
        hermes_param_prefix: str = DEFAULT_HERMES_PARAM_PREFIX,
        hermes_param_close: str = DEFAULT_HERMES_PARAM_CLOSE,
    ):
        super().__init__(tool_start, tool_end, parse_tool_calls, correct_tool_calls)
        self._hermes_func_prefix = hermes_func_prefix
        self._hermes_func_close = hermes_func_close
        self._hermes_param_prefix = hermes_param_prefix
        self._hermes_param_close = hermes_param_close

    def _parse(self, text: str, final: bool = False) -> ParsedOutput:
        content: list[str] = []
        calls: list[ParsedToolCall] = []
        cursor = 0
        while cursor < len(text):
            qwen_pos = text.find(self.tool_start, cursor) if self.parse_tool_calls else -1
            hermes_pos = (
                text.find(self._hermes_func_prefix, cursor)
                if self.parse_tool_calls and self._hermes_func_prefix
                else -1
            )
            if qwen_pos < 0 and hermes_pos < 0:
                tail = text[cursor:]
                if not final and self.parse_tool_calls:
                    tail = _without_partial_marker(tail, self.tool_start)
                    if self._hermes_func_prefix:
                        tail = _without_partial_marker(tail, self._hermes_func_prefix)
                content.append(tail)
                break
            if hermes_pos >= 0 and (qwen_pos < 0 or hermes_pos <= qwen_pos):
                content.append(text[cursor:hermes_pos])
                func_end = text.find(self._hermes_func_close, hermes_pos)
                if func_end < 0:
                    if not final:
                        break
                    content.append(text[hermes_pos:])
                    break
                block = text[hermes_pos:func_end + len(self._hermes_func_close)]
                hermes = self._parse_hermes_xml(block)
                if hermes:
                    call_id = self.call_ids[len(calls)] if len(calls) < len(self.call_ids) else ""
                    calls.append(ParsedToolCall(call_id, hermes[0], hermes[1]))
                else:
                    content.append(block)
                cursor = func_end + len(self._hermes_func_close)
                continue
            start = qwen_pos
            content.append(text[cursor:start])
            body_start = start + len(self.tool_start)
            end = text.find(self.tool_end, body_start)
            if end < 0:
                partial = self._parse_incomplete_body(text[body_start:], len(calls), final)
                if partial:
                    calls.append(partial)
                break
            parsed = self._parse_body(text[body_start:end], len(calls))
            if parsed:
                calls.append(parsed)
            else:
                content.append(text[start:end + len(self.tool_end)])
            cursor = end + len(self.tool_end)
        return ParsedOutput("".join(content), calls)

    def _parse_incomplete_body(self, body: str, index: int, final: bool) -> ParsedToolCall | None:
        """Parse a streaming body only when its format supports partial output.

        Wrapped Hermes calls must remain buffered until ``</function>``.
        This decision is based on the XML framing rather than whether JSON
        correction is enabled. JSON correction still requires the complete
        body because a repaired partial value cannot be emitted monotonically.
        """
        stripped = body.strip()
        if not final and self._is_incomplete_hermes_body(stripped):
            return None
        if self.corrector:
            return None
        return self._parse_body(body, index)

    def _is_incomplete_hermes_body(self, body: str) -> bool:
        if not self._hermes_func_prefix:
            return False
        if not body or self._hermes_func_prefix.startswith(body):
            return True
        return body.startswith(self._hermes_func_prefix) and self._hermes_func_close not in body

    def _parse_body(self, body: str, index: int) -> ParsedToolCall | None:
        stripped = body.strip()
        logger.debug(
            "tool_call parse begin body_len=%d body_preview=%r",
            len(stripped),
            stripped[:200],
        )
        hermes = self._parse_hermes_xml(stripped)
        if hermes:
            name, arguments = hermes
            logger.debug(
                "tool_call parsed via hermes name=%s arguments_len=%d arguments_preview=%r",
                name,
                len(arguments),
                arguments[:200],
            )
            return ParsedToolCall(self.call_ids[index] if index < len(self.call_ids) else "", name, arguments)
        text_to_parse = stripped
        if self.corrector:
            corrected = self.corrector.correct(stripped)
            if corrected is None:
                logger.warning(
                    "tool_call correction returned None name=unknown body_len=%d body_preview=%r",
                    len(stripped),
                    stripped[:200],
                )
            elif corrected != stripped:
                logger.debug(
                    "tool_call correction applied original_len=%d corrected_len=%d corrected_preview=%r",
                    len(stripped),
                    len(corrected),
                    corrected[:200],
                )
            text_to_parse = corrected or ""
        try:
            value = json.loads(text_to_parse)
            if not isinstance(value, dict) or not isinstance(value.get("name"), str):
                logger.warning(
                    "tool_call json invalid structure body_len=%d body_preview=%r",
                    len(text_to_parse),
                    text_to_parse[:200],
                )
                return None
            name = value["name"]
        except json.JSONDecodeError:
            name_match = re.search(r'"name"\s*:\s*"([^"]*)"', text_to_parse)
            if not name_match:
                logger.warning(
                    "tool_call name not found body_len=%d body_preview=%r",
                    len(text_to_parse),
                    text_to_parse[:200],
                )
                return None
            name = name_match.group(1)
            logger.debug("tool_call name via regex name=%s body_len=%d", name, len(text_to_parse))
        arguments = _extract_arguments_raw(text_to_parse)
        logger.debug(
            "tool_call parsed via json name=%s arguments_len=%d arguments_preview=%r has_arguments_key=%s",
            name,
            len(arguments),
            arguments[:200],
            '"arguments"' in text_to_parse,
        )
        return ParsedToolCall(self.call_ids[index] if index < len(self.call_ids) else "", name, arguments)

    def _parse_hermes_xml(self, body: str) -> tuple[str, str] | None:
        """Parse Hermes-style XML tool call.

        Expected layout::

            <function=NAME>
            <parameter=KEY>VALUE</parameter>
            ...
            </function>

        Returns ``(name, arguments_json)`` or ``None`` when *body* does not
        match the Hermes layout.
        """
        if not body.startswith(self._hermes_func_prefix):
            return None
        tag_end = body.find(">", len(self._hermes_func_prefix))
        if tag_end < 0:
            return None
        name = body[len(self._hermes_func_prefix):tag_end].strip()
        if not name:
            return None
        func_end = body.find(self._hermes_func_close, tag_end + 1)
        if func_end < 0:
            return None
        inner = body[tag_end + 1:func_end]
        arguments: dict[str, Any] = {}
        cursor = 0
        while cursor < len(inner):
            p_start = inner.find(self._hermes_param_prefix, cursor)
            if p_start < 0:
                break
            p_tag_end = inner.find(">", p_start + len(self._hermes_param_prefix))
            if p_tag_end < 0:
                break
            param_name = inner[p_start + len(self._hermes_param_prefix):p_tag_end].strip()
            p_value_start = p_tag_end + 1
            p_end = inner.find(self._hermes_param_close, p_value_start)
            if p_end < 0:
                break
            raw_value = inner[p_value_start:p_end].strip()
            arguments[param_name] = _coerce_hermes_value(raw_value)
            cursor = p_end + len(self._hermes_param_close)
        return name, json.dumps(arguments, ensure_ascii=False)


class Gemma4ToolParser(ToolCallParser):
    """Gemma4 (AgentModel) tool-call parser.

    Handles the ``call:FUNC{ARGS}`` body format where ARGS may be:
    - Native: ``{KEY:<|"|>VAL<|"|>,KEY:BARE}`` with quote tokens
    - JSON: ``{{"KEY":"VAL"}}`` (double-braced) or ``{"KEY":"VAL"}``
    """

    def __init__(
        self,
        correct_tool_calls: bool = False,
        tool_start: str = DEFAULT_TOOL_START,
        tool_end: str = DEFAULT_TOOL_END,
        parse_tool_calls: bool = True,
        tool_call_prefix: str = "",
        tool_call_quote_token: str = "",
    ):
        super().__init__(tool_start, tool_end, parse_tool_calls, correct_tool_calls)
        self._tool_call_prefix = tool_call_prefix
        self._tool_call_quote_token = tool_call_quote_token

    def _parse_body(self, body: str, index: int) -> ParsedToolCall | None:
        stripped = body.strip()
        logger.debug(
            "tool_call parse begin body_len=%d body_preview=%r",
            len(stripped),
            stripped[:200],
        )
        if not self._tool_call_prefix or not stripped.startswith(self._tool_call_prefix):
            return None
        rest = stripped[len(self._tool_call_prefix):]
        brace = rest.find("{")
        if brace < 0:
            return None
        name = rest[:brace].strip()
        if not name:
            return None
        json_text = self._gemma4_args_to_json(rest[brace:])
        if json_text is None:
            return None
        try:
            json.loads(json_text)
        except (json.JSONDecodeError, ValueError):
            logger.debug(
                "tool_call gemma4 json parse failed name=%s json_len=%d json_preview=%r",
                name,
                len(json_text),
                json_text[:200],
            )
            return None
        logger.debug(
            "tool_call parsed via gemma4 name=%s arguments_len=%d arguments_preview=%r",
            name,
            len(json_text),
            json_text[:200],
        )
        return ParsedToolCall(self.call_ids[index] if index < len(self.call_ids) else "", name, json_text)

    def _gemma4_args_to_json(self, args_text: str) -> str | None:
        """Convert Gemma4 call args to valid JSON.

        Handles layouts:
        - ``{{...}}`` double-brace JSON: strip one brace layer, parse as JSON
        - ``{...}`` containing ``<|"|>`` quote tokens: state-machine escape
        - ``{...}`` plain JSON: pass through
        - ``{...}`` with malformed JSON (e.g. unquoted keys): apply corrector rules
        - ``{{...}`` with trailing garbage / mismatched braces: extract JSON prefix
        """
        if args_text.startswith("{{") and args_text.endswith("}}"):
            inner = args_text[1:-1]
            try:
                json.loads(inner)
                return inner
            except (json.JSONDecodeError, ValueError):
                pass
        if args_text.startswith("{") and args_text.endswith("}"):
            quote = self._tool_call_quote_token
            if quote and quote in args_text:
                return self._gemma4_native_to_json(args_text)
            try:
                json.loads(args_text)
                return args_text
            except (json.JSONDecodeError, ValueError):
                pass
            if self.corrector:
                corrected = self._apply_arg_correction_rules(args_text)
                if corrected is not None:
                    logger.debug(
                        "tool_call gemma4 args corrected original_len=%d corrected_len=%d original_preview=%r corrected_preview=%r",
                        len(args_text),
                        len(corrected),
                        args_text[:200],
                        corrected[:200],
                    )
                    return corrected
        return self._extract_json_prefix(args_text)

    def _apply_arg_correction_rules(self, args_text: str) -> str | None:
        candidate = args_text
        for rule in self.corrector.rules:
            candidate = rule(candidate)
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            return None

    def _extract_json_prefix(self, args_text: str) -> str | None:
        """Extract a valid JSON object from args_text, ignoring trailing
        garbage. Handles ``{{`` prefix (mismatched closing brace) by
        stripping one leading brace.

        Two strategies:
        1. ``raw_decode``: trailing garbage AFTER the closing ``}``
        2. ``_salvage_json_object``: trailing garbage BEFORE the closing ``}``
           (model forgot to close object before emitting extra text)
        """
        candidate = args_text[1:] if args_text.startswith("{{") else args_text
        if not candidate.startswith("{"):
            return None
        try:
            obj, _ = json.JSONDecoder().raw_decode(candidate)
            if isinstance(obj, dict):
                logger.debug(
                    "tool_call gemma4 args extracted via raw_decode args_preview=%r extracted_preview=%r",
                    args_text[:200],
                    json.dumps(obj, ensure_ascii=False)[:200],
                )
                return json.dumps(obj, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            pass
        return self._salvage_json_object(candidate)

    def _salvage_json_object(self, candidate: str) -> str | None:
        """Try to close the JSON object after each string-value end position.

        Handles cases where the model emitted trailing garbage (e.g.
        ``| head -100``) after a string value but before the closing ``}``.
        Tries closing positions from rightmost to leftmost so the maximal
        valid prefix is returned.
        """
        string_ends: list[int] = []
        in_string = False
        escaped = False
        for i, ch in enumerate(candidate):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                    string_ends.append(i + 1)
            elif ch == '"':
                in_string = True
        for end in reversed(string_ends):
            trial = candidate[:end] + "}"
            try:
                obj = json.loads(trial)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                logger.debug(
                    "tool_call gemma4 args salvaged trial_end=%d args_preview=%r salvaged_preview=%r",
                    end,
                    candidate[:200],
                    json.dumps(obj, ensure_ascii=False)[:200],
                )
                return json.dumps(obj, ensure_ascii=False)
        return None

    def _gemma4_native_to_json(self, args_text: str) -> str | None:
        """Convert ``{KEY:<|"|>VAL<|"|>,KEY:BARE}`` to JSON via state machine.

        String values delimited by ``tool_call_quote_token`` may contain
        literal quotes, newlines, commas, or colons.  Values are encoded
        with :func:`json.dumps` so all special characters (``"``, ``\\``,
        newlines, tabs, etc.) are properly escaped.  Keys are quoted;
        bare values (numbers/bools/null) pass through.
        """
        inner = args_text[1:-1]
        if not inner.strip():
            return "{}"
        quote = self._tool_call_quote_token
        parts: list[str] = ["{"]
        pos = 0
        first = True
        while pos < len(inner):
            if not first:
                if inner[pos] != ",":
                    return None
                pos += 1
                parts.append(",")
                pos = _skip_ws(inner, pos)
            first = False
            colon = inner.find(":", pos)
            if colon < 0:
                return None
            key = inner[pos:colon].strip()
            if not key:
                return None
            if not (key.startswith('"') and key.endswith('"') and len(key) >= 2):
                key = json.dumps(key)
            parts.append(key + ":")
            pos = _skip_ws(inner, colon + 1)
            if quote and inner[pos:pos + len(quote)] == quote:
                pos += len(quote)
                end = inner.find(quote, pos)
                if end < 0:
                    return None
                value = inner[pos:end]
                parts.append(json.dumps(value))
                pos = end + len(quote)
            else:
                end = inner.find(",", pos)
                if end < 0:
                    end = len(inner)
                value = inner[pos:end].strip()
                if not value:
                    return None
                parts.append(value)
                pos = end
        parts.append("}")
        return "".join(parts)


def make_parser(
    config: "ParseConfig",
    has_tools: bool,
    enable_correction: bool,
) -> ToolCallParser:
    """Create the model-specific tool-call parser for a ParseConfig."""
    if config.model_type == "gemma4":
        return Gemma4ToolParser(
            correct_tool_calls=enable_correction,
            tool_start=config.tool_start,
            tool_end=config.tool_end,
            parse_tool_calls=has_tools,
            tool_call_prefix=config.tool_call_prefix,
            tool_call_quote_token=config.tool_call_quote_token,
        )
    return QwenToolParser(
        correct_tool_calls=enable_correction,
        tool_start=config.tool_start,
        tool_end=config.tool_end,
        parse_tool_calls=has_tools,
        hermes_func_prefix=config.hermes_func_prefix,
        hermes_func_close=config.hermes_func_close,
        hermes_param_prefix=config.hermes_param_prefix,
        hermes_param_close=config.hermes_param_close,
    )


class ReasoningTransformer:
    """Split Qwen  IMD output after response conversion without delaying normal text."""

    def __init__(
        self,
        separate: bool,
        fallback_delimiter: str = "",
        reasoning_starts: tuple[str, ...] = _REASONING_START_MARKERS,
        reasoning_ends: tuple[str, ...] = _REASONING_END_MARKERS,
        skip_prefix_opens: tuple[str, ...] = _CHANNEL_NAME_SKIP_OPENS,
    ):
        self.separate = separate
        self.fallback_delimiter = fallback_delimiter
        self.state = "content"
        self.buffer = ""
        self.used_tag = False
        self.fallback_pending = bool(separate and fallback_delimiter)
        self._reasoning_starts = reasoning_starts
        self._reasoning_ends = reasoning_ends
        self._skip_prefix_opens = skip_prefix_opens

    def feed(self, text: str) -> list[OutputDelta]:
        if not self.separate:
            return [OutputDelta(content=text)] if text else []
        self.buffer += text
        if self.fallback_pending and not self.used_tag:
            if any(marker in self.buffer for marker in self._reasoning_starts):
                self.fallback_pending = False
            elif any(marker in self.buffer for marker in self._reasoning_ends):
                self.fallback_pending = False
            elif self.fallback_delimiter in self.buffer:
                reasoning, self.buffer = self.buffer.split(self.fallback_delimiter, 1)
                self.fallback_pending = False
                return [OutputDelta(reasoning_content=reasoning)] + self._drain()
            else:
                return []
        return self._drain()

    def finish(self, pending_as_reasoning: bool = False) -> list[OutputDelta]:
        if not self.separate:
            return []
        if self.fallback_pending:
            value, self.buffer = self.buffer, ""
            self.fallback_pending = False
            if not value:
                return []
            if pending_as_reasoning:
                return [OutputDelta(reasoning_content=value)]
            return [OutputDelta(content=value)]
        value, self.buffer = self.buffer, ""
        if not value:
            return []
        return [OutputDelta(**{self.state + "_content" if self.state == "reasoning" else "content": value})]

    def _drain(self) -> list[OutputDelta]:
        output: list[OutputDelta] = []
        while self.buffer:
            if self.state == "channel_name":
                newline = self.buffer.find("\n")
                if newline < 0:
                    break
                self.buffer = self.buffer[newline + 1:]
                self.state = "reasoning"
                continue
            if self.state == "reasoning":
                markers = self._reasoning_ends
            else:
                markers = self._reasoning_starts + self._reasoning_ends
            index, marker = _earliest_marker(self.buffer, markers)
            if index >= 0:
                value = self.buffer[:index]
                is_end_marker = marker in self._reasoning_ends
                if value:
                    if self.state == "content" and is_end_marker:
                        output.append(OutputDelta(reasoning_content=value))
                    else:
                        output.append(self._text_delta(value))
                self.buffer = self.buffer[index + len(marker):]
                if self.state == "reasoning":
                    self.state = "content"
                elif is_end_marker:
                    pass
                elif marker in self._skip_prefix_opens:
                    self.state = "channel_name"
                else:
                    self.state = "reasoning"
                self.used_tag = True
                continue
            safe_length = len(self.buffer) - _partial_marker_length_any(self.buffer, markers)
            if safe_length <= 0:
                break
            value, self.buffer = self.buffer[:safe_length], self.buffer[safe_length:]
            output.append(self._text_delta(value))
        return output

    def _text_delta(self, value: str) -> OutputDelta:
        return OutputDelta(reasoning_content=value) if self.state == "reasoning" else OutputDelta(content=value)


def _correct_tool_blocks(
    text: str,
    corrector: ToolCallCorrector,
    tool_start: str,
    tool_end: str,
) -> tuple[str, bool]:
    output: list[str] = []
    changed = False
    cursor = 0
    while cursor < len(text):
        start = text.find(tool_start, cursor)
        if start < 0:
            output.append(text[cursor:])
            break
        output.append(text[cursor:start])
        body_start = start + len(tool_start)
        end = text.find(tool_end, body_start)
        if end < 0:
            output.append(text[start:])
            break
        body = text[body_start:end]
        corrected, block_changed = corrector.correct_with_status(body.strip(), log=False)
        if corrected:
            output.append(f"{tool_start}\n{corrected}\n{tool_end}")
            changed = changed or block_changed
        else:
            output.append(text[start:end + len(tool_end)])
        cursor = end + len(tool_end)
    return "".join(output), changed


def _coerce_hermes_value(raw: str) -> Any:
    """Best-effort conversion of a Hermes parameter string to a JSON value."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _skip_ws(text: str, pos: int) -> int:
    """Return the first index at/after *pos* that is not whitespace."""
    while pos < len(text) and text[pos] in " \t\n\r":
        pos += 1
    return pos


def _extract_arguments_raw(text: str) -> str:
    match = re.search(r'"arguments"\s*:\s*', text)
    if not match:
        logger.debug(
            "tool_call arguments key not found in body body_len=%d body_preview=%r",
            len(text),
            text[:200],
        )
        return ""
    rest = text[match.end():]
    try:
        _, end = json.JSONDecoder().raw_decode(rest)
        extracted = rest[:end]
    except json.JSONDecodeError:
        logger.warning(
            "tool_call arguments raw_decode failed body_len=%d rest_preview=%r",
            len(text),
            rest[:200],
        )
        return rest
    if not extracted or extracted in {"{}", "[]", '""', "null"}:
        logger.warning(
            "tool_call arguments empty or trivial arguments=%r body_preview=%r",
            extracted,
            text[:200],
        )
    return extracted


def _suffix(previous: str, current: str) -> str:
    if not current.startswith(previous):
        raise RuntimeError(
            f"Model output parser produced a non-monotonic stream: "
            f"previous={previous!r}, current={current!r}"
        )
    return current[len(previous):]


def _partial_marker_length(value: str, marker: str) -> int:
    return max((size for size in range(1, min(len(value), len(marker) - 1) + 1) if value.endswith(marker[:size])), default=0)


def _partial_marker_length_any(value: str, markers) -> int:
    """Largest partial-match suffix of *value* for any marker in *markers*.

    Keeps a prefix of a marker buffered (instead of emitting it as content)
    so a marker that arrives split across two feed() calls is still recognized.
    """
    return max((_partial_marker_length(value, marker) for marker in markers), default=0)


def _earliest_marker(value: str, markers) -> tuple[int, str]:
    """Return (index, marker) of the earliest marker found in *value*.

    When no marker is present, returns ``(-1, "")``.
    """
    best_index = -1
    best_marker = ""
    for marker in markers:
        index = value.find(marker)
        if index < 0:
            continue
        if best_index < 0 or index < best_index:
            best_index = index
            best_marker = marker
    return best_index, best_marker


def _without_partial_marker(value: str, marker: str) -> str:
    size = _partial_marker_length(value, marker)
    return value[:-size] if size else value
