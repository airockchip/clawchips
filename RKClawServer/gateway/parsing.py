"""Model-aware parsing configuration.

Each model type declares which special tokens it needs via a
:class:`ModelProfile`. :func:`build_parse_config` resolves those tokens
from the tokenizer (falling back to hardcoded strings when the GGUF
lacks named metadata) and produces a :class:`ParseConfig` that drives
tool-call, reasoning, and system-prompt parsing.

Tool-call markers are still extracted from the chat template by
:class:`gateway.template.ChatTemplateEngine`; the profile can override
them via ``tool_start``/``tool_end`` when a model uses markers that
the template regex cannot find.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("gateway")


# Default markers shared by the parser, template engine, and profiles.
# Declared here so model-specific values live in one place -- callers
# (QwenToolParser, ChatTemplateEngine) import these as constructor
# fallback defaults, overridden at runtime by ParseConfig.
DEFAULT_TOOL_START = "<" + "tool_call" + ">"
DEFAULT_TOOL_END = "<" + "/tool_call" + ">"
DEFAULT_HERMES_FUNC_PREFIX = "<function="
DEFAULT_HERMES_FUNC_CLOSE = "<" + "/function" + ">"
DEFAULT_HERMES_PARAM_PREFIX = "<parameter="
DEFAULT_HERMES_PARAM_CLOSE = "<" + "/parameter" + ">"


@dataclass(frozen=True)
class TokenSpec:
    """A special token the parser needs, resolved from the tokenizer.

    ``name`` is the logical key (e.g. ``"sot_token"``). At build time
    :meth:`GGUFTokenizer.get_special_token` is called with this name;
    when the GGUF exposes no such metadata the ``fallback`` string is
    used instead.
    """

    name: str
    fallback: str
    metadata_key: str = ""


@dataclass(frozen=True)
class ModelProfile:
    """Declarative per-model parsing configuration."""

    model_type: str
    tokens: tuple[TokenSpec, ...]
    reasoning_start_names: tuple[str, ...]
    reasoning_end_names: tuple[str, ...]
    skip_prefix_names: tuple[str, ...] = ()
    system_block_name: str = ""
    tool_start: str = ""
    tool_end: str = ""
    tool_call_prefix: str = ""
    tool_call_quote_token: str = ""
    block_end_name: str = ""
    assistant_marker: str = ""
    empty_thinking_template: str = ""
    tool_response_open: str = ""
    tool_response_close: str = ""
    rewrite_tool_format: bool = True
    json_format_anchor: str = "return a json object"
    rewrite_anchor: str = "For each function call"
    hermes_func_prefix: str = DEFAULT_HERMES_FUNC_PREFIX
    hermes_func_close: str = DEFAULT_HERMES_FUNC_CLOSE
    hermes_param_prefix: str = DEFAULT_HERMES_PARAM_PREFIX
    hermes_param_close: str = DEFAULT_HERMES_PARAM_CLOSE
    logits_name: str = "output"


@dataclass(frozen=True)
class ParseConfig:
    """Resolved parsing configuration with actual token strings."""

    model_type: str
    tool_start: str
    tool_end: str
    reasoning_starts: tuple[str, ...]
    reasoning_ends: tuple[str, ...]
    skip_prefix_opens: tuple[str, ...]
    system_block_delimiter: str
    tool_call_prefix: str = ""
    tool_call_quote_token: str = ""
    block_end_delimiter: str = ""
    assistant_marker: str = ""
    empty_thinking_template: str = ""
    tool_response_open: str = ""
    tool_response_close: str = ""
    rewrite_tool_format: bool = True
    json_format_anchor: str = "return a json object"
    rewrite_anchor: str = "For each function call"
    hermes_func_prefix: str = DEFAULT_HERMES_FUNC_PREFIX
    hermes_func_close: str = DEFAULT_HERMES_FUNC_CLOSE
    hermes_param_prefix: str = DEFAULT_HERMES_PARAM_PREFIX
    hermes_param_close: str = DEFAULT_HERMES_PARAM_CLOSE


# --------------------------------------------------------------------------
# Built-in profiles
# --------------------------------------------------------------------------

QWEN3_PROFILE = ModelProfile(
    model_type="qwen3",
    tokens=(
        TokenSpec("im_start_token", "<|im_start|>"),
        TokenSpec("eos_token", "<|im_end|>"),
        TokenSpec("think_start", "<" + "think" + ">"),
        TokenSpec("think_end", "<" + "/think" + ">"),
    ),
    reasoning_start_names=("think_start",),
    reasoning_end_names=("think_end",),
    system_block_name="im_start_token",
    block_end_name="eos_token",
    tool_start="<" + "tool_call" + ">",
    tool_end="<" + "/tool_call" + ">",
    assistant_marker="<|im_start|>" + "assistant\n",
    empty_thinking_template="<" + "think" + ">\n\n<" + "/think" + ">\n\n",
    tool_response_open="<" + "tool_response" + ">\n",
    tool_response_close="\n<" + "/tool_response" + ">",
    rewrite_tool_format=True,
    logits_name="output",
)

# Qwen3.5 shares token layout, reasoning format, and block markers with
# Qwen3, but uses Hermes XML tool calls natively -- the Qwen tool
# description in the system prompt is rewritten to Hermes instructions
# so the model emits <function=...> blocks instead of the Qwen JSON block.
QWEN35_PROFILE = ModelProfile(
    model_type="qwen3_5",
    tokens=(
        TokenSpec("im_start_token", "<|im_start|>"),
        TokenSpec("eos_token", "<|im_end|>"),
        TokenSpec("think_start", "<" + "think" + ">"),
        TokenSpec("think_end", "<" + "/think" + ">"),
    ),
    reasoning_start_names=("think_start",),
    reasoning_end_names=("think_end",),
    system_block_name="im_start_token",
    block_end_name="eos_token",
    tool_start="<" + "tool_call" + ">",
    tool_end="<" + "/tool_call" + ">",
    assistant_marker="<|im_start|>" + "assistant\n",
    empty_thinking_template="<" + "think" + ">\n\n<" + "/think" + ">\n\n",
    tool_response_open="<" + "tool_response" + ">\n",
    tool_response_close="\n<" + "/tool_response" + ">",
    rewrite_tool_format=True,
    logits_name="output",
)

GEMMA4_PROFILE = ModelProfile(
    model_type="gemma4",
    tokens=(
        TokenSpec("sot_token", "<|turn>"),
        TokenSpec("eot_token", "<turn|>"),
        TokenSpec("soc_token", "<|channel>"),
        TokenSpec("eoc_token", "<channel|>"),
    ),
    reasoning_start_names=("soc_token",),
    reasoning_end_names=("eoc_token",),
    skip_prefix_names=("soc_token",),
    system_block_name="sot_token",
    block_end_name="eot_token",
    tool_start="<|" + "tool_call" + ">",
    tool_end="<" + "tool_call" + "|>",
    tool_call_prefix="call:",
    tool_call_quote_token="<|" + '"' + "|>",
    assistant_marker="<|turn>" + "model\n",
    empty_thinking_template="<|channel>" + "thought\n\n" + "<channel|>" + "\n\n",
    tool_response_open="",
    tool_response_close="",
    rewrite_tool_format=False,
    logits_name="logits_gathered",
)

_PROFILES: dict[str, ModelProfile] = {
    "qwen3": QWEN3_PROFILE,
    "qwen3_5": QWEN35_PROFILE,
    "qwen35": QWEN35_PROFILE,  # alias: some tooling emits the underscore-less form
    "gemma4": GEMMA4_PROFILE,
}


def get_profile(model_type: str) -> ModelProfile:
    """Return the profile for *model_type*, falling back to Qwen3."""
    key = (model_type or "").lower()
    profile = _PROFILES.get(key)
    if profile is not None:
        return profile
    logger.info("Unknown model_type=%r, falling back to qwen3 profile", model_type)
    return QWEN3_PROFILE


def _resolve_token(spec: TokenSpec, tokenizer: Any) -> str:
    """Resolve a TokenSpec to its string via the tokenizer, else fallback."""
    if spec.metadata_key:
        value = _safe_get_metadata(tokenizer, spec.metadata_key)
        if value:
            return value
    getter = getattr(tokenizer, "get_special_token", None)
    if callable(getter):
        try:
            value = getter(spec.name)
        except Exception:
            logger.exception("get_special_token failed for %s", spec.name)
            value = ""
        if value:
            return value
    return spec.fallback


def _safe_get_metadata(tokenizer: Any, key: str) -> str:
    getter = getattr(tokenizer, "get_metadata", None)
    if not callable(getter):
        return ""
    try:
        return getter(key) or ""
    except Exception:
        return ""


def build_parse_config(
    profile: ModelProfile,
    tokenizer: Any,
    tool_start: str,
    tool_end: str,
) -> ParseConfig:
    """Resolve tokens and build a :class:`ParseConfig`.

    ``tool_start``/``tool_end`` are the template-extracted markers passed
    in by the caller; the profile overrides them only when it declares
    non-empty values.
    """
    resolved: dict[str, str] = {}
    for spec in profile.tokens:
        resolved[spec.name] = _resolve_token(spec, tokenizer)

    reasoning_starts = tuple(
        resolved[name] for name in profile.reasoning_start_names if resolved.get(name)
    )
    reasoning_ends = tuple(
        resolved[name] for name in profile.reasoning_end_names if resolved.get(name)
    )
    skip_prefix_opens = tuple(
        resolved[name] for name in profile.skip_prefix_names if resolved.get(name)
    )
    system_block_delimiter = (
        resolved.get(profile.system_block_name, "") if profile.system_block_name else ""
    )
    block_end_delimiter = (
        resolved.get(profile.block_end_name, "") if profile.block_end_name else ""
    )

    final_tool_start = profile.tool_start or tool_start
    final_tool_end = profile.tool_end or tool_end

    logger.info(
        "ParseConfig built model_type=%s reasoning_starts=%r reasoning_ends=%r "
        "skip_prefix_opens=%r system_block_delimiter=%r block_end_delimiter=%r",
        profile.model_type,
        reasoning_starts,
        reasoning_ends,
        skip_prefix_opens,
        system_block_delimiter,
        block_end_delimiter,
    )

    return ParseConfig(
        model_type=profile.model_type,
        tool_start=final_tool_start,
        tool_end=final_tool_end,
        tool_call_prefix=profile.tool_call_prefix,
        tool_call_quote_token=profile.tool_call_quote_token,
        reasoning_starts=reasoning_starts,
        reasoning_ends=reasoning_ends,
        skip_prefix_opens=skip_prefix_opens,
        system_block_delimiter=system_block_delimiter,
        block_end_delimiter=block_end_delimiter,
        assistant_marker=profile.assistant_marker,
        empty_thinking_template=profile.empty_thinking_template,
        tool_response_open=profile.tool_response_open,
        tool_response_close=profile.tool_response_close,
        rewrite_tool_format=profile.rewrite_tool_format,
        json_format_anchor=profile.json_format_anchor,
        rewrite_anchor=profile.rewrite_anchor,
        hermes_func_prefix=profile.hermes_func_prefix,
        hermes_func_close=profile.hermes_func_close,
        hermes_param_prefix=profile.hermes_param_prefix,
        hermes_param_close=profile.hermes_param_close,
    )
