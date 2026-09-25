import pytest

from jaylog.endpoints import derive_host_endpoint


@pytest.mark.parametrize(
    ("log_endpoint", "host_endpoint"),
    [
        ("https://api/logs/add", "https://api/logs/host"),
        ("https://api/logs/add/", "https://api/logs/host"),
        ("https://api/v1/logs/add", "https://api/v1/logs/host"),
        ("https://api/add", "https://api/host"),
        ("https://api", "https://api/host"),
        ("https://api/logs/add?x=1", "https://api/logs/host?x=1"),
    ],
)
def test_derives_host_endpoint(log_endpoint: str, host_endpoint: str) -> None:
    assert derive_host_endpoint(log_endpoint) == host_endpoint


@pytest.mark.parametrize(
    ("log_endpoint", "metrics_endpoint"),
    [
        ("https://api/logs/add", "https://api/logs/host-metrics"),
        ("https://api/logs/add/", "https://api/logs/host-metrics"),
        ("https://api", "https://api/host-metrics"),
        ("https://api/logs/add?x=1#f", "https://api/logs/host-metrics?x=1#f"),
    ],
)
def test_derives_metrics_endpoint(log_endpoint: str, metrics_endpoint: str) -> None:
    from jaylog.endpoints import derive_endpoint

    assert derive_endpoint(log_endpoint, "host-metrics") == metrics_endpoint
