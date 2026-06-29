import getpass
import io
import logging
import socket
import sys
import traceback
from datetime import datetime, timezone


def _get_host_info() -> tuple[str, str, str]:
    hostname = socket.gethostname()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            host_ip = s.getsockname()[0]
    except OSError:
        host_ip = "unknown"
    try:
        username = getpass.getuser()
    except Exception:
        username = "unknown"
    return username, hostname, host_ip


_HOST_USERNAME, _HOSTNAME, _HOST_IP = _get_host_info()

_screenshot_enabled: bool = True

_MAX_BYTES = 1 * 1024 * 1024  # 1 MB


def configure_screenshot(enabled: bool) -> None:
    global _screenshot_enabled
    _screenshot_enabled = enabled


def _capture_screenshot() -> bytes | None:
    if not _screenshot_enabled:
        return None
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab()

        quality = 85
        scale = 1.0
        buf = io.BytesIO()

        while True:
            buf.seek(0)
            buf.truncate()

            current = img
            if scale < 1.0:
                w = int(img.width * scale)
                h = int(img.height * scale)
                current = img.resize((w, h))

            current.save(buf, format="JPEG", quality=quality, optimize=True)

            if buf.tell() <= _MAX_BYTES:
                break

            if quality > 30:
                quality -= 10
            elif scale > 0.5:
                scale -= 0.1
            else:
                break

        buf.seek(0)
        return buf.read()
    except Exception as exc:
        print(f"[jaylog] screenshot: {exc}", file=sys.stderr)
        return None


def build_log_entry_dict(record: logging.LogRecord) -> dict:
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
        "username": _HOST_USERNAME,
        "hostname": _HOSTNAME,
        "ipv4": _HOST_IP,
        "service_path": record.pathname,
        "log_img": _capture_screenshot() if (record.levelno >= logging.ERROR or is_exception) else None,
    }


class PlainTextFormatter(logging.Formatter):
    """Human-readable single-line formatter for .log files."""

    def __init__(self, show_service: bool = True) -> None:
        super().__init__()
        self.show_service = show_service

    def format(self, record: logging.LogRecord) -> str:
        entry = build_log_entry_dict(record)
        log_timestamp = datetime.fromisoformat(entry['log_timestamp']).strftime('%d/%m/%Y %X')
        log_level = f'[{entry["log_level"]}]'
        service_segment = f'[{entry["service"]}] | ' if self.show_service else ''
        return (
            f'{log_timestamp} '
            f'{log_level.ljust(11)} | '
            f'{service_segment}'
            f"{entry['log_message']}"
        )
