from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from typing import Any


CorrectionRule = Callable[[str], str]
logger = logging.getLogger("gateway")


class ToolCallCorrector:
    """Best-effort repair pipeline for model-generated tool-call JSON.

    Text rules are deliberately small and ordered. New repair rules can be added
    by passing them to the constructor without changing the output parser.
    """

    def __init__(self, rules: Iterable[CorrectionRule] | None = None):
        self.rules = tuple(rules or DEFAULT_RULES)

    def correct(self, body: str) -> str | None:
        corrected, _ = self.correct_with_status(body)
        return corrected

    def correct_with_status(self, body: str, log: bool = True) -> tuple[str | None, bool]:
        original = body.strip()
        original_valid, original_value = _load_tool_call(original)
        if original_valid and original_value is not None:
            return _canonical_tool_call(original_value, original, log=log)

        candidate = original
        for rule in self.rules:
            candidate = rule(candidate)
        corrected_valid, value = _load_tool_call(candidate)
        if not corrected_valid or value is None:
            if original_valid and original_value is not None:
                return _canonical_tool_call(original_value, original, log=log)
            if original and log:
                logger.warning(
                    "tool_call correction failed original_len=%d corrected_len=%d",
                    len(original),
                    len(candidate),
                )
                logger.debug("tool_call correction failed original=%r corrected=%r", original, candidate)
            return None, False
        added_arguments = "arguments" not in value
        value = _ensure_arguments(value)
        corrected = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        canonical_original = (
            json.dumps(_ensure_arguments(original_value), ensure_ascii=False, separators=(",", ":"))
            if original_valid and original_value is not None
            else None
        )
        changed = corrected != canonical_original
        if changed and log:
            logger.warning(
                "tool_call correction applied function=%s original_len=%d corrected_len=%d added_arguments=%s",
                value["name"],
                len(original),
                len(corrected),
                added_arguments,
            )
            logger.debug("tool_call correction original=%r corrected=%r", original, corrected)
        elif added_arguments:
            logger.warning(
                "tool_call missing arguments key function=%s original_len=%d original_preview=%r",
                value["name"],
                len(original),
                original[:200],
            )
        return corrected, changed


def _load_tool_call(text: str) -> tuple[bool, dict[str, Any] | None]:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False, None
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        return False, None
    return True, value


def trim_escaped_newlines(text: str) -> str:
    """Remove literal ``\\n`` emitted immediately around the JSON object."""
    text = re.sub(r"^(?:\s|\\[rn])*(?=\{)", "", text)
    return re.sub(r"(?<=\})(?:\s|\\[rn])*$", "", text)


def escape_control_chars_in_strings(text: str) -> str:
    """Escape raw control characters emitted inside JSON string values."""
    output: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                output.append(char)
                escaped = False
                continue
            if char == "\\":
                output.append(char)
                escaped = True
                continue
            if char == '"':
                output.append(char)
                in_string = False
                continue
            if char == "\n":
                output.append("\\n")
                continue
            if char == "\r":
                output.append("\\r")
                continue
            if char == "\t":
                output.append("\\t")
                continue
            if ord(char) < 0x20:
                output.append(json.dumps(char)[1:-1])
                continue
            output.append(char)
            continue

        output.append(char)
        if char == '"':
            in_string = True
    return "".join(output)


def insert_missing_commas_between_members(text: str) -> str:
    """Insert a comma between adjacent object members outside strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        output.append(char)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                next_index = _next_nonspace(text, index + 1)
                if _looks_like_member_key(text, next_index):
                    output.append(",")
            index += 1
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char in "}]" or char in "0123456789" or char in "eElrs":
            next_index = _next_nonspace(text, index + 1)
            if _looks_like_member_key(text, next_index) and _looks_like_value_end(text, index):
                output.append(",")
        index += 1
    return "".join(output)


def quote_unquoted_keys(text: str) -> str:
    """Quote unquoted object keys outside JSON strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        output.append(char)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char not in "{,":
            index += 1
            continue

        key_start = index + 1
        while key_start < len(text) and text[key_start].isspace():
            key_start += 1
        if key_start >= len(text) or not re.match(r"[A-Za-z_]", text[key_start]):
            index += 1
            continue

        key_end = key_start + 1
        while key_end < len(text) and re.match(r"[A-Za-z0-9_]", text[key_end]):
            key_end += 1
        separator = key_end
        while separator < len(text) and text[separator].isspace():
            separator += 1
        if separator >= len(text) or text[separator] != ":":
            index += 1
            continue

        output.append(text[index + 1:key_start])
        output.append(json.dumps(text[key_start:key_end], ensure_ascii=False))
        output.append(text[key_end:separator])
        index = separator
    return "".join(output)


def normalize_object_key_separators(text: str) -> str:
    """Convert object-member ``key=value``/``key.value`` to JSON-style ``"key":value``."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        output.append(char)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char not in "{,":
            index += 1
            continue

        key_start = index + 1
        while key_start < len(text) and text[key_start].isspace():
            key_start += 1
        if key_start >= len(text):
            index += 1
            continue
        if text[key_start] == '"':
            key_end = _quoted_token_end(text, key_start)
            if key_end is None:
                index += 1
                continue
            key_prefix = text[index + 1:key_end]
        elif re.match(r"[A-Za-z_]", text[key_start]):
            key_end = key_start + 1
            while key_end < len(text) and re.match(r"[A-Za-z0-9_]", text[key_end]):
                key_end += 1
            key_prefix = text[index + 1:key_start] + json.dumps(text[key_start:key_end], ensure_ascii=False)
        else:
            index += 1
            continue
        separator = key_end
        while separator < len(text) and text[separator].isspace():
            separator += 1
        if separator >= len(text) or text[separator] not in "=.":
            index += 1
            continue

        output.append(key_prefix)
        output.append(text[key_end:separator])
        output.append(":")
        index = separator + 1
    return "".join(output)


def remove_trailing_commas(text: str) -> str:
    """Remove commas immediately before closing object/array delimiters outside strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == "," and _next_nonspace(text, index + 1) < len(text) and text[_next_nonspace(text, index + 1)] in "}]":
            index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def quote_unquoted_string_values(text: str) -> str:
    """Quote simple bare string values (not JSON literals, numbers, objects or arrays)."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        char = text[index]
        output.append(char)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char != ":":
            index += 1
            continue

        start = index + 1
        while start < len(text) and text[start].isspace():
            output.append(text[start])
            start += 1
        if start >= len(text) or text[start] in '"{[':
            index = start
            continue
        end = start
        while end < len(text) and text[end] not in ",}\r\n":
            end += 1
        value = text[start:end].strip()
        if value in {"true", "false", "null"} or re.fullmatch(
            r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", value
        ):
            index = start
            continue
        output.append(json.dumps(value, ensure_ascii=False))
        index = end
    return "".join(output)


def _next_nonspace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _quoted_token_end(text: str, start: int) -> int | None:
    index = start + 1
    escaped = False
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return index + 1
        index += 1
    return None


def _looks_like_member_key(text: str, index: int) -> bool:
    if index >= len(text):
        return False
    if text[index] == '"':
        key_end = _quoted_token_end(text, index)
        if key_end is None:
            return False
        separator = _next_nonspace(text, key_end)
        return separator < len(text) and text[separator] in ":=."
    if not re.match(r"[A-Za-z_]", text[index]):
        return False
    end = index + 1
    while end < len(text) and re.match(r"[A-Za-z0-9_]", text[end]):
        end += 1
    separator = _next_nonspace(text, end)
    return separator < len(text) and text[separator] in ":=."


def _looks_like_value_end(text: str, index: int) -> bool:
    if text[index] in "}]0123456789":
        return True
    return any(text.startswith(literal, index - len(literal) + 1) for literal in ("true", "false", "null"))


def _ensure_arguments(value: dict[str, Any]) -> dict[str, Any]:
    if "arguments" in value:
        return value
    return {
        "name": value["name"],
        "arguments": {key: item for key, item in value.items() if key != "name"},
    }


def _canonical_tool_call(value: dict[str, Any], original: str, log: bool = True) -> tuple[str, bool]:
    added_arguments = "arguments" not in value
    canonical_original = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    value = _ensure_arguments(value)
    corrected = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    changed = corrected != canonical_original
    if changed and log:
        logger.warning(
            "tool_call correction applied function=%s original_len=%d corrected_len=%d added_arguments=%s",
            value["name"],
            len(original),
            len(corrected),
            added_arguments,
        )
        logger.debug("tool_call correction original=%r corrected=%r", original, corrected)
    return corrected, changed


DEFAULT_RULES: tuple[CorrectionRule, ...] = (
    trim_escaped_newlines,
    escape_control_chars_in_strings,
    insert_missing_commas_between_members,
    normalize_object_key_separators,
    quote_unquoted_keys,
    quote_unquoted_string_values,
    remove_trailing_commas,
)
