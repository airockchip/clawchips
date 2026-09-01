from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings_from_text
from .logging_utils import server_log_files
from .management import (
    COOKIE_NAME,
    ConfigurationConflict,
    ConfigFile,
    LoginRateLimited,
    ManagementAuthError,
    REDACTED_TOKEN,
    ServiceSupervisor,
    WebUIAuth,
)


_ASSET_DIR = Path(__file__).with_name("webui_assets")
_PROCESS_FIELDS = {
    "host": "server.host",
    "port": "server.port",
    "webui_enabled": "webui.enabled",
    "webui_auth_token": "webui.auth_token",
    "webui_data_path": "webui.data_path",
}
_LOG_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[^ ]*\s+[^ ]+)\s+"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"\[(?P<logger>[^]]+)\]\s*(?P<message>.*)$"
)
_RKNN_DEVICE_RE = re.compile(
    r"\|\s*\d+\s+(?P<name>RK\d+)\s*\|\s*(?P<id>[0-9A-Fa-f]{4}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.\d+)\s*\|"
)


def register_webui(
    application: FastAPI,
    supervisor: ServiceSupervisor,
    auth: WebUIAuth,
    config_file: ConfigFile | None,
) -> None:
    application.mount(
        "/webui/assets",
        StaticFiles(directory=_ASSET_DIR),
        name="webui-assets",
    )

    @application.get("/webui", include_in_schema=False)
    @application.get("/webui/", include_in_schema=False)
    async def webui_index() -> FileResponse:
        return FileResponse(
            _ASSET_DIR / "index.html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @application.post("/api/webui/auth/login")
    async def login(request: Request):
        body = await _json_body(request)
        token = body.get("token")
        if not isinstance(token, str):
            return _error("invalid_request", "token is required", 400)
        try:
            session, csrf = auth.login(token, _client_ip(request))
        except LoginRateLimited as exc:
            return _error("rate_limited", str(exc), 429)
        except ManagementAuthError as exc:
            return _error("invalid_credentials", str(exc), 401)
        response = JSONResponse({"authenticated": True, "csrf_token": csrf})
        response.set_cookie(
            COOKIE_NAME,
            session,
            max_age=auth.settings.webui_session_cookie_ttl_s,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return response

    @application.post("/api/webui/auth/logout")
    async def logout(request: Request):
        rejected = _authorize(auth, request, csrf=True)
        if rejected:
            return rejected
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @application.get("/api/webui/auth/session")
    async def auth_session(request: Request):
        rejected = _authorize(auth, request)
        if rejected:
            return rejected
        session = request.cookies.get(COOKIE_NAME, "")
        return {
            "authenticated": True,
            "csrf_token": auth.csrf_for(session),
            "expires_in_s": auth.settings.webui_session_cookie_ttl_s,
        }

    @application.get("/api/webui/dashboard")
    async def dashboard(request: Request, range: str = "24h"):
        rejected = _authorize(auth, request)
        if rejected:
            return rejected
        payload = (
            supervisor.store.dashboard(range)
            if supervisor.store is not None
            else {"range": range, "totals": {}, "trend": []}
        )
        payload["server"] = supervisor.status_payload()
        return payload

    @application.get("/api/webui/sessions")
    async def sessions(
        request: Request,
        cursor: str = "",
        limit: int = 25,
        query: str = "",
        status: str = "",
    ):
        rejected = _authorize(auth, request)
        if rejected:
            return rejected
        if supervisor.store is None:
            return {"enabled": False, "items": [], "next_cursor": None}
        return supervisor.store.list_sessions(
            cursor=cursor, limit=limit, query=query, status=status
        )

    @application.get("/api/webui/sessions/{session_id}")
    async def session_detail(request: Request, session_id: str):
        rejected = _authorize(auth, request)
        if rejected:
            return rejected
        if supervisor.store is None:
            return _error("not_found", "Session not found", 404)
        item = supervisor.store.session_detail(session_id)
        if item is None:
            return _error("not_found", "Session not found", 404)
        return item

    @application.get("/api/webui/server-logs")
    async def server_logs(
        request: Request,
        cursor: str = "",
        limit: int = 250,
        level: str = "",
        query: str = "",
    ):
        rejected = _authorize(auth, request)
        if rejected:
            return rejected
        return _read_server_logs(
            supervisor.settings,
            cursor=cursor,
            limit=limit,
            level=level,
            query=query,
        )

    @application.get("/api/webui/config")
    async def get_config(request: Request):
        rejected = _authorize(auth, request)
        if rejected:
            return rejected
        if config_file is None:
            return _error("unavailable", "Configuration file is unavailable", 503)
        try:
            payload = config_file.payload()
        except Exception as exc:
            return _error("config_error", str(exc), 500)
        payload["active_model"] = supervisor.settings.model_id
        payload["saved_is_active"] = (
            supervisor._active_config_text is not None
            and hashlib.sha256(
                supervisor._active_config_text.encode("utf-8")
            ).hexdigest()
            == payload["revision"]
        )
        return payload

    @application.get("/api/webui/files")
    async def browse_files(request: Request, path: str = "/userdata"):
        rejected = _authorize(auth, request)
        if rejected:
            return rejected
        try:
            return await asyncio.to_thread(_browse_files, path)
        except ValueError as exc:
            return _error("invalid_path", str(exc), 400)
        except FileNotFoundError:
            return _error("not_found", f"Path does not exist: {path}", 404)
        except NotADirectoryError:
            return _error("not_a_directory", f"Path is not a directory: {path}", 400)
        except PermissionError:
            return _error("permission_denied", f"Permission denied: {path}", 403)
        except OSError as exc:
            return _error("filesystem_error", str(exc), 500)

    @application.get("/api/webui/devices")
    async def devices(request: Request):
        rejected = _authorize(auth, request)
        if rejected:
            return rejected
        try:
            return {"devices": await asyncio.to_thread(_discover_rknn_devices)}
        except (OSError, subprocess.SubprocessError) as exc:
            return _error("device_discovery_failed", str(exc), 503)

    @application.post("/api/webui/config/validate")
    async def validate_config(request: Request):
        rejected = _authorize(auth, request, csrf=True)
        if rejected:
            return rejected
        if config_file is None:
            return _error("unavailable", "Configuration file is unavailable", 503)
        body = await _json_body(request)
        text = body.get("toml")
        if not isinstance(text, str):
            return _error("invalid_request", "toml is required", 400)
        try:
            settings, warnings = config_file.validate(text)
        except Exception as exc:
            return _error("invalid_config", str(exc), 422)
        structured = dataclasses.asdict(settings)
        structured["webui_auth_token"] = REDACTED_TOKEN
        return {"valid": True, "warnings": warnings, "structured": structured}

    @application.put("/api/webui/config")
    async def save_config(request: Request):
        rejected = _authorize(auth, request, csrf=True)
        if rejected:
            return rejected
        if config_file is None:
            return _error("unavailable", "Configuration file is unavailable", 503)
        body = await _json_body(request)
        text = body.get("toml")
        revision = body.get("revision")
        if not isinstance(text, str) or not isinstance(revision, str):
            return _error("invalid_request", "toml and revision are required", 400)
        try:
            candidate, _ = config_file.validate(text)
            current = supervisor.settings
            restart_required = [
                display
                for field, display in _PROCESS_FIELDS.items()
                if getattr(current, field) != getattr(candidate, field)
            ]
            result = config_file.save(text, revision)
        except ConfigurationConflict as exc:
            return _error("config_conflict", str(exc), 409)
        except Exception as exc:
            return _error("invalid_config", str(exc), 422)
        result["restart_required_fields"] = restart_required
        result["saved_is_active"] = False
        return result

    @application.post("/api/webui/reloads")
    async def start_reload(request: Request):
        rejected = _authorize(auth, request, csrf=True)
        if rejected:
            return rejected
        try:
            operation = supervisor.start_reload()
        except ConfigurationConflict as exc:
            return _error("reload_conflict", str(exc), 409)
        except Exception as exc:
            return _error("reload_error", str(exc), 503)
        return JSONResponse(operation.to_dict(), status_code=202)

    @application.get("/api/webui/reloads/{operation_id}")
    async def reload_status(request: Request, operation_id: str):
        rejected = _authorize(auth, request)
        if rejected:
            return rejected
        operation = supervisor.reload_status(operation_id)
        if operation is None:
            return _error("not_found", "Reload operation not found", 404)
        return operation.to_dict()


def _authorize(
    auth: WebUIAuth, request: Request, *, csrf: bool = False
) -> JSONResponse | None:
    try:
        auth.verify(request, csrf=csrf)
    except ManagementAuthError as exc:
        return _error("unauthorized", str(exc), 401)
    return None


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _browse_files(path_text: str) -> dict[str, Any]:
    if not path_text:
        path_text = "/"
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise ValueError("Path must be absolute")
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise NotADirectoryError(str(path))

    entries: list[dict[str, Any]] = []
    for child in path.iterdir():
        try:
            stat = child.stat()
            is_dir = child.is_dir()
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "path": str(child),
            "is_dir": is_dir,
            "size": 0 if is_dir else stat.st_size,
        })
    entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    truncated = len(entries) > 500
    return {
        "path": str(path),
        "parent": str(path.parent),
        "entries": entries[:500],
        "truncated": truncated,
    }


def _discover_rknn_devices() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["rknn-smi", "info"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OSError("rknn-smi is not installed") from exc

    devices: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _RKNN_DEVICE_RE.finditer(result.stdout):
        device_id = match.group("id")
        if device_id in seen:
            continue
        seen.add(device_id)
        name = match.group("name")
        devices.append({
            "id": device_id,
            "name": name,
            "label": f"{name} · {device_id}",
        })
    if not devices and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise OSError(detail or f"rknn-smi exited with code {result.returncode}")
    return devices


def _read_server_logs(
    settings: Any,
    *,
    cursor: str,
    limit: int,
    level: str,
    query: str,
) -> dict[str, Any]:
    paths = list(reversed(server_log_files(settings)))
    if not paths:
        return {
            "enabled": bool(settings.server_log_path),
            "lines": [],
            "next_cursor": "",
        }
    chunks: list[bytes] = []
    signature_parts: list[str] = []
    for path in paths:
        stat = path.stat()
        signature_parts.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
        with path.open("rb") as handle:
            if stat.st_size > 4 * 1024 * 1024:
                handle.seek(stat.st_size - 4 * 1024 * 1024)
                handle.readline()
            chunks.append(handle.read())
    blob = b"\n".join(chunks)
    signature = hashlib.sha256("|".join(signature_parts).encode()).hexdigest()[:16]
    saved_signature, offset = _decode_log_cursor(cursor)
    if saved_signature != signature or offset > len(blob):
        offset = max(0, len(blob) - 512 * 1024)
        if offset:
            boundary = blob.find(b"\n", offset)
            offset = boundary + 1 if boundary >= 0 else 0
    raw_lines = blob[offset:].decode("utf-8", errors="replace").splitlines()
    level = level.upper()
    query_lower = query.lower()
    parsed: list[dict[str, str]] = []
    for line in raw_lines:
        match = _LOG_RE.match(line)
        item = (
            match.groupdict()
            if match
            else {
                "timestamp": "",
                "level": "",
                "logger": "",
                "message": line,
            }
        )
        if level and item["level"] != level:
            continue
        if query_lower and query_lower not in line.lower():
            continue
        parsed.append(item)
    limit = min(max(limit, 1), 500)
    return {
        "enabled": True,
        "lines": parsed[-limit:],
        "next_cursor": _encode_log_cursor(signature, len(blob)),
    }


def _encode_log_cursor(signature: str, offset: int) -> str:
    raw = json.dumps([signature, offset], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_log_cursor(cursor: str) -> tuple[str, int]:
    if not cursor:
        return "", 0
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        signature, offset = json.loads(raw)
        return str(signature), max(0, int(offset))
    except (TypeError, ValueError):
        return "", 0
