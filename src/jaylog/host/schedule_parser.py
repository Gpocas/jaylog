"""Conversão pura de triggers do Task Scheduler em agendas normalizadas."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
MAX_TIMES_PER_DAY = 24
WEEKDAYS = ("SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY")
REPETITIONS = ("CONTINUO", "DIARIO", "SEMANA", "DIA")

_XML_WEEKDAYS = {name.capitalize(): name for name in WEEKDAYS}
_MONTHS = frozenset(
    (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
)
_DAY_MIN = 24 * 60
_WEEK_MIN = 7 * _DAY_MIN
_NEUTRAL_CHILDREN = frozenset(
    ("StartBoundary", "EndBoundary", "Enabled", "ExecutionTimeLimit", "Repetition", "Id")
)
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
_FRACTION_RE = re.compile(r"\.\d+")
_MAX_OFFSETS = 100_000


class _Skip(Exception):
    """Trigger não representável sem perda."""


@dataclass(frozen=True)
class ExecAction:
    command: str
    arguments: str
    working_directory: str


def local_name(tag) -> str | None:
    """``{namespace}Task`` -> ``Task``; comentários não têm tag textual."""
    if not isinstance(tag, str):
        return None
    return tag.rsplit("}", 1)[-1]


def _find(element: ET.Element | None, *names: str) -> ET.Element | None:
    for name in names:
        if element is None:
            return None
        element = element.find(NS + name)
    return element


def _text(element: ET.Element | None, *names: str) -> str | None:
    found = _find(element, *names)
    return found.text.strip() if found is not None and found.text is not None else None


def _is_true(value: str | None) -> bool:
    return value is None or value.lower() == "true"


def task_enabled(task: ET.Element) -> bool:
    return _is_true(_text(task, "Settings", "Enabled"))


def exec_actions(task: ET.Element) -> list[ExecAction]:
    actions = _find(task, "Actions")
    if actions is None:
        return []
    return [
        ExecAction(
            _text(action, "Command") or "",
            _text(action, "Arguments") or "",
            _text(action, "WorkingDirectory") or "",
        )
        for action in actions.findall(NS + "Exec")
    ]


def parse_boundary(text: str, tz: tzinfo | None = None) -> datetime:
    cleaned = _FRACTION_RE.sub("", text.strip())
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    moment = datetime.fromisoformat(cleaned)
    if moment.tzinfo is not None:
        moment = moment.astimezone(tz).replace(tzinfo=None)
    return moment


def parse_duration(text: str) -> timedelta:
    match = _DURATION_RE.match(text)
    if not match or text in ("P", "PT"):
        raise _Skip("duração inválida")
    days, hours, minutes, seconds = (int(value) if value else 0 for value in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def _row(
    repetition: str,
    task_path: str,
    *,
    time: str | None = None,
    day_of_week: str | None = None,
    day_of_month: int | None = None,
) -> dict:
    return {
        "repetition": repetition,
        "day_of_week": day_of_week,
        "day_of_month": day_of_month,
        "time": time,
        "task_path": task_path,
    }


def _continuous(task_path: str) -> list[dict]:
    return [_row("CONTINUO", task_path)]


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _weekdays(schedule: ET.Element) -> list[str]:
    container = _find(schedule, "DaysOfWeek")
    names = [local_name(child.tag) for child in (container if container is not None else [])]
    days = [_XML_WEEKDAYS[name] for name in names if name in _XML_WEEKDAYS]
    if not days or len(days) != len([name for name in names if name]):
        raise _Skip("DaysOfWeek vazio ou desconhecido")
    return days


def _month_days(schedule: ET.Element) -> list[int] | None:
    months = _find(schedule, "Months")
    if months is not None and {local_name(child.tag) for child in months} - {None} != _MONTHS:
        return None
    container = _find(schedule, "DaysOfMonth")
    texts = [(child.text or "").strip() for child in (container if container is not None else [])]
    if not texts or any(not text.isdigit() for text in texts):
        return None
    days = [int(text) for text in texts]
    if any(day < 1 or day > 31 for day in days):
        raise _Skip("dia do mês fora de 1..31")
    return days


def _repetition(trigger: ET.Element) -> tuple[int, timedelta | None] | None:
    repetition = _find(trigger, "Repetition")
    if repetition is None:
        return None
    if any(
        local_name(child.tag) not in {None, "Interval", "Duration", "StopAtDurationEnd"}
        for child in repetition
    ):
        raise _Skip("filho desconhecido em Repetition")
    interval_text = _text(repetition, "Interval")
    if not interval_text:
        return None
    interval = parse_duration(interval_text)
    if interval <= timedelta(0) or interval.total_seconds() % 60:
        raise _Skip("intervalo de repetição inválido")
    duration_text = _text(repetition, "Duration")
    return int(interval.total_seconds() // 60), parse_duration(
        duration_text
    ) if duration_text else None


def _offsets(interval: int, duration: timedelta):
    offset = 0
    while timedelta(minutes=offset) < duration:
        if offset // interval >= _MAX_OFFSETS:
            raise _Skip("repetição longa demais")
        yield offset
        offset += interval


def _expand_indefinite(base: int, interval: int, task_path: str) -> list[dict]:
    if _DAY_MIN / interval > MAX_TIMES_PER_DAY:
        return _continuous(task_path)
    if _DAY_MIN % interval:
        return []
    return [
        _row("DIARIO", task_path, time=_hhmm((base + offset) % _DAY_MIN))
        for offset in range(0, _DAY_MIN, interval)
    ]


def _expand_daily(base: int, repetition, task_path: str) -> list[dict]:
    if repetition is None:
        return [_row("DIARIO", task_path, time=_hhmm(base))]
    interval, duration = repetition
    if duration is None:
        return _expand_indefinite(base, interval, task_path)
    offsets = list(_offsets(interval, duration))
    # Cada disparo diário pode ainda estar ativo no dia seguinte; por isso a
    # contagem real, e não só os horários distintos, decide o limite.
    if len(offsets) > MAX_TIMES_PER_DAY:
        return _continuous(task_path)
    return [
        _row("DIARIO", task_path, time=_hhmm(minute))
        for minute in sorted({(base + offset) % _DAY_MIN for offset in offsets})
    ]


def _expand_weekly(base: int, days: list[str], repetition, task_path: str) -> list[dict]:
    if repetition is None:
        return [_row("SEMANA", task_path, day_of_week=day, time=_hhmm(base)) for day in days]
    interval, duration = repetition
    if duration is None:
        return _expand_indefinite(base, interval, task_path)
    slots: dict[str, set[int]] = {}
    counts: dict[str, int] = {}
    for day in days:
        start = WEEKDAYS.index(day) * _DAY_MIN + base
        for offset in _offsets(interval, duration):
            minute_of_week = (start + offset) % _WEEK_MIN
            weekday = WEEKDAYS[minute_of_week // _DAY_MIN]
            counts[weekday] = counts.get(weekday, 0) + 1
            if counts[weekday] > MAX_TIMES_PER_DAY:
                return _continuous(task_path)
            slots.setdefault(weekday, set()).add(minute_of_week % _DAY_MIN)
    return [
        _row("SEMANA", task_path, day_of_week=weekday, time=_hhmm(minute))
        for weekday in WEEKDAYS
        for minute in sorted(slots.get(weekday, set()))
    ]


def _expand_monthly(base: int, days: list[int], repetition, task_path: str) -> list[dict]:
    if repetition is None:
        return [_row("DIA", task_path, day_of_month=day, time=_hhmm(base)) for day in days]
    interval, duration = repetition
    if duration is None:
        return _expand_indefinite(base, interval, task_path)
    offsets = list(_offsets(interval, duration))
    if any(base + offset >= _DAY_MIN for offset in offsets):
        return []
    if len(offsets) > MAX_TIMES_PER_DAY:
        return _continuous(task_path)
    return [
        _row("DIA", task_path, day_of_month=day, time=_hhmm(base + offset))
        for day in days
        for offset in offsets
    ]


def parse_trigger(
    trigger: ET.Element, task_path: str, *, tz: tzinfo | None = None, now: datetime | None = None
) -> list[dict]:
    kind = local_name(trigger.tag)
    if not _is_true(_text(trigger, "Enabled")):
        return []
    end = _text(trigger, "EndBoundary")
    if end and parse_boundary(end, tz) <= (now or datetime.now()):
        return []
    if kind in ("BootTrigger", "LogonTrigger"):
        return _continuous(task_path)
    if kind not in ("TimeTrigger", "CalendarTrigger"):
        return []

    schedule = None
    for child in trigger:
        name = local_name(child.tag)
        if name is None:
            continue
        if name.startswith("ScheduleBy"):
            if schedule is not None:
                raise _Skip("mais de um calendário")
            schedule = child
        elif name not in _NEUTRAL_CHILDREN:
            raise _Skip(f"elemento desconhecido: {name}")
    start_text = _text(trigger, "StartBoundary")
    if not start_text:
        raise _Skip("sem StartBoundary")
    start = parse_boundary(start_text, tz)
    base = start.hour * 60 + start.minute
    repetition = _repetition(trigger)

    if kind == "TimeTrigger":
        if schedule is not None:
            raise _Skip("TimeTrigger com calendário")
        return (
            _expand_indefinite(base, repetition[0], task_path)
            if repetition and repetition[1] is None
            else []
        )
    if schedule is None:
        raise _Skip("CalendarTrigger sem ScheduleBy")
    schedule_kind = local_name(schedule.tag)
    if schedule_kind == "ScheduleByDay":
        if any(local_name(child.tag) not in {None, "DaysInterval"} for child in schedule):
            raise _Skip("filho desconhecido em ScheduleByDay")
        if int(_text(schedule, "DaysInterval") or "1") != 1:
            return []
        return _expand_daily(base, repetition, task_path)
    if schedule_kind == "ScheduleByWeek":
        if any(
            local_name(child.tag) not in {None, "WeeksInterval", "DaysOfWeek"} for child in schedule
        ):
            raise _Skip("filho desconhecido em ScheduleByWeek")
        if int(_text(schedule, "WeeksInterval") or "1") != 1:
            return []
        return _expand_weekly(base, _weekdays(schedule), repetition, task_path)
    if schedule_kind == "ScheduleByMonth":
        if any(local_name(child.tag) not in {None, "DaysOfMonth", "Months"} for child in schedule):
            raise _Skip("filho desconhecido em ScheduleByMonth")
        days = _month_days(schedule)
        return _expand_monthly(base, days, repetition, task_path) if days is not None else []
    return []


def parse_task(
    task: ET.Element, task_path: str, *, tz: tzinfo | None = None, now: datetime | None = None
) -> list[dict]:
    triggers = _find(task, "Triggers")
    if triggers is None:
        return []
    rows: list[dict] = []
    for trigger in triggers:
        if local_name(trigger.tag) is None:
            continue
        try:
            rows.extend(parse_trigger(trigger, task_path, tz=tz, now=now))
        except Exception:
            continue
    return rows


def _sort_key(row: dict) -> tuple:
    return (
        REPETITIONS.index(row["repetition"]),
        WEEKDAYS.index(row["day_of_week"]) if row["day_of_week"] else -1,
        row["day_of_month"] or 0,
        row["time"] or "",
    )


def consolidate(rows: list[dict]) -> list[dict]:
    for row in rows:
        if row["repetition"] == "CONTINUO":
            return [row]
    seen: set[tuple] = set()
    unique: list[dict] = []
    for row in rows:
        key = (row["repetition"], row["day_of_week"], row["day_of_month"], row["time"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return sorted(unique, key=_sort_key)


__all__ = [
    "MAX_TIMES_PER_DAY",
    "NS",
    "REPETITIONS",
    "WEEKDAYS",
    "ExecAction",
    "consolidate",
    "exec_actions",
    "local_name",
    "parse_boundary",
    "parse_duration",
    "parse_task",
    "parse_trigger",
    "task_enabled",
]
