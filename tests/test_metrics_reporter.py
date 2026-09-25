import pytest

from jaylog.host import metrics_reporter
from jaylog.host.metrics_reporter import MAX_BUFFERED, JaylogMetricsReporter, seconds_until_next
from jaylog.runtime import RUN_ID


class FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


class FakeSession:
    def __init__(self, responses: list) -> None:
        self.headers: dict[str, str] = {}
        self.proxies: dict[str, str] = {}
        self.responses = responses
        self.bodies: list[dict] = []

    def post(self, _url, json=None, **_kwargs):
        self.bodies.append(json)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeSampler:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def sample(self):
        self.calls += 1
        if self.calls == 1:
            return None  # base, como o MetricsSampler real
        return {"sampled_at": f"t{self.calls}"}


def make(responses, *, sampler=None, resend=None):
    session = FakeSession(list(responses))
    reporter = JaylogMetricsReporter(
        "ORDERS",
        "https://api/logs/host-metrics",
        "key",
        session=session,
        sampler=sampler or FakeSampler(),
        request_resend=resend or (lambda service: True),
    )
    reporter._sampler.sample()  # base
    return reporter, session


@pytest.fixture(autouse=True)
def reset_warning():
    metrics_reporter._unsupported_warned = False
    yield
    metrics_reporter._unsupported_warned = False


def test_posts_contract_envelope_and_clears_buffer_on_success():
    reporter, session = make([FakeResponse(200)])

    reporter.collect()
    assert reporter.flush() is True

    assert session.bodies == [
        {"run_id": RUN_ID, "service": "ORDERS", "samples": [{"sampled_at": "t2"}]}
    ]
    assert len(reporter.buffer) == 0
    assert session.headers["x-api-key"] == "key"
    assert session.headers["x-jaylog-run-id"] == RUN_ID


@pytest.mark.parametrize("failure", [FakeResponse(500), FakeResponse(429), ConnectionError("fora")])
def test_transient_failure_keeps_samples_for_next_cycle(failure):
    reporter, session = make([failure, FakeResponse(200)])

    reporter.collect()
    assert reporter.flush() is False
    reporter.collect()
    assert reporter.flush() is True

    assert [s["sampled_at"] for s in session.bodies[1]["samples"]] == ["t2", "t3"]
    assert reporter.disabled is False


def test_buffer_is_bounded_and_drops_oldest():
    reporter, _ = make([])

    for _ in range(MAX_BUFFERED + 5):
        reporter.collect()

    assert len(reporter.buffer) == MAX_BUFFERED
    assert reporter.buffer[0]["sampled_at"] == "t7"


@pytest.mark.parametrize("status", [404, 405])
def test_old_backend_disables_with_single_warning(status, capsys):
    first, _ = make([FakeResponse(status)])
    second, _ = make([FakeResponse(status)])

    for reporter in (first, second):
        reporter.collect()
        reporter.flush()
        assert reporter.disabled is True

    assert capsys.readouterr().err.count("host-metrics") == 1


@pytest.mark.parametrize("status", [401, 403, 422, 400])
def test_definitive_rejection_disables(status, capsys):
    reporter, session = make([FakeResponse(status, text="nope")])

    reporter.collect()
    reporter.flush()
    reporter.collect()
    assert reporter.flush() is False

    assert reporter.disabled is True
    assert len(session.bodies) == 1
    assert str(status) in capsys.readouterr().err


def test_host_required_header_requests_host_resend():
    calls = []
    reporter, _ = make(
        [FakeResponse(200, headers={"x-jaylog-host-required": "1"})],
        resend=calls.append,
    )

    reporter.collect()
    reporter.flush()

    assert calls == ["ORDERS"]


def test_stop_sends_a_final_sample():
    reporter, session = make([FakeResponse(200)])
    reporter.interval = 3600  # a thread só vai acordar pelo stop
    reporter.start()

    reporter.stop()

    assert len(session.bodies) == 1
    assert session.bodies[0]["samples"][-1]["sampled_at"] == "t3"


def test_start_without_psutil_warns_and_stays_down(capsys):
    sampler = FakeSampler()
    sampler.available = False
    reporter, _ = make([], sampler=sampler)

    reporter.start()

    assert reporter.disabled is True
    assert reporter._thread is None
    assert "psutil" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("interval", "now", "expected"),
    [(60, 120.0, 60.0), (60, 125.5, 54.5), (60, 179.9, 0.1), (30, 45.0, 15.0)],
)
def test_seconds_until_next_aligns_to_wall_clock(interval, now, expected):
    assert seconds_until_next(interval, now) == pytest.approx(expected)


def test_module_singleton_replaces_previous_reporter():
    first, _ = make([])
    second, _ = make([])

    metrics_reporter.start(first)
    metrics_reporter.start(second)

    assert metrics_reporter.active() is second
    assert first._stop.is_set()

    metrics_reporter.stop()
    assert metrics_reporter.active() is None
