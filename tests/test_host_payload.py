import json

from jaylog.host.models import HostInfo
from jaylog.host.payload import build_host_payload
from jaylog.runtime import RUN_ID


def test_host_payload_is_flat_json_with_nulls() -> None:
    payload = build_host_payload("ORDERS", HostInfo(hostname="host", python_frozen=False))

    assert set(payload) == set(HostInfo.model_fields)
    assert payload["run_id"] == RUN_ID
    assert payload["service"] == "ORDERS"
    assert payload["python_frozen"] is False
    assert payload["git_branch"] is None
    assert payload["git_commit_msg"] is None
    assert payload["git_commit_datetime"] is None
    assert json.loads(json.dumps(payload)) == payload


def test_host_payload_carries_machine_limits_as_nulls_by_default() -> None:
    payload = build_host_payload("ORDERS", HostInfo())

    for field in ("cpu_count", "memory_total_bytes", "disk_total_bytes", "disk_path"):
        assert field in payload
        assert payload[field] is None


def test_collected_host_info_fills_machine_limits(monkeypatch) -> None:
    from jaylog.host import collectors, metrics

    monkeypatch.setattr(
        metrics,
        "collect_limits",
        lambda *_a, **_k: metrics.Limits(2, 8 * 1024**3, 10 * 1024**3, "C:\\"),
    )

    info = collectors.collect_host_info(git_enabled=False)

    assert info.cpu_count == 2
    assert info.memory_total_bytes == 8 * 1024**3
    assert info.disk_total_bytes == 10 * 1024**3
    assert info.disk_path == "C:\\"


def test_protocol_version_is_4() -> None:
    from jaylog._version import PROTOCOL_VERSION

    assert PROTOCOL_VERSION == 4
    assert HostInfo().protocol_version == 4
