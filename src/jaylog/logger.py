import atexit
import logging
import signal
from logging.handlers import QueueHandler, QueueListener
from queue import Queue

from jaylog.formatters import configure_screenshot
from jaylog.handlers.file_handler import JaylogFileHandler
from jaylog.handlers.http_handler import JaylogHttpHandler
from jaylog.settings import JaylogSettings

# Registry: name -> (logger, listener) so callers can shut down cleanly
_registry: dict[str, tuple[logging.Logger, QueueListener]] = {}
_shutdown_registered = False


def _register_shutdown_hooks() -> None:
    global _shutdown_registered
    if _shutdown_registered:
        return
    _shutdown_registered = True

    atexit.register(shutdown)

    def _sigterm_handler(signum, frame):  # noqa: ANN001
        shutdown()
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.raise_signal(signal.SIGTERM)

    signal.signal(signal.SIGTERM, _sigterm_handler)


def get_logger(settings: JaylogSettings | None = None) -> logging.Logger:
    """
    Return a configured logger.

    The logger name is read from JAYLOG_LOGGER_NAME (default: "jaylog").
    Calling this multiple times with the same logger name returns the **same**
    logger without re-attaching handlers.

    Architecture:
        logger  →  QueueHandler  →  Queue  →  QueueListener  →  [FileHandler, HttpHandler?]

    The QueueListener runs in a background thread so `emit()` never blocks the
    calling thread.
    """
    if settings is None:
        settings = JaylogSettings()

    name = settings.app_name

    configure_screenshot(settings.log_screenshot_enabled)

    if name in _registry:
        return _registry[name][0]

    # ------------------------------------------------------------------
    # Build the actual (downstream) handlers
    # ------------------------------------------------------------------
    downstream: list[logging.Handler] = []

    log_path = settings.log_dir / settings.log_filename
    file_handler = JaylogFileHandler(
        filename=log_path,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        retention_days=settings.log_retention_days,
    )
    file_handler.setLevel(settings.log_level)
    downstream.append(file_handler)

    if settings.log_http_endpoint and settings.log_http_api_key:
        http_handler = JaylogHttpHandler(
            endpoint=settings.log_http_endpoint,
            api_key=settings.log_http_api_key,
            timeout=settings.log_http_timeout,
            proxy=settings.log_http_proxy,
            proxy_user=settings.log_http_proxy_user,
            proxy_password=settings.log_http_proxy_password,
        )
        http_handler.setLevel(settings.log_level)
        downstream.append(http_handler)

    # ------------------------------------------------------------------
    # Wire up the Queue + QueueListener
    # ------------------------------------------------------------------
    queue: Queue = Queue(maxsize=-1)  # unbounded
    queue_handler = QueueHandler(queue)

    listener = QueueListener(queue, *downstream, respect_handler_level=True)
    listener.start()

    # ------------------------------------------------------------------
    # Configure the logger
    # ------------------------------------------------------------------
    logger = logging.getLogger(name)
    logger.setLevel(settings.log_level)
    logger.addHandler(queue_handler)
    logger.propagate = False

    _registry[name] = (logger, listener)
    _register_shutdown_hooks()
    return logger


def shutdown(name: str | None = None) -> None:
    """
    Stop the QueueListener(s) gracefully, flushing any remaining records.

    Pass a logger `name` to stop a single logger, or omit to stop all.
    """
    targets = [name] if name else list(_registry.keys())
    for n in targets:
        if n in _registry:
            _, listener = _registry.pop(n)
            listener.stop()
