import atexit
import logging
import signal
import sys
from logging.handlers import QueueHandler, QueueListener
from queue import Queue
from typing import cast

from jaylog.filters import ExceptionFlagFilter
from jaylog.handlers.console_handler import JaylogConsoleHandler
from jaylog.handlers.file_handler import JaylogFileHandler
from jaylog.handlers.http_handler import JaylogHttpHandler
from jaylog.host import metrics_reporter, reporter
from jaylog.host.collectors import collect_host_info
from jaylog.host.metrics_reporter import JaylogMetricsReporter
from jaylog.host.payload import build_host_payload
from jaylog.host.reporter import JaylogHostReporter
from jaylog.screenshot import configure_screenshot
from jaylog.settings import JaylogSettings

# Registry: name -> (logger, listener, queue_handler) so callers can shut down
# cleanly. O QueueHandler entrou na tupla porque sem ele `shutdown()` não tinha
# como desanexá-lo do logger.
_registry: dict[str, tuple[logging.Logger, QueueListener, QueueHandler]] = {}
_shutdown_registered = False
# name (== app_name) -> settings registrada via configure()
_settings_registry: dict[str, JaylogSettings] = {}

_insecure_transport_warned = False


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

    É também aqui que o registro de ambiente (``POST /logs/host``) é disparado,
    numa thread daemon por serviço. O import é cedo demais — não há ``app_name``,
    endpoint nem api key — e o primeiro ``_build_logger()`` é tarde demais e
    incerto: via ``_LazyLogger`` ele pode demorar minutos ou nunca acontecer, e
    o registro de host chegaria *depois* dos logs que apontam para ele.
    ``configure()`` é o momento documentado em que "o jaylog começa".

    A coleta em si (snapshot de processos + até seis subprocessos ``git``) custa
    ~100-500 ms e roda inteira dentro da thread: ``configure()`` continua
    instantâneo para o chamador.
    """
    items = [settings] if isinstance(settings, JaylogSettings) else list(settings)

    seen: set[str] = set()
    for item in items:
        if item.app_name in seen:
            raise ValueError(f"app_name duplicado em configure(): '{item.app_name}'")
        seen.add(item.app_name)

    shutdown()
    _settings_registry.clear()
    for item in items:
        _settings_registry[item.app_name] = item

    _warn_insecure_transport(items)
    _start_host_reporters(items)
    _start_metrics_reporter(items)


def _warn_insecure_transport(items: list[JaylogSettings]) -> None:
    """
    Aviso único sobre ``log_http_verify=False``.

    O padrão continua inseguro na 0.3.x de propósito (ver ``JaylogSettings``),
    mas silêncio total transformaria a dívida em esquecimento.
    """
    global _insecure_transport_warned
    if _insecure_transport_warned:
        return
    for item in items:
        endpoint = item.log_http_endpoint or ""
        if endpoint.startswith("https://") and item.log_http_verify is False:
            _insecure_transport_warned = True
            print(
                "[jaylog] o certificado TLS do endpoint de log NÃO está sendo "
                "verificado (padrão atual). Defina "
                "JAYLOG_LOG_HTTP_VERIFY=/caminho/ca-bundle.pem (ou =true) para "
                "habilitar. O padrão passa a ser 'true' na 0.4.0.",
                file=sys.stderr,
            )
            return


def _host_payload_factory(settings: JaylogSettings):
    def factory() -> dict:
        info = collect_host_info(
            git_enabled=settings.host_git_enabled,
            git_dir=settings.host_git_dir,
            git_timeout=settings.host_git_timeout,
            git_dirty_enabled=settings.host_git_dirty_enabled,
            git_remote_enabled=settings.host_git_remote_enabled,
        )
        return build_host_payload(settings.app_name, info)

    return factory


def _start_host_reporters(items: list[JaylogSettings]) -> None:
    for item in items:
        if not item.host_report_enabled:
            continue
        endpoint = item.effective_host_endpoint
        if not endpoint or not item.log_http_api_key:
            continue
        host_reporter = JaylogHostReporter(
            service=item.app_name,
            endpoint=endpoint,
            api_key=item.log_http_api_key,
            timeout=item.effective_host_timeout,
            proxy=item.log_http_proxy,
            verify=item.log_http_verify,
            payload_factory=_host_payload_factory(item),
        )
        reporter.register(host_reporter)
        host_reporter.start()


def _start_metrics_reporter(items: list[JaylogSettings]) -> None:
    """
    Um coletor de métricas por processo, vinculado ao primeiro item elegível.

    CPU e memória são do processo: um coletor por ``app_name`` mandaria a mesma
    amostra várias vezes. O ``service`` do vínculo só serve para o backend
    validar o serviço e para o pedido de reenvio do registro de host.
    """
    for item in items:
        if not (item.host_report_enabled and item.host_metrics_enabled):
            continue
        endpoint = item.effective_host_metrics_endpoint
        if not endpoint or not item.log_http_api_key:
            continue
        metrics_reporter.start(
            JaylogMetricsReporter(
                service=item.app_name,
                endpoint=endpoint,
                api_key=item.log_http_api_key,
                interval=item.host_metrics_interval,
                timeout=item.log_http_timeout,
                proxy=item.log_http_proxy,
                verify=item.log_http_verify,
            )
        )
        return


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

    # `signal.signal()` só funciona na main thread — e `_build_logger()` roda em
    # qualquer thread quando vem de um `_LazyLogger`, que é justamente o caminho
    # que a lib anuncia. Sem a proteção, o primeiro `logger.info()` de uma worker
    # thread levantaria `ValueError: signal only works in main thread`.
    #
    # A checagem de `SIG_DFL` cobre o outro lado: se a aplicação já instalou o
    # próprio handler de SIGTERM, o jaylog não o rouba.
    try:
        if signal.getsignal(signal.SIGTERM) is signal.SIG_DFL:
            signal.signal(signal.SIGTERM, _sigterm_handler)
    except (ValueError, OSError, AttributeError):
        pass


def get_logger(name: str | None = None) -> logging.Logger:
    """
    Return a configured logger.

    O ``name`` deve ser exatamente o ``app_name`` de uma configuração registrada
    via ``configure()``. Quando omitido, retorna o logger da **primeira**
    configuração registrada. Chamadas repetidas com o mesmo nome retornam o
    **mesmo** logger, sem re-anexar handlers.

    Se ``configure()`` ainda não tiver sido chamado, retorna um proxy
    preguiçoso: nenhum erro é levantado aqui. O erro só aparece no primeiro
    uso efetivo (``logger.info(...)``, ``logger.warning(...)`` etc.), o que
    permite fazer ``logger = get_logger()`` no topo de um módulo antes de
    ``configure()`` ter rodado em outro lugar.

    Architecture:
        logger  →  QueueHandler  →  Queue  →  QueueListener  →  [FileHandler, HttpHandler?]

    The QueueListener runs in a background thread so `emit()` never blocks the
    calling thread.
    """
    if not _settings_registry:
        # _LazyLogger não é um logging.Logger de verdade: é um proxy que
        # delega (via __getattr__) para o logger real assim que ele existir.
        return cast(logging.Logger, _LazyLogger(name))
    return _build_logger(name)


def _build_logger(name: str | None) -> logging.Logger:
    if not _settings_registry:
        raise Exception(
            "Não é possivel retornar uma instancia de logger sem configuração\n"
            "use jaylog.configure() antes de jaylog.get_logger()"
        )

    if name is None:
        name = next(iter(_settings_registry))
    elif name not in _settings_registry:
        disponiveis = ", ".join(_settings_registry) or "(nenhum)"
        raise KeyError(
            f"Nenhuma configuração registrada para '{name}'. Nomes disponíveis: {disponiveis}"
        )

    settings = _settings_registry[name]

    configure_screenshot(settings.log_screenshot_enabled)

    if name in _registry:
        return _registry[name][0]

    # ------------------------------------------------------------------
    # Build the actual (downstream) handlers
    # ------------------------------------------------------------------
    downstream: list[logging.Handler] = []
    show_service = len(_settings_registry) > 1

    if settings.log_dir is not None:
        assert settings.log_filename is not None
        log_path = settings.log_dir / settings.log_filename
        file_handler = JaylogFileHandler(
            filename=log_path,
            max_bytes=settings.log_max_bytes,
            backup_count=settings.log_backup_count,
            retention_days=settings.log_retention_days,
            show_service=show_service,
        )
        file_handler.setLevel(settings.log_level)
        downstream.append(file_handler)

    if settings.log_console_enabled:
        console_handler = JaylogConsoleHandler(
            show_service=show_service,
            color=settings.log_console_color,
        )
        console_handler.setLevel(settings.log_level)
        downstream.append(console_handler)

    if settings.log_http_endpoint and settings.log_http_api_key:
        http_handler = JaylogHttpHandler(
            endpoint=settings.log_http_endpoint,
            api_key=settings.log_http_api_key,
            proxy=settings.log_http_proxy,
            timeout=settings.log_http_timeout,
            verify=settings.log_http_verify,
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

    _registry[name] = (logger, listener, queue_handler)
    _register_shutdown_hooks()
    return logger


class _LazyLogger:
    """
    Proxy devolvido por ``get_logger()`` quando ``configure()`` ainda não
    rodou. Resolve o logger real (e levanta o erro de configuração ausente,
    se for o caso) apenas no primeiro atributo acessado — ou seja, na
    primeira chamada de ``.info()``, ``.warning()`` etc.

    A resolução **não** é cacheada: um proxy que memorizasse o logger ficaria
    preso a ele depois de um ``configure()`` posterior, escrevendo para um
    listener já parado — os logs sumiriam sem erro. Resolver a cada acesso custa
    uma busca em dicionário, irrelevante perto do custo de formatar um log.

    Limitação conhecida: ``isinstance(get_logger(), logging.Logger)`` é ``False``
    enquanto o proxy não resolve. Não há conserto sem transformar o proxy numa
    subclasse real de ``Logger``.
    """

    def __init__(self, name: str | None) -> None:
        self._name = name

    def _resolve(self) -> logging.Logger:
        return _build_logger(self._name)

    def __getattr__(self, item):
        return getattr(self._resolve(), item)


def shutdown(name: str | None = None) -> None:
    """
    Stop the QueueListener(s) gracefully, flushing any remaining records.

    Pass a logger `name` to stop a single logger, or omit to stop all.

    Desanexa também o ``QueueHandler`` do logger. Sem isso — e como
    ``configure()`` chama ``shutdown()`` — um segundo ``configure()`` deixava no
    logger um ``QueueHandler`` apontando para um listener morto e adicionava
    outro por cima: os registros iam para uma fila que ninguém mais drenava.
    """
    targets = [name] if name else list(_registry.keys())
    for n in targets:
        if n in _registry:
            logger, listener, queue_handler = _registry.pop(n)
            # remover antes de parar o listener: nada novo entra na fila, e o
            # que já está nela ainda é drenado pelo `stop()`.
            logger.removeHandler(queue_handler)
            listener.stop()
            queue_handler.close()

    if name is None:
        reporter.stop_all()
        metrics_reporter.stop()
    else:
        reporter.stop(name)
        active = metrics_reporter.active()
        if active is not None and active.service == name:
            metrics_reporter.stop()
