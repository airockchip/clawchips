from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import Settings
from gateway.management import ConfigFile, REDACTED_TOKEN
from gateway.schemas import Usage


def config_text(tmp_path: Path, model_id: str = "test-model") -> str:
    return (
        '[server]\n'
        'host = "127.0.0.1"\n'
        'port = 8080\n'
        'queue_size = 8\n'
        'enable_streaming = true\n\n'
        '[runtime]\n'
        'target = "rk1820"\n'
        'device_id = ""\n'
        'core_mask = 255\n'
        'native_library = ""\n\n'
        '[model]\n'
        f'id = "{model_id}"\n'
        'rknn_path = "/model/model.rknn"\n'
        'weight_path = "/model/model.weight"\n'
        'tokenizer_path = "/model/tokenizer.gguf"\n'
        'embed_path = "/model/embed.bin"\n'
        'max_context_tokens = 4096\n'
        'max_new_tokens = 512\n\n'
        '[sampling]\n'
        'temperature = 0.3\n'
        'top_p = 0.9\n'
        'top_k = 1\n'
        'repeat_penalty = 1.0\n\n'
        '[logging]\n'
        'session_logs_enabled = true\n'
        'session_retention_days = 30\n'
        f'server_log_path = "{tmp_path / "server.log"}"\n'
        'server_log_max_bytes = 1048576\n'
        'server_log_backup_count = 2\n\n'
        '[webui]\n'
        'enabled = true\n'
        'auth_token = "secret-token"\n'
        f'data_path = "{tmp_path / "webui.sqlite3"}"\n'
        'stats_retention_days = 90\n'
        'reload_drain_timeout_s = 1\n'
        'session_cookie_ttl_s = 3600\n'
    )


def settings(tmp_path: Path, **overrides: Any) -> Settings:
    values = {
        "model_id": "test-model",
        "rknn_path": "/model/model.rknn",
        "weight_path": "/model/model.weight",
        "tokenizer_path": "/model/tokenizer.gguf",
        "embed_path": "/model/embed.bin",
        "webui_enabled": True,
        "webui_auth_token": "secret-token",
        "webui_data_path": str(tmp_path / "webui.sqlite3"),
        "session_logs_enabled": True,
        "server_log_path": str(tmp_path / "server.log"),
    }
    values.update(overrides)
    return Settings(**values)


class FakeService:
    def __init__(self, config: Settings, fail_start: bool = False):
        self.settings = config
        self.ready = False
        self.created = int(time.time())
        self.fail_start = fail_start
        self._usage: dict[str, Usage] = {}
        self._traces: dict[str, dict[str, str]] = {}

    async def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("candidate load failed")
        self.ready = True

    async def close(self) -> None:
        self.ready = False

    async def complete(self, request: Any, req_id: str) -> dict[str, Any]:
        self._usage[req_id] = Usage(4, 2)
        self._traces[req_id] = {"input": "<rendered>hello</rendered>", "output": "raw model world"}
        return {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": request.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "world"},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            },
        }

    def stream(self, request: Any, req_id: str):
        async def iterate():
            self._usage[req_id] = Usage(4, 2)
            self._traces[req_id] = {"input": "<rendered>hello</rendered>", "output": "raw model world"}
            yield b'data: {"choices":[{"delta":{"content":"world"}}]}\n\n'
            yield b"data: [DONE]\n\n"
        return iterate()

    def take_usage(self, req_id: str) -> Usage | None:
        return self._usage.pop(req_id, None)

    def take_model_trace(self, req_id: str) -> dict[str, str] | None:
        return self._traces.pop(req_id, None)


def login(client: TestClient) -> str:
    response = client.post(
        "/api/webui/auth/login", json={"token": "secret-token"}
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_webui_auth_dashboard_session_and_config(tmp_path: Path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(config_text(tmp_path))
    config = settings(tmp_path)
    client = TestClient(
        create_app(settings=config, service=FakeService(config), config_path=path)
    )

    with client:
        assert client.get("/webui").status_code == 200
        assert client.get("/api/webui/dashboard").status_code == 401
        assert client.post(
            "/api/webui/auth/login", json={"token": "bad"}
        ).status_code == 401

        csrf = login(client)
        config_response = client.get("/api/webui/config")
        assert config_response.status_code == 200
        assert "secret-token" not in config_response.json()["toml"]
        assert REDACTED_TOKEN in config_response.json()["toml"]

        body = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
        }
        completion = client.post("/v1/chat/completions", json=body)
        assert completion.status_code == 200
        assert completion.headers["x-request-id"]

        dashboard = client.get("/api/webui/dashboard?range=1h").json()
        assert dashboard["totals"]["requests"] == 1
        assert dashboard["totals"]["input_tokens"] == 4
        assert dashboard["server"]["state"] == "ready"

        sessions = client.get("/api/webui/sessions").json()
        assert sessions["enabled"] is True
        assert len(sessions["items"]) == 1
        session_id = sessions["items"][0]["id"]
        detail = client.get("/api/webui/sessions/" + session_id).json()
        assert detail["requests"][0]["request"]["messages"][0]["content"] == "hello"
        assert detail["requests"][0]["input_tokens"] == 4
        assert detail["requests"][0]["output_tokens"] == 2
        assert detail["requests"][0]["model_input"] == "<rendered>hello</rendered>"
        assert detail["requests"][0]["model_output"] == "raw model world"

        rejected = client.post(
            "/api/webui/config/validate",
            json={"toml": config_response.json()["toml"]},
        )
        assert rejected.status_code == 401
        validated = client.post(
            "/api/webui/config/validate",
            json={"toml": config_response.json()["toml"]},
            headers={"X-CSRF-Token": csrf},
        )
        assert validated.status_code == 200

        streamed = client.post(
            "/v1/chat/completions",
            json={**body, "stream": True},
        )
        assert streamed.status_code == 200
        assert "data: [DONE]" in streamed.text
        assert client.get("/api/webui/dashboard?range=1h").json()[
            "totals"
        ]["requests"] == 2
        session_items = client.get("/api/webui/sessions").json()["items"]
        assert {item["status"] for item in session_items} == {"success"}



def test_webui_file_browser_and_device_discovery(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(config_text(tmp_path))
    config = settings(tmp_path)
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "model.rknn").write_bytes(b"rknn")
    (tmp_path / "notes.txt").write_text("test")
    smi_output = """
| 0             RK1828  | 0003:31:00.0  | 45 | 4230 / 5120 |
| 0             RK1820  | 0001:11:00.0  | 45 | 3719 / 5120 |
"""
    monkeypatch.setattr(
        "gateway.webui.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout=smi_output, stderr=""
        ),
    )
    client = TestClient(
        create_app(settings=config, service=FakeService(config), config_path=path)
    )

    with client:
        assert client.get("/api/webui/files", params={"path": str(tmp_path)}).status_code == 401
        assert client.get("/api/webui/devices").status_code == 401
        login(client)

        listing = client.get(
            "/api/webui/files", params={"path": str(tmp_path)}
        )
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["path"] == str(tmp_path)
        assert payload["entries"][0]["name"] == "models"
        assert payload["entries"][0]["is_dir"] is True
        assert any(item["name"] == "notes.txt" for item in payload["entries"])
        assert client.get(
            "/api/webui/files", params={"path": "relative/path"}
        ).status_code == 400

        devices = client.get("/api/webui/devices")
        assert devices.status_code == 200
        assert [item["id"] for item in devices.json()["devices"]] == [
            "0003:31:00.0",
            "0001:11:00.0",
        ]

        page = client.get("/webui")
        assert "<title>RKClawServer</title>" in page.text
        assert page.headers["cache-control"] == "no-store, max-age=0"
        assert "/webui/assets/styles.css?v=20260811-export1" in page.text
        assert "/webui/assets/app.js?v=20260811-export1" in page.text
        assert 'width="64" height="64"' in page.text
        assert "<span>01</span>" not in page.text
        assert "page-kicker" not in page.text
        assert "model-chip" not in page.text
        assert 'id="runtime-model"' in page.text
        assert "CURRENT RUNTIME" not in page.text
        assert "<th>INPUT TOKENS</th>" in page.text
        assert "<th>OUTPUT TOKENS</th>" in page.text
        assert 'id="session-api-tab"' in page.text
        assert 'id="session-model-tab"' in page.text
        assert 'id="export-session"' in page.text
        logo = client.get("/webui/assets/logo.png")
        assert logo.status_code == 200
        assert logo.headers["content-type"] == "image/png"
        script = client.get("/webui/assets/app.js")
        assert script.status_code == 200
        assert 'select.id = "model-card-count"' in script.text
        assert '["", "单卡", "双卡", "三卡"' in script.text
        assert "function renderMulticard" not in script.text
        assert 'return "state-ready"' in script.text
        assert 'return "state-warning"' in script.text
        assert 'document.createElement("details")' in script.text
        assert 'summary.className = "request-summary"' in script.text
        assert "expandedSessionRequests: new Set()" in script.text
        assert "JSON.stringify(state.sessionDetail, null, 2)" in script.text
        assert 'type: "application/json;charset=utf-8"' in script.text
        assert '"rkclaw-session-" + safeDownloadPart(state.sessionDetail.id)' in script.text
        assert "safe.slice(0, 120)" in script.text
        assert "URL.createObjectURL(blob)" in script.text
        assert "URL.revokeObjectURL(objectUrl)" in script.text
        styles = client.get("/webui/assets/styles.css")
        assert styles.status_code == 200
        assert "--success: #16a34a" in styles.text
        assert ".state-inactive" in styles.text
        assert ".dialog-head-actions" in styles.text

def test_config_file_redacts_and_preserves_token_on_save(tmp_path: Path) -> None:
    path = tmp_path / "gateway.toml"
    path.write_text(config_text(tmp_path))
    manager = ConfigFile(path)
    payload = manager.payload()
    assert "secret-token" not in payload["toml"]
    assert REDACTED_TOKEN in payload["toml"]

    candidate = payload["toml"].replace("temperature = 0.3", "temperature = 0.4")
    result = manager.save(candidate, payload["revision"])
    assert result["revision"]
    saved = path.read_text()
    assert 'auth_token = "secret-token"' in saved
    assert "temperature = 0.4" in saved
    assert list(tmp_path.glob("gateway.toml.*.bak"))

def test_config_file_redacts_multiline_token(tmp_path: Path) -> None:
    from gateway.config import settings_from_text

    path = tmp_path / "gateway.toml"
    text = config_text(tmp_path).replace(
        'auth_token = "secret-token"',
        'auth_token = """secret\nmultiline"""',
    )
    path.write_text(text)
    manager = ConfigFile(path)
    payload = manager.payload()

    assert "secret" not in payload["toml"]
    result = manager.save(payload["toml"], payload["revision"])
    assert result["revision"]
    assert settings_from_text(path.read_text()).webui_auth_token == (
        "secret\nmultiline"
    )


def test_observability_migrates_legacy_session_request_model_log_columns(
    tmp_path: Path,
) -> None:
    from gateway.observability import ObservabilityStore

    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE session_requests (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            latency_ms REAL NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            request_payload BLOB NOT NULL,
            response_payload BLOB,
            error TEXT
        )
        """
    )
    connection.commit()
    connection.close()

    store = ObservabilityStore(settings(tmp_path, webui_data_path=str(database)))
    columns = {
        row["name"]
        for row in store._connection.execute("PRAGMA table_info(session_requests)")
    }
    assert {"model_input_payload", "model_output_payload"} <= columns
    store.close()


def test_observability_groups_message_prefixes_and_splits_repeats(
    tmp_path: Path,
) -> None:
    from gateway.observability import ObservabilityStore

    store = ObservabilityStore(settings(tmp_path))
    first = {"messages": [{"role": "user", "content": "hello"}]}
    first_response = {
        "choices": [{"message": {"role": "assistant", "content": "world"}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }
    store.record_request(
        request_id="first",
        request_body=first,
        model="test-model",
        started_at=time.time(),
        latency_ms=10,
        status="success",
        response=first_response,
    )
    followup = {
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
            {"role": "user", "content": "again"},
        ]
    }
    store.record_request(
        request_id="second",
        request_body=followup,
        model="test-model",
        started_at=time.time() + 1,
        latency_ms=20,
        status="success",
        response=first_response,
    )
    store.record_request(
        request_id="repeat",
        request_body=first,
        model="test-model",
        started_at=time.time() + 2,
        latency_ms=30,
        status="success",
        response=first_response,
    )

    listed = store.list_sessions(limit=10)
    assert len(listed["items"]) == 2
    assert sorted(item["request_count"] for item in listed["items"]) == [1, 2]
    store.close()


def test_observability_groups_normalized_streaming_tool_followup(
    tmp_path: Path,
) -> None:
    from gateway.observability import ObservabilityStore

    store = ObservabilityStore(settings(tmp_path))
    messages = [
        {"role": "system", "content": "Use tools when needed."},
        {"role": "user", "content": "福州今天天气"},
    ]
    streamed_response = "\n\n".join([
        'data: {"choices":[{"delta":{"reasoning_content":"先查天气。"}}]}',
        'data: {"choices":[{"delta":{"content":"\\n\\n"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"id":"call_weather","type":"function","function":'
        '{"name":"Web","arguments":"{\\\"query\\\":\\\""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"name":"Search","arguments":"福州\\\"}"}}]}}]}',
        'data: [DONE]',
    ])
    store.record_request(
        request_id="tool-request",
        request_body={"messages": messages},
        model="test-model",
        started_at=time.time(),
        latency_ms=10,
        status="success",
        response=streamed_response,
    )

    followup = {
        "messages": messages + [
            {
                "role": "assistant",
                "content": "\n\n",
                # Some clients rename reasoning_content when echoing history.
                "reasoning": "先查天气。",
                "tool_calls": [{
                    "id": "call_weather",
                    "type": "function",
                    "function": {
                        "name": "WebSearch",
                        "arguments": '{"query":"福州"}',
                    },
                    "client_metadata": "ignored",
                }],
            },
            {
                "role": "tool",
                "tool_call_id": "call_weather",
                "content": "晴，35℃",
            },
        ]
    }
    store.record_request(
        request_id="tool-followup",
        request_body=followup,
        model="test-model",
        started_at=time.time() + 1,
        latency_ms=20,
        status="success",
        response={
            "choices": [{
                "message": {"role": "assistant", "content": "今天晴。"}
            }]
        },
    )

    listed = store.list_sessions(limit=10)
    assert len(listed["items"]) == 1
    assert listed["items"][0]["request_count"] == 2
    store.close()


async def test_supervisor_reloads_saved_model_configuration(
    tmp_path: Path,
) -> None:
    from gateway.management import ServiceSupervisor

    path = tmp_path / "gateway.toml"
    path.write_text(config_text(tmp_path, model_id="old-model"))
    original = settings(tmp_path, model_id="old-model")
    created: list[str] = []

    def factory(candidate: Settings):
        created.append(candidate.model_id)
        return FakeService(candidate)

    supervisor = ServiceSupervisor(
        original,
        FakeService(original),
        config_file=ConfigFile(path),
        service_factory=factory,
    )
    await supervisor.start()
    path.write_text(config_text(tmp_path, model_id="new-model"))
    operation = supervisor.start_reload()
    assert supervisor._reload_task is not None
    await supervisor._reload_task

    assert operation.status == "succeeded"
    assert supervisor.settings.model_id == "new-model"
    assert created == ["new-model"]
    await supervisor.close()


async def test_supervisor_keeps_current_model_on_parse_failure(
    tmp_path: Path,
) -> None:
    from gateway.management import ServiceSupervisor

    path = tmp_path / "gateway.toml"
    original_text = config_text(tmp_path, model_id="old-model")
    path.write_text(original_text)
    original = settings(tmp_path, model_id="old-model")
    current_service = FakeService(original)
    created: list[str] = []

    def factory(candidate: Settings):
        created.append(candidate.model_id)
        return FakeService(candidate)

    supervisor = ServiceSupervisor(
        original,
        current_service,
        config_file=ConfigFile(path),
        service_factory=factory,
    )
    await supervisor.start()
    path.write_text("invalid = [")
    operation = supervisor.start_reload()
    assert supervisor._reload_task is not None
    await supervisor._reload_task

    assert operation.status == "failed"
    assert operation.rollback == "succeeded"
    assert supervisor.state == "ready"
    assert supervisor.service is current_service
    assert created == []
    assert path.read_text() == original_text
    assert operation.failed_config
    assert Path(operation.failed_config).read_text() == "invalid = ["
    await supervisor.close()


async def test_supervisor_restores_last_good_config_on_load_failure(
    tmp_path: Path,
) -> None:
    from gateway.management import ServiceSupervisor

    path = tmp_path / "gateway.toml"
    original_text = config_text(tmp_path, model_id="old-model")
    path.write_text(original_text)
    original = settings(tmp_path, model_id="old-model")

    def factory(candidate: Settings):
        return FakeService(
            candidate,
            fail_start=candidate.model_id == "bad-model",
        )

    supervisor = ServiceSupervisor(
        original,
        FakeService(original),
        config_file=ConfigFile(path),
        service_factory=factory,
    )
    await supervisor.start()
    path.write_text(config_text(tmp_path, model_id="bad-model"))
    operation = supervisor.start_reload()
    assert supervisor._reload_task is not None
    await supervisor._reload_task

    assert operation.status == "failed"
    assert operation.rollback == "succeeded"
    assert supervisor.state == "ready"
    assert supervisor.settings.model_id == "old-model"
    assert path.read_text() == original_text
    assert operation.failed_config
    assert Path(operation.failed_config).read_text() == config_text(
        tmp_path, model_id="bad-model"
    )
    await supervisor.close()

def test_webui_routes_are_absent_when_disabled(tmp_path: Path) -> None:
    config = Settings(
        model_id="test-model",
        rknn_path="/model/model.rknn",
        weight_path="/model/model.weight",
        tokenizer_path="/model/tokenizer.gguf",
        embed_path="/model/embed.bin",
    )
    client = TestClient(create_app(settings=config, service=FakeService(config)))
    with client:
        assert client.get("/webui").status_code == 404
        assert client.get("/api/webui/dashboard").status_code == 404

def test_webui_environment_token_has_priority(
    tmp_path: Path, monkeypatch,
) -> None:
    from gateway.config import settings_from_text

    monkeypatch.setenv("RKCLAW_WEBUI_TOKEN", "environment-token")
    text = config_text(tmp_path).replace(
        'auth_token = "secret-token"',
        'auth_token = "file-token"',
    )
    parsed = settings_from_text(text)
    assert parsed.webui_auth_token == "environment-token"
