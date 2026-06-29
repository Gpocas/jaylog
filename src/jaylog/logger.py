import atexit
import logging
import signal
from logging.handlers import QueueHandler, QueueListener
from queue import Queue

from jaylog.filters import ExceptionFlagFilter
from jaylog.formatters import configure_screenshot
from jaylog.handlers.file_handler import JaylogFileHandler
from jaylog.handlers.http_handler import JaylogHttpHandler
from jaylog.settings import JaylogSettings

# Registry: name -> (logger, listener) so callers can shut down cleanly
_registry: dict[str, tuple[logging.Logger, QueueListener]] = {}
_shutdown_registered = False
# name (== app_name) -> settings registrada via configure()
_settings_registry: dict[str, JaylogSettings] = {}


def configure(settings: JaylogSettings | list[JaylogSettings]) -> None:
    """
    Registra uma ou mais configurações de logger.

    Aceita uma única ``JaylogSettings`` ou uma lista delas. Cada configuração é
    registrada sob o seu próprio ``app_name``, permitindo vários loggers com
    configurações diferentes no mesmo projeto::

        configure(settings_order)
        configure([settings_order, settings_billing])

    Cada chamada é a "fonte da verdade" do conjunto de loggers: derruba os
    loggers registrados anteriormente e registra apenas os informados aqui.
    Levanta ``ValueError`` se a lista contiver ``app_name`` duplicado.
    """
    items = [settings] if isinstance(settings, JaylogSettings) else list(settings)

    seen: set[str] = set()
    for item in items:
        if item.app_name in seen:
            raise ValueError(
                f"app_name duplicado em configure(): '{item.app_name}'"
            )
        seen.add(item.app_name)

    shutdown()
    _settings_registry.clear()
    for item in items:
        _settings_registry[item.app_name] = item


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


def get_logger(name: str | None = None) -> logging.Logger:
    """
    Return a configured logger.

    O ``name`` deve ser exatamente o ``app_name`` de uma configuração registrada
    via ``configure()``. Quando omitido, retorna o logger da **primeira**
    configuração registrada. Chamadas repetidas com o mesmo nome retornam o
    **mesmo** logger, sem re-anexar handlers.

    Architecture:
        logger  →  QueueHandler  →  Queue  →  QueueListener  →  [FileHandler, HttpHandler?]

    The QueueListener runs in a background thread so `emit()` never blocks the
    calling thread.
    """
    if not _settings_registry:
        raise Exception(
            'Não é possivel retornar uma instancia de logger sem configuração\n'
            'use jaylog.configure() antes de jaylog.get_logger()'
        )

    if name is None:
        name = next(iter(_settings_registry))
    elif name not in _settings_registry:
        disponiveis = ', '.join(_settings_registry) or '(nenhum)'
        raise KeyError(
            f"Nenhuma configuração registrada para '{name}'. "
            f"Nomes disponíveis: {disponiveis}"
        )

    settings = _settings_registry[name]

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
            proxy=settings.log_http_proxy,
            timeout=settings.log_http_timeout,
        )
        http_handler.setLevel(settings.log_level)
        downstream.append(http_handler)

    # ------------------------------------------------------------------
    # Wire up the Queue + QueueListener
    # ------------------------------------------------------------------
    queue: Queue = Queue(maxsize=-1)  # unbounded
    queue_handler = QueueHandler(queue)
    queue_handler.addFilter(ExceptionFlagFilter())

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
