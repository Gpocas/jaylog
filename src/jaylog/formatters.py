"""
Formatação legível para arquivo.

O módulo encolheu: identidade da máquina foi para ``host/identity.py`` (lazy),
screenshot para ``screenshot.py`` e a montagem do dicionário de log para
``context.py``. ``build_log_entry_dict`` continua reexportado aqui por compat —
remover na 0.4.0.
"""

import logging
from datetime import datetime

from jaylog.context import build_log_entry_dict, record_entry
from jaylog.screenshot import configure_screenshot

__all__ = ["PlainTextFormatter", "build_log_entry_dict", "configure_screenshot"]


class PlainTextFormatter(logging.Formatter):
    """Human-readable single-line formatter for .log files."""

    def __init__(self, show_service: bool = True) -> None:
        super().__init__()
        self.show_service = show_service

    def format(self, record: logging.LogRecord) -> str:
        entry = record_entry(record)
        log_timestamp = (
            datetime.fromisoformat(entry["log_timestamp"]).astimezone().strftime("%d/%m/%Y %X")
        )
        log_level = f"[{entry['log_level']}]"
        service_segment = f"[{entry['service']}] | " if self.show_service else ""
        return f"{log_timestamp} {log_level.ljust(11)} | {service_segment}{entry['log_message']}"
