from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import gateway.app
import gateway.__main__ as gateway_main


def test_main_uses_explicit_config_path(monkeypatch) -> None:
    settings = SimpleNamespace(host="0.0.0.0", port=9090)
    application = object()
    loaded_paths: list[Path] = []

    def fake_get_settings(path: Path):
        loaded_paths.append(path)
        return settings

    monkeypatch.setattr(gateway_main, "get_settings", fake_get_settings)
    monkeypatch.setattr(gateway.app, "create_app", lambda **kwargs: application)
    run_calls = []
    monkeypatch.setattr(
        gateway_main.uvicorn,
        "run",
        lambda *args, **kwargs: run_calls.append((args, kwargs)),
    )

    gateway_main.main(["--config", "/tmp/custom-gateway.toml"])

    assert loaded_paths == [Path("/tmp/custom-gateway.toml")]
    assert run_calls == [((application,), {"host": "0.0.0.0", "port": 9090})]


def test_main_defaults_to_gateway_toml_in_current_directory(monkeypatch) -> None:
    settings = SimpleNamespace(host="127.0.0.1", port=8080)
    application = object()
    loaded_paths: list[Path] = []

    def fake_get_settings(path: Path):
        loaded_paths.append(path)
        return settings

    monkeypatch.setattr(gateway_main, "get_settings", fake_get_settings)
    monkeypatch.setattr(gateway.app, "create_app", lambda **kwargs: application)
    monkeypatch.setattr(
        gateway_main.uvicorn,
        "run",
        lambda *args, **kwargs: None,
    )

    gateway_main.main([])

    assert loaded_paths == [Path("gateway.toml")]
