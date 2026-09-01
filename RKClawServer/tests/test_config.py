from __future__ import annotations

import pytest

from gateway.config import get_settings


CONFIG = '''
[server]
host = "0.0.0.0"
port = 9090
queue_size = 8
enable_streaming = false
sse_heartbeat_interval_s = 18.5

[runtime]
target = "rk1828"
core_mask = "0x0f"
toolkit_lite_wheel = "/sdk/rknn.whl"
native_library = "/sdk/librkclaw_native.so"

[model]
id = "Qwen3-4B-Instruct"
rknn_path = "/models/model.rknn"
weight_path = "/models/model.weight"
tokenizer_path = "/models/tokenizer"
embed_path = "/models/embed.bin"
max_context_tokens = 8192
max_new_tokens = 1024
enable_thinking = false

[checkpoint]
enabled = true
start_pos = 2048
interval = 512
max_count = 7

[sampling]
temperature = 0.2
top_p = 0.8
top_k = 2
repeat_penalty = 1.1
frequency_penalty = 0.25
presence_penalty = 0.5

[reasoning]
separate_output = true
fallback_delimiter = ""

[tool_call_correction]
enabled = true

[xgrammar]
enabled = true
model_structure = "qwen3.5"
debug = true

[logging]
debug_logs = true
openai_request_log = "file"
llm_input_log = "logger"
llm_output_log = "both"
openai_response_log = "off"
logger_detail_log_max_chars = 1000
'''


def test_get_settings_loads_local_runtime_config(tmp_path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(CONFIG)
    settings = get_settings(path)
    assert (settings.host, settings.port, settings.queue_size) == ("0.0.0.0", 9090, 8)
    assert settings.enable_streaming is False
    assert settings.sse_heartbeat_interval_s == 18.5
    assert (settings.runtime_target, settings.core_mask) == ("rk1828", 0x0F)
    assert settings.tokenizer_library == "/sdk/librkclaw_native.so"
    assert settings.sampling_library == "/sdk/librkclaw_native.so"
    assert settings.rknn_path == "/models/model.rknn"
    assert settings.max_context_tokens == 8192
    assert settings.enable_thinking is False
    assert settings.frequency_penalty == 0.25
    assert settings.presence_penalty == 0.5
    assert settings.kv_cache_dir == "/userdata/RKClawServer/kv_cache"
    assert settings.checkpoint_enabled is True
    assert settings.checkpoint_policy_values(8192) == (2048, 512, 7)
    assert settings.separate_reasoning is True
    assert settings.enable_tool_call_correction is True
    assert settings.enable_xgrammar is True
    assert settings.xgrammar_model_structure == "qwen3.5"
    assert settings.xgrammar_debug is True
    assert settings.enable_native_sampling is False
    assert settings.native_sampling_seed == -1
    assert settings.native_repeat_last_n == 64
    assert settings.multicard_enabled is False
    assert settings.debug_logs is True
    assert settings.openai_request_log == "file"
    assert settings.llm_input_log == "logger"
    assert settings.llm_output_log == "both"
    assert settings.openai_response_log == "off"
    assert settings.logger_detail_log_max_chars == 1000


def test_checkpoint_values_are_bounded_for_small_context() -> None:
    from gateway.config import Settings

    settings = Settings(
        checkpoint_start_pos=10240,
        checkpoint_interval=1024,
        checkpoint_max_count=18,
    )

    assert settings.checkpoint_policy_values(4096) == (1024, 1024, 3)


def test_debug_logs_enable_detail_logger_targets_by_default(tmp_path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(CONFIG.replace('openai_request_log = "file"\nllm_input_log = "logger"\nllm_output_log = "both"\nopenai_response_log = "off"\n', ""))
    settings = get_settings(path)

    assert settings.openai_request_log == "logger"
    assert settings.llm_input_log == "logger"
    assert settings.llm_output_log == "logger"
    assert settings.openai_response_log == "logger"


def test_xgrammar_debug_defaults_to_false(tmp_path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(CONFIG.replace("debug = true\n", ""))
    settings = get_settings(path)

    assert settings.xgrammar_debug is False


def test_xgrammar_model_structure_defaults_to_qwen3(tmp_path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(CONFIG.replace('model_structure = "qwen3.5"\n', ""))
    settings = get_settings(path)

    assert settings.xgrammar_model_structure == "qwen3"


def test_xgrammar_model_structure_rejects_unknown_value(tmp_path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(CONFIG.replace('model_structure = "qwen3.5"', 'model_structure = "unknown"'))

    with pytest.raises(RuntimeError, match="xgrammar.model_structure"):
        get_settings(path)


def test_get_settings_requires_config_file(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="Configuration file not found"):
        get_settings(tmp_path / "missing.toml")


def test_get_settings_requires_model_artifacts(tmp_path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text("[model]\nrknn_path = '/model.rknn'\n")
    with pytest.raises(RuntimeError, match="model.weight_path"):
        get_settings(path)


def test_native_library_defaults_to_packaged_library(tmp_path, monkeypatch) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(CONFIG.replace('native_library = "/sdk/librkclaw_native.so"\n', ""))
    packaged_library = "/venv/site-packages/gateway/_native/librkclaw_native.so"
    monkeypatch.setattr("gateway.config.resolve_native_library", lambda path="": path or packaged_library)

    settings = get_settings(path)

    assert settings.tokenizer_library == packaged_library
    assert settings.sampling_library == packaged_library


def test_native_sampling_settings_load(tmp_path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(CONFIG + '\n[native_sampling]\nenabled = true\nseed = 123\nrepeat_last_n = 32\n')
    settings = get_settings(path)

    assert settings.enable_native_sampling is True
    assert settings.native_sampling_seed == 123
    assert settings.native_repeat_last_n == 32


def test_multicard_settings_use_explicit_stage_order_and_do_not_require_scalar_model_paths(tmp_path) -> None:
    config = CONFIG.replace('rknn_path = "/models/model.rknn"\n', "").replace(
        'weight_path = "/models/model.weight"\n', ""
    )
    config += '''

[multicard]
bucket_size = 64

[[multicard.stages]]
device_id = "0002:21:00.0"
rknn_path = "/models/large_seg0.rknn"
weight_path = "/models/large_seg0.weight"
output_tensor_name = "hidden_states"

[[multicard.stages]]
device_id = "0003:31:00.0"
rknn_path = "/models/large_seg1.rknn"
weight_path = "/models/large_seg1.weight"

[[multicard.stages]]
device_id = "0004:41:00.0"
rknn_path = "/models/large_seg2.rknn"
weight_path = "/models/large_seg2.weight"
'''
    path = tmp_path / "gateway.toml"
    path.write_text(config)

    settings = get_settings(path)

    assert settings.multicard_enabled is True
    assert settings.multicard_bucket_size == 64
    assert [stage.device_id for stage in settings.multicard_stages] == [
        "0002:21:00.0",
        "0003:31:00.0",
        "0004:41:00.0",
    ]
    assert settings.multicard_stages[0].output_tensor_name == "hidden_states"
    assert settings.rknn_path == ""
    assert settings.weight_path == ""


def test_multicard_settings_reject_duplicate_devices(tmp_path) -> None:
    config = CONFIG + '''

[[multicard.stages]]
device_id = "same"
rknn_path = "/models/seg0.rknn"
weight_path = "/models/seg0.weight"

[[multicard.stages]]
device_id = "same"
rknn_path = "/models/seg1.rknn"
weight_path = "/models/seg1.weight"
'''
    path = tmp_path / "gateway.toml"
    path.write_text(config)

    with pytest.raises(RuntimeError, match="device_id values must be unique"):
        get_settings(path)


@pytest.mark.parametrize("bucket_size", ["0", "-1", '"invalid"'])
def test_multicard_settings_reject_invalid_bucket_size(tmp_path, bucket_size) -> None:
    config = CONFIG + f'''

[multicard]
bucket_size = {bucket_size}

[[multicard.stages]]
device_id = "device-0"
rknn_path = "/models/seg0.rknn"
weight_path = "/models/seg0.weight"

[[multicard.stages]]
device_id = "device-1"
rknn_path = "/models/seg1.rknn"
weight_path = "/models/seg1.weight"
'''
    path = tmp_path / "gateway.toml"
    path.write_text(config)

    with pytest.raises(RuntimeError, match="multicard.bucket_size"):
        get_settings(path)
