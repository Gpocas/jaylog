import sys

from jaylog.host.detect_venv import detect_venv


def test_frozen_wins_over_prefix(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "prefix", "/tmp/app", raising=False)
    monkeypatch.setattr(sys, "base_prefix", "/usr", raising=False)

    info = detect_venv()

    assert (info.active, info.kind, info.path) == (False, "frozen", None)


def test_conda_and_tool_override(monkeypatch) -> None:
    monkeypatch.delenv("PIPENV_ACTIVE", raising=False)
    monkeypatch.setenv("CONDA_PREFIX", "/opt/conda")

    assert detect_venv().kind == "conda"

    monkeypatch.setenv("POETRY_ACTIVE", "1")
    assert detect_venv().kind == "poetry"


def test_reads_uv_marker_from_pyvenv_cfg(monkeypatch, tmp_path) -> None:
    (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\nuv = 0.8.0\n")
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.delenv("PIPENV_ACTIVE", raising=False)
    monkeypatch.delenv("POETRY_ACTIVE", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(sys, "base_prefix", "/usr")

    info = detect_venv()

    assert (info.active, info.kind, info.path) == (True, "uv", str(tmp_path))
