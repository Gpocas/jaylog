from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class LogEntry(BaseModel):
    timestamp: datetime
    level: str
    is_exception: bool
    msg: str
    traceback_msg: Optional[str] = None
    logger_name: str
    host_username: str
    hostname: str
    host_ip: str
