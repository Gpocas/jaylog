"""
Detecção e habilitação de cores ANSI no console.

O console legado do Windows (``conhost.exe``, usado pelo `cmd.exe` do Windows 10
quando não está hospedado no Windows Terminal) não interpreta sequências ANSI
por padrão: o flag ``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` existe desde o build
10586, mas vem desligado. Por isso o mesmo log que sai colorido no Windows 11 +
Windows Terminal aparece com lixo (``←[32m``) — ou sem cor — no Windows 10 + cmd.

Este módulo resolve os dois lados do problema:

* liga o modo VT no handle do console quando o Windows suporta;
* quando não suporta (ou a saída foi redirecionada para arquivo/pipe),
  desabilita as cores para que o log saia em texto limpo, sem escape codes.
"""

import os
import sys
from typing import IO

GREEN = "\033[32m"
BLUE = "\033[34m"
PURPLE = "\033[35m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
_STD_HANDLE_BY_FILENO = {1: -11, 2: -12}  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE

# handles já processados, para não chamar SetConsoleMode a cada logger criado
_vt_cache: dict[int, bool] = {}


def _env_flag(name: str) -> bool | None:
    """Lê uma env var no padrão NO_COLOR/FORCE_COLOR: ausente/vazia = indefinido."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _enable_windows_vt(stream: IO[str]) -> bool:
    """
    Liga ``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` no console do Windows.

    Retorna ``True`` se o stream passa a interpretar ANSI (ou já interpretava).
    """
    try:
        fileno = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return False

    handle_id = _STD_HANDLE_BY_FILENO.get(fileno)
    if handle_id is None:
        return False

    if fileno in _vt_cache:
        return _vt_cache[fileno]

    enabled = False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        handle = kernel32.GetStdHandle(wintypes.DWORD(handle_id & 0xFFFFFFFF))
        invalid_handle = ctypes.c_void_p(-1).value
        if not handle or handle == invalid_handle:
            enabled = False
        else:
            mode = wintypes.DWORD()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # não é um console de verdade (pipe, arquivo, serviço sem console)
                enabled = False
            elif mode.value & _ENABLE_VIRTUAL_TERMINAL_PROCESSING:
                enabled = True
            else:
                enabled = bool(
                    kernel32.SetConsoleMode(
                        handle, mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
                    )
                )
    except Exception:
        # build antigo do Windows, ctypes indisponível, etc.
        enabled = False

    _vt_cache[fileno] = enabled
    return enabled


def supports_color(stream: IO[str] | None = None, force: bool | None = None) -> bool:
    """
    Decide se ``stream`` deve receber sequências ANSI.

    ``force=True``/``False`` sobrepõe a detecção automática (vem de
    ``JAYLOG_LOG_CONSOLE_COLOR``); mesmo com ``force=True`` o modo VT do Windows
    ainda é ligado, senão o "forçar" só produziria lixo na tela.

    Sem override, respeita as convenções ``NO_COLOR`` e ``FORCE_COLOR``, exige
    um TTY e, no Windows, exige que o modo VT tenha sido habilitado com sucesso.
    """
    stream = stream if stream is not None else sys.stdout

    if sys.platform == "win32":
        vt_enabled = _enable_windows_vt(stream)
    else:
        vt_enabled = True

    if force is not None:
        return force

    if _env_flag("NO_COLOR"):
        return False

    forced_by_env = _env_flag("FORCE_COLOR")
    if forced_by_env is not None:
        return forced_by_env

    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False

    if os.environ.get("TERM", "").strip().lower() in {"dumb", "unknown"}:
        return False

    return vt_enabled
