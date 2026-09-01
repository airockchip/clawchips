from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import DEFAULT_CONFIG_PATH, Settings, get_settings
from .logging_utils import (
    configure_server_log,
    detail_logs_enabled,
    log_payload,
    log_target_enabled,
)
from .management import (
    ConfigFile,
    ServiceReloading,
    ServiceSupervisor,
    WebUIAuth,
)
from .observability import ObservabilityStore
from .runtime import RuntimeBusyError
from .schemas import InvalidRequest, parse_chat_request
from .service import GatewayService, InferenceFailed
from .webui import register_webui

logger = logging.getLogger("gateway")

OPENAI_REQUEST_LABEL = "OpenAI API request"


class ClientDisconnected(RuntimeError):
    pass


def create_app(
    *,
    settings: Settings | None = None,
    service: GatewayService | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(instance: FastAPI):
        active_settings = settings or get_settings(config_path or DEFAULT_CONFIG_PATH)
        _configure_logging(_debug_logging_enabled(active_settings))
        configure_server_log(active_settings)
        active_service = service or GatewayService(active_settings)
        location = Path(config_path or DEFAULT_CONFIG_PATH)
        config_file = ConfigFile(location) if location.is_file() else None
        store = (
            ObservabilityStore(active_settings)
            if active_settings.webui_enabled
            else None
        )
        supervisor = ServiceSupervisor(
            active_settings,
            active_service,
            config_file=config_file,
            store=store,
        )
        instance.state.settings = active_settings
        instance.state.service = active_service
        instance.state.supervisor = supervisor
        if (
            active_settings.webui_enabled
            and not getattr(instance.state, "webui_registered", False)
        ):
            register_webui(
                instance,
                supervisor,
                WebUIAuth(active_settings),
                config_file,
            )
            instance.state.webui_registered = True
        try:
            await supervisor.start()
            yield
        finally:
            await supervisor.close()

    application = FastAPI(title="RKClawServer", lifespan=lifespan)

    @application.middleware("http")
    async def request_logger(request: Request, call_next):
        active_settings = request.app.state.supervisor.settings
        is_chat_completion = (
            request.method == "POST"
            and request.url.path == "/v1/chat/completions"
        )
        req_id = ""
        if is_chat_completion:
            req_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            request.state.chat_req_id = req_id
            if (
                detail_logs_enabled(active_settings)
                and log_target_enabled(active_settings.openai_request_log)
            ):
                body_bytes = await request.body()
                log_payload(
                    OPENAI_REQUEST_LABEL,
                    body_bytes,
                    req_id,
                    active_settings.openai_request_log,
                    active_settings.logger_detail_log_max_chars,
                )
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            latency = (time.perf_counter() - start) * 1000
            logger.exception(
                "request failed method=%s path=%s latency=%.1fms",
                request.method,
                request.url.path,
                latency,
            )
            raise
        latency = (time.perf_counter() - start) * 1000
        if req_id:
            response.headers["X-Request-ID"] = req_id
            logger.info(
                "request completed req_id=%s method=%s path=%s status=%d latency=%.1fms",
                req_id,
                request.method,
                request.url.path,
                response.status_code,
                latency,
            )
        return response

    @application.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/readyz")
    async def readyz(request: Request):
        active = _service(request)
        if not active.ready:
            return JSONResponse(
                {"status": active.state, "error": active.last_error},
                status_code=503,
            )
        return {"status": "ok"}

    @application.get("/v1/models")
    async def models(request: Request) -> dict[str, Any]:
        active = _service(request)
        return {
            "object": "list",
            "data": [{
                "id": active.settings.model_id,
                "object": "model",
                "created": active.service.created,
                "owned_by": "rknn",
            }],
        }

    @application.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        active = _service(request)
        try:
            body = await request.json()
            parsed = parse_chat_request(body, active.settings)
            req_id = getattr(request.state, "chat_req_id", "")
            if parsed.stream and active.settings.enable_streaming:
                stream = active.stream(parsed, body, req_id)
                return StreamingResponse(
                    stream,
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                    },
                )
            response = await _complete_until_disconnect(
                request,
                active,
                parsed,
                req_id,
                request_body=body,
            )
            return JSONResponse(response)
        except InvalidRequest as exc:
            return openai_error(str(exc), 400, "invalid_request_error", "invalid_request")
        except ServiceReloading as exc:
            return openai_error(str(exc), 503, "server_error", "service_reloading")
        except RuntimeBusyError as exc:
            return openai_error(str(exc), 429, "rate_limit_error", "queue_full")
        except InferenceFailed as exc:
            return openai_error(str(exc), 500, "server_error", "inference_error")
        except ValueError as exc:
            return openai_error(str(exc), 400, "invalid_request_error", "invalid_request")
        except RuntimeError as exc:
            return openai_error(str(exc), 500, "server_error", "server_error")

    return application


def _service(request: Request) -> ServiceSupervisor:
    return request.app.state.supervisor


async def _complete_until_disconnect(
    request: Request,
    service: Any,
    parsed: Any,
    req_id: str = "",
    *,
    request_body: dict[str, Any] | None = None,
):
    if request_body is None:
        completion = service.complete(parsed, req_id)
    else:
        completion = service.complete(parsed, request_body, req_id)
    task = asyncio.create_task(completion)
    disconnect = asyncio.create_task(_wait_for_disconnect(request))
    try:
        done, _ = await asyncio.wait(
            (task, disconnect), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        raise ClientDisconnected("Client disconnected")
    finally:
        if not task.done():
            task.cancel()
        if not disconnect.done():
            disconnect.cancel()
        await asyncio.gather(task, disconnect, return_exceptions=True)


async def _wait_for_disconnect(request: Request) -> None:
    # ``Request.is_disconnected`` performs a non-blocking receive probe. With
    # middleware-wrapped receive callables that probe can be cancelled at the
    # first checkpoint before it observes the queued ``http.disconnect``.
    # Waiting for the ASGI message in a dedicated task avoids that race while
    # the completion task continues independently.
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


def openai_error(message: str, status: int, error_type: str, code: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": error_type, "param": None, "code": code}},
        status_code=status,
    )


def _debug_logging_enabled(settings: Settings) -> bool:
    return settings.debug_logs or any(
        target in {"logger", "both"}
        for target in (
            settings.openai_request_log,
            settings.llm_input_log,
            settings.llm_output_log,
            settings.openai_response_log,
        )
    )


def _configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level)
        for handler in root_logger.handlers:
            handler.setLevel(level)
    else:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
    logger.setLevel(level)


app = create_app()
