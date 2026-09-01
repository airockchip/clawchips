from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import re
from typing import Any

from jinja2 import Environment, Undefined

from .config import Settings
from .parsing import (
    DEFAULT_TOOL_END,
    DEFAULT_TOOL_START,
    QWEN3_PROFILE,
    build_parse_config,
)
from .schemas import GenerationRequest

logger = logging.getLogger("gateway")


CHATML_TEMPLATE = """{%- for message in messages -%}
{{- '<|im_start|>' + message['role'] + '\n' + (message['content'] or '') + '<|im_end|>\n' -}}
{%- endfor -%}
{%- if add_generation_prompt -%}{{- '<|im_start|>assistant\n' -}}{%- endif -%}"""


@dataclass(frozen=True)
class TemplateResult:
    prompt: str
    source: str
    variant: str
    system_prompt: str


def load_tokenizer(settings: Settings):
    from .native_tokenizer import GGUFTokenizer

    return GGUFTokenizer(
        settings.tokenizer_path,
        settings.tokenizer_library,
        settings.max_context_tokens,
    )


class ChatTemplateEngine:
    """llama.cpp-compatible template selection with local Jinja rendering."""

    def __init__(self, tokenizer: Any, settings: Settings):
        self.tokenizer = tokenizer
        self.settings = settings
        self.environment = Environment(undefined=Undefined, autoescape=False, keep_trailing_newline=True)
        self.environment.filters["tojson"] = lambda value: json.dumps(value, ensure_ascii=False)
        self.environment.filters["items"] = _lenient_items
        self.environment.globals["raise_exception"] = self._raise_exception
        self.environment.globals["strftime_now"] = lambda fmt: datetime.now().strftime(fmt)
        self.tool_start, self.tool_end = self._init_tool_markers()
        # Default to Qwen3 profile; service.py overrides with the actual
        # model's profile via set_parse_config() after backend.start().
        self.set_parse_config(
            build_parse_config(QWEN3_PROFILE, tokenizer, self.tool_start, self.tool_end)
        )

    def set_parse_config(self, config) -> None:
        """Inject model-aware parsing config.

        Drives tool-call markers, system block delimiter, assistant
        marker, empty thinking template, tool response tags, tool format
        rewriting, and Hermes XML markers -- all resolved from the model
        profile.
        """
        if config.tool_start:
            self.tool_start = config.tool_start
        if config.tool_end:
            self.tool_end = config.tool_end
        self._system_block_delimiter = config.system_block_delimiter or ""
        self._block_end_delimiter = config.block_end_delimiter or ""
        self._assistant_marker = config.assistant_marker or ""
        self._empty_thinking_template = config.empty_thinking_template or ""
        self._tool_response_open = config.tool_response_open or ""
        self._tool_response_close = config.tool_response_close or ""
        self._rewrite_tool_format = config.rewrite_tool_format
        self._json_format_anchor = config.json_format_anchor
        self._rewrite_anchor = config.rewrite_anchor
        self._hermes_func_prefix = config.hermes_func_prefix
        self._hermes_func_close = config.hermes_func_close
        self._hermes_param_prefix = config.hermes_param_prefix
        self._hermes_param_close = config.hermes_param_close

    def _init_tool_markers(self) -> tuple[str, str]:
        """Extract tool-call markers from available template sources."""
        sources: list[str] = []
        templates = getattr(self.tokenizer, "chat_templates", None)
        if isinstance(templates, dict):
            for key in ("tool_use", "default"):
                value = templates.get(key)
                if isinstance(value, str):
                    sources.append(value)
        template = getattr(self.tokenizer, "chat_template", None)
        if isinstance(template, str) and template:
            sources.append(template)
        elif isinstance(template, dict):
            for key in ("tool_use", "default"):
                value = template.get(key)
                if isinstance(value, str):
                    sources.append(value)
        for source in sources:
            start, end = self._extract_tool_markers(source)
            if start and end:
                logger.info("Tool-call markers extracted: start=%r end=%r", start, end)
                return start, end
        logger.info("Tool-call markers not found in template, using defaults")
        return DEFAULT_TOOL_START, DEFAULT_TOOL_END

    @staticmethod
    def _extract_tool_markers(source: str) -> tuple[str, str]:
        """Extract opening/closing tool-call tags from a Jinja2 template source.

        Two formats are supported:
        - Qwen-style: ``'<tag>\\n{' ... '}\\n</tag>'``
        - AgentModel-style: ``'<|tag>call:' ... '}<tag|>'`` (Gemma bracket
          convention with the pipe on the outside)

        Returns empty strings when neither pattern is found.
        """
        start = ""
        end = ""
        m = re.search(r"""['\"](<[\w]+>)\\n\{""", source)
        if m:
            start = m.group(1)
        m = re.search(r"""\}\\n(</[\w]+>)['\"]""", source)
        if m:
            end = m.group(1)
        if not start:
            m = re.search(r"""['\"](<\|[\w]+>)call:['\"]""", source)
            if m:
                start = m.group(1)
        if not end:
            m = re.search(r"""\}(<[\w]+\|>)['\"]""", source)
            if m:
                end = m.group(1)
        return start, end

    def render(self, request: GenerationRequest) -> TemplateResult:
        source, variant = self._select(bool(request.tools))
        messages = [message.to_template_dict() for message in request.messages]
        context = {
            "messages": messages,
            "tools": request.tools,
            "add_generation_prompt": True,
            "enable_thinking": request.enable_thinking,
            "parallel_tool_calls": request.parallel_tool_calls,
            "tool_choice": request.tool_choice,
            "bos_token": getattr(self.tokenizer, "bos_token", ""),
            "eos_token": getattr(self.tokenizer, "eos_token", ""),
        }
        try:
            prompt = self.environment.from_string(source).render(**context)
        except Exception as exc:
            logger.error(
                "Chat template render failed variant=%s source_len=%d error=%s\nsource:\n%s",
                variant,
                len(source),
                exc,
                source[:2000],
            )
            raise ValueError(f"Failed to apply chat template: {exc}") from exc
        prompt = self._post_process_prompt(prompt, source, request)
        system_prompt = self._extract_system_prompt(source, prompt)
        return TemplateResult(prompt=prompt, source=source, variant=variant, system_prompt=system_prompt)

    def _post_process_prompt(self, prompt: str, source: str, request: GenerationRequest) -> str:
        """Apply fixes for templates that lack enable_thinking / tool_call_id support."""
        if "enable_thinking" not in source and not request.enable_thinking:
            prompt = self._append_empty_thinking(prompt)
        if request.tools and "tool_call_id" not in source:
            prompt = self._inject_tool_call_ids(prompt, request)
        if request.tools and self._should_rewrite_tool_format(source):
            prompt = self._rewrite_tool_call_format(prompt)
        return prompt

    def _should_rewrite_tool_format(self, source: str) -> bool:
        """Return True only when rewriting Qwen format to Hermes is safe.

        Disabled entirely when the profile sets ``rewrite_tool_format=False``.
        When enabled, skipped for templates that already describe JSON output
        (detected via the profile's ``json_format_anchor``).
        """
        if not self._rewrite_tool_format:
            return False
        return self._json_format_anchor not in source

    def _append_empty_thinking(self, prompt: str) -> str:
        """Append empty thinking tags so the model skips the reasoning phase."""
        if not self._assistant_marker or not self._empty_thinking_template:
            return prompt
        if not prompt.endswith(self._assistant_marker):
            return prompt
        return prompt + self._empty_thinking_template

    def _inject_tool_call_ids(self, prompt: str, request: GenerationRequest) -> str:
        """Insert tool_call_id lines into each tool_response block in the rendered prompt."""
        if not self._tool_response_open:
            return prompt
        tool_messages = [m for m in request.messages if m.role == "tool" and m.tool_call_id]
        if not tool_messages:
            return prompt
        response_open = self._tool_response_open
        response_close = self._tool_response_close
        cursor = 0
        for message in tool_messages:
            start = prompt.find(response_open, cursor)
            if start < 0:
                break
            content_start = start + len(response_open)
            end = prompt.find(response_close, content_start)
            if end < 0:
                break
            insertion = f"tool_call_id: {message.tool_call_id}\n"
            prompt = prompt[:content_start] + insertion + prompt[content_start:]
            cursor = end + len(insertion) + len(response_close)
        return prompt

    def _rewrite_tool_call_format(self, prompt: str) -> str:
        """Replace the Qwen tool_call format description with Hermes instructions.

        Scoped to the first system block (up to the first block-end delimiter)
        so user/assistant content containing the anchor phrase is never touched.
        """
        if not self._block_end_delimiter:
            return prompt
        block_end = prompt.find(self._block_end_delimiter)
        if block_end < 0:
            return prompt
        block = prompt[:block_end]
        start = block.find(self._rewrite_anchor)
        if start < 0:
            return prompt
        hermes_instructions = (
            self._rewrite_anchor + ", use Hermes XML format:\n"
            + self._hermes_func_prefix + "name>\n"
            + self._hermes_param_prefix + "key>value" + self._hermes_param_close + "\n"
            + self._hermes_func_close
        )
        return prompt[:start] + hermes_instructions + prompt[block_end:]

    @staticmethod
    def _find_block_delimiter(source: str) -> str:
        """Find the block-start delimiter (e.g. ``<|im_start|>``) from the template source.

        Searches for a string literal concatenated with ``message['role']``
        (or ``message.role``) – the token the template emits before every
        message block.  Returns an empty string when the pattern is absent.
        """
        for pattern in (
            r"""['"]([^'"]+)['"]\s*[+~]\s*message\[['"]role['"]\]""",
            r"""['"]([^'"]+)['"]\s*[+~]\s*message\.role""",
        ):
            match = re.search(pattern, source)
            if match:
                return match.group(1)
        return ""

    def _extract_system_prompt(self, source: str, full_prompt: str) -> str:
        """Extract the system block from the fully rendered prompt.

        Derives the block-start delimiter from the template source (no
        hardcoded marker), then returns everything from the beginning of
        the rendered prompt up to – but not including – the *second*
        occurrence of that delimiter, i.e. the first block which is the
        system block.  Tool-call descriptions injected into the system
        block by the template are naturally included.

        When :attr:`_system_block_delimiter` is set by the model profile,
        that delimiter is used directly instead of deriving it from the
        Jinja source.
        """
        delimiter = self._system_block_delimiter or self._find_block_delimiter(source)
        if not delimiter:
            return ""
        first = full_prompt.find(delimiter)
        if first == -1:
            return ""
        second = full_prompt.find(delimiter, first + len(delimiter))
        if second == -1:
            return full_prompt
        return full_prompt[:second]

    def validate(self) -> None:
        from .schemas import ChatMessage, GenerationRequest

        request = GenerationRequest(
            model=self.settings.model_id,
            messages=[ChatMessage("user", "test")],
            tools=[{
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "echo",
                    "parameters": {
                        "type": "object",
                        "properties": {"msg": {"type": "string", "description": "message"}},
                        "required": ["msg"],
                    },
                },
            }],
            tool_choice="auto",
            parallel_tool_calls=True,
            stream=False,
            include_usage=False,
            max_new_tokens=1,
            temperature=self.settings.temperature,
            top_p=self.settings.top_p,
            top_k=self.settings.top_k,
            repeat_penalty=self.settings.repeat_penalty,
            stop=[],
            enable_thinking=False,
            frequency_penalty=self.settings.frequency_penalty,
            presence_penalty=self.settings.presence_penalty,
        )
        result = self.render(request)
        if self._assistant_marker and self._assistant_marker not in result.prompt:
            raise RuntimeError(
                "Chat template is not compatible: assistant generation marker is missing"
            )
        if result.variant != "chatml" and self.tool_start not in result.source:
            raise RuntimeError("Chat template does not describe tool_call output")

    def _select(self, has_tools: bool) -> tuple[str, str]:
        templates = getattr(self.tokenizer, "chat_templates", None)
        if isinstance(templates, dict):
            if has_tools and isinstance(templates.get("tool_use"), str):
                return templates["tool_use"], "tool_use"
            if isinstance(templates.get("default"), str):
                return templates["default"], "default"
        template = getattr(self.tokenizer, "chat_template", None)
        if isinstance(template, dict):
            if has_tools and isinstance(template.get("tool_use"), str):
                return template["tool_use"], "tool_use"
            if isinstance(template.get("default"), str):
                return template["default"], "default"
            if template:
                value = next((value for value in template.values() if isinstance(value, str)), None)
                if value:
                    return value, "default"
        elif isinstance(template, str) and template:
            return template, "default"
        return CHATML_TEMPLATE, "chatml"

    @staticmethod
    def _raise_exception(message: str) -> None:
        raise ValueError(message)


def _lenient_items(value: Any):
    """Jinja2 ``|items`` filter that tolerates non-mapping values.

    The standard filter raises ``TypeError`` when *value* is not a mapping.
    Some chat templates call ``|items`` on optional fields that may be
    strings, lists, or ``None``; returning an empty iterator for those
    cases keeps rendering working instead of failing the whole request.

    OpenAI tool-call ``arguments`` arrives as a JSON string. Templates that
    iterate ``arguments|items`` (e.g. CoPaw) need it as a dict; templates
    that emit the string directly (e.g. AgentModel) never invoke ``|items``
    and receive the original string. Parsing here keeps the on-the-wire
    data shape unchanged while letting ``|items``-based templates work.
    """
    if isinstance(value, Undefined):
        return
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return
    if not isinstance(value, dict):
        return
    yield from value.items()
