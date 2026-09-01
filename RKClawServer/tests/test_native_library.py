from __future__ import annotations

from gateway import native_library


def test_native_library_resolution_precedence(tmp_path, monkeypatch) -> None:
    packaged = tmp_path / "site-packages/gateway/_native/librkclaw_native.so"
    system = tmp_path / "usr/lib/librkclaw_native.so"
    packaged.parent.mkdir(parents=True)
    system.parent.mkdir(parents=True)
    packaged.touch()
    system.touch()
    monkeypatch.setattr(native_library, "PACKAGED_NATIVE_LIBRARY", packaged)
    monkeypatch.setattr(native_library, "SYSTEM_NATIVE_LIBRARY", system)

    assert native_library.resolve_native_library("/custom/librkclaw_native.so") == (
        "/custom/librkclaw_native.so"
    )

    monkeypatch.setenv("RKCLAW_NATIVE_LIB", "/environment/librkclaw_native.so")
    assert native_library.resolve_native_library() == "/environment/librkclaw_native.so"

    monkeypatch.delenv("RKCLAW_NATIVE_LIB")
    assert native_library.resolve_native_library() == str(packaged)

    packaged.unlink()
    assert native_library.resolve_native_library() == str(system)

    system.unlink()
    assert native_library.resolve_native_library() == str(packaged)
