from jaylog.settings import JaylogSettings


def test_host_settings_and_lazy_log_filename(tmp_path) -> None:
    settings = JaylogSettings(
        app_name="ORDERS",
        log_dir=tmp_path,
        log_http_endpoint="https://api.example/logs/add",
        log_http_timeout=4,
    )

    assert settings.effective_host_endpoint == "https://api.example/logs/host"
    assert settings.effective_host_timeout == 8
    assert settings.log_filename is not None
    assert settings.host_report_enabled is True
    assert settings.log_http_verify is False
    assert settings.debug is False
    assert settings.debug_handlers == "console"
    assert settings.effective_debug_handlers == {"console"}


def test_debug_setting_can_be_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("JAYLOG_DEBUG", "true")
    monkeypatch.setenv("JAYLOG_DEBUG_HANDLERS", " HTTP, file ")

    settings = JaylogSettings(app_name="ORDERS", _env_file=None)

    assert settings.debug is True
    assert settings.debug_handlers == "file,http"
    assert settings.effective_debug_handlers == {"file", "http"}


def test_debug_handlers_reject_unknown_destination() -> None:
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError, match="handlers de diagnóstico inválidos"):
        JaylogSettings(app_name="ORDERS", debug_handlers="console,database")


def test_host_metrics_settings_defaults_and_derived_endpoint() -> None:
    settings = JaylogSettings(app_name="ORDERS", log_http_endpoint="https://api.example/logs/add")

    assert settings.host_metrics_enabled is True
    assert settings.host_metrics_interval == 60
    assert settings.effective_host_metrics_endpoint == "https://api.example/logs/host-metrics"


def test_host_metrics_endpoint_override_and_missing_log_endpoint() -> None:
    override = JaylogSettings(
        app_name="ORDERS",
        log_http_endpoint="https://api.example/logs/add",
        host_metrics_http_endpoint="https://other/metrics",
    )
    assert override.effective_host_metrics_endpoint == "https://other/metrics"

    assert (
        JaylogSettings(app_name="ORDERS", log_http_endpoint=None).effective_host_metrics_endpoint
        is None
    )


def test_host_metrics_interval_has_a_floor() -> None:
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        JaylogSettings(app_name="ORDERS", host_metrics_interval=5)


def test_host_schedule_settings_defaults_and_derived_endpoint() -> None:
    settings = JaylogSettings(app_name="ORDERS", log_http_endpoint="https://api.example/logs/add")

    assert settings.host_schedule_enabled is True
    assert settings.host_schedule_timeout == 10
    assert settings.effective_host_schedule_endpoint == "https://api.example/logs/host-schedules"


def test_host_schedule_endpoint_override_and_missing_log_endpoint() -> None:
    override = JaylogSettings(
        app_name="ORDERS",
        log_http_endpoint="https://api.example/logs/add",
        host_schedule_http_endpoint="https://other/schedules",
    )
    assert override.effective_host_schedule_endpoint == "https://other/schedules"
    assert (
        JaylogSettings(app_name="ORDERS", log_http_endpoint=None).effective_host_schedule_endpoint
        is None
    )


def test_host_heartbeat_settings_defaults_and_derived_endpoint() -> None:
    settings = JaylogSettings(app_name="ORDERS", log_http_endpoint="https://api.example/logs/add")

    assert settings.host_heartbeat_enabled is True
    assert settings.host_heartbeat_interval == 60
    assert settings.effective_host_heartbeat_endpoint == "https://api.example/logs/heartbeat"


def test_host_heartbeat_endpoint_override_and_missing_log_endpoint() -> None:
    override = JaylogSettings(
        app_name="ORDERS",
        log_http_endpoint="https://api.example/logs/add",
        host_heartbeat_http_endpoint="https://other/beat",
    )
    assert override.effective_host_heartbeat_endpoint == "https://other/beat"

    assert (
        JaylogSettings(app_name="ORDERS", log_http_endpoint=None).effective_host_heartbeat_endpoint
        is None
    )


def test_host_heartbeat_interval_has_a_floor() -> None:
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError, match="JAYLOG_HOST_HEARTBEAT_INTERVAL"):
        JaylogSettings(app_name="ORDERS", host_heartbeat_interval=5)
