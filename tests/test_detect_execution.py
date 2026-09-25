from jaylog.host import detect_execution, win32


def test_windows_service_and_scheduler_classification(monkeypatch) -> None:
    monkeypatch.setattr(win32, "session_id", lambda: 0)

    monkeypatch.setattr(
        win32,
        "process_table",
        lambda: {10: (20, "python.exe"), 20: (4, "services.exe"), 4: (0, "System")},
    )
    service = detect_execution._detect_windows(10)
    assert service.mode == detect_execution.WINDOWS_SERVICE
    assert service.parent_process_name == "services.exe"

    monkeypatch.setattr(
        win32,
        "process_table",
        lambda: {10: (20, "python.exe"), 20: (4, "svchost.exe"), 4: (0, "System")},
    )
    assert detect_execution._detect_windows(10).mode == detect_execution.TASK_SCHEDULER

    monkeypatch.setattr(
        win32,
        "process_table",
        lambda: {10: (20, "python.exe"), 20: (4, "taskeng.exe"), 4: (0, "System")},
    )
    assert detect_execution._detect_windows(10).mode == detect_execution.TASK_SCHEDULER


def test_windows_service_through_nssm_and_python_launcher(monkeypatch) -> None:
    monkeypatch.setattr(win32, "session_id", lambda: 0)
    monkeypatch.setattr(
        win32,
        "process_table",
        lambda: {
            10: (20, "python.exe"),
            20: (30, "python.exe"),
            30: (40, "nssm.exe"),
            40: (50, "services.exe"),
            50: (0, "wininit.exe"),
        },
    )

    info = detect_execution._detect_windows(10)

    assert info.mode == detect_execution.WINDOWS_SERVICE
    assert info.parent_process_name == "python.exe"
    assert info.detail == "python.exe < python.exe < nssm.exe < services.exe < wininit.exe"


def test_windows_task_scheduler_through_cmd_and_python_launcher(monkeypatch) -> None:
    monkeypatch.setattr(win32, "session_id", lambda: 0)
    monkeypatch.setattr(
        win32,
        "process_table",
        lambda: {
            10: (20, "python.exe"),
            20: (30, "python.exe"),
            30: (40, "cmd.exe"),
            40: (50, "svchost.exe"),
            50: (60, "services.exe"),
            60: (0, "wininit.exe"),
        },
    )

    info = detect_execution._detect_windows(10)

    assert info.mode == detect_execution.TASK_SCHEDULER
    assert info.parent_process_name == "python.exe"
    assert info.detail == (
        "python.exe < python.exe < cmd.exe < svchost.exe < services.exe < wininit.exe"
    )


def test_windows_unknown_when_process_table_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(win32, "session_id", lambda: 0)
    monkeypatch.setattr(win32, "process_table", lambda: {})

    info = detect_execution._detect_windows(10)

    assert info.mode == detect_execution.UNKNOWN
    assert "sessão 0" in (info.detail or "")


def test_win32_functions_are_safe_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(win32, "is_windows", lambda: False)

    assert win32.session_id() is None
    assert win32.process_table() == {}
    assert win32.oem_codepage() is None
