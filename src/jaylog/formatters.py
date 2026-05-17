import getpass
import logging
import socket
import traceback
from datetime import datetime, timezone


def _get_host_info() -> tuple[str, str, str]:
    hostname = socket.gethostname()
    try:
        host_ip = socket.gethostbyname(hostname)
    except OSError:
        host_ip = "unknown"
    try:
        username = getpass.getuser()
    except Exception:
        username = "unknown"
    return username, hostname, host_ip


_HOST_USERNAME, _HOSTNAME, _HOST_IP = _get_host_info()


def build_log_entry_dict(record: logging.LogRecord) -> dict:
    is_exception = record.exc_info is not None and record.exc_info[0] is not None

    traceback_msg: str | None = None
    if is_exception and record.exc_info:
        traceback_msg = "".join(traceback.format_exception(*record.exc_info)).strip()

    return {
        "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
        "level": record.levelname,
        "is_exception": is_exception,
        "msg": record.getMessage(),
        "traceback_msg": traceback_msg,
        "logger_name": record.name,
        "host_username": _HOST_USERNAME,
        "hostname": _HOSTNAME,
        "host_ip": _HOST_IP,
    }


class PlainTextFormatter(logging.Formatter):
    """Human-readable single-line formatter for .log files."""

    def format(self, record: logging.LogRecord) -> str:
        entry = build_log_entry_dict(record)
        line = (
            f"{entry['timestamp']} [{entry['level']}]"
            f" {entry['hostname']}({entry['host_ip']}) {entry['host_username']}"
            f" | {entry['msg']}"
        )
        if entry["traceback_msg"]:
            line += f"\n{entry['traceback_msg']}"
        return line
