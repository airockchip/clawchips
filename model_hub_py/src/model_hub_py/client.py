import base64
import logging
import time

import httpx

_LOG = logging.getLogger(__name__)


class ModelHubSessionFailed(RuntimeError):
    """Session finished with model_hub status ``failed`` (no upstream result stored)."""

    def __init__(self, session_id: str, error: str) -> None:
        self.session_id = session_id
        self.error = error
        super().__init__(f"session {session_id} failed: {error}")


def _normalize_session_status(raw: object) -> str:
    """Accept ``\"failed\"``, ``SessionStatus.FAILED``-style strings, etc."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if "." in s and s.split(".")[-1].isidentifier():
        s = s.split(".")[-1]
    return s.lower()


class ModelHubPyClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float | None = 30.0,
        transport=None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        )

    def submit(
        self,
        service_name: str,
        *,
        method: str,
        path: str,
        query=None,
        headers=None,
        json_body=None,
        body=None,
    ) -> str:
        payload = {
            "method": method,
            "path": path,
            "query": query or {},
            "headers": headers or {},
        }
        if json_body is not None:
            payload["json_body"] = json_body
        if body is not None:
            if isinstance(body, bytes):
                payload["body_base64"] = base64.b64encode(body).decode("utf-8")
            else:
                payload["body_base64"] = body

        _LOG.debug(
            "model_hub submit: service=%s method=%s path=%s body_base64_len=%s",
            service_name,
            method,
            path,
            len(payload.get("body_base64") or ""),
        )
        response = self._client.post(f"/services/{service_name}", json=payload)
        response.raise_for_status()
        session_id = response.json()["session_id"]
        _LOG.debug("model_hub submit ok: session_id=%s", session_id)
        return session_id

    def wait(
        self,
        session_id: str,
        *,
        poll_interval: float = 1.0,
        timeout: float | None = None,
    ) -> dict:
        deadline = None if timeout is None else time.monotonic() + timeout
        poll = 0
        last_st = ""
        while True:
            response = self._client.get(f"/sessions/{session_id}")
            response.raise_for_status()
            payload = response.json()
            st = _normalize_session_status(payload.get("status"))
            raw_st = payload.get("status")
            if st != last_st:
                _LOG.debug(
                    "model_hub session poll: session_id=%s poll=%d status_raw=%r status_norm=%r",
                    session_id,
                    poll,
                    raw_st,
                    st,
                )
                last_st = st
            if st in {"finished", "failed", "expired"}:
                _LOG.debug(
                    "model_hub session terminal: session_id=%s status=%s err=%r",
                    session_id,
                    st,
                    payload.get("error"),
                )
                return payload
            if deadline is not None and time.monotonic() > deadline:
                _LOG.debug(
                    "model_hub wait timeout: session_id=%s last_status=%r polls=%d",
                    session_id,
                    raw_st,
                    poll,
                )
                raise TimeoutError(f"timed out waiting for {session_id}")
            poll += 1
            time.sleep(poll_interval)

    def get_result(self, session_id: str) -> dict:
        _LOG.debug("model_hub get_result: session_id=%s", session_id)
        response = self._client.get(f"/sessions/{session_id}/result")
        response.raise_for_status()
        data = response.json()
        us = data.get("upstream_status_code")
        _LOG.debug(
            "model_hub get_result ok: session_id=%s upstream_status_code=%s",
            session_id,
            us,
        )
        return data

    def get_service(self, service_name: str) -> dict:
        response = self._client.get(f"/services/{service_name}")
        response.raise_for_status()
        return response.json()

    def start_service(self, service_name: str) -> dict:
        response = self._client.post(f"/services/{service_name}/start")
        response.raise_for_status()
        return response.json()

    def healthcheck_service(self, service_name: str) -> dict:
        response = self._client.post(f"/services/{service_name}/healthcheck")
        response.raise_for_status()
        return response.json()

    def touch_service_lease(
        self,
        service_name: str,
        *,
        lease_id: str | None = None,
        ttl_seconds: int = 30,
        reserve_device_slot: bool = False,
        reserve_wait_timeout_seconds: float | None = None,
    ) -> dict:
        # Always send reserve_device_slot so the hub and packet captures see an explicit
        # boolean (omitting the key is handled as false server-side).
        payload: dict = {
            "ttl_seconds": ttl_seconds,
            "reserve_device_slot": bool(reserve_device_slot),
        }
        if lease_id:
            payload["lease_id"] = lease_id
        if reserve_device_slot and reserve_wait_timeout_seconds is not None:
            payload["reserve_wait_timeout_seconds"] = reserve_wait_timeout_seconds
        response = self._client.post(f"/services/{service_name}/leases", json=payload)
        response.raise_for_status()
        return response.json()

    def release_service_lease(self, service_name: str, lease_id: str) -> dict:
        response = self._client.delete(f"/services/{service_name}/leases/{lease_id}")
        response.raise_for_status()
        return response.json()

    def run(
        self,
        service_name: str,
        *,
        method: str,
        path: str,
        query=None,
        headers=None,
        json_body=None,
        body=None,
        timeout: float | None = None,
    ) -> dict:
        session_id = self.submit(
            service_name,
            method=method,
            path=path,
            query=query,
            headers=headers,
            json_body=json_body,
            body=body,
        )
        final = self.wait(session_id, timeout=timeout)
        status = _normalize_session_status(final.get("status"))
        if status == "failed":
            err = str(final.get("error") or "unknown error")
            _LOG.debug(
                "model_hub run: session failed, not fetching /result: session_id=%s error=%s",
                session_id,
                err,
            )
            raise ModelHubSessionFailed(session_id, err)
        if status == "expired":
            _LOG.debug("model_hub run: session expired: session_id=%s", session_id)
            msg = f"session {session_id} expired before result could be read"
            raise RuntimeError(msg)
        if status == "finished":
            return self.get_result(session_id)
        msg = f"unexpected session status after wait: {final.get('status')!r}"
        _LOG.debug("model_hub run: unexpected status: session_id=%s %s", session_id, msg)
        raise RuntimeError(msg)
