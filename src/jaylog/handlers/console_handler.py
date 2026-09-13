import logging
import sys
from datetime import datetime
from typing import IO

from jaylog.colors import BLUE, GREEN, PURPLE, RED, RESET, YELLOW, supports_color
from jaylog.formatters import build_log_entry_dict

_LEVEL_COLORS = {
    "DEBUG": PURPLE,
    "INFO": BLUE,
    "WARNING": YELLOW,
}


def _level_color(level: str) -> str:
    return _LEVEL_COLORS.get(level, RED)


class ConsoleFormatter(logging.Formatter):
    """
    Formata o log para o console.

    Quando ``use_color`` é falso (console legado do Windows sem suporte a ANSI,
    saída redirecionada para arquivo/pipe, ``NO_COLOR`` etc.) a mesma linha é
    emitida sem nenhum escape code, em vez de sujar a tela com ``←[32m``.
    """

    def __init__(self, show_service: bool = True, use_color: bool = True) -> None:
        super().__init__()
        self.show_service = show_service
        self.use_color = use_color

    def _paint(self, text: str, color: str) -> str:
        if not self.use_color:
            return text
        return f"{color}{text}{RESET}"

    def format(self, record: logging.LogRecord) -> str:
        entry = build_log_entry_dict(record)
        log_timestamp = datetime.fromisoformat(entry["log_timestamp"]).astimezone().strftime("%d/%m/%Y %X")
        log_level = f'[{entry["log_level"]}]'
        colored_timestamp = self._paint(log_timestamp, GREEN)
        colored_level = self._paint(log_level.ljust(11), _level_color(entry['log_level']))
        service_segment = f'[{entry["service"]}] | ' if self.show_service else ''
        return f"{colored_timestamp} {colored_level} | {service_segment}{entry['log_message']}"


class JaylogConsoleHandler(logging.StreamHandler):
    """
    Handler de console.

    ``color=None`` (padrão) detecta automaticamente o suporte a ANSI do stream —
    ligando o modo VT do console do Windows quando disponível. ``True``/``False``
    forçam o comportamento.
    """

    def __init__(
        self,
        show_service: bool = True,
        color: bool | None = None,
        stream: IO[str] | None = None,
    ) -> None:
        stream = stream if stream is not None else sys.stdout
        super().__init__(stream=stream)
        self.setFormatter(
            ConsoleFormatter(
                show_service=show_service,
                use_color=supports_color(stream, force=color),
            )
        )
