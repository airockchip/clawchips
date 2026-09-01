from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .native_library import resolve_native_library


DEFAULT_CONFIG_PATH = Path("gateway.toml")


@dataclass(frozen=True)
class MulticardStageSettings:
    device_id: str
    rknn_path: str
    weight_path: str
    output_tensor_name: str = ""


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8080
    queue_size: int = 32
    enable_streaming: bool = True
    sse_heartbeat_interval_s: float = 20.0
    debug_logs: bool = False
    openai_request_log: str = ""
    llm_input_log: str = ""
    llm_output_log: str = ""
    openai_response_log: str = ""
    logger_detail_log_max_chars: int = 0
    session_logs_enabled: bool = False
    session_retention_days: int = 30
    server_log_path: str = ""
    server_log_max_bytes: int = 10 * 1024 * 1024
    server_log_backup_count: int = 5
    webui_enabled: bool = False
    webui_auth_token: str = ""
    webui_data_path: str = "logs/webui.sqlite3"
    webui_stats_retention_days: int = 90
    webui_reload_drain_timeout_s: float = 300.0
    webui_session_cookie_ttl_s: int = 8 * 60 * 60
    runtime_target: str = "rk1820"
    device_id: str = ""
    core_mask: int = 0xFF
    toolkit_lite_wheel: str = ""
    tokenizer_library: str = field(default_factory=resolve_native_library)
    sampling_library: str = field(default_factory=resolve_native_library)
    model_id: str = "Qwen3-4B-Instruct"
    rknn_path: str = ""
    weight_path: str = ""
    tokenizer_path: str = ""
    embed_path: str = ""
    per_layer_embed_path: str = ""
    rope_cache_path: str = ""
    max_context_tokens: int = 32768
    max_new_tokens: int = 4096
    enable_thinking: bool = True
    clear_kv_cache: bool = False
    kv_cache_dir: str = ""
    kv_cache_system_marker: str = ""
    checkpoint_enabled: bool = True
    checkpoint_start_pos: int = 10240
    checkpoint_interval: int = 1024
    checkpoint_max_count: int = 18
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 1
    repeat_penalty: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    separate_reasoning: bool = True
    fallback_delimiter: str = ""
    enable_tool_call_correction: bool = False
    enable_xgrammar: bool = False
    xgrammar_model_structure: str = "qwen3"
    xgrammar_debug: bool = False
    enable_native_sampling: bool = False
    native_sampling_seed: int = -1
    native_repeat_last_n: int = 64
    native_penalize_newline: bool = False
    multicard_bucket_size: int = 128
    multicard_stages: tuple[MulticardStageSettings, ...] = ()

    def __post_init__(self) -> None:
        default_detail_log = "logger" if self.debug_logs else "off"
        for name in ("openai_request_log", "llm_input_log", "llm_output_log", "openai_response_log"):
            value = getattr(self, name)
            normalized = default_detail_log if value == "" else parse_log_target(value, default_detail_log)
            object.__setattr__(self, name, normalized)

    @property
    def multicard_enabled(self) -> bool:
        return len(self.multicard_stages) >= 2

    def checkpoint_policy_values(self, max_context: int) -> tuple[int, int, int]:
        """Return checkpoint values bounded to the model context window."""
        interval = min(self.checkpoint_interval, max(1, max_context // 4))
        start_pos = self.checkpoint_start_pos
        if start_pos >= max_context - interval:
            start_pos = interval
        max_count = max(
            1,
            min(
                self.checkpoint_max_count,
                (max_context - start_pos) // interval,
            ),
        )
        return start_pos, interval, max_count


def get_settings(config_path: str | Path = DEFAULT_CONFIG_PATH) -> Settings:
    return settings_from_config(load_config(config_path))


def settings_from_text(text: str) -> Settings:
    try:
        config = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"Invalid TOML configuration: {exc}") from exc
    return settings_from_config(config)


def settings_from_config(config: dict[str, Any]) -> Settings:
    server = section(config, "server")
    runtime = section(config, "runtime")
    model = section(config, "model")
    sampling = section(config, "sampling")
    reasoning = section(config, "reasoning")
    tool_call_correction = section(config, "tool_call_correction")
    xgrammar = section(config, "xgrammar")
    native_sampling = section(config, "native_sampling")
    checkpoint = section(config, "checkpoint")
    logging_config = section(config, "logging")
    webui = section(config, "webui")
    multicard = section(config, "multicard")
    multicard_stages = parse_multicard_stages(multicard.get("stages"))

    native_library = parse_str(runtime.get("native_library"), "")

    required = {
        "model.tokenizer_path": parse_str(model.get("tokenizer_path"), ""),
        "model.embed_path": parse_str(model.get("embed_path"), ""),
    }
    if not multicard_stages:
        required.update({
            "model.rknn_path": parse_str(model.get("rknn_path"), ""),
            "model.weight_path": parse_str(model.get("weight_path"), ""),
        })
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")

    debug_logs = parse_bool(logging_config.get("debug_logs"))
    default_detail_log = "logger" if debug_logs else "off"

    webui_enabled = parse_bool(webui.get("enabled"), False)
    webui_auth_token = os.environ.get("RKCLAW_WEBUI_TOKEN") or parse_str(
        webui.get("auth_token"), ""
    )
    if webui_enabled and not webui_auth_token:
        raise RuntimeError(
            "webui.enabled requires RKCLAW_WEBUI_TOKEN or webui.auth_token"
        )

    return Settings(
        host=parse_str(server.get("host"), "127.0.0.1"),
        port=parse_int(server.get("port"), 8080, 1, 65535),
        queue_size=parse_int(server.get("queue_size"), 32, 1),
        enable_streaming=parse_bool(server.get("enable_streaming"), True),
        sse_heartbeat_interval_s=parse_float(
            server.get("sse_heartbeat_interval_s"), 20.0, 1.0, 300.0),
        debug_logs=debug_logs,
        openai_request_log=parse_log_target(logging_config.get("openai_request_log"), default_detail_log),
        llm_input_log=parse_log_target(logging_config.get("llm_input_log"), default_detail_log),
        llm_output_log=parse_log_target(logging_config.get("llm_output_log"), default_detail_log),
        openai_response_log=parse_log_target(logging_config.get("openai_response_log"), default_detail_log),
        logger_detail_log_max_chars=parse_int(logging_config.get("logger_detail_log_max_chars"), 0, 0),
        session_logs_enabled=parse_bool(logging_config.get("session_logs_enabled"), False),
        session_retention_days=parse_int(logging_config.get("session_retention_days"), 30, 1),
        server_log_path=parse_str(logging_config.get("server_log_path"), ""),
        server_log_max_bytes=parse_int(
            logging_config.get("server_log_max_bytes"), 10 * 1024 * 1024, 1024
        ),
        server_log_backup_count=parse_int(
            logging_config.get("server_log_backup_count"), 5, 1, 100
        ),
        webui_enabled=webui_enabled,
        webui_auth_token=webui_auth_token,
        webui_data_path=parse_str(webui.get("data_path"), "logs/webui.sqlite3"),
        webui_stats_retention_days=parse_int(
            webui.get("stats_retention_days"), 90, 1
        ),
        webui_reload_drain_timeout_s=parse_float(
            webui.get("reload_drain_timeout_s"), 300.0, 1.0, 3600.0
        ),
        webui_session_cookie_ttl_s=parse_int(
            webui.get("session_cookie_ttl_s"),
            8 * 60 * 60,
            300,
            7 * 24 * 60 * 60,
        ),
        runtime_target=parse_str(runtime.get("target"), "rk1820"),
        device_id=parse_str(runtime.get("device_id"), ""),
        core_mask=parse_int(runtime.get("core_mask"), 0xFF, 1),
        toolkit_lite_wheel=parse_str(runtime.get("toolkit_lite_wheel"), ""),
        tokenizer_library=resolve_native_library(native_library),
        sampling_library=resolve_native_library(native_library),
        model_id=parse_str(model.get("id"), "Qwen3-4B-Instruct"),
        rknn_path=parse_str(model.get("rknn_path"), ""),
        weight_path=parse_str(model.get("weight_path"), ""),
        tokenizer_path=required["model.tokenizer_path"],
        embed_path=required["model.embed_path"],
        per_layer_embed_path=parse_str(model.get("per_layer_embed_path"), ""),
        rope_cache_path=parse_str(model.get("rope_cache_path"), ""),
        max_context_tokens=parse_int(model.get("max_context_tokens"), 32768, 1),
        max_new_tokens=parse_int(model.get("max_new_tokens"), 4096, 1),
        enable_thinking=parse_bool(model.get("enable_thinking"), True),
        clear_kv_cache=parse_bool(model.get("clear_kv_cache"), False),
        kv_cache_dir=parse_str(model.get("kv_cache_dir"), "/userdata/RKClawServer/kv_cache"),
        kv_cache_system_marker=parse_str(model.get("kv_cache_system_marker"), ""),
        checkpoint_enabled=parse_bool(checkpoint.get("enabled"), True),
        checkpoint_start_pos=parse_int(checkpoint.get("start_pos"), 10240, 0),
        checkpoint_interval=parse_int(checkpoint.get("interval"), 1024, 1),
        checkpoint_max_count=parse_int(checkpoint.get("max_count"), 18, 1),
        temperature=parse_float(sampling.get("temperature"), 0.7, 0.0),
        top_p=parse_float(sampling.get("top_p"), 0.9, 0.0, 1.0),
        top_k=parse_int(sampling.get("top_k"), 1, 0),
        repeat_penalty=parse_float(sampling.get("repeat_penalty"), 1.0, 0.0),
        frequency_penalty=parse_float(sampling.get("frequency_penalty"), 0.0, -2.0, 2.0),
        presence_penalty=parse_float(sampling.get("presence_penalty"), 0.0, -2.0, 2.0),
        separate_reasoning=parse_bool(reasoning.get("separate_output"), True),
        fallback_delimiter=parse_str(reasoning.get("fallback_delimiter"), ""),
        enable_tool_call_correction=parse_bool(tool_call_correction.get("enabled"), False),
        enable_xgrammar=parse_bool(xgrammar.get("enabled"), False),
        xgrammar_model_structure=parse_model_structure(xgrammar.get("model_structure")),
        xgrammar_debug=parse_bool(xgrammar.get("debug"), False),
        enable_native_sampling=parse_bool(native_sampling.get("enabled"), False),
        native_sampling_seed=parse_int(native_sampling.get("seed"), -1),
        native_repeat_last_n=parse_int(native_sampling.get("repeat_last_n"), 64, 0),
        native_penalize_newline=parse_bool(
            native_sampling.get("penalize_newline"), False),
        multicard_bucket_size=parse_positive_int(
            multicard.get("bucket_size"),
            128,
            "multicard.bucket_size",
        ),
        multicard_stages=multicard_stages,
    )


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise RuntimeError(f"Configuration file not found: {path}")
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(f"Invalid configuration file {path}: {exc}") from exc


def section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name, {})
    if not isinstance(value, dict):
        raise RuntimeError(f"Configuration section [{name}] must be a table")
    return value


def parse_multicard_stages(value: Any) -> tuple[MulticardStageSettings, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RuntimeError("multicard.stages must be an array of tables")
    if len(value) < 2:
        raise RuntimeError("multicard.stages must contain at least two stages")

    stages: list[MulticardStageSettings] = []
    for index, raw_stage in enumerate(value):
        if not isinstance(raw_stage, dict):
            raise RuntimeError(f"multicard.stages[{index}] must be a table")
        stage = MulticardStageSettings(
            device_id=parse_str(raw_stage.get("device_id"), "").strip(),
            rknn_path=parse_str(raw_stage.get("rknn_path"), "").strip(),
            weight_path=parse_str(raw_stage.get("weight_path"), "").strip(),
            output_tensor_name=parse_str(raw_stage.get("output_tensor_name"), "").strip(),
        )
        missing = [
            name
            for name, field_value in (
                ("device_id", stage.device_id),
                ("rknn_path", stage.rknn_path),
                ("weight_path", stage.weight_path),
            )
            if not field_value
        ]
        if missing:
            joined = ", ".join(f"multicard.stages[{index}].{name}" for name in missing)
            raise RuntimeError(f"Missing required configuration: {joined}")
        stages.append(stage)

    device_ids = [stage.device_id for stage in stages]
    if len(set(device_ids)) != len(device_ids):
        raise RuntimeError("multicard.stages device_id values must be unique")
    return tuple(stages)


def parse_str(value: Any, default: str) -> str:
    return default if value is None else str(value)


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_model_structure(value: Any) -> str:
    model_structure = parse_str(value, "qwen3").strip().lower()
    if model_structure not in {"qwen3", "qwen3.5"}:
        raise RuntimeError("xgrammar.model_structure must be one of: qwen3, qwen3.5")
    return model_structure


def parse_log_target(value: Any, default: str) -> str:
    target = default if value is None else str(value).strip().lower()
    if target not in {"off", "logger", "file", "both"}:
        raise RuntimeError("Log target must be one of: off, logger, file, both")
    return target


def parse_int(value: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(str(value), 0) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def parse_positive_int(value: Any, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(str(value), 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return parsed


def parse_float(value: Any, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed
