import threading

import pytest

from jaylog._version import PROTOCOL_VERSION
from jaylog.host.heartbeat_reporter import HeartbeatTarget, JaylogHeartbeatReporter
from jaylog.runtime import RUN_ID


class FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


class FakeSession:
    def __init__(self, responses=(), on_post=None) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.on_post = on_post

    def post(self, url, json=None, **kwargs):
        self.calls.append({"url": url, "json": json, **kwargs})
        if self.on_post is not None:
            self.on_post()
        response = self.responses.pop(0) if self.responses else FakeResponse(200)
        if isinstance(response, Exception):
            raise response
        return response


def target(service: str = "ORDERS", **overrides) -> HeartbeatTarget:
    values = {
        "service": service,
        "endpoint": f"https://api/{service}/logs/heartbeat",
        "api_key": f"key-{service}",
        **overrides,
    }
    return HeartbeatTarget(**values)


def make(targets=None, responses=(), *, resend=None, on_post=None):
    session = FakeSession(responses, on_post)
    reporter = JaylogHeartbeatReporter(
        session=session,
        request_resend=resend or (lambda service: True),
        autostart=False,
    )
    reporter.register(targets if targets is not None else [target()])
    return reporter, session


def test_beat_for_unknown_service_is_a_noop():
    reporter, session = make()

    assert reporter.beat("OUTRO") is False
    reporter.deliver()

    assert session.calls == []
    assert reporter._thread is None


def test_posts_contract_with_per_request_credentials():
    reporter, session = make([target()], [FakeResponse(200)])

    assert reporter.beat("ORDERS") is True
    reporter.deliver()

    call = session.calls[0]
    assert call["url"] == "https://api/ORDERS/logs/heartbeat"
    assert call["json"] == {"run_id": RUN_ID, "service": "ORDERS"}
    assert call["headers"]["x-api-key"] == "key-ORDERS"
    assert call["headers"]["x-jaylog-run-id"] == RUN_ID
    assert call["headers"]["x-jaylog-protocol"] == str(PROTOCOL_VERSION)


def test_each_service_uses_its_own_endpoint_and_key():
    reporter, session = make([target("ORDERS"), target("BILLING", api_key="outra")])

    reporter.beat("ORDERS")
    reporter.beat("BILLING")
    reporter.deliver()

    by_service = {c["json"]["service"]: c for c in session.calls}
    assert by_service["ORDERS"]["headers"]["x-api-key"] == "key-ORDERS"
    assert by_service["BILLING"]["headers"]["x-api-key"] == "outra"
    assert by_service["BILLING"]["url"] == "https://api/BILLING/logs/heartbeat"


def test_target_network_settings_are_sent_per_request():
    reporter, session = make([target(proxy="http://proxy:3128", verify="/ca.pem", timeout=7.0)])

    reporter.beat("ORDERS")
    reporter.deliver()

    call = session.calls[0]
    assert call["proxies"] == {"http": "http://proxy:3128", "https": "http://proxy:3128"}
    assert call["verify"] == "/ca.pem"
    assert call["timeout"] == 7.0


def test_no_post_without_a_new_beat():
    reporter, session = make()

    reporter.beat("ORDERS")
    reporter.deliver()
    reporter.deliver()

    assert len(session.calls) == 1


def test_many_beats_between_cycles_produce_a_single_post():
    reporter, session = make()

    for _ in range(10_000):
        reporter.beat("ORDERS")
    reporter.deliver()

    assert len(session.calls) == 1
    assert reporter.beats == {"ORDERS": 10_000}


def test_beat_arriving_during_post_stays_pending():
    holder = {}
    reporter, session = make(on_post=lambda: holder["r"].beat("ORDERS"))
    holder["r"] = reporter

    reporter.beat("ORDERS")
    reporter.deliver()
    assert len(session.calls) == 1

    session.on_post = None
    reporter.deliver()
    assert len(session.calls) == 2
    reporter.deliver()
    assert len(session.calls) == 2


@pytest.mark.parametrize("failure", [FakeResponse(500), FakeResponse(429), ConnectionError("fora")])
def test_transient_failure_keeps_beat_pending(failure):
    reporter, session = make(responses=[failure, FakeResponse(200)])

    reporter.beat("ORDERS")
    reporter.deliver()
    reporter.deliver()
    assert len(session.calls) == 2

    reporter.deliver()
    assert len(session.calls) == 2
    assert reporter.beat("ORDERS") is True  # não foi desativado


@pytest.mark.parametrize("status", [404, 405])
def test_old_backend_disables_only_that_service(status, capsys):
    reporter, _ = make(
        [target("ORDERS"), target("BILLING")],
        [FakeResponse(status), FakeResponse(200)],
    )

    reporter.beat("ORDERS")
    reporter.beat("BILLING")
    reporter.deliver()

    assert reporter.beat("ORDERS") is False
    assert reporter.beat("BILLING") is True
    err = capsys.readouterr().err
    assert "ORDERS" in err and str(status) in err
    assert "BILLING" not in err


def test_other_4xx_disables_with_body_excerpt(capsys):
    reporter, _ = make(responses=[FakeResponse(401, text="chave inválida")])

    reporter.beat("ORDERS")
    reporter.deliver()

    assert reporter.beat("ORDERS") is False
    assert "chave inválida" in capsys.readouterr().err


def test_host_required_header_requests_resend_for_that_service():
    asked: list[str] = []
    reporter, _ = make(
        responses=[FakeResponse(200, {"x-jaylog-host-required": "1"})],
        resend=lambda service: asked.append(service) or True,
    )

    reporter.beat("ORDERS")
    reporter.deliver()

    assert asked == ["ORDERS"]


def test_first_beat_starts_the_thread_and_stop_sends_pending():
    session = FakeSession()
    reporter = JaylogHeartbeatReporter(session=session, request_resend=lambda s: True)
    reporter.register([target(interval=60.0)])
    assert reporter._thread is None

    reporter.beat("ORDERS")
    assert reporter._thread is not None
    reporter.stop()

    assert len(session.calls) == 1
    assert reporter.targets == {}
    assert reporter._thread is None


def test_stop_flushes_a_pending_beat_when_no_thread_is_running():
    reporter, session = make(responses=[FakeResponse(200)])

    reporter.beat("ORDERS")
    reporter.stop()

    assert len(session.calls) == 1


def test_loop_ends_when_every_target_is_disabled():
    reporter, _ = make(responses=[FakeResponse(404)])
    reporter.beat("ORDERS")

    reporter._run(threading.Event())  # ficaria esperando para sempre se não terminasse

    assert reporter.beat("ORDERS") is False


def test_loop_delivers_once_and_ends_when_stopped():
    reporter, session = make(responses=[FakeResponse(200)])
    reporter.beat("ORDERS")
    stop = threading.Event()
    stop.set()

    reporter._run(stop)

    assert len(session.calls) == 1


def test_register_discards_previous_state():
    reporter, session = make()
    reporter.beat("ORDERS")

    reporter.register([target("ORDERS")])
    reporter.deliver()

    assert session.calls == []
    assert reporter.beats == {}


def test_remove_drops_only_that_target():
    reporter, session = make([target("ORDERS"), target("BILLING")])
    reporter.beat("ORDERS")
    reporter.beat("BILLING")

    reporter.remove("ORDERS")
    reporter.deliver()

    assert [c["json"]["service"] for c in session.calls] == ["BILLING"]


def test_interval_comes_from_the_first_target():
    reporter, _ = make([target("ORDERS", interval=30.0), target("BILLING", interval=90.0)])

    assert reporter.interval == 30.0
