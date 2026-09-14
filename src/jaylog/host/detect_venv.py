"""
Em que ambiente virtual o processo roda — e de que ferramenta ele saiu.

A ordem das checagens importa:

1. ``sys.frozen`` **primeiro**. Num executável do PyInstaller ``sys.prefix``
   aponta para o diretório temporário ``_MEIPASS``, diferente de
   ``sys.base_prefix`` — o teste do PEP 405 daria falso-positivo e todo `.exe`
   da frota apareceria como "rodando num venv".
2. ``CONDA_PREFIX``: conda não segue o PEP 405.
3. ``sys.prefix != sys.base_prefix``: o teste canônico.

O *tipo* do venv sai do ``pyvenv.cfg``: o ``uv`` escreve a chave ``uv = 0.x.y``,
o ``virtualenv`` escreve ``virtualenv = 20.x``, e o ``venv`` da stdlib não
escreve nenhuma das duas.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from jaylog.host._safe import safe


@dataclass(frozen=True)
class VenvInfo:
    active: bool | None
    kind: str | None
    path: str | None


def _read_pyvenv_kind(prefix: Path) -> str:
    cfg = prefix / "pyvenv.cfg"
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "venv"

    keys = set()
    for line in text.splitlines():
        key, sep, _value = line.partition("=")
        if sep:
            keys.add(key.strip().lower())

    if "uv" in keys:
        return "uv"
    if "virtualenv" in keys:
        return "virtualenv"
    return "venv"


def _tool_override() -> str | None:
    """``pipenv``/``poetry`` rodam *sobre* um venv — o marcador deles ganha."""
    if os.environ.get("PIPENV_ACTIVE"):
        return "pipenv"
    if os.environ.get("POETRY_ACTIVE"):
        return "poetry"
    return None


@safe(default=VenvInfo(None, None, None))
def detect_venv() -> VenvInfo:
    if getattr(sys, "frozen", False):
        return VenvInfo(False, "frozen", None)

    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        return VenvInfo(True, _tool_override() or "conda", conda_prefix)

    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    if sys.prefix != base_prefix:
        kind = _tool_override() or _read_pyvenv_kind(Path(sys.prefix))
        return VenvInfo(True, kind, sys.prefix)

    return VenvInfo(False, None, None)


__all__ = ["VenvInfo", "detect_venv"]
