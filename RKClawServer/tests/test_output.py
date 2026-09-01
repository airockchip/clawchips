from __future__ import annotations

import json
import logging

from gateway.output import (
    Gemma4ToolParser,
    QwenToolParser,
    ReasoningTransformer,
    _REASONING_END_MARKERS,
    _REASONING_START_MARKERS,
)

# Use the module-level marker constants so the test stays correct even if
# the underlying token strings (Qwen  IMD/ IMD, Gemma4 <|channel>/<channel|>)
# change.
_OPEN, _GEMMA_OPEN = _REASONING_START_MARKERS
_CLOSE, _GEMMA_CLOSE = _REASONING_END_MARKERS


def test_reasoning_tag_split_across_chunks() -> None:
    transformer = ReasoningTransformer(True)
    assert transformer.feed("<thi") == []
    first = transformer.feed("nk>reason</thi")
    assert [delta.reasoning_content for delta in first] == ["reason"]
    second = transformer.feed("nk>answer") + transformer.finish()
    assert "".join(delta.content for delta in second) == "answer"


def test_reasoning_disabled_preserves_tags() -> None:
    transformer = ReasoningTransformer(False)
    deltas = transformer.feed("<think>reason</think>answer")
    assert deltas[0].content == "<think>reason</think>answer"


def test_fallback_delimiter() -> None:
    transformer = ReasoningTransformer(True, "\n\n\n")
    assert transformer.feed("reason\n\n") == []
    deltas = transformer.feed("\nanswer") + transformer.finish()
    assert "".join(delta.reasoning_content for delta in deltas) == "reason"
    assert "".join(delta.content for delta in deltas) == "answer"


def test_pending_fallback_can_finish_as_reasoning() -> None:
    transformer = ReasoningTransformer(True, "\n\n\n")
    assert transformer.feed("reason without delimiter") == []
    deltas = transformer.finish(pending_as_reasoning=True)
    assert "".join(delta.reasoning_content for delta in deltas) == "reason without delimiter"
    assert "".join(delta.content for delta in deltas) == ""


def test_pending_fallback_finishes_as_content_without_tool_call() -> None:
    transformer = ReasoningTransformer(True, "\n\n\n")
    assert transformer.feed("answer without delimiter") == []
    deltas = transformer.finish()
    assert "".join(delta.reasoning_content for delta in deltas) == ""
    assert "".join(delta.content for delta in deltas) == "answer without delimiter"


def test_gemma4_channel_block_split_into_reasoning_and_content() -> None:
    """Gemma4 wraps reasoning in <|channel>NAME\n...\n<channel|>.

    The channel name (e.g. "thought") and its trailing newline are not part
    of the reasoning text and must be skipped.
    """
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed(_GEMMA_OPEN + "thought\nmy reasoning\n" + _GEMMA_CLOSE + "answer")
    assert "".join(d.reasoning_content for d in deltas) == "my reasoning\n"
    assert "".join(d.content for d in deltas) == "answer"


def test_gemma4_channel_marker_split_across_chunks() -> None:
    """Channel markers and channel name may arrive in separate feed() calls."""
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed(_GEMMA_OPEN[:3])  # "<|c"
    assert deltas == []
    deltas = transformer.feed(_GEMMA_OPEN[3:] + "tho")  # "hannel>tho"
    assert deltas == []
    deltas = transformer.feed("ught\nreasoning\n" + _GEMMA_CLOSE + "answer")
    assert "".join(d.reasoning_content for d in deltas) == "reasoning\n"
    assert "".join(d.content for d in deltas) == "answer"


def test_gemma4_channel_name_split_across_chunks() -> None:
    """The channel name itself may be split across feed() calls."""
    transformer = ReasoningTransformer(True)
    assert transformer.feed(_GEMMA_OPEN + "tho") == []
    deltas = transformer.feed("ught\nreasoning\n" + _GEMMA_CLOSE + "answer")
    assert "".join(d.reasoning_content for d in deltas) == "reasoning\n"
    assert "".join(d.content for d in deltas) == "answer"


def test_gemma4_multiple_channel_blocks() -> None:
    """Multiple <|channel>...<channel|> blocks each become reasoning."""
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed(
        _GEMMA_OPEN + "thought\nfirst\n" + _GEMMA_CLOSE
        + "mid"
        + _GEMMA_OPEN + "thought\nsecond\n" + _GEMMA_CLOSE
        + "end"
    )
    assert "".join(d.reasoning_content for d in deltas) == "first\nsecond\n"
    assert "".join(d.content for d in deltas) == "midend"


def test_gemma4_unclosed_channel_finishes_as_reasoning_for_tool_calls() -> None:
    """When a tool call interrupts, an open channel is flushed as reasoning."""
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed(_GEMMA_OPEN + "thought\nopen reasoning")
    deltas += transformer.finish(pending_as_reasoning=True)
    assert "".join(d.reasoning_content for d in deltas) == "open reasoning"
    assert "".join(d.content for d in deltas) == ""


def test_gemma4_channel_with_content_before() -> None:
    """Content before the channel block stays as content."""
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed("hello " + _GEMMA_OPEN + "thought\nreasoning\n" + _GEMMA_CLOSE + "answer")
    assert "".join(d.reasoning_content for d in deltas) == "reasoning\n"
    assert "".join(d.content for d in deltas) == "hello answer"


def test_end_marker_without_start_treats_preceding_as_reasoning() -> None:
    """A closing think tag without an opening tag means everything before
    it is reasoning content (model omitted the opening <think>)."""
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed("my reasoning" + _CLOSE + "answer") + transformer.finish()
    assert "".join(d.reasoning_content for d in deltas) == "my reasoning"
    assert "".join(d.content for d in deltas) == "answer"


def test_end_marker_without_start_at_beginning() -> None:
    """A leading closing tag with no preceding text emits nothing as reasoning."""
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed(_CLOSE + "answer") + transformer.finish()
    assert "".join(d.reasoning_content for d in deltas) == ""
    assert "".join(d.content for d in deltas) == "answer"


def test_end_marker_without_start_then_normal_think_block() -> None:
    """After an orphan end marker, a normal think block still works."""
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed(
        "first reasoning" + _CLOSE
        + "mid"
        + _OPEN + "second reasoning" + _CLOSE
        + "end"
    ) + transformer.finish()
    assert "".join(d.reasoning_content for d in deltas) == "first reasoningsecond reasoning"
    assert "".join(d.content for d in deltas) == "midend"


def test_end_marker_without_start_split_across_chunks() -> None:
    """The orphan end marker arriving split across feeds is still recognized.

    Uses fallback_delimiter (matching Qwen3.5's config) so content is
    buffered until a marker is seen -- without it, the text before an
    orphan </think> arriving in a later chunk would already be emitted
    as content.
    """
    transformer = ReasoningTransformer(True, "\n\n\n")
    deltas = transformer.feed("reasoning</thi")
    assert deltas == []
    deltas = transformer.feed("nk>answer") + transformer.finish()
    assert "".join(d.reasoning_content for d in deltas) == "reasoning"
    assert "".join(d.content for d in deltas) == "answer"


def test_gemma4_end_marker_without_start_treats_preceding_as_reasoning() -> None:
    """Gemma4 orphan close marker also routes preceding text to reasoning."""
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed("my reasoning" + _GEMMA_CLOSE + "answer") + transformer.finish()
    assert "".join(d.reasoning_content for d in deltas) == "my reasoning"
    assert "".join(d.content for d in deltas) == "answer"


def test_qwen_and_gemma4_markers_coexist() -> None:
    """Both marker sets are recognized; a single request never mixes them
    but both code paths must work in the same transformer."""
    # Qwen format
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed(_OPEN + "reason" + _CLOSE + "answer")
    assert "".join(d.reasoning_content for d in deltas) == "reason"
    assert "".join(d.content for d in deltas) == "answer"
    # Gemma4 format on a fresh transformer
    transformer = ReasoningTransformer(True)
    deltas = transformer.feed(_GEMMA_OPEN + "thought\nreason\n" + _GEMMA_CLOSE + "answer")
    assert "".join(d.reasoning_content for d in deltas) == "reason\n"
    assert "".join(d.content for d in deltas) == "answer"


def test_explicit_markers_via_kwargs_override_defaults() -> None:
    """A transformer configured with only Qwen markers must not treat Gemma4
    channel markers as reasoning, and vice versa."""
    qwen_only = ReasoningTransformer(
        True,
        reasoning_starts=(_OPEN,),
        reasoning_ends=(_CLOSE,),
        skip_prefix_opens=(),
    )
    deltas = qwen_only.feed(_GEMMA_OPEN + "thought\nreason\n" + _GEMMA_CLOSE + "answer")
    assert "".join(d.reasoning_content for d in deltas) == ""
    assert "".join(d.content for d in deltas) == _GEMMA_OPEN + "thought\nreason\n" + _GEMMA_CLOSE + "answer"

    gemma_only = ReasoningTransformer(
        True,
        reasoning_starts=(_GEMMA_OPEN,),
        reasoning_ends=(_GEMMA_CLOSE,),
        skip_prefix_opens=(_GEMMA_OPEN,),
    )
    deltas = gemma_only.feed(_OPEN + "reason" + _CLOSE + "answer")
    assert "".join(d.reasoning_content for d in deltas) == ""
    assert "".join(d.content for d in deltas) == _OPEN + "reason" + _CLOSE + "answer"
    # And the gemma-only transformer still handles the channel-name skip
    deltas = gemma_only.feed(_GEMMA_OPEN + "thought\nreason\n" + _GEMMA_CLOSE + "answer")
    assert "".join(d.reasoning_content for d in deltas) == "reason\n"
    assert "".join(d.content for d in deltas) == "answer"


def test_tool_marker_and_json_split_across_chunks() -> None:
    parser = QwenToolParser()
    text, calls = parser.feed("before<tool")
    assert text == "before"
    assert calls == []
    text, calls = parser.feed('_call>{"name":"echo","arguments":{"x":')
    assert text == ""
    assert calls[0]["function"]["name"] == "echo"
    text, calls = parser.feed('1}}</tool_call>after')
    assert text == "after"
    assert calls[0]["function"]["arguments"] == "1}"
    full = parser.full()
    assert full.content == "beforeafter"
    assert full.tool_calls[0].arguments == '{"x":1}'


def test_invalid_tool_block_remains_visible_after_completion() -> None:
    parser = QwenToolParser()
    parser.feed("<tool_call>not-json</tool_call>")
    parser.finish()
    assert parser.full().content == "<tool_call>not-json</tool_call>"


def test_correction_wraps_missing_arguments_and_repairs_quotes() -> None:
    parser = QwenToolParser(correct_tool_calls=True)
    parser.feed(
        '<tool_call>{"name": "write_file", "path": '
        '/home/nanobot/.nanobot/workspace/memory/history.jsonl, '
        'content: "hello"}</tool_call>'
    )
    call = parser.full().tool_calls[0]
    assert json.loads(call.arguments) == {
        "path": "/home/nanobot/.nanobot/workspace/memory/history.jsonl",
        "content": "hello",
    }


def test_correction_logs_when_applied(caplog) -> None:
    parser = QwenToolParser(correct_tool_calls=True)
    with caplog.at_level(logging.WARNING, logger="gateway"):
        parser.feed('<tool_call>{"name":"grep","arguments":{path:"/tmp"}}</tool_call>')

    assert "tool_call correction applied" in caplog.text
    assert "function=grep" in caplog.text


def test_correction_does_not_rewrite_json_like_content_inside_string() -> None:
    parser = QwenToolParser(correct_tool_calls=True)
    content = '[{"cursor":"2026-06-13 06:50","content":"用户问：测试"}]'
    body = (
        '<tool_call>{"name":"write_file","path":/tmp/history.jsonl,'
        f'content:{json.dumps(content, ensure_ascii=False)}}}</tool_call>'
    )
    parser.feed(body)
    assert json.loads(parser.full().tool_calls[0].arguments) == {
        "path": "/tmp/history.jsonl",
        "content": content,
    }


def test_correction_repairs_unquoted_argument_keys() -> None:
    parser = QwenToolParser(correct_tool_calls=True)
    parser.feed(
        '<tool_call>{"name":"grep","arguments":{"pattern":"nanobot",'
        'path:"/tmp",case_insensitive:true,output_mode:"count"}}</tool_call>'
    )
    assert json.loads(parser.full().tool_calls[0].arguments) == {
        "pattern": "nanobot",
        "path": "/tmp",
        "case_insensitive": True,
        "output_mode": "count",
    }


def test_correction_repairs_unquoted_argument_keys_with_equals() -> None:
    parser = QwenToolParser(correct_tool_calls=True)
    parser.feed(
        '<tool_call>{"name":"grep","arguments":{"pattern":"决赛周",'
        '"path":"memory/history.jsonl",case_insensitive=true}}</tool_call>'
    )
    assert json.loads(parser.full().tool_calls[0].arguments) == {
        "pattern": "决赛周",
        "path": "memory/history.jsonl",
        "case_insensitive": True,
    }


def test_correction_repairs_missing_comma_dot_separator_and_equals() -> None:
    parser = QwenToolParser(correct_tool_calls=True)
    parser.feed(
        '<tool_call>{"name":"grep","arguments":{"pattern":"决赛周" '
        '"path"."memory/history.jsonl",case_insensitive=true}}</tool_call>'
    )
    assert json.loads(parser.full().tool_calls[0].arguments) == {
        "pattern": "决赛周",
        "path": "memory/history.jsonl",
        "case_insensitive": True,
    }


def test_correction_removes_trailing_commas() -> None:
    parser = QwenToolParser(correct_tool_calls=True)
    parser.feed(
        '<tool_call>{"name":"grep","arguments":{"pattern":"决赛周",'
        '"path":"memory/history.jsonl",}}</tool_call>'
    )
    assert json.loads(parser.full().tool_calls[0].arguments) == {
        "pattern": "决赛周",
        "path": "memory/history.jsonl",
    }


def test_correction_does_not_rewrite_equals_inside_string() -> None:
    parser = QwenToolParser(correct_tool_calls=True)
    parser.feed(
        '<tool_call>{"name":"write_file","arguments":{"path":"/tmp/out.txt",'
        '"content":"keep {case_insensitive=true} unchanged"}}</tool_call>'
    )
    assert json.loads(parser.full().tool_calls[0].arguments) == {
        "path": "/tmp/out.txt",
        "content": "keep {case_insensitive=true} unchanged",
    }


def test_correction_trims_literal_newlines_and_buffers_stream() -> None:
    parser = QwenToolParser(correct_tool_calls=True)
    _, calls = parser.feed(r'<tool_call>\n{"name":"message","arguments":')
    assert calls == []
    _, calls = parser.feed(r'{"content":"ok","channel":"cli"}}\n</tool_call>')
    assert calls[0]["function"] == {
        "name": "message",
        "arguments": '{"content":"ok","channel":"cli"}',
    }


def test_correction_is_disabled_by_default() -> None:
    parser = QwenToolParser()
    parser.feed('<tool_call>{"name":"grep","arguments":{path:"/tmp"}}</tool_call>')
    assert parser.full().tool_calls[0].arguments == '{path:"/tmp"}}'


def test_wrapped_hermes_stream_buffers_without_correction_or_warnings(caplog) -> None:
    parser = QwenToolParser(correct_tool_calls=False)
    incomplete_chunks = (
        "<tool_call>",
        "<",
        "function",
        "=read_file>",
        "\n<parameter=path>/tmp/x.md</parameter>",
    )

    with caplog.at_level(logging.WARNING, logger="gateway"):
        for chunk in incomplete_chunks:
            _, calls = parser.feed(chunk)
            assert calls == []

        _, calls = parser.feed("</function>")

    assert "tool_call name not found" not in caplog.text
    assert calls[0]["function"]["name"] == "read_file"
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "/tmp/x.md"}

    _, closing_calls = parser.feed("</tool_call>")
    assert closing_calls == []
    full = parser.full()
    assert full.content == ""
    assert full.tool_calls[0].name == "read_file"
    assert json.loads(full.tool_calls[0].arguments) == {"path": "/tmp/x.md"}


def test_hermes_xml_ignored_when_tool_parsing_disabled() -> None:
    """When request has no tools, Hermes-style XML stays as content."""
    parser = QwenToolParser(parse_tool_calls=False)
    fn_open = chr(60) + "function=echo" + chr(62)
    fn_close = chr(60) + "/function" + chr(62)
    parser.feed(fn_open + fn_close)
    full = parser.full()
    assert full.tool_calls == []
    assert fn_open + fn_close in full.content


def test_qwen_tool_call_ignored_when_tool_parsing_disabled() -> None:
    """When request has no tools, Qwen-style tool_call tags stay as content."""
    parser = QwenToolParser(parse_tool_calls=False)
    tc_open = chr(60) + "tool_call" + chr(62)
    tc_close = chr(60) + "/tool_call" + chr(62)
    body = tc_open + '{"name":"grep","arguments":{}}' + tc_close
    parser.feed(body)
    full = parser.full()
    assert full.tool_calls == []
    assert body in full.content


def test_gemma4_call_format_parsed() -> None:
    """Gemma4 AgentModel emits call:FUNC{KEY:<|"|>VAL<|"|>} -- <|"|> is its quote token."""
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    quote_tok = '<|"|>'
    body = tc_open + "call:read_file{path:" + quote_tok + "/tmp/x.md" + quote_tok + "}" + tc_close
    parser = Gemma4ToolParser(
        tool_start=tc_open, tool_end=tc_close,
        tool_call_prefix="call:", tool_call_quote_token=quote_tok,
    )
    parser.feed(body)
    parsed = parser.full()
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "read_file"
    assert json.loads(parsed.tool_calls[0].arguments) == {"path": "/tmp/x.md"}


def test_gemma4_call_format_multiple_params() -> None:
    """Multiple parameters in the Gemma4 call format."""
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    quote_tok = '<|"|>'
    body = (
        tc_open
        + "call:search{query:" + quote_tok + "hello" + quote_tok
        + ",limit:5}"
        + tc_close
    )
    parser = Gemma4ToolParser(
        tool_start=tc_open, tool_end=tc_close,
        tool_call_prefix="call:", tool_call_quote_token=quote_tok,
    )
    parser.feed(body)
    parsed = parser.full()
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "search"
    assert json.loads(parsed.tool_calls[0].arguments) == {"query": "hello", "limit": 5}


def test_gemma4_call_format_not_triggered_for_plain_json() -> None:
    """Bodies without the call: prefix fall through to normal JSON parsing."""
    tc_open = chr(60) + "tool_call" + chr(62)
    tc_close = chr(60) + "/tool_call" + chr(62)
    parser = QwenToolParser()
    parser.feed(tc_open + '{"name":"grep","arguments":{}}' + tc_close)
    parsed = parser.full()
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "grep"


def test_gemma4_call_format_string_value_with_literal_quotes() -> None:
    """String values wrapped in <|"|> may contain literal double quotes that
    must be escaped when converted to JSON (not naive string replacement)."""
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    quote_tok = '<|"|>'
    body = (
        tc_open
        + "call:exec{command:" + quote_tok + 'curl -s "wttr.in/福州?format=%l:+%c+%t"' + quote_tok
        + ",timeout:15}"
        + tc_close
    )
    parser = Gemma4ToolParser(
        tool_start=tc_open, tool_end=tc_close,
        tool_call_prefix="call:", tool_call_quote_token=quote_tok,
    )
    parser.feed(body)
    parsed = parser.full()
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "exec"
    assert json.loads(parsed.tool_calls[0].arguments) == {
        "command": 'curl -s "wttr.in/福州?format=%l:+%c+%t"',
        "timeout": 15,
    }


def test_gemma4_call_format_string_value_with_comma_and_colon() -> None:
    """Commas and colons inside a <|"|>-delimited string must not split keys."""
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    quote_tok = '<|"|>'
    body = (
        tc_open
        + "call:run{cmd:" + quote_tok + "echo a,b:c" + quote_tok + "}"
        + tc_close
    )
    parser = Gemma4ToolParser(
        tool_start=tc_open, tool_end=tc_close,
        tool_call_prefix="call:", tool_call_quote_token=quote_tok,
    )
    parser.feed(body)
    parsed = parser.full()
    assert len(parsed.tool_calls) == 1
    assert json.loads(parsed.tool_calls[0].arguments) == {"cmd": "echo a,b:c"}


def test_gemma4_call_format_string_value_with_newlines() -> None:
    """String values may contain newlines and double quotes (shell commands).
    json.dumps must be used so newlines become \\n, not literal newlines
    (which are invalid inside JSON strings)."""
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    quote_tok = '<|"|>'
    cmd = 'cd /tmp && python3 -c "\nimport json\nprint(\'hi\')\n" 2>&1'
    body = (
        tc_open
        + "call:exec{command:" + quote_tok + cmd + quote_tok
        + ",timeout:10}"
        + tc_close
    )
    parser = Gemma4ToolParser(
        tool_start=tc_open, tool_end=tc_close,
        tool_call_prefix="call:", tool_call_quote_token=quote_tok,
    )
    parser.feed(body)
    parsed = parser.full()
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "exec"
    assert json.loads(parsed.tool_calls[0].arguments) == {
        "command": cmd,
        "timeout": 10,
    }


def test_gemma4_call_format_double_brace_json() -> None:
    """Gemma4 sometimes emits call:FUNC{{"KEY":"VAL"}} with double braces."""
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    body = tc_open + 'call:web_search{{"query":"福州天气","timeRange":"1d"}}' + tc_close
    parser = Gemma4ToolParser(tool_start=tc_open, tool_end=tc_close,
                              tool_call_prefix="call:")
    parser.feed(body)
    parsed = parser.full()
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "web_search"
    assert json.loads(parsed.tool_calls[0].arguments) == {
        "query": "福州天气", "timeRange": "1d",
    }


def test_gemma4_call_format_unquoted_keys_with_regular_quotes() -> None:
    """Gemma4 may emit call:FUNC{KEY:"VAL",KEY2:"VAL2"} with unquoted keys but
    regular double-quoted string values (no <|"|> token). Corrector rules
    should quote the keys so the args parse as valid JSON."""
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    quote_tok = '<|"|>'
    body = (
        tc_open
        + 'call:translate_markdown{input_path:"/home/user/file.md",target_lang:"en"}'
        + tc_close
    )
    parser = Gemma4ToolParser(
        tool_start=tc_open, tool_end=tc_close,
        tool_call_prefix="call:", tool_call_quote_token=quote_tok,
        correct_tool_calls=True,
    )
    parser.feed(body)
    parsed = parser.full()
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "translate_markdown"
    assert json.loads(parsed.tool_calls[0].arguments) == {
        "input_path": "/home/user/file.md",
        "target_lang": "en",
    }


def test_gemma4_call_format_unquoted_keys_without_corrector_returns_none() -> None:
    """Without the corrector, unquoted keys cannot be repaired and the body
    falls through to content (preserves prior behavior)."""
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    quote_tok = '<|"|>'
    body = (
        tc_open
        + 'call:translate_markdown{input_path:"/home/user/file.md",target_lang:"en"}'
        + tc_close
    )
    parser = Gemma4ToolParser(
        tool_start=tc_open, tool_end=tc_close,
        tool_call_prefix="call:", tool_call_quote_token=quote_tok,
        correct_tool_calls=False,
    )
    parser.feed(body)
    parsed = parser.full()
    assert parsed.tool_calls == []
    assert body in parsed.content


def test_gemma4_call_format_double_brace_with_trailing_garbage() -> None:
    """Model sometimes emits call:FUNC{{"k":"v" | trailing}} with mismatched
    braces ({{ but single }) and trailing text inside the object. The salvage
    path should close the object after the last valid string value."""
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    quote_tok = '<|"|>'
    body = (
        tc_open
        + 'call:exec{{"command": "find /userdata -name \\"*.py\\" 2>/dev/null | sort" | head -100}'
        + tc_close
    )
    parser = Gemma4ToolParser(
        tool_start=tc_open, tool_end=tc_close,
        tool_call_prefix="call:", tool_call_quote_token=quote_tok,
        correct_tool_calls=True,
    )
    parser.feed(body)
    parsed = parser.full()
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "exec"
    assert json.loads(parsed.tool_calls[0].arguments) == {
        "command": 'find /userdata -name "*.py" 2>/dev/null | sort',
    }


def test_gemma4_call_format_trailing_garbage_after_brace() -> None:
    """Trailing garbage after the closing } should be handled by raw_decode."""
    tc_open = chr(60) + "|tool_call" + chr(62)
    tc_close = chr(60) + "tool_call|" + chr(62)
    quote_tok = '<|"|>'
    body = (
        tc_open
        + 'call:exec{{"command": "ls -la"}} extra trailing text'
        + tc_close
    )
    parser = Gemma4ToolParser(
        tool_start=tc_open, tool_end=tc_close,
        tool_call_prefix="call:", tool_call_quote_token=quote_tok,
        correct_tool_calls=True,
    )
    parser.feed(body)
    parsed = parser.full()
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "exec"
    assert json.loads(parsed.tool_calls[0].arguments) == {"command": "ls -la"}


def test_make_parser_returns_gemma4_for_gemma4_config() -> None:
    from gateway.output import make_parser
    from gateway.parsing import ParseConfig

    config = ParseConfig(
        model_type="gemma4",
        tool_start="<|tool_call>",
        tool_end="<tool_call|>",
        reasoning_starts=(),
        reasoning_ends=(),
        skip_prefix_opens=(),
        system_block_delimiter="",
        tool_call_prefix="call:",
        tool_call_quote_token='<|"|>',
    )
    parser = make_parser(config, has_tools=True, enable_correction=False)
    assert isinstance(parser, Gemma4ToolParser)


def test_make_parser_returns_qwen_for_non_gemma4_config() -> None:
    from gateway.output import make_parser
    from gateway.parsing import ParseConfig

    config = ParseConfig(
        model_type="qwen3",
        tool_start="<tool_call>",
        tool_end="</tool_call>",
        reasoning_starts=(),
        reasoning_ends=(),
        skip_prefix_opens=(),
        system_block_delimiter="",
    )
    parser = make_parser(config, has_tools=True, enable_correction=True)
    assert isinstance(parser, QwenToolParser)
    assert parser.corrector is not None
