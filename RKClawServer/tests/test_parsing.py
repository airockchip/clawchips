from __future__ import annotations

from gateway.parsing import (
    GEMMA4_PROFILE,
    QWEN3_PROFILE,
    QWEN35_PROFILE,
    ParseConfig,
    build_parse_config,
    get_profile,
)


class FakeTokenizer:
    """Stub tokenizer mimicking GGUFTokenizer.get_special_token resolution."""

    def __init__(
        self,
        special_tokens: dict[str, str] | None = None,
        metadata: dict[str, str] | None = None,
        pieces: dict[int, str] | None = None,
    ):
        self._special_tokens = special_tokens or {}
        self._metadata = metadata or {}
        self._pieces = pieces or {}
        self.bos_token = self._special_tokens.get("bos_token", "")
        self.eos_token = self._special_tokens.get("eos_token", "")

    def get_special_token(self, name: str) -> str:
        if name == "bos_token":
            return self.bos_token
        if name == "eos_token":
            return self.eos_token
        value = self.get_metadata(f"tokenizer.ggml.{name}")
        if value:
            return value
        id_str = self.get_metadata(f"tokenizer.ggml.{name}_id")
        if id_str:
            try:
                return self.token_to_piece(int(id_str))
            except (ValueError, TypeError):
                pass
        return self._special_tokens.get(name, "")

    def get_metadata(self, key: str) -> str:
        return self._metadata.get(key, "")

    def token_to_piece(self, token_id: int) -> str:
        return self._pieces.get(token_id, "")


def test_get_profile_returns_gemma4_for_gemma4() -> None:
    assert get_profile("gemma4") is GEMMA4_PROFILE
    assert get_profile("GEMMA4") is GEMMA4_PROFILE


def test_get_profile_returns_qwen3_for_qwen3() -> None:
    assert get_profile("qwen3") is QWEN3_PROFILE
    assert get_profile("Qwen3") is QWEN3_PROFILE


def test_get_profile_returns_qwen35_for_qwen35() -> None:
    assert get_profile("qwen3_5") is QWEN35_PROFILE
    assert get_profile("qwen35") is QWEN35_PROFILE  # alias
    assert get_profile("Qwen3_5") is QWEN35_PROFILE


def test_get_profile_falls_back_to_qwen3_for_unknown() -> None:
    assert get_profile("unknown_model") is QWEN3_PROFILE
    assert get_profile("") is QWEN3_PROFILE


def test_build_parse_config_qwen3_with_explicit_tokens() -> None:
    tokenizer = FakeTokenizer(
        special_tokens={
            "im_start_token": "<|im_start|>",
            "think_start": "<" + "think" + ">",
            "think_end": "<" + "/think" + ">",
        }
    )
    config = build_parse_config(QWEN3_PROFILE, tokenizer, "<tool_call>", "</tool_call>")
    assert isinstance(config, ParseConfig)
    assert config.model_type == "qwen3"
    assert config.tool_start == "<tool_call>"
    assert config.tool_end == "</tool_call>"
    assert config.reasoning_starts == ("<" + "think" + ">",)
    assert config.reasoning_ends == ("<" + "/think" + ">",)
    assert config.skip_prefix_opens == ()
    assert config.system_block_delimiter == "<|im_start|>"


def test_build_parse_config_qwen3_with_fallbacks() -> None:
    tokenizer = FakeTokenizer()
    config = build_parse_config(QWEN3_PROFILE, tokenizer, "<tool_call>", "</tool_call>")
    assert config.reasoning_starts == ("<" + "think" + ">",)
    assert config.reasoning_ends == ("<" + "/think" + ">",)
    assert config.skip_prefix_opens == ()
    assert config.system_block_delimiter == "<|im_start|>"


def test_build_parse_config_gemma4_with_explicit_tokens() -> None:
    tokenizer = FakeTokenizer(
        special_tokens={
            "sot_token": "<|turn>",
            "eot_token": "<turn|>",
            "soc_token": "<|channel>",
            "eoc_token": "<channel|>",
        }
    )
    config = build_parse_config(GEMMA4_PROFILE, tokenizer, "<tool_call>", "</tool_call>")
    assert config.model_type == "gemma4"
    assert config.reasoning_starts == ("<|channel>",)
    assert config.reasoning_ends == ("<channel|>",)
    assert config.skip_prefix_opens == ("<|channel>",)
    assert config.system_block_delimiter == "<|turn>"


def test_build_parse_config_gemma4_with_fallbacks() -> None:
    tokenizer = FakeTokenizer()
    config = build_parse_config(GEMMA4_PROFILE, tokenizer, "<tool_call>", "</tool_call>")
    assert config.reasoning_starts == ("<|channel>",)
    assert config.reasoning_ends == ("<channel|>",)
    assert config.skip_prefix_opens == ("<|channel>",)
    assert config.system_block_delimiter == "<|turn>"


def test_build_parse_config_gemma4_resolves_via_id_metadata() -> None:
    tokenizer = FakeTokenizer(
        metadata={
            "tokenizer.ggml.sot_token_id": "105",
            "tokenizer.ggml.eot_token_id": "106",
            "tokenizer.ggml.soc_token_id": "100",
            "tokenizer.ggml.eoc_token_id": "101",
        },
        pieces={105: "<|turn>", 106: "<turn|>", 100: "<|channel>", 101: "<channel|>"},
    )
    config = build_parse_config(GEMMA4_PROFILE, tokenizer, "<tool_call>", "</tool_call>")
    assert config.reasoning_starts == ("<|channel>",)
    assert config.reasoning_ends == ("<channel|>",)
    assert config.skip_prefix_opens == ("<|channel>",)
    assert config.system_block_delimiter == "<|turn>"


def test_build_parse_config_profile_overrides_tool_markers() -> None:
    class Profile:
        model_type = "custom"
        tokens = ()
        reasoning_start_names = ()
        reasoning_end_names = ()
        skip_prefix_names = ()
        system_block_name = ""
        tool_start = "<custom_tool>"
        tool_end = "</custom_tool>"
        tool_call_prefix = ""
        tool_call_quote_token = ""
        block_end_name = ""
        assistant_marker = ""
        empty_thinking_template = ""
        tool_response_open = ""
        tool_response_close = ""
        rewrite_tool_format = True
        json_format_anchor = "return a json object"
        rewrite_anchor = "For each function call"
        hermes_func_prefix = "<function="
        hermes_func_close = "</function>"
        hermes_param_prefix = "<parameter="
        hermes_param_close = "</parameter>"
        logits_name = "output"

    config = build_parse_config(Profile(), FakeTokenizer(), "<tool_call>", "</tool_call>")
    assert config.tool_start == "<custom_tool>"
    assert config.tool_end == "</custom_tool>"


def test_build_parse_config_uses_template_tool_markers_when_profile_empty() -> None:
    """When the profile leaves tool_start/tool_end unset, the template-extracted
    markers passed in by the caller are used as the fallback."""
    class Profile:
        model_type = "custom"
        tokens = ()
        reasoning_start_names = ()
        reasoning_end_names = ()
        skip_prefix_names = ()
        system_block_name = ""
        tool_start = ""
        tool_end = ""
        tool_call_prefix = ""
        tool_call_quote_token = ""
        block_end_name = ""
        assistant_marker = ""
        empty_thinking_template = ""
        tool_response_open = ""
        tool_response_close = ""
        rewrite_tool_format = True
        json_format_anchor = "return a json object"
        rewrite_anchor = "For each function call"
        hermes_func_prefix = "<function="
        hermes_func_close = "</function>"
        hermes_param_prefix = "<parameter="
        hermes_param_close = "</parameter>"
        logits_name = "output"

    config = build_parse_config(Profile(), FakeTokenizer(), "<hermes>", "</hermes>")
    assert config.tool_start == "<hermes>"
    assert config.tool_end == "</hermes>"

def test_qwen3_profile_new_fields() -> None:
    """Qwen3 profile declares block end, assistant marker, thinking template, etc."""
    assert QWEN3_PROFILE.block_end_name == "eos_token"
    assert QWEN3_PROFILE.assistant_marker == "<|im_start|>assistant\n"
    assert QWEN3_PROFILE.empty_thinking_template == "<think>\n\n</think>\n\n"
    assert QWEN3_PROFILE.tool_response_open == "<tool_response>\n"
    assert QWEN3_PROFILE.tool_response_close == "\n</tool_response>"
    assert QWEN3_PROFILE.rewrite_tool_format is True
    assert QWEN3_PROFILE.logits_name == "output"


def test_gemma4_profile_new_fields() -> None:
    """Gemma4 profile declares its own block end, assistant marker, etc."""
    assert GEMMA4_PROFILE.block_end_name == "eot_token"
    assert GEMMA4_PROFILE.assistant_marker == "<|turn>model\n"
    assert GEMMA4_PROFILE.empty_thinking_template == "<|channel>thought\n\n<channel|>\n\n"
    assert GEMMA4_PROFILE.tool_response_open == ""
    assert GEMMA4_PROFILE.tool_response_close == ""
    assert GEMMA4_PROFILE.rewrite_tool_format is False
    assert GEMMA4_PROFILE.logits_name == "logits_gathered"


def test_build_parse_config_qwen3_block_end_delimiter() -> None:
    """block_end_name resolves to the eos_token string."""
    tokenizer = FakeTokenizer(special_tokens={"eos_token": "<|im_end|>"})
    config = build_parse_config(QWEN3_PROFILE, tokenizer, "<t>", "</t>")
    assert config.block_end_delimiter == "<|im_end|>"


def test_build_parse_config_gemma4_block_end_delimiter() -> None:
    """Gemma4 block_end_name resolves to eot_token string."""
    tokenizer = FakeTokenizer(special_tokens={"eot_token": "<turn|>"})
    config = build_parse_config(GEMMA4_PROFILE, tokenizer, "<t>", "</t>")
    assert config.block_end_delimiter == "<turn|>"


def test_build_parse_config_passes_hermes_tags() -> None:
    """Hermes tags flow from profile to ParseConfig."""
    config = build_parse_config(QWEN3_PROFILE, FakeTokenizer(), "<t>", "</t>")
    assert config.hermes_func_prefix == "<function="
    assert config.hermes_func_close == "</function>"
    assert config.hermes_param_prefix == "<parameter="
    assert config.hermes_param_close == "</parameter>"


def test_build_parse_config_passes_assistant_and_thinking() -> None:
    """assistant_marker and empty_thinking_template flow from profile to ParseConfig."""
    config = build_parse_config(QWEN3_PROFILE, FakeTokenizer(), "<t>", "</t>")
    assert config.assistant_marker == "<|im_start|>assistant\n"
    assert config.empty_thinking_template == "<think>\n\n</think>\n\n"
    assert config.tool_response_open == "<tool_response>\n"
    assert config.tool_response_close == "\n</tool_response>"
    assert config.rewrite_tool_format is True


def test_build_parse_config_gemma4_disables_rewrite() -> None:
    """Gemma4 profile disables tool format rewriting."""
    config = build_parse_config(GEMMA4_PROFILE, FakeTokenizer(), "<t>", "</t>")
    assert config.rewrite_tool_format is False
    assert config.tool_response_open == ""


def test_profile_logits_name_read_by_backend() -> None:
    """get_profile().logits_name is what rknn3.py reads for the tensor name."""
    assert get_profile("qwen3").logits_name == "output"
    assert get_profile("qwen3_5").logits_name == "output"
    assert get_profile("gemma4").logits_name == "logits_gathered"
    assert get_profile("").logits_name == "output"  # unknown falls back to qwen3


def test_qwen35_profile_fields() -> None:
    """Qwen3.5 profile shares Qwen3's token layout but is a distinct profile."""
    assert QWEN35_PROFILE.model_type == "qwen3_5"
    assert QWEN35_PROFILE is not QWEN3_PROFILE
    assert QWEN35_PROFILE.block_end_name == "eos_token"
    assert QWEN35_PROFILE.assistant_marker == "<|im_start|>assistant\n"
    assert QWEN35_PROFILE.empty_thinking_template == "<think>\n\n</think>\n\n"
    assert QWEN35_PROFILE.tool_response_open == "<tool_response>\n"
    assert QWEN35_PROFILE.tool_response_close == "\n</tool_response>"
    assert QWEN35_PROFILE.rewrite_tool_format is True
    assert QWEN35_PROFILE.logits_name == "output"


def test_qwen35_profile_uses_hermes_tags() -> None:
    """Qwen3.5 declares Hermes XML tool-call tags (the native format)."""
    assert QWEN35_PROFILE.hermes_func_prefix == "<function="
    assert QWEN35_PROFILE.hermes_func_close == "</function>"
    assert QWEN35_PROFILE.hermes_param_prefix == "<parameter="
    assert QWEN35_PROFILE.hermes_param_close == "</parameter>"


def test_build_parse_config_qwen35_resolves_tokens() -> None:
    """build_parse_config with QWEN35_PROFILE resolves tokens like Qwen3."""
    tokenizer = FakeTokenizer(
        special_tokens={
            "im_start_token": "<|im_start|>",
            "eos_token": "<|im_end|>",
            "think_start": "<" + "think" + ">",
            "think_end": "<" + "/think" + ">",
        }
    )
    config = build_parse_config(QWEN35_PROFILE, tokenizer, "<tool_call>", "</tool_call>")
    assert config.model_type == "qwen3_5"
    assert config.system_block_delimiter == "<|im_start|>"
    assert config.block_end_delimiter == "<|im_end|>"
    assert config.reasoning_starts == ("<" + "think" + ">",)
    assert config.reasoning_ends == ("<" + "/think" + ">",)
    assert config.rewrite_tool_format is True
    assert config.hermes_func_prefix == "<function="
