import logging
import sys
from datetime import datetime

from jaylog.formatters import build_log_entry_dict

_GREEN = "\033[32m"
_BLUE = "\033[34m"
_PURPLE = "\033[35m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_RESET = "\033[0m"

_LEVEL_COLORS = {
    "DEBUG": _PURPLE,
    "INFO": _BLUE,
    "WARNING": _YELLOW,
}


def _level_color(level: str) -> str:
    return _LEVEL_COLORS.get(level, _RED)


class ConsoleFormatter(logging.Formatter):
    def __init__(self, show_service: bool = True) -> None:
        super().__init__()
        self.show_service = show_service

    def format(self, record: logging.LogRecord) -> str:
        entry = build_log_entry_dict(record)
        log_timestamp = datetime.fromisoformat(entry["log_timestamp"]).strftime("%d/%m/%Y %X")
        log_level = f'[{entry["log_level"]}]'
        colored_timestamp = f"{_GREEN}{log_timestamp}{_RESET}"
        colored_level = f"{_level_color(entry['log_level'])}{log_level.ljust(11)}{_RESET}"
        service_segment = f'[{entry["service"]}] | ' if self.show_service else ''
        return f"{colored_timestamp} {colored_level} | {service_segment}{entry['log_message']}"


class JaylogConsoleHandler(logging.StreamHandler):
    def __init__(self, show_service: bool = True) -> None:
        super().__init__(stream=sys.stdout)
        self.setFormatter(ConsoleFormatter(show_service=show_service))
