"""Qual interpretador está rodando."""

import platform
import sys
from dataclasses import dataclass

from jaylog.host._safe import safe


@dataclass(frozen=True)
class PythonInfo:
    version: str | None
    implementation: str | None
    executable: str | None
    frozen: bool | None


@safe(default=PythonInfo(None, None, None, None))
def detect_python() -> PythonInfo:
    return PythonInfo(
        version=platform.python_version(),
        implementation=platform.python_implementation(),
        executable=sys.executable or None,
        frozen=bool(getattr(sys, "frozen", False)),
    )


__all__ = ["PythonInfo", "detect_python"]
