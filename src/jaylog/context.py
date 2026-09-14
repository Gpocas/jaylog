"""
Enriquecimento do ``LogRecord``, memoizado **no próprio record**.

O problema que isto resolve: ``build_log_entry_dict()`` é chamado uma vez por
handler downstream (arquivo, console, HTTP) e mais uma vez dentro do
``PlainTextFormatter``. Com screenshot ligado, o mesmo erro capturava a tela até
três vezes — três ``ImageGrab.grab()`` sequenciais, cada um na casa das centenas
de milissegundos, para produzir três imagens quase idênticas das quais duas eram
descartadas. Memoizar no record resolve porque o record é exatamente o escopo do
"uma vez por evento de log".

O screenshot saiu do caminho compartilhado: ``record_screenshot()`` é chamado só
pelo ``JaylogHttpHandler``, que é o único com para onde mandá-lo.

.. warning::
   Este enriquecimento tem que continuar **downstream da fila**.
   ``QueueHandler.prepare()`` já faz ``record.msg = self.format(record)`` e zera
   ``exc_info``; por isso, aqui embaixo, ``record.exc_info`` é sempre ``None`` e
   o traceback sobrevive apenas por já estar embutido em ``getMessage()``. Mover
   o enriquecimento para antes da fila (um ``Filter`` no ``QueueHandler``, por
   exemplo) duplicaria o traceback — e ainda rodaria na thread do chamador.
   Há um teste de regressão travando isso.
"""

import logging
import traceback
from datetime import datetime, timezone

from jaylog.host import identity
from jaylog.screenshot import capture_screenshot

_ENTRY_ATTR = "_jaylog_entry"
_SCREENSHOT_ATTR = "_jaylog_screenshot"


def build_log_entry_dict(record: logging.LogRecord) -> dict:
    """
    Campos do log, sem ``log_img``.

    ``hostname``/``username``/``ipv4`` continuam no corpo de todo log — a
    tabela ``logs`` os mantém desnormalizados de forma permanente, então não há
    o que migrar.
    """
    is_exception = getattr(record, "is_exception", False)

    log_message = record.getMessage()
    if is_exception and record.exc_info:
        tb = "".join(traceback.format_exception(*record.exc_info)).strip()
        log_message = f"{log_message}\n{tb}"

    return {
        "log_timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        "log_level": "EXCEPTION" if is_exception else record.levelname,
        "is_exception": is_exception,
        "log_message": log_message,
        "service": record.name,
        "username": identity.username(),
        "hostname": identity.hostname(),
        "ipv4": identity.ipv4(),
        "service_path": record.pathname,
        "line_number": record.lineno,
    }


def record_entry(record: logging.LogRecord) -> dict:
    """``build_log_entry_dict`` com o resultado guardado no record."""
    entry = getattr(record, _ENTRY_ATTR, None)
    if entry is None:
        entry = build_log_entry_dict(record)
        setattr(record, _ENTRY_ATTR, entry)
    return entry


def wants_screenshot(record: logging.LogRecord) -> bool:
    return record.levelno >= logging.ERROR or bool(getattr(record, "is_exception", False))


def record_screenshot(record: logging.LogRecord) -> bytes | None:
    """
    Screenshot do evento, capturado no máximo uma vez por record.

    O sentinela guardado no record distingue "ainda não tentamos" de "tentamos e
    não veio nada" — sem ele, um ``None`` legítimo faria a próxima chamada
    capturar de novo.
    """
    if _SCREENSHOT_ATTR in record.__dict__:
        return record.__dict__[_SCREENSHOT_ATTR]
    image = capture_screenshot() if wants_screenshot(record) else None
    record.__dict__[_SCREENSHOT_ATTR] = image
    return image


__all__ = ["build_log_entry_dict", "record_entry", "record_screenshot", "wants_screenshot"]
