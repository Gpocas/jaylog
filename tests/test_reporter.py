from jaylog.host.reporter import JaylogHostReporter


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.proxies: dict[str, str] = {}
        self.responses = responses
        self.calls = 0

    def post(self, *_args, **_kwargs) -> FakeResponse:
        self.calls += 1
        return self.responses.pop(0)


def make_reporter(statuses: list[int]) -> tuple[JaylogHostReporter, FakeSession]:
    session = FakeSession([FakeResponse(status) for status in statuses])
    reporter = JaylogHostReporter(
        "ORDERS",
        "https://example.test/logs/host",
        "key",
        session=session,
        backoff=(0, 0),
        payload_factory=lambda: {"service": "ORDERS"},
    )
    return reporter, session


def test_retries_transient_errors_until_delivered() -> None:
    reporter, session = make_reporter([500, 500, 202])

    reporter.deliver()

    assert session.calls == 3
    assert reporter.sent is True
    assert reporter.fatal is False


def test_404_disables_reporter_and_422_is_fatal() -> None:
    unsupported, _ = make_reporter([404])
    unsupported.deliver()
    assert unsupported.unsupported is True

    rejected, _ = make_reporter([422])
    rejected.deliver()
    assert rejected.fatal is True


def test_resend_is_debounced() -> None:
    reporter, _ = make_reporter([])
    reporter.sent = True

    assert reporter.request_resend() is True
    assert reporter.sent is False
    assert reporter.request_resend() is False
