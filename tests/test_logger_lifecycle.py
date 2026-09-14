import logging
import threading

from jaylog import JaylogSettings, configure, get_logger, shutdown
from jaylog import logger as logger_module


def _settings() -> JaylogSettings:
    return JaylogSettings(app_name="ORDERS", log_console_enabled=False, host_report_enabled=False)


def test_reconfigure_removes_old_queue_handler() -> None:
    configure(_settings())
    log = get_logger("ORDERS")
    assert len(log.handlers) == 1

    configure(_settings())
    rebuilt = get_logger("ORDERS")
    assert len(rebuilt.handlers) == 1

    shutdown()
    assert rebuilt.handlers == []


def test_lazy_build_from_worker_thread_does_not_raise() -> None:
    configure(_settings())
    errors: list[Exception] = []

    def build() -> None:
        try:
            get_logger("ORDERS").info("from worker")
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    thread = threading.Thread(target=build)
    thread.start()
    thread.join()

    assert errors == []
    assert "ORDERS" in logger_module._registry
    assert isinstance(logger_module._registry["ORDERS"][0], logging.Logger)
