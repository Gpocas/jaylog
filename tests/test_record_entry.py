import logging
import sys
from logging.handlers import QueueHandler
from queue import Queue

from jaylog import context


def _record(level: int = logging.ERROR) -> logging.LogRecord:
    return logging.LogRecord("ORDERS", level, __file__, 12, "message", (), None)


def test_screenshot_is_memoized_on_the_record(monkeypatch) -> None:
    calls = 0

    def capture() -> bytes:
        nonlocal calls
        calls += 1
        return b"image"

    monkeypatch.setattr(context, "capture_screenshot", capture)
    record = _record()

    assert context.record_entry(record) is context.record_entry(record)
    assert context.record_screenshot(record) == b"image"
    assert context.record_screenshot(record) == b"image"
    assert calls == 1


def test_non_http_or_low_level_record_never_captures_screenshot(monkeypatch) -> None:
    monkeypatch.setattr(
        context, "capture_screenshot", lambda: (_ for _ in ()).throw(AssertionError())
    )

    assert context.record_screenshot(_record(logging.INFO)) is None


def test_traceback_is_not_duplicated_after_queue_prepare() -> None:
    try:
        raise ValueError("once")
    except ValueError:
        record = logging.LogRecord(
            "ORDERS", logging.ERROR, __file__, 12, "failed", (), sys.exc_info()
        )
    record.is_exception = True

    prepared = QueueHandler(Queue()).prepare(record)
    entry = context.record_entry(prepared)

    assert entry["log_message"].count("ValueError: once") == 1
