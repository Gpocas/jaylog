"""
Shim de depreciação de ``jaylog.models``.

``LogEntry`` era código morto: nunca foi construído em lugar nenhum, e o tipo
declarado (``log_img: Optional[str]``) nem batia com os ``bytes`` que o handler
HTTP envia. Fica acessível por mais um release via ``jaylog.LogEntry``, que
emite ``DeprecationWarning``. Remover na 0.4.0.
"""

from datetime import datetime

from pydantic import BaseModel


class LogEntry(BaseModel):
    log_timestamp: datetime
    log_level: str
    is_exception: bool
    log_message: str
    service: str
    username: str
    hostname: str
    ipv4: str
    service_path: str
    line_number: int
    log_img: str | None = None


__all__ = ["LogEntry"]
