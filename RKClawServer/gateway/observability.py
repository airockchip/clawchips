from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

from .config import Settings


_RANGE_SECONDS = {
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}
_TREND_BUCKET_SECONDS = {
    "1h": 60,
    "24h": 15 * 60,
    "7d": 60 * 60,
    "30d": 6 * 60 * 60,
}


class ObservabilityStore:
    """Persistent request metrics and optional full session history."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = Path(settings.webui_data_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._last_cleanup = 0.0
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, timeout=10
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metric_buckets (
                    bucket_start INTEGER PRIMARY KEY,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    cancelled_count INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_latency_ms REAL NOT NULL DEFAULT 0,
                    max_latency_ms REAL NOT NULL DEFAULT 0
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    started_at REAL NOT NULL,
                    last_activity REAL NOT NULL,
                    model TEXT NOT NULL,
                    preview TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_count INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_latency_ms REAL NOT NULL DEFAULT 0,
                    continuation_hash TEXT NOT NULL,
                    continuation_count INTEGER NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS session_requests (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    request_payload BLOB NOT NULL,
                    response_payload BLOB,
                    error TEXT,
                    model_input_payload BLOB,
                    model_output_payload BLOB
                )
                """
            )
            request_columns = {
                str(row["name"])
                for row in self._connection.execute("PRAGMA table_info(session_requests)")
            }
            if "model_input_payload" not in request_columns:
                self._connection.execute(
                    "ALTER TABLE session_requests ADD COLUMN model_input_payload BLOB"
                )
            if "model_output_payload" not in request_columns:
                self._connection.execute(
                    "ALTER TABLE session_requests ADD COLUMN model_output_payload BLOB"
                )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_last_activity
                ON sessions(last_activity DESC)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_continuation
                ON sessions(continuation_hash, continuation_count)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_requests_session_created
                ON session_requests(session_id, created_at)
                """
            )
            self._connection.execute("PRAGMA optimize")
            self._connection.commit()
        self.cleanup()

    def update_settings(self, settings: Settings) -> None:
        self.settings = settings

    def record_request(
        self,
        *,
        request_id: str,
        request_body: dict[str, Any],
        model: str,
        started_at: float,
        latency_ms: float,
        status: str,
        response: Any = None,
        usage: tuple[int, int] | None = None,
        error: str | None = None,
        model_input: str | None = None,
        model_output: str | None = None,
    ) -> None:
        input_tokens, output_tokens = (
            usage if usage is not None else _usage_from_response(response)
        )
        bucket = int(started_at // 60) * 60
        success = int(status == "success")
        cancelled = int(status == "cancelled")
        failed = int(status not in {"success", "cancelled"})
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO metric_buckets (
                    bucket_start, request_count, success_count, error_count,
                    cancelled_count, input_tokens, output_tokens,
                    total_latency_ms, max_latency_ms
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_start) DO UPDATE SET
                    request_count = request_count + 1,
                    success_count = success_count + excluded.success_count,
                    error_count = error_count + excluded.error_count,
                    cancelled_count = cancelled_count + excluded.cancelled_count,
                    input_tokens = input_tokens + excluded.input_tokens,
                    output_tokens = output_tokens + excluded.output_tokens,
                    total_latency_ms = total_latency_ms + excluded.total_latency_ms,
                    max_latency_ms = MAX(max_latency_ms, excluded.max_latency_ms)
                """,
                (
                    bucket,
                    success,
                    failed,
                    cancelled,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    latency_ms,
                ),
            )
            if self.settings.session_logs_enabled:
                self._record_session(
                    request_id=request_id,
                    request_body=request_body,
                    model=model,
                    started_at=started_at,
                    latency_ms=latency_ms,
                    status=status,
                    response=response,
                    error=error,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    model_input=model_input,
                    model_output=model_output,
                )
            self._connection.commit()
        if time.time() - self._last_cleanup >= 3600:
            self.cleanup()

    def _record_session(
        self,
        *,
        request_id: str,
        request_body: dict[str, Any],
        model: str,
        started_at: float,
        latency_ms: float,
        status: str,
        response: Any,
        error: str | None,
        input_tokens: int,
        output_tokens: int,
        model_input: str | None,
        model_output: str | None,
    ) -> None:
        messages = request_body.get("messages")
        if not isinstance(messages, list):
            messages = []
        prefixes = _prefix_fingerprints(messages)
        explicit_session_id = request_body.get("session_id")
        row = None
        if isinstance(explicit_session_id, str) and explicit_session_id:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (explicit_session_id,)
            ).fetchone()
        if row is None and prefixes:
            placeholders = ",".join("?" for _ in prefixes)
            row = self._connection.execute(
                f"""
                SELECT * FROM sessions
                WHERE continuation_hash IN ({placeholders})
                ORDER BY continuation_count DESC, last_activity DESC
                LIMIT 1
                """,
                tuple(prefixes),
            ).fetchone()

        if row is None:
            session_id = (
                explicit_session_id
                if isinstance(explicit_session_id, str) and explicit_session_id
                else uuid.uuid4().hex
            )
            preview = _conversation_preview(messages)
            continuation_hash, continuation_count = _continuation(
                messages, response, status
            )
            self._connection.execute(
                """
                INSERT INTO sessions (
                    id, started_at, last_activity, model, preview, status,
                    request_count, input_tokens, output_tokens, total_latency_ms,
                    continuation_hash, continuation_count
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    started_at,
                    started_at,
                    model,
                    preview,
                    status,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    continuation_hash,
                    continuation_count,
                ),
            )
        else:
            session_id = str(row["id"])
            continuation_hash, continuation_count = _continuation(
                messages, response, status
            )
            self._connection.execute(
                """
                UPDATE sessions SET
                    last_activity = ?,
                    model = ?,
                    status = ?,
                    request_count = request_count + 1,
                    input_tokens = input_tokens + ?,
                    output_tokens = output_tokens + ?,
                    total_latency_ms = total_latency_ms + ?,
                    continuation_hash = ?,
                    continuation_count = ?
                WHERE id = ?
                """,
                (
                    started_at,
                    model,
                    status,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    continuation_hash,
                    continuation_count,
                    session_id,
                ),
            )

        self._connection.execute(
            """
            INSERT OR REPLACE INTO session_requests (
                id, session_id, created_at, model, status, latency_ms,
                input_tokens, output_tokens, request_payload, response_payload,
                error, model_input_payload, model_output_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                session_id,
                started_at,
                model,
                status,
                latency_ms,
                input_tokens,
                output_tokens,
                _pack(request_body),
                _pack(response) if response is not None else None,
                error,
                _pack(model_input) if model_input is not None else None,
                _pack(model_output) if model_output is not None else None,
            ),
        )

    def dashboard(self, range_name: str = "24h") -> dict[str, Any]:
        if range_name not in _RANGE_SECONDS:
            range_name = "24h"
        now = time.time()
        start = now - _RANGE_SECONDS[range_name]
        with self._lock:
            totals = self._connection.execute(
                """
                SELECT
                    COALESCE(SUM(request_count), 0) AS request_count,
                    COALESCE(SUM(success_count), 0) AS success_count,
                    COALESCE(SUM(error_count), 0) AS error_count,
                    COALESCE(SUM(cancelled_count), 0) AS cancelled_count,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(total_latency_ms), 0) AS total_latency_ms,
                    COALESCE(MAX(max_latency_ms), 0) AS max_latency_ms
                FROM metric_buckets
                WHERE bucket_start >= ?
                """,
                (int(start),),
            ).fetchone()
            trend_bucket = _TREND_BUCKET_SECONDS[range_name]
            rows = self._connection.execute(
                """
                SELECT
                    (bucket_start / ?) * ? AS bucket_start,
                    SUM(request_count) AS request_count,
                    SUM(success_count) AS success_count,
                    SUM(error_count) AS error_count,
                    SUM(input_tokens) AS input_tokens,
                    SUM(output_tokens) AS output_tokens,
                    SUM(total_latency_ms) AS total_latency_ms
                FROM metric_buckets
                WHERE bucket_start >= ?
                GROUP BY (bucket_start / ?)
                ORDER BY bucket_start
                """,
                (trend_bucket, trend_bucket, int(start), trend_bucket),
            ).fetchall()
        request_count = int(totals["request_count"])
        total_latency = float(totals["total_latency_ms"])
        success_count = int(totals["success_count"])
        return {
            "range": range_name,
            "totals": {
                "requests": request_count,
                "success": success_count,
                "errors": int(totals["error_count"]),
                "cancelled": int(totals["cancelled_count"]),
                "success_rate": (
                    round(success_count * 100 / request_count, 2)
                    if request_count
                    else 0.0
                ),
                "input_tokens": int(totals["input_tokens"]),
                "output_tokens": int(totals["output_tokens"]),
                "average_latency_ms": (
                    round(total_latency / request_count, 2)
                    if request_count
                    else 0.0
                ),
                "max_latency_ms": round(float(totals["max_latency_ms"]), 2),
            },
            "trend": [
                {
                    "timestamp": int(row["bucket_start"]),
                    "requests": int(row["request_count"]),
                    "success": int(row["success_count"]),
                    "errors": int(row["error_count"]),
                    "input_tokens": int(row["input_tokens"]),
                    "output_tokens": int(row["output_tokens"]),
                    "average_latency_ms": (
                        round(
                            float(row["total_latency_ms"])
                            / int(row["request_count"]),
                            2,
                        )
                        if int(row["request_count"])
                        else 0.0
                    ),
                }
                for row in rows
            ],
        }

    def list_sessions(
        self,
        *,
        cursor: str = "",
        limit: int = 25,
        query: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        offset = _decode_cursor(cursor)
        limit = min(max(limit, 1), 100)
        clauses: list[str] = []
        values: list[Any] = []
        if query:
            clauses.append("(preview LIKE ? OR id LIKE ? OR model LIKE ?)")
            match = f"%{query}%"
            values.extend([match, match, match])
        if status:
            clauses.append("status = ?")
            values.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT * FROM sessions
                {where}
                ORDER BY last_activity DESC
                LIMIT ? OFFSET ?
                """,
                (*values, limit + 1, offset),
            ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "enabled": self.settings.session_logs_enabled,
            "items": [_session_summary(row) for row in rows],
            "next_cursor": _encode_cursor(offset + limit) if has_more else None,
        }

    def session_detail(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None:
                return None
            requests = self._connection.execute(
                """
                SELECT * FROM session_requests
                WHERE session_id = ?
                ORDER BY created_at
                """,
                (session_id,),
            ).fetchall()
        result = _session_summary(session)
        result["requests"] = [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "model": row["model"],
                "status": row["status"],
                "latency_ms": row["latency_ms"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "request": _unpack(row["request_payload"]),
                "response": (
                    _unpack(row["response_payload"])
                    if row["response_payload"] is not None
                    else None
                ),
                "error": row["error"],
                "model_input": (
                    _unpack(row["model_input_payload"])
                    if row["model_input_payload"] is not None
                    else None
                ),
                "model_output": (
                    _unpack(row["model_output_payload"])
                    if row["model_output_payload"] is not None
                    else None
                ),
            }
            for row in requests
        ]
        return result

    def cleanup(self, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        self._last_cleanup = timestamp
        session_cutoff = timestamp - self.settings.session_retention_days * 86400
        stats_cutoff = timestamp - self.settings.webui_stats_retention_days * 86400
        with self._lock:
            self._connection.execute(
                "DELETE FROM sessions WHERE last_activity < ?", (session_cutoff,)
            )
            self._connection.execute(
                "DELETE FROM metric_buckets WHERE bucket_start < ?",
                (int(stats_cutoff),),
            )
            self._connection.execute("PRAGMA optimize")
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _session_summary(row: sqlite3.Row) -> dict[str, Any]:
    count = int(row["request_count"])
    total_latency = float(row["total_latency_ms"])
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "last_activity": row["last_activity"],
        "model": row["model"],
        "preview": row["preview"],
        "status": row["status"],
        "request_count": count,
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "average_latency_ms": round(total_latency / count, 2) if count else 0.0,
    }


def _canonical_messages(messages: list[Any]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        normalized = {
            key: message.get(key)
            for key in ("role", "content", "name", "tool_call_id")
            if key in message
        }
        # Reasoning is intentionally excluded from session identity. Clients
        # may echo it as ``reasoning_content``, rename it to ``reasoning``, or
        # omit it entirely even though the visible conversation is unchanged.
        if "tool_calls" in message:
            normalized["tool_calls"] = _canonical_tool_calls(
                message.get("tool_calls")
            )
        canonical.append(normalized)
    return canonical


def _canonical_tool_calls(value: Any) -> Any:
    """Return semantic tool calls independent of SSE/client framing.

    Streaming responses identify tool-call fragments with ``index`` while the
    assistant message echoed by a client normally omits that field. Merge such
    fragments and retain only fields that are meaningful in message history.
    """
    if not isinstance(value, list):
        return value

    indexed: dict[int, dict[str, Any]] = {}
    unindexed: list[Any] = []
    for raw_call in value:
        if not isinstance(raw_call, dict):
            unindexed.append(raw_call)
            continue
        index = raw_call.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            target = indexed.setdefault(index, {})
            _merge_canonical_tool_call(target, raw_call)
        else:
            target = {}
            _merge_canonical_tool_call(target, raw_call)
            unindexed.append(target)

    if indexed and not unindexed:
        return [indexed[index] for index in sorted(indexed)]
    if not indexed:
        return unindexed
    return [indexed[index] for index in sorted(indexed)] + unindexed


def _merge_canonical_tool_call(
    target: dict[str, Any], raw_call: dict[str, Any]
) -> None:
    for key in ("id", "type"):
        value = raw_call.get(key)
        if key in raw_call and key not in target:
            target[key] = value

    raw_function = raw_call.get("function")
    if not isinstance(raw_function, dict):
        return
    function = target.setdefault("function", {})
    for key in ("name", "arguments"):
        fragment = raw_function.get(key)
        if not isinstance(fragment, str):
            continue
        previous = function.get(key)
        if not isinstance(previous, str) or not previous:
            function[key] = fragment
        elif fragment == previous:
            continue
        elif fragment.startswith(previous):
            # Some parsers emit the full value again as it grows.
            function[key] = fragment
        else:
            # OpenAI-compatible SSE may emit name/argument suffixes.
            function[key] = previous + fragment


def _hash_messages(messages: list[Any]) -> str:
    encoded = json.dumps(
        _canonical_messages(messages),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prefix_fingerprints(messages: list[Any]) -> list[str]:
    return [_hash_messages(messages[:index]) for index in range(1, len(messages) + 1)]


def _continuation(
    messages: list[Any], response: Any, status: str
) -> tuple[str, int]:
    transcript = list(messages)
    if status == "success":
        assistant = _assistant_from_response(response)
        if assistant is not None:
            transcript.append(assistant)
    return _hash_messages(transcript), len(transcript)


def _assistant_from_response(response: Any) -> dict[str, Any] | None:
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message")
            if isinstance(message, dict):
                return message
        return None
    if not isinstance(response, str):
        return None

    content: list[str] = []
    reasoning: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for line in response.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            payload = json.loads(line[6:])
        except (TypeError, ValueError):
            continue
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        delta = choices[0].get("delta", {})
        if not isinstance(delta, dict):
            continue
        if isinstance(delta.get("content"), str):
            content.append(delta["content"])
        if isinstance(delta.get("reasoning_content"), str):
            reasoning.append(delta["reasoning_content"])
        if isinstance(delta.get("tool_calls"), list):
            tool_calls.extend(delta["tool_calls"])
    if not content and not reasoning and not tool_calls:
        return None
    result: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if reasoning:
        result["reasoning_content"] = "".join(reasoning)
    if tool_calls:
        result["tool_calls"] = tool_calls
    return result


def _usage_from_response(response: Any) -> tuple[int, int]:
    payloads: list[dict[str, Any]] = []
    if isinstance(response, dict):
        payloads.append(response)
    elif isinstance(response, str):
        for line in response.splitlines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            try:
                parsed = json.loads(line[6:])
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                payloads.append(parsed)
    for payload in reversed(payloads):
        usage = payload.get("usage")
        if isinstance(usage, dict):
            return (
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
            )
    return 0, 0


def _conversation_preview(messages: list[Any]) -> str:
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                compact = " ".join(content.split())
                return compact[:160] or "(empty user message)"
    return "(no user message)"


def _pack(value: Any) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return zlib.compress(raw, level=6)


def _unpack(value: bytes) -> Any:
    return json.loads(zlib.decompress(value).decode("utf-8"))


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> int:
    if not value:
        return 0
    try:
        padding = "=" * (-len(value) % 4)
        return max(0, int(base64.urlsafe_b64decode(value + padding)))
    except (TypeError, ValueError):
        return 0
