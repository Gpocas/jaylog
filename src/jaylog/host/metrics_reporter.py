"""
``JaylogMetricsReporter``: uma amostra de CPU/memória/disco por intervalo para o
``POST /logs/host-metrics``.

**Um por processo, e não um por ``app_name`` como o ``JaylogHostReporter``:**
CPU e memória são do processo. Dois loggers no mesmo processo mandariam a mesma
amostra duas vezes — e o backend a chaveia por ``run_id``, que é um só. O
``service`` do corpo serve apenas para o backend validar o serviço; o coletor
se vincula ao primeiro item elegível de ``configure()``.

Sem backoff próprio: o ciclo já é de 60 s. Falha transitória só mantém as
amostras no buffer para o próximo ciclo; o buffer é limitado a 1 h.
"""

import sys
import threading
import time
import warnings
from collections import deque

import requests
import urllib3

from jaylog._version import PROTOCOL_VERSION, __version__
from jaylog.host import reporter as host_reporter
from jaylog.runtime import RUN_ID

#: 60 amostras = 1 h de backend fora do ar com o intervalo padrão. Estourou,
#: as mais antigas saem: o gráfico perde o começo do buraco, não o fim.
MAX_BUFFERED = 60

_STOP_JOIN_TIMEOUT = 2.0

_unsupported_warned = False


def _warn(message: str) -> None:
    print(f"[jaylog] metrics: {message}", file=sys.stderr)


def seconds_until_next(interval: float, now: float) -> float:
    """
    Espera até o próximo múltiplo de ``interval`` no relógio de parede.

    Alinhar ao relógio (e não somar ``interval`` ao último despertar) dá uma
    amostra por minuto cheio — exatamente um ponto por bucket de 1 min no
    backend, sem minutos com duas amostras nem minutos vazios por deriva.
    """
    return interval - (now % interval)


class JaylogMetricsReporter:
    """
    Coleta e entrega amostras numa thread daemon.

    ``session``, ``sampler``, ``clock`` e ``request_resend`` são injetáveis para
    que os testes cubram a máquina de estados sem rede, sem psutil e sem dormir.
    """

    def __init__(
        self,
        service: str,
        endpoint: str,
        api_key: str,
        *,
        interval: float = 60.0,
        timeout: float = 5.0,
        proxy: str | None = None,
        verify: bool | str = False,
        session=None,
        sampler=None,
        clock=time.time,
        request_resend=None,
    ) -> None:
        self.service = service
        self.endpoint = endpoint
        self.interval = interval
        self.timeout = timeout
        self.verify = verify

        self.buffer: deque = deque(maxlen=MAX_BUFFERED)
        #: desligado pelo resto do processo (backend antigo, rejeição, sem psutil)
        self.disabled = False

        if sampler is None:
            from jaylog.host.metrics import MetricsSampler

            sampler = MetricsSampler()
        self._sampler = sampler
        self._clock = clock
        self._request_resend = request_resend or host_reporter.request_resend
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._session = session if session is not None else requests.Session()
        self._session.headers["x-api-key"] = api_key
        self._session.headers["x-jaylog-version"] = __version__
        self._session.headers["x-jaylog-protocol"] = str(PROTOCOL_VERSION)
        self._session.headers["x-jaylog-run-id"] = RUN_ID
        if proxy:
            self._session.proxies.update({"http": proxy, "https": proxy})

    # ------------------------------------------------------------------
    # ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None or self.disabled:
            return
        if not getattr(self._sampler, "available", True):
            self.disabled = True
            _warn("psutil indisponível; a coleta de métricas foi desativada neste processo")
            return
        self._thread = threading.Thread(target=self._run, name="jaylog-metrics", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = _STOP_JOIN_TIMEOUT) -> None:
        """
        Para a thread e envia uma última amostra, para a execução não terminar
        com um buraco no minuto final.

        A amostra final só sai se a thread já terminou: se ela ainda estiver
        presa num POST, disputar o buffer com ela não vale o risco.
        """
        deadline = time.monotonic() + timeout
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        if thread.is_alive():
            thread.join(timeout)
        if thread.is_alive() or self.disabled:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        self.collect()
        self.flush(timeout=min(self.timeout, remaining))

    def _run(self) -> None:
        try:
            self._sampler.sample()  # só a base dos deltas de CPU e E/S
            while not self._stop.wait(seconds_until_next(self.interval, self._clock())):
                self.collect()
                self.flush()
                if self.disabled:
                    return
        except Exception as exc:  # pragma: no cover - sampler já é @safe
            _warn(f"coletor encerrado por erro inesperado: {exc}")

    # ------------------------------------------------------------------
    # coleta e entrega (síncronas — os testes chamam direto)
    # ------------------------------------------------------------------

    def collect(self) -> None:
        sample = self._sampler.sample()
        if sample is not None:
            self.buffer.append(sample)

    def flush(self, timeout: float | None = None) -> bool:
        """POST do buffer inteiro. ``True`` = entregue (ou nada a entregar)."""
        global _unsupported_warned

        if self.disabled:
            return False
        if not self.buffer:
            return True

        batch = list(self.buffer)
        body = {"run_id": RUN_ID, "service": self.service, "samples": batch}

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                response = self._session.post(
                    self.endpoint,
                    json=body,
                    timeout=self.timeout if timeout is None else timeout,
                    verify=self.verify,
                )
        except Exception:
            return False  # rede fora, DNS, timeout: fica para o próximo ciclo

        status = response.status_code

        if 200 <= status < 300:
            # Só esta thread mexe no buffer enquanto ela vive (o stop() espera o
            # join), então as primeiras `len(batch)` entradas são as enviadas.
            for _ in batch:
                self.buffer.popleft()
            if response.headers.get("x-jaylog-host-required") == "1":
                self._request_resend(self.service)
            return True

        if status == 429 or status >= 500:
            return False

        self.disabled = True
        self.buffer.clear()

        if status in (404, 405):
            # Backend anterior à rota. Mesmo contrato do /logs/host: "serviço
            # desconhecido" é 422, nunca 404, para este caso ser inequívoco.
            if not _unsupported_warned:
                _unsupported_warned = True
                _warn(
                    f"o backend não suporta POST {self.endpoint} (HTTP {status}); "
                    "a coleta de métricas foi desativada neste processo"
                )
            return False

        _warn(
            f"POST {self.endpoint} devolveu HTTP {status}; coleta desativada: "
            f"{_body_excerpt(response)}"
        )
        return False


def _body_excerpt(response, limit: int = 300) -> str:
    try:
        return response.text[:limit]
    except Exception:
        return "<corpo ilegível>"


# ----------------------------------------------------------------------
# singleton de módulo
# ----------------------------------------------------------------------

_active: JaylogMetricsReporter | None = None
_lock = threading.Lock()


def start(reporter: JaylogMetricsReporter) -> None:
    global _active
    with _lock:
        previous, _active = _active, reporter
    if previous is not None and previous is not reporter:
        previous.stop()
    reporter.start()


def stop(timeout: float = _STOP_JOIN_TIMEOUT) -> None:
    global _active
    with _lock:
        current, _active = _active, None
    if current is not None:
        current.stop(timeout)


def active() -> JaylogMetricsReporter | None:
    with _lock:
        return _active


__all__ = [
    "JaylogMetricsReporter",
    "MAX_BUFFERED",
    "active",
    "seconds_until_next",
    "start",
    "stop",
]
