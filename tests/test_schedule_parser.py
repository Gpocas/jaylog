import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from jaylog.host.schedule_parser import NS, consolidate, parse_task


def _task(triggers: str) -> ET.Element:
    return ET.fromstring(f'<Task xmlns="{NS[1:-1]}"><Triggers>{triggers}</Triggers></Task>')


def test_daily_weekly_monthly_and_continuous_triggers() -> None:
    task = _task(
        "<CalendarTrigger><StartBoundary>2030-01-01T08:00:00</StartBoundary>"
        "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
        "<CalendarTrigger><StartBoundary>2030-01-01T07:30:00</StartBoundary>"
        "<ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek><Monday/><Friday/>"
        "</DaysOfWeek></ScheduleByWeek></CalendarTrigger>"
        "<CalendarTrigger><StartBoundary>2030-01-01T09:00:00</StartBoundary>"
        "<ScheduleByMonth><DaysOfMonth><Day>5</Day><Day>20</Day></DaysOfMonth></ScheduleByMonth>"
        "</CalendarTrigger><BootTrigger/>"
    )

    assert consolidate(parse_task(task, "\\task")) == [
        {
            "repetition": "CONTINUO",
            "day_of_week": None,
            "day_of_month": None,
            "time": None,
            "task_path": "\\task",
        }
    ]


def test_repetition_handles_midnight_and_more_than_24_per_day() -> None:
    weekly = _task(
        "<CalendarTrigger><StartBoundary>2030-01-07T22:00:00</StartBoundary>"
        "<Repetition><Interval>PT1H</Interval><Duration>PT4H</Duration></Repetition>"
        "<ScheduleByWeek><WeeksInterval>1</WeeksInterval><DaysOfWeek><Monday/></DaysOfWeek>"
        "</ScheduleByWeek></CalendarTrigger>"
    )
    assert parse_task(weekly, "\\task") == [
        {
            "repetition": "SEMANA",
            "day_of_week": "MONDAY",
            "day_of_month": None,
            "time": "22:00",
            "task_path": "\\task",
        },
        {
            "repetition": "SEMANA",
            "day_of_week": "MONDAY",
            "day_of_month": None,
            "time": "23:00",
            "task_path": "\\task",
        },
        {
            "repetition": "SEMANA",
            "day_of_week": "TUESDAY",
            "day_of_month": None,
            "time": "00:00",
            "task_path": "\\task",
        },
        {
            "repetition": "SEMANA",
            "day_of_week": "TUESDAY",
            "day_of_month": None,
            "time": "01:00",
            "task_path": "\\task",
        },
    ]
    continuous = _task(
        "<CalendarTrigger><StartBoundary>2030-01-01T08:00:00</StartBoundary>"
        "<Repetition><Interval>PT30M</Interval><Duration>P1D</Duration></Repetition>"
        "<ScheduleByDay/></CalendarTrigger>"
    )
    assert parse_task(continuous, "\\task")[0]["repetition"] == "CONTINUO"


def test_invalid_trigger_does_not_hide_valid_one_and_timezone_is_local() -> None:
    task = _task(
        "<CalendarTrigger><StartBoundary>not-a-date</StartBoundary><ScheduleByDay/></CalendarTrigger>"
        "<CalendarTrigger><StartBoundary>2030-01-01T10:00:00Z</StartBoundary><ScheduleByDay/>"
        "</CalendarTrigger>"
    )
    rows = parse_task(task, "\\task", tz=timezone.utc, now=datetime(2020, 1, 1))
    assert rows[0]["time"] == "10:00"


def test_consolidate_keeps_first_path_and_continuous_wins() -> None:
    duplicate = {
        "repetition": "DIARIO",
        "day_of_week": None,
        "day_of_month": None,
        "time": "08:00",
        "task_path": "\\first",
    }
    second = {**duplicate, "task_path": "\\second"}
    assert consolidate([second, duplicate]) == [second]
    continuous = {
        "repetition": "CONTINUO",
        "day_of_week": None,
        "day_of_month": None,
        "time": None,
        "task_path": "\\continuous",
    }
    assert consolidate([duplicate, continuous]) == [continuous]
