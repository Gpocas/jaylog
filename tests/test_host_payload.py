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
    assert json.loads(json.dumps(payload)) == payload
