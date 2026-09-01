from __future__ import annotations

import os
from pathlib import Path


PACKAGED_NATIVE_LIBRARY = (
    Path(__file__).resolve().parent / "_native" / "librkclaw_native.so"
)
SYSTEM_NATIVE_LIBRARY = Path("/usr/lib/librkclaw_native.so")


def resolve_native_library(explicit_path: str | None = None) -> str:
    """Resolve the unified native library, preferring the wheel-bundled copy."""
    if explicit_path:
        return explicit_path

    environment_path = os.environ.get("RKCLAW_NATIVE_LIB")
    if environment_path:
        return environment_path

    if PACKAGED_NATIVE_LIBRARY.is_file():
        return str(PACKAGED_NATIVE_LIBRARY)
    if SYSTEM_NATIVE_LIBRARY.is_file():
        return str(SYSTEM_NATIVE_LIBRARY)
    # Preserve a useful error path when running from a source tree before building.
    return str(PACKAGED_NATIVE_LIBRARY)
