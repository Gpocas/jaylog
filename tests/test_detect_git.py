from jaylog.host import detect_git


def test_redacts_credentials_from_remote_url() -> None:
    assert (
        detect_git.redact_remote_url("https://user:ghp_live_token@github.com/org/repo.git")
        == "https://github.com/org/repo.git"
    )
    assert (
        detect_git.redact_remote_url("ssh://git@github.com/org/repo.git")
        == "ssh://github.com/org/repo.git"
    )


def test_git_unavailable_is_distinct_from_not_a_repo(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(detect_git, "_run_git", lambda *_args: None)

    info = detect_git.detect_git(git_dir=tmp_path)

    assert info.available is False
    assert info.repo is None


def test_collects_detached_repo_and_redacts_remote(monkeypatch, tmp_path) -> None:
    outputs = {
        ("--version",): "git version 2.43.0",
        ("rev-parse", "--show-toplevel"): str(tmp_path),
        ("rev-parse", "--abbrev-ref", "HEAD"): "HEAD",
        ("rev-parse", "HEAD"): "123456789abcdef",
        ("log", "-1", "--format=%s"): "Add host reporting",
        ("log", "-1", "--format=%cI"): "2026-09-16T19:25:39-03:00",
        ("status", "--porcelain"): "",
        ("remote", "get-url", "origin"): "https://bot:secret@github.com/org/repo.git",
    }
    monkeypatch.setattr(
        detect_git,
        "_run_git",
        lambda args, _cwd, _timeout: outputs.get(tuple(args)),
    )

    info = detect_git.detect_git(git_dir=tmp_path)

    assert info.available is True
    assert info.repo is True
    assert info.branch is None
    assert info.commit == "123456789abcdef"
    assert info.commit_short == "1234567"
    assert info.commit_msg == "Add host reporting"
    assert info.commit_datetime == "2026-09-16T19:25:39-03:00"
    assert info.dirty is False
    assert info.remote_url == "https://github.com/org/repo.git"
