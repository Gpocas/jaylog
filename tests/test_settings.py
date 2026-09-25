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

    assert JaylogSettings(app_name="ORDERS", log_http_endpoint=None).effective_host_metrics_endpoint is None


def test_host_metrics_interval_has_a_floor() -> None:
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        JaylogSettings(app_name="ORDERS", host_metrics_interval=5)
