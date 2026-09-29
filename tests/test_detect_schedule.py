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


REAL_BAT = (
    "call .venv\\Scripts\\activate.bat\r\n"
    'python "G:\\Bots\\BOT-MULTAS-TRS\\src\\Main.py"\r\n'
    "call deactivate\r\n"
    "popd\r\n"
)


def test_batch_in_project_root_matches_script_in_subfolder_by_absolute_path() -> None:
    target = r"G:\Bots\BOT-MULTAS-TRS\src\Main.py"
    action = ExecAction(r"G:\Bots\BOT-MULTAS-TRS\run.bat", "", "")
    assert detect_schedule.matches_entrypoint([action], target, read_file=lambda _path: REAL_BAT)
    # Outro Main.py do mesmo projeto não pode herdar as agendas deste .bat.
    other = r"G:\Bots\BOT-MULTAS-TRS\src\API\Main.py"
    assert not detect_schedule.matches_entrypoint([action], other, read_file=lambda _path: REAL_BAT)
    # Mesmo nome de arquivo em outro projeto também não casa.
    foreign = r"G:\Bots\OUTRO-BOT\src\Main.py"
    assert not detect_schedule.matches_entrypoint(
        [action], foreign, read_file=lambda _path: REAL_BAT
    )


def test_batch_matches_script_cited_relative_to_batch_folder() -> None:
    target = r"G:\Bots\BOT-MULTAS-TRS\src\Main.py"
    read = lambda _path: "python src\\Main.py"  # noqa: E731
    assert detect_schedule.matches_entrypoint(
        [ExecAction(r"G:\Bots\BOT-MULTAS-TRS\run.bat", "", "")], target, read_file=read
    )
    assert detect_schedule.matches_entrypoint(
        [ExecAction("run.bat", "", r"G:\Bots\BOT-MULTAS-TRS")], target, read_file=read
    )
    assert not detect_schedule.matches_entrypoint(
        [ExecAction(r"G:\Bots\BOT-MULTAS-TRS\run.bat", "", "")],
        r"G:\Bots\BOT-MULTAS-TRS\tools\src\Main.py",
        read_file=read,
    )


def test_collect_only_runs_for_task_scheduler(monkeypatch) -> None:
    monkeypatch.setattr(detect_schedule.win32, "is_windows", lambda: True)
    monkeypatch.setattr(
        detect_schedule.detect_execution,
        "detect_execution",
        lambda: type("Mode", (), {"mode": "terminal"})(),
    )
    assert detect_schedule.collect_schedules(query=lambda _timeout: []) is None
