"""
``JaylogHostReporter``: entrega o retrato do ambiente ao ``POST /logs/host``.

**Por que uma classe separada, e não um método do ``JaylogHttpHandler``:**

1. Não é dirigido por record. Tem ciclo de vida de minutos, com sleeps — algo
   absurdo dentro de um ``logging.Handler``, cujo contrato é "trate este
   registro agora".
2. ``JaylogHttpHandler`` é construído em ``_build_logger()``, que via
   ``_LazyLogger`` **pode nunca rodar**. O registro de host tem que sair de
   qualquer jeito, porque é ele que dá sentido aos logs que já saíram.
3. O handler é destruído e recriado a cada ``shutdown()``/``configure()``. O
   registro de host, não: ele é do processo.

A comunicação nos dois sentidos passa pelo registry de módulo — o handler
chama ``request_resend(service)`` ao ver ``x-jaylog-host-required: 1`` sem
precisar ser dono do reporter.
"""

import sys
import threading
import time
import warnings

import requests
import urllib3

from jaylog._version import PROTOCOL_VERSION, __version__
from jaylog.host.payload import build_host_payload
from jaylog.runtime import RUN_ID

#: Pausas entre tentativas: ~10 min de insistência no total. Depois disso o
#: reporter dorme num Event até que alguém peça reenvio — continuar batendo
#: num backend fora do ar não traz o registro de volta mais cedo.
DEFAULT_BACKOFF = (1, 2, 5, 15, 30, 60, 120, 300, 300)

#: Uma rajada de logs gera uma rajada de ``x-jaylog-host-required``. O backend
#: não faz debounce (o header sai em toda linha afetada), então ele é aqui.
RESEND_DEBOUNCE_SECONDS = 30.0

_STOP_JOIN_TIMEOUT = 2.0

_unsupported_warned: set[str] = set()


def _warn(message: str, label: str = "host") -> None:
    print(f"[jaylog] {label}: {message}", file=sys.stderr)


class JaylogHostReporter:
    """
    Entrega — com retry — um ``HostInfo`` para um ``service``.

    ``session`` e ``backoff`` são injetáveis para que os testes cubram a
    máquina de estados inteira sem rede e sem dormir no relógio.
    """

    def __init__(
        self,
        service: str,
        endpoint: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        proxy: str | None = None,
        verify: bool | str = False,
        session=None,
        backoff=DEFAULT_BACKOFF,
        payload_factory=None,
        label: str = "host",
        noun: str = "ambiente",
        one_shot: bool = False,
    ) -> None:
        self.service = service
        self.endpoint = endpoint
        self.timeout = timeout
        self.verify = verify
        self.label = label
        self.noun = noun
        self._one_shot = one_shot

        #: entregue com sucesso — não há mais nada a fazer
        self.sent = False
        #: backend antigo (404/405): desligado permanentemente neste processo
        self.unsupported = False
        #: rejeição definitiva (401/403/422): retry não ajuda
        self.fatal = False

        self._backoff = tuple(backoff)
        self._payload_factory = payload_factory or (lambda: build_host_payload(self.service))
        self._stop = threading.Event()
        self._wakeup = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_resend = 0.0

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
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"jaylog-{self.label}-{self.service}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = _STOP_JOIN_TIMEOUT) -> None:
        self._stop.set()
        self._wakeup.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            # daemon: mesmo que o join estoure, a thread nunca segura a saída
            # do processo além deste timeout.
            thread.join(timeout)

    def request_resend(self) -> bool:
        """
        Sinaliza que o backend não conhece este ``run_id``.

        Devolve ``True`` se o sinal foi aceito, ``False`` se caiu no debounce ou
        se o reporter já desistiu em definitivo.
        """
        if self.unsupported or self.fatal or self._stop.is_set():
            return False
        now = self._now()
        if now - self._last_resend < RESEND_DEBOUNCE_SECONDS:
            return False
        self._last_resend = now
        self.sent = False
        self._wakeup.set()
        return True

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    # ------------------------------------------------------------------
    # loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            self.deliver()
            if self._stop.is_set() or self._one_shot:
                return
            # dorme até um request_resend() (ou até o stop). Sem polling.
            self._wakeup.wait()
            self._wakeup.clear()

    def deliver(self) -> None:
        """Uma rodada completa de tentativas. Síncrona — os testes chamam direto."""
        if self.unsupported or self.fatal:
            return
        for delay in (0.0, *self._backoff):
            if delay and self._stop.wait(delay):
                return
            if self._stop.is_set():
                return
            outcome = self._post_once()
            if outcome is True:
                self.sent = True
                return
            if outcome is None:
                return

    def _post_once(self) -> bool | None:
        """``True`` = entregue, ``False`` = tentar de novo, ``None`` = desistir."""
        try:
            payload = self._payload_factory()
        except Exception as exc:  # pragma: no cover - coletor já é @safe
            _warn(f"falha ao montar o payload de {self.service}: {exc}", self.label)
            self.fatal = True
            return None

        if payload is None:
            return None

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                response = self._session.post(
                    self.endpoint,
                    json=payload,
                    timeout=self.timeout,
                    verify=self.verify,
                )
        except Exception:
            return False  # rede fora, DNS, timeout: exatamente o caso do retry

        status = response.status_code

        if 200 <= status < 300:
            return True

        if status in (404, 405):
            # Backend anterior à 0.3: a rota não existe. Insistir só gastaria
            # banda até o fim do processo. É por isso que o contrato exige que
            # "serviço desconhecido" devolva 422, e nunca 404.
            self.unsupported = True
            if self.label not in _unsupported_warned:
                _unsupported_warned.add(self.label)
                _warn(
                    f"o backend não suporta POST {self.endpoint} (HTTP {status}); "
                    f"o registro de {self.noun} foi desativado neste processo",
                    self.label,
                )
            return None

        if status in (401, 403):
            self.fatal = True
            _warn(f"autenticação recusada em {self.endpoint} (HTTP {status})", self.label)
            return None

        if status == 422:
            self.fatal = True
            _warn(
                f"payload de {self.noun} rejeitado para '{self.service}' (HTTP 422): "
                f"{_body_excerpt(response)}",
                self.label,
            )
            return None

        if status == 429 or status >= 500:
            return False

        # demais 4xx: erro do cliente, repetir não muda o resultado
        self.fatal = True
        _warn(f"POST {self.endpoint} devolveu HTTP {status}; desistindo", self.label)
        return None


def _body_excerpt(response, limit: int = 300) -> str:
    try:
        return response.text[:limit]
    except Exception:
        return "<corpo ilegível>"


# ----------------------------------------------------------------------
# registry de módulo
# ----------------------------------------------------------------------

_reporters: dict[str, JaylogHostReporter] = {}
_registry_lock = threading.Lock()


def register(reporter: JaylogHostReporter) -> None:
    with _registry_lock:
        existing = _reporters.get(reporter.service)
        if existing is not None and existing is not reporter:
            existing.stop()
        _reporters[reporter.service] = reporter


def request_resend(service: str) -> bool:
    """Chamado pelo handler HTTP ao ver ``x-jaylog-host-required: 1``."""
    with _registry_lock:
        reporter = _reporters.get(service)
    if reporter is None:
        return False
    return reporter.request_resend()


def stop(service: str, timeout: float = _STOP_JOIN_TIMEOUT) -> None:
    with _registry_lock:
        reporter = _reporters.pop(service, None)
    if reporter is not None:
        reporter.stop(timeout)


def stop_all(timeout: float = _STOP_JOIN_TIMEOUT) -> None:
    with _registry_lock:
        reporters = list(_reporters.values())
        _reporters.clear()
    for reporter in reporters:
        reporter.stop(timeout)


def active_reporters() -> dict[str, JaylogHostReporter]:
    with _registry_lock:
        return dict(_reporters)


__all__ = [
    "JaylogHostReporter",
    "DEFAULT_BACKOFF",
    "RESEND_DEBOUNCE_SECONDS",
    "register",
    "request_resend",
    "stop",
    "stop_all",
    "active_reporters",
]
