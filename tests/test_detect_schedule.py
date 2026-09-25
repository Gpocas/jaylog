from jaylog.host import detect_schedule
from jaylog.host.schedule_parser import ExecAction


def test_decode_and_split_tasks_preserves_preceding_comment(monkeypatch) -> None:
    monkeypatch.setattr(detect_schedule.win32, "oem_codepage", lambda: 850)
    text = '<!-- \\RPA\\Automações --><Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Settings/></Task>'
    tasks = detect_schedule.split_tasks(detect_schedule.decode_output(text.encode("cp850")))
    assert tasks[0].path == "\\RPA\\Automações"


def test_matches_script_and_batch_actions() -> None:
    target = r"C:\bots\main.py"
    assert detect_schedule.matches_entrypoint(
        [ExecAction(r"C:\Python\python.exe", '"main.py" --run', r'"C:\bots\\"')], target
    )
    assert detect_schedule.matches_entrypoint(
        [ExecAction(r"C:\bots\run.bat", "", "")],
        target,
        read_file=lambda _path: "python main.py",
    )
    assert not detect_schedule.matches_entrypoint(
        [ExecAction(r"C:\bots\run.bat", "", "")],
        target,
        read_file=lambda _path: "python another.py",
    )


def test_collect_only_runs_for_task_scheduler(monkeypatch) -> None:
    monkeypatch.setattr(detect_schedule.win32, "is_windows", lambda: True)
    monkeypatch.setattr(
        detect_schedule.detect_execution,
        "detect_execution",
        lambda: type("Mode", (), {"mode": "terminal"})(),
    )
    assert detect_schedule.collect_schedules(query=lambda _timeout: []) is None
