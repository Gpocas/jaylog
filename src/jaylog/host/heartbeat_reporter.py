"""
``jaylog.heartbeat()``: sinal explícito de que o loop do usuário está progredindo.

**Uma thread por processo, um alvo por serviço.** A thread é criada na 1ª chamada
de ``beat()`` (quem não usa heartbeat não paga thread) e percorre os serviços com
beat pendente a cada ciclo. O estado é um contador por serviço, não um horário:
quem carimba a hora é o backend, então relógio de VM à deriva não atrapalha, e um
beat que chega durante o POST continua pendente (o contador lido *antes* do POST é
o que se marca como enviado).

**Endpoint e chave por serviço.** Cada ``JaylogSettings`` tem a própria URL e API
key; o POST usa as do item daquele serviço, passadas por requisição — a sessão não
fixa ``x-api-key`` como a de métricas faz, porque lá o destino é um só.

Sem buffer e sem backoff próprio: o ciclo já é de 60 s, e o único beat que importa
é o mais recente. Falha transitória só mantém o beat pendente.
"""

import sys
import threading
import time
import warnings
from dataclasses import dataclass

import requests
import urllib3

from jaylog._version import PROTOCOL_VERSION, __version__
from jaylog.diagnostics import emit as debug
from jaylog.host import reporter as host_reporter
from jaylog.runtime import RUN_ID

_STOP_JOIN_TIMEOUT = 2.0
_DEFAULT_INTERVAL = 60.0


def _warn(message: str) -> None:
    print(f"[jaylog] heartbeat: {message}", file=sys.stderr)


@dataclass(frozen=True)
class HeartbeatTarget:
    """Destino do heartbeat de um serviço: tudo vem do ``JaylogSettings`` dele."""

    service: str
    endpoint: str
    api_key: str
    interval: float = _DEFAULT_INTERVAL
    timeout: float = 5.0
    proxy: str | None = None
    #: seguro por padrão; quem decide o valor real é o `log_http_verify` do item,
    #: passado por `configure()` (hoje `False` por compatibilidade, `True` na 0.4.0)
    verify: bool | str = True


class JaylogHeartbeatReporter:
    """
    ``session`` e ``request_resend`` são injetáveis para os testes cobrirem a
    máquina de estados sem rede. ``autostart=False`` permite dirigir ``deliver()``
    à mão, sem a thread.
    """

    def __init__(self, *, session=None, request_resend=None, autostart: bool = True) -> None:
        self._session = session
        self._request_resend = request_resend or host_reporter.request_resend
        self._autostart = autostart
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        #: um Event por thread: um `stop()` que não espera a thread presa num POST
        #: não pode deixar um Event novo e "limpo" nas mãos dela.
        self._stop_event: threading.Event | None = None
        self._targets: dict[str, HeartbeatTarget] = {}
        self._beats: dict[str, int] = {}
        self._sent: dict[str, int] = {}
        self._disabled: set[str] = set()
        self._ignored: set[str] = set()

    # ------------------------------------------------------------------
    # estado
    # ------------------------------------------------------------------

    @property
    def targets(self) -> dict[str, HeartbeatTarget]:
        with self._lock:
            return dict(self._targets)

    @property
    def beats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._beats)

    @property
    def interval(self) -> float:
        """Vale o intervalo do primeiro alvo registrado (um por processo)."""
        with self._lock:
            for target in self._targets.values():
                return target.interval
        return _DEFAULT_INTERVAL

    def register(self, targets: list[HeartbeatTarget]) -> None:
        """Substitui os alvos e descarta todo o estado anterior."""
        with self._lock:
            self._targets = {t.service: t for t in targets}
            self._beats.clear()
            self._sent.clear()
            self._disabled.clear()
            self._ignored.clear()

    def remove(self, service: str) -> None:
        with self._lock:
            self._targets.pop(service, None)
            self._beats.pop(service, None)
            self._sent.pop(service, None)
            self._disabled.discard(service)

    def beat(self, service: str) -> bool:
        """
        Registra um beat. Só incrementa um contador sob lock: sem rede e sem
        bloquear, porque roda dentro do loop do usuário. ``False`` = ignorado
        (serviço sem alvo elegível, ou já desativado).
        """
        with self._lock:
            if service not in self._targets or service in self._disabled:
                if service not in self._ignored:
                    self._ignored.add(service)
                    debug(
                        "heartbeat",
                        f"beat ignorado; serviço={service}; motivo=sem alvo elegível ou desativado",
                    )
                return False
            self._beats[service] = self._beats.get(service, 0) + 1
            if self._autostart and (self._thread is None or not self._thread.is_alive()):
                self._start_locked()
        return True

    # ------------------------------------------------------------------
    # thread
    # ------------------------------------------------------------------

    def _start_locked(self) -> None:
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(self._stop_event,),
            name="jaylog-heartbeat",
            daemon=True,
        )
        self._thread.start()
        debug("heartbeat", f"thread iniciada; serviços={','.join(self._targets)}")

    def _run(self, stop: threading.Event) -> None:
        try:
            while True:
                self.deliver()
                if not self._has_active_targets():
                    debug("heartbeat", "thread encerrada; motivo=nenhum serviço ativo")
                    return
                if stop.wait(self.interval):
                    return
        except Exception as exc:  # pragma: no cover - _post já captura a rede
            _warn(f"thread encerrada por erro inesperado: {exc}")

    def _has_active_targets(self) -> bool:
        with self._lock:
            return any(service not in self._disabled for service in self._targets)

    def stop(self, timeout: float = _STOP_JOIN_TIMEOUT) -> None:
        """
        Para a thread e envia o que ainda estiver pendente, para a execução não
        terminar com o último beat perdido. O envio final só sai se a thread já
        terminou: disputar o estado com um POST em voo não vale o risco.
        """
        deadline = time.monotonic() + timeout
        with self._lock:
            thread, event = self._thread, self._stop_event
        if event is not None:
            event.set()
        alive = False
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
            alive = thread.is_alive()
        if not alive:
            self.deliver(deadline=deadline)
        self._reset()

    def _reset(self) -> None:
        with self._lock:
            self._thread = None
            self._stop_event = None
            self._targets = {}
            self._beats.clear()
            self._sent.clear()
            self._disabled.clear()
            self._ignored.clear()

    # ------------------------------------------------------------------
    # entrega (síncrona — os testes chamam direto)
    # ------------------------------------------------------------------

    def deliver(self, deadline: float | None = None) -> None:
        """Um POST por serviço com beat pendente; respeita ``deadline`` (monotonic)."""
        with self._lock:
            pending = [
                (target, self._beats[target.service])
                for target in self._targets.values()
                if target.service not in self._disabled
                and self._beats.get(target.service, 0) > self._sent.get(target.service, 0)
            ]
        for target, seq in pending:
            timeout = target.timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    debug("heartbeat", "envio interrompido; motivo=prazo de encerramento esgotado")
                    return
                timeout = min(timeout, remaining)
            if self._post(target, timeout):
                with self._lock:
                    if target.service in self._targets:
                        self._sent[target.service] = max(self._sent.get(target.service, 0), seq)

    def _post(self, target: HeartbeatTarget, timeout: float) -> bool:
        """``True`` = entregue. ``False`` = tentar de novo no próximo ciclo (ou desativado)."""
        if self._session is None:
            self._session = requests.Session()
        kwargs: dict = {
            "json": {"run_id": RUN_ID, "service": target.service},
            "headers": {
                "x-api-key": target.api_key,
                "x-jaylog-version": __version__,
                "x-jaylog-protocol": str(PROTOCOL_VERSION),
                "x-jaylog-run-id": RUN_ID,
            },
            "timeout": timeout,
            "verify": target.verify,
        }
        if target.proxy:
            kwargs["proxies"] = {"http": target.proxy, "https": target.proxy}

        try:
            debug(
                "heartbeat",
                f"requisição HTTP iniciada; método=POST; endpoint={target.endpoint}; "
                f"serviço={target.service}; timeout={timeout:g}s",
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                response = self._session.post(target.endpoint, **kwargs)
        except Exception as exc:
            debug(
                "heartbeat",
                f"requisição HTTP falhou; serviço={target.service}; "
                f"tipo={type(exc).__name__}; detalhe={exc}",
            )
            return False  # rede fora, DNS, timeout: o beat fica pendente

        status = response.status_code
        debug(
            "heartbeat",
            f"resposta HTTP recebida; serviço={target.service}; status={status}",
        )

        if 200 <= status < 300:
            if response.headers.get("x-jaylog-host-required") == "1":
                accepted = self._request_resend(target.service)
                debug(
                    "heartbeat",
                    f"backend solicitou sincronização do host; serviço={target.service}; "
                    f"reenvio_aceito={accepted}",
                )
            return True

        if status == 429 or status >= 500:
            return False

        with self._lock:
            self._disabled.add(target.service)
        if status in (404, 405):
            # Backend anterior à rota. O mesmo contrato do /logs/host-metrics:
            # "serviço desconhecido" é 422, nunca 404, para este caso ser inequívoco.
            _warn(
                f"o backend não suporta POST {target.endpoint} (HTTP {status}); "
                f"heartbeat de '{target.service}' desativado neste processo"
            )
        else:
            _warn(
                f"POST {target.endpoint} devolveu HTTP {status}; "
                f"heartbeat de '{target.service}' desativado: {_body_excerpt(response)}"
            )
        return False


def _body_excerpt(response, limit: int = 300) -> str:
    try:
        return response.text[:limit]
    except Exception:
        return "<corpo ilegível>"


# ----------------------------------------------------------------------
# singleton de módulo — as funções resolvem `_reporter` a cada chamada, para os
# testes poderem trocá-lo por uma instância com sessão falsa
# ----------------------------------------------------------------------

_reporter = JaylogHeartbeatReporter()


def register(targets: list[HeartbeatTarget]) -> None:
    _reporter.register(targets)


def beat(service: str) -> bool:
    return _reporter.beat(service)


def remove(service: str) -> None:
    _reporter.remove(service)


def stop(timeout: float = _STOP_JOIN_TIMEOUT) -> None:
    _reporter.stop(timeout)


def targets() -> dict[str, HeartbeatTarget]:
    return _reporter.targets


__all__ = [
    "HeartbeatTarget",
    "JaylogHeartbeatReporter",
    "beat",
    "register",
    "remove",
    "stop",
    "targets",
]
