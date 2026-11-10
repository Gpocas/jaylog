import warnings

import pydantic
import pytest
import urllib3

from jaylog import _http, logger
from jaylog._version import PROTOCOL_VERSION
from jaylog.runtime import RUN_ID
from jaylog.settings import JaylogSettings


def _settings(**overrides) -> JaylogSettings:
    return JaylogSettings(app_name="ORDERS", _env_file=None, **overrides)


# ----------------------------------------------------------------------
# parsing de log_http_verify
# ----------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["true", "TRUE", " True ", "1", "yes", "on"])
def test_verify_true_from_environment_is_bool(monkeypatch, raw) -> None:
    # Antes virava a string "true", que o `requests` trata como caminho de CA
    # bundle: `OSError: invalid path: true` em todo POST.
    monkeypatch.setenv("JAYLOG_LOG_HTTP_VERIFY", raw)

    assert _settings().log_http_verify is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "off"])
def test_verify_false_from_environment_is_bool(monkeypatch, raw) -> None:
    monkeypatch.setenv("JAYLOG_LOG_HTTP_VERIFY", raw)

    assert _settings().log_http_verify is False


def test_verify_accepts_existing_ca_bundle(monkeypatch, tmp_path) -> None:
    bundle = tmp_path / "corp-ca.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setenv("JAYLOG_LOG_HTTP_VERIFY", str(bundle))

    assert _settings().log_http_verify == str(bundle)


def test_verify_accepts_path_object(tmp_path) -> None:
    assert _settings(log_http_verify=tmp_path).log_http_verify == str(tmp_path)


def test_verify_rejects_missing_ca_bundle(tmp_path) -> None:
    missing = tmp_path / "nao-existe.pem"

    with pytest.raises(pydantic.ValidationError, match="CA bundle não encontrado"):
        _settings(log_http_verify=str(missing))


def test_verify_rejects_empty_value(monkeypatch) -> None:
    monkeypatch.setenv("JAYLOG_LOG_HTTP_VERIFY", "  ")

    with pytest.raises(pydantic.ValidationError, match="log_http_verify vazio"):
        _settings()


def test_verify_default_is_not_marked_as_explicit() -> None:
    settings = _settings()

    assert settings.log_http_verify is _http.DEFAULT_VERIFY
    assert "log_http_verify" not in settings.model_fields_set


# ----------------------------------------------------------------------
# aviso de transporte inseguro em configure()
# ----------------------------------------------------------------------


@pytest.fixture
def fresh_warning(monkeypatch):
    monkeypatch.setattr(logger, "_insecure_transport_warned", False)


def test_default_verify_warns_about_0_4_0(fresh_warning, capsys) -> None:
    logger._warn_insecure_transport([_settings(log_http_endpoint="https://api.example/logs/add")])

    assert "O padrão passa a ser 'true' na 0.4.0" in capsys.readouterr().err


def test_explicit_false_from_environment_warns(fresh_warning, monkeypatch, capsys) -> None:
    # Antes "false" virava string, escapava do `is False` e não avisava nada.
    monkeypatch.setenv("JAYLOG_LOG_HTTP_VERIFY", "false")

    logger._warn_insecure_transport([_settings(log_http_endpoint="https://api.example/logs/add")])

    assert "DESATIVADA por configuração" in capsys.readouterr().err


def test_verify_enabled_does_not_warn(fresh_warning, capsys) -> None:
    logger._warn_insecure_transport(
        [_settings(log_http_endpoint="https://api.example/logs/add", log_http_verify=True)]
    )

    assert capsys.readouterr().err == ""


def test_plain_http_does_not_warn(fresh_warning, capsys) -> None:
    logger._warn_insecure_transport([_settings(log_http_endpoint="http://api.example/logs/add")])

    assert capsys.readouterr().err == ""


def test_https_override_of_derived_endpoint_warns(fresh_warning, capsys) -> None:
    logger._warn_insecure_transport(
        [
            _settings(
                log_http_endpoint="http://api.example/logs/add",
                host_metrics_http_endpoint="https://metrics.example/logs/host-metrics",
            )
        ]
    )

    assert "NÃO está sendo verificado" in capsys.readouterr().err


# ----------------------------------------------------------------------
# sessão e supressão do InsecureRequestWarning
# ----------------------------------------------------------------------


@pytest.fixture
def isolated_filters(monkeypatch):
    monkeypatch.setattr(_http, "_silenced_hosts", set())
    with warnings.catch_warnings():
        yield


def _unverified(host: str) -> None:
    # Mesmo texto que o urllib3 emite em `connectionpool._validate_conn`.
    warnings.warn(
        f"Unverified HTTPS request is being made to host '{host}'. "
        "Adding certificate verification is strongly advised.",
        urllib3.exceptions.InsecureRequestWarning,
        stacklevel=1,
    )


def test_silence_only_affects_jaylog_host(isolated_filters) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", urllib3.exceptions.InsecureRequestWarning)
        _http.silence_insecure_warning("https://api.example/logs/add", False)
        _unverified("api.example")
        _unverified("outro.example")

    assert [str(w.message).split("'")[1] for w in caught] == ["outro.example"]


def test_silence_is_noop_when_verifying(isolated_filters) -> None:
    before = list(warnings.filters)

    _http.silence_insecure_warning("https://api.example/logs/add", True)
    _http.silence_insecure_warning("https://api.example/logs/add", "/ca.pem")

    assert warnings.filters == before


def test_silence_installs_filter_once_per_host(isolated_filters) -> None:
    before = len(warnings.filters)

    for _ in range(3):
        _http.silence_insecure_warning("https://api.example/logs/add", False)
        _http.silence_insecure_warning("https://api.example/logs/host", False)

    assert len(warnings.filters) == before + 1


def test_prepare_session_sets_headers_and_proxy_but_not_verify() -> None:
    session = _http.prepare_session("key", "http://proxy:3128")

    assert session.headers["x-api-key"] == "key"
    assert session.headers["x-jaylog-protocol"] == str(PROTOCOL_VERSION)
    assert session.headers["x-jaylog-run-id"] == RUN_ID
    assert session.proxies == {"http": "http://proxy:3128", "https": "http://proxy:3128"}
    # verify fica por requisição: `session.verify=False` perderia para REQUESTS_CA_BUNDLE.
    assert session.verify is True


def test_http_handler_passes_verify_per_request(monkeypatch, isolated_filters) -> None:
    import logging

    from jaylog.handlers.http_handler import JaylogHttpHandler

    calls: list[dict] = []

    class Response:
        status_code = 202
        headers: dict = {}

    class Session:
        def __init__(self) -> None:
            self.headers: dict = {}
            self.proxies: dict = {}

        def post(self, url, **kwargs):
            calls.append(kwargs)
            return Response()

    monkeypatch.setattr(_http.requests, "Session", Session)
    handler = JaylogHttpHandler(
        endpoint="https://api.example/logs/add",
        api_key="key",
        proxy="http://proxy:3128",
        verify="/ca.pem",
    )
    record = logging.LogRecord("ORDERS", logging.INFO, __file__, 1, "olá", (), None)

    filters_before = list(warnings.filters)
    handler.emit(record)

    assert calls[0]["verify"] == "/ca.pem"
    assert handler._session.proxies == {"http": "http://proxy:3128", "https": "http://proxy:3128"}
    assert warnings.filters == filters_before
