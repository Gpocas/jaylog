"""Envio único das agendas do Task Scheduler, fora do registry de host."""

import threading

from jaylog.host import detect_schedule, identity
from jaylog.host.reporter import JaylogHostReporter
from jaylog.runtime import RUN_ID

_STOP_JOIN_TIMEOUT = 2.0
_active: JaylogHostReporter | None = None
_lock = threading.Lock()


def payload_factory(service: str, timeout: float):
    """Coleta na thread: ``configure()`` nunca espera pelo ``schtasks``."""

    def factory() -> dict | None:
        rows = detect_schedule.collect_schedules(timeout=timeout)
        if not rows:
            return None
        return {
            "run_id": RUN_ID,
            "service": service,
            "hostname": identity.hostname(),
            "schedules": rows,
        }

    return factory


def start(reporter: JaylogHostReporter) -> None:
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


def active() -> JaylogHostReporter | None:
    with _lock:
        return _active


__all__ = ["active", "payload_factory", "start", "stop"]
