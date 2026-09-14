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
