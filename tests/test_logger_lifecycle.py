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


def test_debug_can_be_routed_exclusively_to_file(tmp_path, capsys) -> None:
    settings = JaylogSettings(
        app_name="ORDERS",
        debug=True,
        debug_handlers="file",
        log_dir=tmp_path,
        log_console_enabled=False,
        host_report_enabled=False,
    )

    configure(settings)
    get_logger("ORDERS")
    shutdown()

    assert capsys.readouterr().err == ""
    content = (tmp_path / settings.log_filename).read_text(encoding="utf-8")
    assert "[DEBUG]" in content
    assert "[jaylog] debug logger:" in content


def test_debug_http_routing_does_not_recurse(monkeypatch, capsys) -> None:
    from jaylog.handlers import http_handler

    class Response:
        status_code = 202
        headers: dict = {}

    class Session:
        def __init__(self) -> None:
            self.headers: dict = {}
            self.proxies: dict = {}
            self.calls = 0

        def post(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    session = Session()
    monkeypatch.setattr(http_handler.requests, "Session", lambda: session)
    settings = _http_settings(
        "ORDERS",
        debug=True,
        debug_handlers="http",
        host_report_enabled=False,
    )

    configure(settings)
    get_logger("ORDERS")
    shutdown()

    assert 1 <= session.calls < 50
    assert "[jaylog] debug" not in capsys.readouterr().err


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


class _OkSession:
    """Sessão sem rede que aceita qualquer POST."""

    def post(self, *_args, **_kwargs):
        import types

        return types.SimpleNamespace(status_code=200, headers={}, text="")


def _fake_heartbeat(monkeypatch, *, autostart: bool = False):
    from jaylog.host import heartbeat_reporter
    from jaylog.host.heartbeat_reporter import JaylogHeartbeatReporter

    fake = JaylogHeartbeatReporter(
        session=_OkSession(), request_resend=lambda service: True, autostart=autostart
    )
    monkeypatch.setattr(heartbeat_reporter, "_reporter", fake)
    return fake


def _heartbeat_threads() -> list:
    return [t for t in threading.enumerate() if t.name == "jaylog-heartbeat" and t.is_alive()]


def test_configure_registers_a_heartbeat_target_per_eligible_logger(monkeypatch) -> None:
    _no_threads(monkeypatch)
    fake = _fake_heartbeat(monkeypatch)

    configure(
        [
            _http_settings("ORDERS", host_heartbeat_interval=30),
            _http_settings(
                "BILLING", log_http_endpoint="https://other/logs/add", log_http_api_key="k2"
            ),
            _http_settings("OFF", host_heartbeat_enabled=False),
            _http_settings("NOHOST", host_report_enabled=False),
            _http_settings("NOKEY", log_http_api_key=None),
        ]
    )

    targets = fake.targets
    assert sorted(targets) == ["BILLING", "ORDERS"]
    assert targets["ORDERS"].endpoint == "https://api.example/logs/heartbeat"
    assert targets["ORDERS"].interval == 30
    assert targets["BILLING"].endpoint == "https://other/logs/heartbeat"
    assert targets["BILLING"].api_key == "k2"


def test_heartbeat_defaults_to_first_registered_logger(monkeypatch) -> None:
    import jaylog

    _no_threads(monkeypatch)
    fake = _fake_heartbeat(monkeypatch)
    configure([_http_settings("ORDERS"), _http_settings("BILLING")])

    jaylog.heartbeat()
    jaylog.heartbeat("BILLING")
    jaylog.heartbeat("NOPE")

    assert fake.beats == {"ORDERS": 1, "BILLING": 1}


def test_heartbeat_is_a_silent_noop_without_configure() -> None:
    import jaylog

    jaylog.heartbeat()
    jaylog.heartbeat("ORDERS")

    assert _heartbeat_threads() == []


def test_heartbeat_is_a_silent_noop_after_shutdown(monkeypatch) -> None:
    import jaylog

    _no_threads(monkeypatch)
    fake = _fake_heartbeat(monkeypatch)
    configure(_http_settings("ORDERS"))
    shutdown()

    jaylog.heartbeat()

    assert fake.beats == {}
    assert _heartbeat_threads() == []


def test_heartbeat_never_raises_even_if_the_reporter_breaks(monkeypatch) -> None:
    import jaylog
    from jaylog.host import heartbeat_reporter

    _no_threads(monkeypatch)
    configure(_http_settings("ORDERS"))

    def boom(service):
        raise RuntimeError("quebrou")

    monkeypatch.setattr(heartbeat_reporter, "beat", boom)

    jaylog.heartbeat()  # não pode propagar para o loop do usuário


def test_heartbeat_thread_starts_on_first_call_and_stops_on_shutdown(monkeypatch) -> None:
    import jaylog

    _no_threads(monkeypatch)
    _fake_heartbeat(monkeypatch, autostart=True)
    configure(_http_settings("ORDERS"))
    assert _heartbeat_threads() == []

    jaylog.heartbeat()
    assert len(_heartbeat_threads()) == 1

    shutdown()
    assert _heartbeat_threads() == []


def test_shutdown_of_one_logger_removes_only_its_heartbeat_target(monkeypatch) -> None:
    _no_threads(monkeypatch)
    fake = _fake_heartbeat(monkeypatch)
    configure([_http_settings("ORDERS"), _http_settings("BILLING")])

    shutdown("BILLING")
    assert sorted(fake.targets) == ["ORDERS"]

    shutdown()
    assert fake.targets == {}


def test_reconfigure_discards_heartbeat_state(monkeypatch) -> None:
    import jaylog

    _no_threads(monkeypatch)
    fake = _fake_heartbeat(monkeypatch)
    configure(_http_settings("ORDERS"))
    jaylog.heartbeat()
    assert fake.beats == {"ORDERS": 1}

    configure(_http_settings("ORDERS"))

    assert fake.beats == {}
    assert sorted(fake.targets) == ["ORDERS"]


def test_configure_registers_the_host_before_any_log_is_emitted(monkeypatch) -> None:
    from jaylog.host.metrics_reporter import JaylogMetricsReporter

    started: list = []

    class _FakeHostReporter:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.service = kwargs["service"]

        def start(self) -> None:
            started.append(self)

        def stop(self, timeout: float = 0.0) -> None:
            pass

    monkeypatch.setattr(logger_module, "JaylogHostReporter", _FakeHostReporter)
    monkeypatch.setattr(JaylogMetricsReporter, "start", lambda self: None)

    configure(_http_settings("ORDERS"))

    # nenhum get_logger()/log foi chamado: o dashboard só enxerga o ambiente da
    # execução ativa porque o registro sai do configure(), e não do 1º log
    assert [r.kwargs["service"] for r in started] == ["ORDERS"]
    assert started[0].kwargs["endpoint"] == "https://api.example/logs/host"
