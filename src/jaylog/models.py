from datetime import datetime
from typing import Optional
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
    log_img: Optional[str] = None
