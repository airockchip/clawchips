from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

import asyncio
import base64
import binascii
import dataclasses
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request

from .config import Settings, settings_from_text
from .logging_utils import configure_server_log
from .observability import ObservabilityStore
from .service import GatewayService


REDACTED_TOKEN = "__RKCLAW_REDACTED__"
COOKIE_NAME = "rkclaw_webui_session"


class ManagementAuthError(RuntimeError):
    pass


class ServiceReloading(RuntimeError):
    pass


class ConfigurationConflict(RuntimeError):
    pass


class LoginRateLimited(RuntimeError):
    pass


class WebUIAuth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def login(self, token: str, client_ip: str) -> tuple[str, str]:
        now = time.time()
        attempts = self._attempts[client_ip]
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 5:
            raise LoginRateLimited("Too many login attempts; try again later")
        if not hmac.compare_digest(
            token.encode("utf-8"),
            self.settings.webui_auth_token.encode("utf-8"),
        ):
            attempts.append(now)
            raise ManagementAuthError("Invalid administrator token")
        attempts.clear()
        expires = int(now + self.settings.webui_session_cookie_ttl_s)
        nonce = uuid.uuid4().hex
        payload = f"{expires}:{nonce}"
        signature = hmac.new(
            self.settings.webui_auth_token.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        session = _b64(f"{payload}:{signature}".encode("utf-8"))
        csrf = self.csrf_for(session)
        return session, csrf

    def verify(self, request: Request, *, csrf: bool = False) -> str:
        session = request.cookies.get(COOKIE_NAME, "")
        if not self._valid_session(session):
            raise ManagementAuthError("Authentication required")
        if csrf:
            expected = self.csrf_for(session)
            supplied = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(supplied, expected):
                raise ManagementAuthError("Invalid CSRF token")
            origin = request.headers.get("origin")
            if origin:
                expected_origin = f"{request.url.scheme}://{request.url.netloc}"
                if origin.rstrip("/") != expected_origin.rstrip("/"):
                    raise ManagementAuthError("Cross-origin request rejected")
        return session

    def csrf_for(self, session: str) -> str:
        return hmac.new(
            self.settings.webui_auth_token.encode("utf-8"),
            ("csrf:" + session).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _valid_session(self, value: str) -> bool:
        try:
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            expires_text, nonce, signature = decoded.decode("utf-8").split(":", 2)
            payload = f"{expires_text}:{nonce}"
            expected = hmac.new(
                self.settings.webui_auth_token.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            return int(expires_text) >= int(time.time()) and hmac.compare_digest(
                signature, expected
            )
        except (TypeError, ValueError, UnicodeError, binascii.Error):
            return False


class ConfigFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read(self) -> tuple[str, str]:
        text = self.path.read_text(encoding="utf-8")
        return text, _revision(text)

    def payload(self) -> dict[str, Any]:
        text, revision = self.read()
        settings = settings_from_text(text)
        structured = dataclasses.asdict(settings)
        structured["webui_auth_token"] = REDACTED_TOKEN
        raw = tomllib.loads(text)
        runtime = raw.get("runtime", {})
        model = raw.get("model", {})
        structured["native_library"] = str(runtime.get("native_library", ""))
        structured["chat_template_file"] = str(model.get("chat_template_file", ""))
        return {
            "toml": redact_token(text),
            "revision": revision,
            "structured": structured,
            "restart_required_fields": [],
        }

    def validate(self, text: str) -> tuple[Settings, list[str]]:
        current, _ = self.read()
        restored = restore_redacted_token(text, current)
        settings = settings_from_text(restored)
        warnings: list[str] = []
        paths = {
            "model.rknn_path": settings.rknn_path,
            "model.weight_path": settings.weight_path,
            "model.tokenizer_path": settings.tokenizer_path,
            "model.embed_path": settings.embed_path,
        }
        if settings.multicard_enabled:
            paths.pop("model.rknn_path", None)
            paths.pop("model.weight_path", None)
            for index, stage in enumerate(settings.multicard_stages):
                paths[f"multicard.stages[{index}].rknn_path"] = stage.rknn_path
                paths[f"multicard.stages[{index}].weight_path"] = stage.weight_path
        for name, value in paths.items():
            if value and not Path(value).is_file():
                warnings.append(f"{name} does not exist on this host: {value}")
        return settings, warnings

    def save(self, text: str, expected_revision: str) -> dict[str, Any]:
        current, revision = self.read()
        if revision != expected_revision:
            raise ConfigurationConflict(
                "Configuration changed since it was opened; reload before saving"
            )
        restored = restore_redacted_token(text, current)
        settings, warnings = self.validate(restored)
        del settings
        backup = self._archive_path("bak")
        shutil.copy2(self.path, backup)
        _atomic_write(self.path, restored)
        return {
            "revision": _revision(restored),
            "warnings": warnings,
            "backup": str(backup),
        }

    def restore(self, text: str) -> None:
        _atomic_write(self.path, text)

    def preserve_failed(self, text: str) -> str:
        path = self._archive_path("failed.toml")
        _atomic_write(path, text)
        return str(path)

    def _archive_path(self, suffix: str) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        unique = uuid.uuid4().hex[:8]
        return self.path.with_name(f"{self.path.name}.{stamp}-{unique}.{suffix}")


@dataclass
class ReloadOperation:
    id: str
    status: str
    stage: str
    started_at: float
    finished_at: float | None = None
    error: str | None = None
    rollback: str | None = None
    failed_config: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class ServiceSupervisor:
    def __init__(
        self,
        settings: Settings,
        service: GatewayService,
        *,
        config_file: ConfigFile | None = None,
        store: ObservabilityStore | None = None,
        service_factory: Callable[[Settings], GatewayService] = GatewayService,
    ):
        self.settings = settings
        self.service = service
        self.config_file = config_file
        self.store = store
        self.service_factory = service_factory
        self.created = int(time.time())
        self.state = "starting"
        self.last_error: str | None = None
        self._accepting = False
        self._inflight = 0
        self._condition = asyncio.Condition()
        self._request_tasks: set[asyncio.Task[Any]] = set()
        self._reload_lock = asyncio.Lock()
        self._reload_task: asyncio.Task[None] | None = None
        self._operations: dict[str, ReloadOperation] = {}
        self._active_config_text: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready" and self.service.ready

    @property
    def active_requests(self) -> int:
        return min(self._inflight, 1)

    @property
    def queued_requests(self) -> int:
        return max(0, self._inflight - 1)

    async def start(self) -> None:
        if self.config_file is not None and self.config_file.path.exists():
            self._active_config_text, _ = self.config_file.read()
        await self.service.start()
        self.state = "ready"
        self._accepting = True

    async def close(self) -> None:
        self._accepting = False
        if self._reload_task is not None and not self._reload_task.done():
            self._reload_task.cancel()
            await asyncio.gather(self._reload_task, return_exceptions=True)
        await self.service.close()
        if self.store is not None:
            await asyncio.to_thread(self.store.close)
        self.state = "stopped"

    async def complete(
        self,
        request: Any,
        request_body: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        await self._enter_request()
        started = time.time()
        response: dict[str, Any] | None = None
        try:
            response = await self.service.complete(request, request_id)
            await self._record(
                request_id,
                request_body,
                started,
                "success",
                response=response,
            )
            return response
        except asyncio.CancelledError:
            await self._record(
                request_id, request_body, started, "cancelled", error="cancelled"
            )
            raise
        except Exception as exc:
            await self._record(
                request_id, request_body, started, "error", error=str(exc)
            )
            raise
        finally:
            await self._leave_request()

    def stream(
        self,
        request: Any,
        request_body: dict[str, Any],
        request_id: str,
    ) -> AsyncIterator[bytes]:
        return self._stream(request, request_body, request_id)

    async def _stream(
        self,
        request: Any,
        request_body: dict[str, Any],
        request_id: str,
    ) -> AsyncIterator[bytes]:
        await self._enter_request()
        started = time.time()
        chunks: list[bytes] = []
        status = "success"
        completed = False
        error: str | None = None
        try:
            events = self.service.stream(request, request_id)
            async for chunk in events:
                chunks.append(chunk)
                yield chunk
            completed = True
        except asyncio.CancelledError:
            completed = True
            status = "cancelled"
            error = "cancelled"
            raise
        except Exception as exc:
            status = "error"
            error = str(exc)
            raise
        finally:
            if status == "success" and not completed:
                status = "cancelled"
                error = "client disconnected"
            await self._record(
                request_id,
                request_body,
                started,
                status,
                response=b"".join(chunks).decode("utf-8", errors="replace"),
                error=error,
            )
            await self._leave_request()

    async def _enter_request(self) -> None:
        async with self._condition:
            if not self._accepting or self.state != "ready":
                raise ServiceReloading("The model service is reloading")
            self._inflight += 1
            task = asyncio.current_task()
            if task is not None:
                self._request_tasks.add(task)

    async def _leave_request(self) -> None:
        async with self._condition:
            task = asyncio.current_task()
            if task is not None:
                self._request_tasks.discard(task)
            self._inflight = max(0, self._inflight - 1)
            if self._inflight == 0:
                self._condition.notify_all()

    async def _record(
        self,
        request_id: str,
        request_body: dict[str, Any],
        started: float,
        status: str,
        *,
        response: Any = None,
        error: str | None = None,
    ) -> None:
        captured_usage = self.service.take_usage(request_id)
        take_model_trace = getattr(self.service, "take_model_trace", None)
        model_trace = take_model_trace(request_id) if callable(take_model_trace) else None
        if self.store is None:
            return
        usage = (
            (captured_usage.prompt_tokens, captured_usage.completion_tokens)
            if captured_usage is not None
            else None
        )
        await asyncio.to_thread(
            self.store.record_request,
            request_id=request_id,
            request_body=request_body,
            model=self.settings.model_id,
            started_at=started,
            latency_ms=(time.time() - started) * 1000,
            status=status,
            response=response,
            error=error,
            usage=usage,
            model_input=model_trace.get("input") if model_trace else None,
            model_output=model_trace.get("output") if model_trace else None,
        )

    def status_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "ready": self.ready,
            "model": self.settings.model_id,
            "created": self.created,
            "uptime_s": max(0, int(time.time()) - self.created),
            "active_requests": self.active_requests,
            "queued_requests": self.queued_requests,
            "last_error": self.last_error,
        }

    def start_reload(self) -> ReloadOperation:
        if self.config_file is None:
            raise RuntimeError("Runtime configuration file is unavailable")
        if self._reload_task is not None and not self._reload_task.done():
            raise ConfigurationConflict("A model reload is already running")
        operation = ReloadOperation(
            id=uuid.uuid4().hex,
            status="running",
            stage="validating",
            started_at=time.time(),
        )
        self._operations[operation.id] = operation
        self._reload_task = asyncio.create_task(self._reload(operation))
        return operation

    def reload_status(self, operation_id: str) -> ReloadOperation | None:
        return self._operations.get(operation_id)

    async def _reload(self, operation: ReloadOperation) -> None:
        async with self._reload_lock:
            previous_text = self._active_config_text
            previous_settings = self.settings
            old_service = self.service
            candidate: GatewayService | None = None
            candidate_text: str | None = None
            old_service_released = False
            try:
                candidate_text, _ = self.config_file.read()
                candidate_settings = settings_from_text(candidate_text)
                operation.stage = "draining"
                self.state = "draining"
                self._accepting = False
                try:
                    async with self._condition:
                        await asyncio.wait_for(
                            self._condition.wait_for(lambda: self._inflight == 0),
                            timeout=previous_settings.webui_reload_drain_timeout_s,
                        )
                    operation.stage = "releasing"
                except TimeoutError:
                    operation.stage = "cancelling"
                    async with self._condition:
                        request_tasks = list(self._request_tasks)
                    for task in request_tasks:
                        task.cancel()
                    if request_tasks:
                        await asyncio.gather(
                            *request_tasks, return_exceptions=True
                        )
                self.state = "reloading"
                old_service_released = True
                await old_service.close()
                operation.stage = "loading"
                candidate = self.service_factory(candidate_settings)
                await candidate.start()
                self.service = candidate
                self.settings = candidate_settings
                if self.store is not None:
                    self.store.update_settings(candidate_settings)
                configure_server_log(candidate_settings)
                self._active_config_text = candidate_text
                self.last_error = None
                self.state = "ready"
                self._accepting = True
                operation.status = "succeeded"
                operation.stage = "ready"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                operation.error = str(exc)
                self.last_error = str(exc)
                if candidate_text is not None:
                    try:
                        operation.failed_config = self.config_file.preserve_failed(
                            candidate_text
                        )
                    except Exception:
                        pass
                if candidate is not None:
                    try:
                        await candidate.close()
                    except Exception:
                        pass
                operation.stage = "rolling_back"
                try:
                    if previous_text is None:
                        raise RuntimeError("No previously active configuration")
                    self.config_file.restore(previous_text)
                    if old_service_released:
                        rollback_service = self.service_factory(previous_settings)
                        await rollback_service.start()
                        self.service = rollback_service
                        self.settings = previous_settings
                        if self.store is not None:
                            self.store.update_settings(previous_settings)
                        configure_server_log(previous_settings)
                    else:
                        self.service = old_service
                        self.settings = previous_settings
                    self.state = "ready"
                    self._accepting = True
                    operation.rollback = "succeeded"
                except Exception as rollback_exc:
                    self.state = "failed"
                    self._accepting = False
                    operation.rollback = f"failed: {rollback_exc}"
                    self.last_error = (
                        f"reload failed: {exc}; rollback failed: {rollback_exc}"
                    )
                operation.status = "failed"
                operation.stage = self.state
            finally:
                operation.finished_at = time.time()


def redact_token(text: str) -> str:
    return _replace_webui_token(text, REDACTED_TOKEN)


def restore_redacted_token(candidate: str, current: str) -> str:
    if REDACTED_TOKEN not in candidate:
        return candidate
    current_token = _extract_webui_token(current)
    return _replace_webui_token(candidate, current_token)


def _extract_webui_token(text: str) -> str:
    try:
        config = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return ""
    webui = config.get("webui", {})
    if not isinstance(webui, dict):
        return ""
    value = webui.get("auth_token", "")
    return value if isinstance(value, str) else ""


def _replace_webui_token(text: str, replacement: str) -> str:
    lines = text.splitlines(keepends=True)
    in_webui = False
    webui_header: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_webui = stripped == "[webui]"
            if in_webui:
                webui_header = index
            continue
        if not in_webui:
            continue
        match = re.match(r"(?P<indent>\s*)auth_token\s*=\s*(?P<rhs>.*)", line)
        if match is None:
            continue
        end = index
        rhs = match.group("rhs").lstrip()
        for delimiter in (chr(34) * 3, chr(39) * 3):
            if rhs.startswith(delimiter) and delimiter not in rhs[len(delimiter):]:
                while end + 1 < len(lines):
                    end += 1
                    if delimiter in lines[end]:
                        break
                break
        newline = "\n" if any(item.endswith("\n") for item in lines[index:end + 1]) else ""
        lines[index:end + 1] = [
            f"{match.group('indent')}auth_token = {json.dumps(replacement)}{newline}"
        ]
        return "".join(lines)
    if replacement and webui_header is not None:
        lines.insert(
            webui_header + 1,
            f"auth_token = {json.dumps(replacement)}\n",
        )
        return "".join(lines)
    if replacement:
        suffix = "" if text.endswith("\n") else "\n"
        return text + suffix + f"\n[webui]\nauth_token = {json.dumps(replacement)}\n"
    return "".join(lines)


def _revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original_mode = path.stat().st_mode if path.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, original_mode)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
