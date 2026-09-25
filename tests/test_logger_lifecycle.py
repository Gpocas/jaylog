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


def _http_settings(name: str, **overrides) -> JaylogSettings:
    values = {
        "app_name": name,
        "log_console_enabled": False,
        "log_http_endpoint": "https://api.example/logs/add",
        "log_http_api_key": "key",
        **overrides,
    }
    return JaylogSettings(**values)


def _no_threads(monkeypatch) -> list:
    """Sem rede: host reporter desligado e o `start` do coletor só registra."""
    from jaylog.host.metrics_reporter import JaylogMetricsReporter

    started: list = []
    monkeypatch.setattr(logger_module, "_start_host_reporters", lambda items: None)
    monkeypatch.setattr(JaylogMetricsReporter, "start", lambda self: started.append(self))
    return started


def test_configure_starts_a_single_metrics_reporter_for_many_loggers(monkeypatch) -> None:
    from jaylog.host import metrics_reporter

    started = _no_threads(monkeypatch)

    configure([_http_settings("ORDERS"), _http_settings("BILLING")])

    assert len(started) == 1
    active = metrics_reporter.active()
    assert active is started[0]
    assert active.service == "ORDERS"
    assert active.endpoint == "https://api.example/logs/host-metrics"

    configure([_http_settings("ORDERS")])
    assert metrics_reporter.active() is started[1]
    assert started[0]._stop.is_set()

    shutdown()
    assert metrics_reporter.active() is None


def test_metrics_reporter_binds_to_first_eligible_logger(monkeypatch) -> None:
    from jaylog.host import metrics_reporter

    _no_threads(monkeypatch)

    configure([_http_settings("ORDERS", host_metrics_enabled=False), _http_settings("BILLING")])

    assert metrics_reporter.active().service == "BILLING"


def test_shutdown_of_other_logger_keeps_metrics_running(monkeypatch) -> None:
    from jaylog.host import metrics_reporter

    _no_threads(monkeypatch)
    configure([_http_settings("ORDERS"), _http_settings("BILLING")])

    shutdown("BILLING")
    assert metrics_reporter.active() is not None

    shutdown("ORDERS")
    assert metrics_reporter.active() is None


def test_metrics_follow_host_report_switch(monkeypatch) -> None:
    from jaylog.host import metrics_reporter

    started = _no_threads(monkeypatch)

    configure(_http_settings("ORDERS", host_report_enabled=False))
    configure(_http_settings("ORDERS", host_metrics_enabled=False))
    configure(_http_settings("ORDERS", log_http_api_key=None))

    assert started == []
    assert metrics_reporter.active() is None


def test_configure_starts_schedule_reporter_only_on_windows(monkeypatch) -> None:
    from jaylog.host import schedule_reporter
    from jaylog.host.reporter import JaylogHostReporter

    started: list[JaylogHostReporter] = []
    monkeypatch.setattr(logger_module, "_start_host_reporters", lambda items: None)
    monkeypatch.setattr(logger_module, "_start_metrics_reporter", lambda items: None)
    monkeypatch.setattr(logger_module.win32, "is_windows", lambda: True)
    monkeypatch.setattr(JaylogHostReporter, "start", lambda self: started.append(self))

    configure([_http_settings("ORDERS"), _http_settings("BILLING")])

    assert len(started) == 1
    active = schedule_reporter.active()
    assert active is started[0]
    assert active.service == "ORDERS"
    assert active.endpoint == "https://api.example/logs/host-schedules"
    assert active.label == "schedule"
    assert active._one_shot is True

    shutdown()
    assert schedule_reporter.active() is None
