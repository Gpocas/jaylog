from jaylog import diagnostics


def test_diagnostic_is_silent_when_disabled(capsys) -> None:
    diagnostics.configure(False)

    diagnostics.emit("http", "requisição iniciada")

    assert capsys.readouterr().err == ""


def test_diagnostic_uses_formal_component_prefix(capsys) -> None:
    diagnostics.configure(True)

    diagnostics.emit("http", "resposta recebida; status=202")

    assert (
        capsys.readouterr().err
        == "[jaylog] debug http: resposta recebida; status=202\n"
    )


def test_environment_enables_diagnostic_before_configuration(monkeypatch, capsys) -> None:
    diagnostics.reset()
    monkeypatch.setenv("JAYLOG_DEBUG", "1")

    diagnostics.emit("logger", "inicialização antecipada")

    assert "[jaylog] debug logger:" in capsys.readouterr().err


def test_non_console_dispatcher_receives_pending_and_new_events(capsys) -> None:
    received: list[tuple[str, frozenset[str]]] = []
    diagnostics.configure(True, "file,http")
    diagnostics.emit("logger", "evento anterior ao handler")

    diagnostics.register_dispatcher("ORDERS", lambda line, targets: received.append((line, targets)))
    diagnostics.emit("logger", "evento posterior ao handler")

    assert capsys.readouterr().err == ""
    assert "anterior" in received[0][0]
    assert "posterior" in received[1][0]
    assert all(targets == {"file", "http"} for _, targets in received)


def test_parse_handlers_rejects_empty_and_unknown_values() -> None:
    import pytest

    with pytest.raises(ValueError):
        diagnostics.parse_handlers("")
    with pytest.raises(ValueError):
        diagnostics.parse_handlers("console,database")
