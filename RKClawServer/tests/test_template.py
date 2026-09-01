from __future__ import annotations

from gateway.config import Settings
from gateway.schemas import ChatMessage, GenerationRequest
from gateway.template import CHATML_TEMPLATE, ChatTemplateEngine


DEFAULT = "default {{ messages[0].content }} <|im_start|>assistant {# <tool_call> #} enable_thinking"
TOOLS = "tools {{ tools | tojson }} <|im_start|>assistant {# <tool_call> #} enable_thinking"


class Tokenizer:
    def __init__(self, template=None, templates=None):
        self.chat_template = template
        self.chat_templates = templates or {}
        self.bos_token = "<bos>"
        self.eos_token = "<eos>"


def request(tools=None, thinking=True):
    return GenerationRequest(
        model="Qwen3-4B-Instruct",
        messages=[ChatMessage("user", "test")],
        tools=tools or [],
        tool_choice="auto",
        parallel_tool_calls=True,
        stream=False,
        include_usage=False,
        max_new_tokens=8,
        temperature=0.7,
        top_p=0.9,
        top_k=1,
        repeat_penalty=1.0,
        stop=[],
        enable_thinking=thinking,
    )


def test_tool_use_template_has_priority() -> None:
    tokenizer = Tokenizer(templates={"default": DEFAULT, "tool_use": TOOLS})
    engine = ChatTemplateEngine(tokenizer, Settings())
    tool = {"type": "function", "function": {"name": "echo", "parameters": {}}}
    result = engine.render(request([tool], thinking=False))
    assert result.variant == "tool_use"
    assert result.prompt.startswith("tools")
    assert '"name": "echo"' in result.prompt


def test_default_and_chatml_fallback_selection() -> None:
    tokenizer = Tokenizer(templates={"default": DEFAULT, "tool_use": TOOLS})
    result = ChatTemplateEngine(tokenizer, Settings()).render(request())
    assert result.variant == "default"
    assert result.prompt.startswith("default test")
    result = ChatTemplateEngine(Tokenizer(), Settings()).render(request())
    assert result.variant == "chatml"
    assert result.source == CHATML_TEMPLATE
    assert result.prompt.endswith("<|im_start|>assistant\n")


FORMAT_AGNOSTIC_TOOL = (
    "<|im_start|>system\n"
    "For each function call, follow the format.<|im_end|>\n"
    "{{ messages[0].content }}<|im_end|>\n"
    "<|im_start|>assistant"
)


QWEN_JSON_TOOL = (
    "<|im_start|>system\n"
    "For each function call, return a json object within "
    "tool_call tags.<|im_end|>\n"
    "{{ messages[0].content }}<|im_end|>\n"
    "<|im_start|>assistant"
)


def _tool_request(user_content, tools):
    return GenerationRequest(
        model="Qwen3-4B-Instruct",
        messages=[ChatMessage("user", user_content)],
        tools=tools,
        tool_choice="auto",
        parallel_tool_calls=True,
        stream=False,
        include_usage=False,
        max_new_tokens=8,
        temperature=0.7,
        top_p=0.9,
        top_k=1,
        repeat_penalty=1.0,
        stop=[],
        enable_thinking=True,
    )


def test_rewrite_skipped_for_qwen_json_format_template() -> None:
    """Templates using Qwen <tool_call> JSON format must not be rewritten to
    Hermes -- the model is trained on Qwen format and rewriting confuses it."""
    tokenizer = Tokenizer(template=QWEN_JSON_TOOL)
    engine = ChatTemplateEngine(tokenizer, Settings())
    tool = {"type": "function", "function": {"name": "echo", "parameters": {}}}
    result = engine.render(_tool_request("hello", [tool]))
    assert "return a json object" in result.prompt
    assert "use Hermes XML format" not in result.prompt


def test_rewrite_tool_call_format_scoped_to_first_system_block() -> None:
    """For format-agnostic templates, rewrite is scoped to the first system
    block so user content containing the anchor phrase is never touched."""
    tokenizer = Tokenizer(template=FORMAT_AGNOSTIC_TOOL)
    engine = ChatTemplateEngine(tokenizer, Settings())
    tool = {"type": "function", "function": {"name": "echo", "parameters": {}}}
    user_content = "Please explain: For each function call, what happens?"
    result = engine.render(_tool_request(user_content, [tool]))
    assert "use Hermes XML format" in result.prompt
    assert "follow the format" not in result.prompt
    assert user_content in result.prompt


def test_tool_call_arguments_json_string_parsed_for_items_filter_template() -> None:
    """OpenAI tool_call.arguments is a JSON string; templates that iterate
    arguments|items (e.g. CoPaw) need it parsed to a dict via the lenient
    items filter, not by unconditionally mutating the message."""
    copaw_like = (
        "{% for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content or '' }}"
        "{% if m.tool_calls %}{% for tc in m.tool_calls %}"
        "{% if tc.function %}{% set tc = tc.function %}{% endif %}"
        "<function={{ tc.name }}>{% for k, v in tc.arguments|items %}"
        "<parameter={{ k }}>{{ v }}</parameter>{% endfor %}</function>"
        "{% endfor %}{% endif %}<|im_end|>\n{% endfor %}<|im_start|>assistant"
    )
    tokenizer = Tokenizer(template=copaw_like)
    engine = ChatTemplateEngine(tokenizer, Settings())
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "read_file",
            "arguments": '{"path": "/tmp/x.md"}',
        },
    }
    msg = ChatMessage("assistant", content=None, tool_calls=[tool_call])
    req = GenerationRequest(
        model="Qwen3-4B-Instruct",
        messages=[ChatMessage("user", "hi"), msg],
        tools=[],
        tool_choice="auto",
        parallel_tool_calls=True,
        stream=False,
        include_usage=False,
        max_new_tokens=8,
        temperature=0.7,
        top_p=0.9,
        top_k=1,
        repeat_penalty=1.0,
        stop=[],
        enable_thinking=True,
    )
    result = engine.render(req)
    assert "<function=read_file>" in result.prompt
    assert "<parameter=path>/tmp/x.md</parameter>" in result.prompt


def test_tool_call_arguments_json_string_preserved_for_direct_emit_template() -> None:
    """Templates that emit arguments directly (e.g. AgentModel with
    `arguments is string` check) must receive the original JSON string,
    not a Python dict repr."""
    tc_open = chr(60) + "tool_call" + chr(62)
    tc_close = chr(60) + "/tool_call" + chr(62)
    agentmodel_like = (
        "{% for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content or '' }}"
        "{% if m.tool_calls %}{% for tc in m.tool_calls %}"
        "{% if tc.function %}{% set tc = tc.function %}{% endif %}"
        + tc_open + "{{ tc.name }} "
        "{% if tc.arguments is string %}{{ tc.arguments }}{% else %}{{ tc.arguments | tojson }}{% endif %}"
        + tc_close
        + "{% endfor %}{% endif %}<|im_end|>\n{% endfor %}<|im_start|>assistant"
    )
    tokenizer = Tokenizer(template=agentmodel_like)
    engine = ChatTemplateEngine(tokenizer, Settings())
    arguments = '{"path": "/tmp/x.md"}'
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": arguments},
    }
    msg = ChatMessage("assistant", content=None, tool_calls=[tool_call])
    req = GenerationRequest(
        model="Qwen3-4B-Instruct",
        messages=[ChatMessage("user", "hi"), msg],
        tools=[],
        tool_choice="auto",
        parallel_tool_calls=True,
        stream=False,
        include_usage=False,
        max_new_tokens=8,
        temperature=0.7,
        top_p=0.9,
        top_k=1,
        repeat_penalty=1.0,
        stop=[],
        enable_thinking=True,
    )
    result = engine.render(req)
    assert tc_open + "read_file " + arguments + tc_close in result.prompt


def test_tool_markers_extracted_from_agentmodel_style_template() -> None:
    """AgentModel-V3 emits tool calls as ``<|tool_call>call:name{...}<tool_call|>``
    (Gemma bracket convention with pipe on the outside). The marker extractor
    must pick these up so the output parser can find them in model output.

    Tests ``_init_tool_markers`` directly because ``__init__`` overrides
    ``tool_start``/``tool_end`` with the default QWEN3_PROFILE values (which
    point at ``<tool_call>``/``</tool_call>``); the real model's profile
    overrides them again in ``service.py`` via ``set_parse_config``.
    """
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    agentmodel_call = (
        "{% for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content or '' }}"
        "{% if m.tool_calls %}{% for tc in m.tool_calls %}"
        "{% if tc.function %}{% set tc = tc.function %}{% endif %}"
        + "'" + tc_open + "call:' + tc.name + '{' + tc.arguments + '}" + tc_close + "'"
        + "{% endfor %}{% endif %}<|im_end|>\n{% endfor %}<|im_start|>assistant"
    )
    tokenizer = Tokenizer(template=agentmodel_call)
    engine = ChatTemplateEngine(tokenizer, Settings())
    start, end = engine._init_tool_markers()
    assert start == tc_open
    assert end == tc_close
