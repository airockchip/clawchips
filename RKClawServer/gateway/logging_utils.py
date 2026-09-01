"""Shared logging helpers for the gateway.

Both :mod:`gateway.app` and :mod:`gateway.service` need to write
detail logs (per-request trace files, logger output) for request
payloads, LLM input/output, and final OpenAI responses.  These helpers
centralise that logic so the two modules stay focused on routing and
orchestration respectively.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config import Settings

logger = logging.getLogger("gateway")

LOG_DIR = Path("logs/requests")
LOG_SEPARATOR = "=" * 80
SERVER_LOG_HANDLER_NAME = "rkclaw-webui-server-log"


# ------------------------------------------------------------------
# Enablement helpers
# ------------------------------------------------------------------

def log_target_enabled(target: str) -> bool:
    """Return *True* when *target* is one of the active log targets."""
    return target in {"logger", "file", "both"}


def detail_logs_enabled(settings: Settings) -> bool:
    """Return *True* when any detail log target is active."""
    return any(
        log_target_enabled(target)
        for target in (
            settings.openai_request_log,
            settings.llm_input_log,
            settings.llm_output_log,
            settings.openai_response_log,
        )
    )


def ensure_detail_req_id(req_id: str, settings: Settings) -> str:
    """Generate a timestamp-based req_id when detail logging is active but no id was provided."""
    if req_id or not detail_logs_enabled(settings):
        return req_id
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def detail_req_id(req_id: str) -> str:
    """Return *req_id* or generate one on the fly."""
    return req_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


# ------------------------------------------------------------------
# Text helpers
# ------------------------------------------------------------------

def truncate_middle(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max_chars // 2
    if keep <= 0:
        return "..."
    return f"{text[:keep]}\n...\n{text[-keep:]}"


def render_log_newlines(value: str) -> str:
    return value.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")


# ------------------------------------------------------------------
# Detail log writers
# ------------------------------------------------------------------

def write_detail_log(label: str, value: str, req_id: str, suffix: str = "txt") -> Path:
    """Append a labelled section to the per-request trace file."""
    trace_id = detail_req_id(req_id)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{trace_id}-trace.log"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(format_detail_section(label, value, trace_id))
    logger.info("%s log written to %s", label, path.resolve())
    return path


def format_detail_section(label: str, value: str, req_id: str) -> str:
    return (
        f"{LOG_SEPARATOR}\n"
        f"[{req_id}] BEGIN {label}\n"
        f"{value}\n"
        f"[{req_id}] END {label}\n"
        f"{LOG_SEPARATOR}\n"
    )


def log_text(
    label: str,
    value: str,
    req_id: str,
    target: str,
    logger_max_chars: int = 0,
) -> None:
    """Log a text payload to logger and/or file depending on *target*."""
    logger_value = render_log_newlines(truncate_middle(value, logger_max_chars))
    if target in {"logger", "both"}:
        logger.debug(
            "\n%s\n[%s] BEGIN %s\n%s\n[%s] END %s\n%s",
            LOG_SEPARATOR,
            req_id,
            label,
            logger_value,
            req_id,
            label,
            LOG_SEPARATOR,
        )
    if target in {"file", "both"}:
        write_detail_log(label, value, req_id, "txt")


def log_json(
    label: str,
    value: dict[str, Any],
    req_id: str,
    target: str,
    logger_max_chars: int = 0,
) -> None:
    """Log a JSON payload (pretty-printed) via :func:`log_text`."""
    log_text(label, json.dumps(value, ensure_ascii=False, indent=2), req_id, target, logger_max_chars)


def log_payload(
    label: str,
    body: bytes,
    req_id: str,
    target: str,
    logger_max_chars: int = 0,
) -> None:
    """Log a raw HTTP body, attempting JSON pretty-print first."""
    try:
        value = json.loads(body)
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except (ValueError, TypeError):
        text = body.decode("utf-8", errors="replace")
    logger_text = truncate_middle(text, logger_max_chars)
    if target in {"logger", "both"}:
        logger.debug(
            "\n%s\n[%s] BEGIN %s\n%s\n[%s] END %s\n%s",
            LOG_SEPARATOR,
            req_id,
            label,
            logger_text,
            req_id,
            label,
            LOG_SEPARATOR,
        )
    if target in {"file", "both"}:
        write_detail_log(label, text, req_id, "json")

def configure_server_log(settings: Settings) -> None:
    """Install or replace the WebUI-readable rotating server log handler."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", "") == SERVER_LOG_HANDLER_NAME:
            root.removeHandler(handler)
            handler.close()
    if not settings.server_log_path:
        return
    path = Path(settings.server_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=settings.server_log_max_bytes,
        backupCount=settings.server_log_backup_count,
        encoding="utf-8",
    )
    handler.name = SERVER_LOG_HANDLER_NAME
    handler.setLevel(logging.DEBUG if settings.debug_logs else logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    root.addHandler(handler)


def server_log_files(settings: Settings) -> list[Path]:
    if not settings.server_log_path:
        return []
    path = Path(settings.server_log_path)
    files = [path]
    files.extend(
        Path(f"{path}.{index}")
        for index in range(1, settings.server_log_backup_count + 1)
    )
    return [candidate for candidate in files if candidate.is_file()]
