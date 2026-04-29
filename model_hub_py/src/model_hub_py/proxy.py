import base64
import time
from typing import Any

import httpx

from .models import UpstreamRequest, UpstreamResult

HOP_BY_HOP_HEADERS = {"host", "content-length", "connection"}


class ProxyClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def forward(self, base_url: str, request: UpstreamRequest) -> UpstreamResult:
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
        }
        body: bytes | None = None
        json_body: Any | None = request.json_body

        if request.body_base64 is not None:
            body = base64.b64decode(request.body_base64.encode("utf-8"))
            json_body = None

        started = time.monotonic()
        response = await self._client.request(
            method=request.method,
            url=f"{base_url.rstrip('/')}{request.path}",
            params=request.query,
            headers=headers,
            json=json_body,
            content=body,
        )
        duration_ms = int((time.monotonic() - started) * 1000)

        try:
            parsed_body = response.json()
            body_base64 = None
        except ValueError:
            parsed_body = None
            body_base64 = base64.b64encode(response.content).decode("utf-8")

        return UpstreamResult(
            upstream_status_code=response.status_code,
            upstream_headers=dict(response.headers),
            upstream_body=parsed_body,
            body_base64=body_base64,
            duration_ms=duration_ms,
        )
