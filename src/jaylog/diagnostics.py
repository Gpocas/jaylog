"""Diagnósticos internos e seus destinos de saída configuráveis."""

import os
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterable

VALID_HANDLERS = frozenset({"console", "file", "http"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_MAX_PENDING = 200

_override: bool | None = None
_configured_handlers: frozenset[str] | None = None
_dispatchers: dict[str, Callable[[str, frozenset[str]], None]] = {}
_pending: deque[str] = deque(maxlen=_MAX_PENDING)
_lock = threading.RLock()


def parse_handlers(value: str | Iterable[str]) -> frozenset[str]:
    """Normaliza uma lista ou uma sequência separada por vírgulas."""
    raw = value.split(",") if isinstance(value, str) else value
    handlers = frozenset(str(item).strip().lower() for item in raw if str(item).strip())
    invalid = handlers - VALID_HANDLERS
    if invalid:
        names = ", ".join(sorted(invalid))
        raise ValueError(f"handlers de diagnóstico inválidos: {names}")
    if not handlers:
        raise ValueError("ao menos um handler de diagnóstico deve ser informado")
    return handlers


def configure(enabled: bool, handlers: str | Iterable[str] = ("console",)) -> None:
    """Define o estado efetivo após a resolução de ``JaylogSettings``."""
    global _override, _configured_handlers
    parsed = parse_handlers(handlers)
    with _lock:
        _override = bool(enabled)
        _configured_handlers = parsed
        _dispatchers.clear()
        _pending.clear()


def reset() -> None:
    """Remove o estado em memória; destinado ao isolamento dos testes."""
    global _override, _configured_handlers
    with _lock:
        _override = None
        _configured_handlers = None
        _dispatchers.clear()
        _pending.clear()


def is_enabled() -> bool:
    """Retorna se o diagnóstico está ativo, inclusive antes de ``configure()``."""
    with _lock:
        override = _override
    if override is not None:
        return override
    return os.environ.get("JAYLOG_DEBUG", "").strip().lower() in _TRUE_VALUES


def handlers() -> frozenset[str]:
    """Retorna os destinos efetivos do diagnóstico."""
    with _lock:
        configured = _configured_handlers
    if configured is not None:
        return configured
    return parse_handlers(os.environ.get("JAYLOG_DEBUG_HANDLERS", "console"))


def register_dispatcher(
    key: str, dispatcher: Callable[[str, frozenset[str]], None]
) -> None:
    """Registra a fila de um logger e reproduz nela os eventos pendentes."""
    with _lock:
        _dispatchers[key] = dispatcher
        pending = tuple(_pending)
        destinations = handlers() - {"console"}
    if not destinations:
        return
    for line in pending:
        try:
            dispatcher(line, destinations)
        except Exception:
            pass


def unregister_dispatcher(key: str) -> None:
    """Remove um logger encerrado dos destinos de diagnóstico."""
    with _lock:
        _dispatchers.pop(key, None)


def emit(component: str, message: str) -> None:
    """Emite uma linha diagnóstica sem interferir na aplicação observada."""
    try:
        if not is_enabled():
            return
        line = f"[jaylog] debug {component}: {message}"
        destinations = handlers()
        with _lock:
            dispatchers = tuple(_dispatchers.values())
            if destinations - {"console"}:
                _pending.append(line)
        if "console" in destinations:
            print(line, file=sys.stderr)
        downstream = destinations - {"console"}
        if downstream:
            for dispatcher in dispatchers:
                try:
                    dispatcher(line, downstream)
                except Exception:
                    pass
    except Exception:
        # Diagnóstico é observabilidade auxiliar e nunca pode afetar o processo.
        pass


__all__ = [
    "VALID_HANDLERS",
    "configure",
    "emit",
    "handlers",
    "is_enabled",
    "parse_handlers",
    "register_dispatcher",
    "unregister_dispatcher",
]
