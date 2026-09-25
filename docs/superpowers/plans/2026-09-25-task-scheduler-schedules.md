# Task Scheduler Schedules (jaylog) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quando o bot roda pelo Agendador do Windows, ler as tasks ativas que executam o entrypoint, converter os triggers (sem perda) para Repetição → Granularidade → Horário e enviar uma vez por processo para `POST /logs/host-schedules`.

**Architecture:** `schedule_parser` (puro, XML → linhas) + `detect_schedule` (subprocess `schtasks /query /xml`, decodificação, matching B/D, orquestração `@safe`) + `schedule_reporter` (singleton que reaproveita `JaylogHostReporter` com `label="schedule"`, `one_shot=True` e factory que pode devolver `None`). Disparado em `configure()` só no Windows; a coleta roda na thread do reporter.

**Tech Stack:** Python 3.10–3.13, `xml.etree.ElementTree`, `ntpath`, `subprocess`, `ctypes`, `requests`, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-25-task-scheduler-schedules-design.md` (contrato HTTP: `backend-nn-analytics/docs/superpowers/specs/2026-09-25-task-scheduler-schedules-design.md`)

## Global Constraints

- Só coleta com `sys.platform == "win32"` **e** `detect_execution().mode == "task_scheduler"`.
- Nenhuma dependência nova. Windows só via `ctypes` e `subprocess` (`CREATE_NO_WINDOW` via `detect_git._popen_kwargs()`).
- Trigger é mapeado inteiro ou ignorado inteiro — nunca aproximado.
- `MAX_TIMES_PER_DAY = 24`: acima disso num dia, o trigger vira `CONTINUO`. Boot/Logon → `CONTINUO`.
- Qualquer `CONTINUO` no resultado → resultado é só `[CONTINUO]`.
- `MAX_ROWS = 500`; lista vazia ou acima do teto → não envia.
- Linha: `{"repetition", "day_of_week", "day_of_month", "time", "task_path"}`, `time` `"HH:MM"` ou `None`; `day_of_week` em `SUNDAY…SATURDAY`.
- Corpo: `{"run_id", "service", "hostname", "schedules"}`; endpoint derivado `/logs/add` → `/logs/host-schedules`.
- `JAYLOG_HOST_SCHEDULE_ENABLED=True`, `JAYLOG_HOST_SCHEDULE_HTTP_ENDPOINT=None`, `JAYLOG_HOST_SCHEDULE_TIMEOUT=10.0`.
- `PROTOCOL_VERSION = 4`; versão `0.3.0a5`.
- XML com `xml.etree.ElementTree` da stdlib, sem `defusedxml`: a entrada é a saída local do `schtasks` (sem DTD), a stdlib não resolve entidades externas e o expat ≥ 2.4 bloqueia expansão exponencial. Não justifica dependência nova.
- Nada pode levantar para o bot nem bloquear `configure()`. Comentários/docstrings em português explicando o porquê. `uv run ruff check` e `uv run ruff format` limpos.

## Review Focus

- Task com várias actions `Exec` onde só a segunda roda o bot → casa (qualquer action serve).
- `WorkingDirectory` com aspas e barra final (`"C:\bots\fat\"`) → casa igual ao sem aspas.
- Trigger com `StartBoundary` inválido ao lado de um trigger válido na mesma task → só o válido gera linhas.
- `Repetition` com `Interval` mas sem `Duration` num `ScheduleByDay` → regra de indefinido (não expansão finita).
- Comentário XML dentro de um `<Task>` (não só entre tasks) → não quebra o parser nem troca o path.

Cada linha acima tem teste nas Tasks 2–4 (marcados com `# review focus`).

---

### Task 1: `entrypoint_path()` e `win32.oem_codepage()`

**Files:**
- Modify: `src/jaylog/host/detect_git.py:71-87`
- Modify: `src/jaylog/host/win32.py` (nova função ao fim, antes de `ancestry` ou depois — seguir a ordem do arquivo)
- Test: `tests/test_detect_git.py`, `tests/test_detect_execution.py`

**Interfaces:**
- Produces:
  ```python
  # detect_git.py
  def entrypoint_path() -> str | None: ...   # arquivo do entrypoint (script ou .exe congelado), absoluto
  def entrypoint_dir() -> str | None: ...    # inalterado por fora; passa a derivar de entrypoint_path()
  # win32.py
  def oem_codepage() -> int | None: ...      # GetOEMCP(); None fora do Windows ou em falha
  ```

- [ ] **Step 1: Testes que falham** (acrescentar a `tests/test_detect_git.py`; imports `sys`, `types` no topo)

```python
import sys
import types

from jaylog.host import detect_git


def test_entrypoint_path_is_the_main_script(monkeypatch, tmp_path) -> None:
    script = tmp_path / "main.py"
    script.write_text("")
    monkeypatch.setitem(sys.modules, "__main__", types.SimpleNamespace(__file__=str(script)))
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert detect_git.entrypoint_path() == str(script)
    assert detect_git.entrypoint_dir() == str(tmp_path)


def test_entrypoint_path_is_the_executable_when_frozen(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bot.exe"))

    assert detect_git.entrypoint_path() == str(tmp_path / "bot.exe")


def test_entrypoint_path_is_none_without_main_file(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "__main__", types.SimpleNamespace())
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert detect_git.entrypoint_path() is None
    assert detect_git.entrypoint_dir() is None
```

E em `tests/test_detect_execution.py`:

```python
def test_oem_codepage_is_none_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(win32, "is_windows", lambda: False)
    assert win32.oem_codepage() is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_detect_git.py tests/test_detect_execution.py -q`
Expected: FAIL — `entrypoint_path` / `oem_codepage` inexistentes.

- [ ] **Step 3: Implementar**

`detect_git.py` — substituir `entrypoint_dir()` por:

```python
def entrypoint_path() -> str | None:
    """
    Arquivo que iniciou o processo: o script de ``__main__`` ou, congelado, o
    próprio ``.exe``.

    O matching das tasks do Agendador precisa do arquivo, não só da pasta: duas
    tasks na mesma pasta podem rodar scripts diferentes.
    """
    try:
        if getattr(sys, "frozen", False):
            return os.path.abspath(sys.executable)
        main = sys.modules.get("__main__")
        main_file = getattr(main, "__file__", None)
        if main_file:
            return os.path.abspath(main_file)
    except Exception:
        return None
    return None


def entrypoint_dir() -> str | None:
    """
    Diretório do script (ou do ``.exe``) que iniciou o processo.

    É o ponto de partida da busca pelo repositório e também o campo
    ``entrypoint`` do payload.
    """
    path = entrypoint_path()
    return os.path.dirname(path) if path else None
```

`win32.py` — atualizar a docstring do módulo ("Duas funções públicas…" passa a listar também `oem_codepage()`) e acrescentar:

```python
@safe(default=None)
def oem_codepage() -> int | None:
    """
    Codepage OEM do sistema (850 no Windows pt-BR).

    É nela que ferramentas de console como o ``schtasks`` escrevem quando a
    saída é redirecionada para um pipe — e o XML das tasks carrega caminhos com
    acento (``C:\\Automações``).
    """
    if not is_windows():
        return None

    import ctypes

    return int(ctypes.WinDLL("kernel32").GetOEMCP()) or None
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest -q`
Expected: PASS (suíte inteira — `entrypoint_dir` é usado por collectors/metrics).

- [ ] **Step 5: Commit**

```bash
git add src/jaylog/host/detect_git.py src/jaylog/host/win32.py tests/test_detect_git.py tests/test_detect_execution.py
git commit -m "Expose entrypoint file path and OEM codepage helpers"
```

---

### Task 2: `schedule_parser` — triggers simples, filtros e consolidação

**Files:**
- Create: `src/jaylog/host/schedule_parser.py`
- Create: `tests/test_schedule_parser.py`

**Interfaces:**
- Produces:
  ```python
  NS: str                          # "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
  MAX_TIMES_PER_DAY: int = 24
  WEEKDAYS: tuple[str, ...]        # ("SUNDAY", ..., "SATURDAY")
  REPETITIONS: tuple[str, ...]     # ("CONTINUO", "DIARIO", "SEMANA", "DIA")
  @dataclass(frozen=True) class ExecAction: command: str; arguments: str; working_directory: str
  def local_name(tag) -> str | None
  def task_enabled(task: ET.Element) -> bool
  def exec_actions(task: ET.Element) -> list[ExecAction]
  def parse_task(task: ET.Element, task_path: str, *, tz: tzinfo | None = None, now: datetime | None = None) -> list[dict]
  def consolidate(rows: list[dict]) -> list[dict]
  ```
  Esta task mapeia triggers **sem** `<Repetition>`. Os quatro `_expand_*` já existem com a assinatura final, mas levantam `_Skip` quando há repetição (trigger ignorado) — a Task 3 os substitui.

- [ ] **Step 1: Testes que falham**

```python
# tests/test_schedule_parser.py
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from jaylog.host import schedule_parser as sp

NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
NOW = datetime(2026, 9, 25, 12, 0)
BRT = timezone(timedelta(hours=-3))
PATH = "\\RPA\\Faturamento"
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip


def task(*triggers: str, extra: str = "") -> ET.Element:
    return ET.fromstring(
        f'<Task version="1.2" xmlns="{NS}"><Triggers>{"".join(triggers)}</Triggers>{extra}</Task>'
    )


def calendar(schedule: str, start: str = "2026-01-01T08:00:00", extra: str = "") -> str:
    return f"<CalendarTrigger><StartBoundary>{start}</StartBoundary>{extra}{schedule}</CalendarTrigger>"


def daily(interval: int = 1) -> str:
    return f"<ScheduleByDay><DaysInterval>{interval}</DaysInterval></ScheduleByDay>"


def weekly(*days: str, interval: int = 1) -> str:
    tags = "".join(f"<{d} />" for d in days)
    return f"<ScheduleByWeek><DaysOfWeek>{tags}</DaysOfWeek><WeeksInterval>{interval}</WeeksInterval></ScheduleByWeek>"


def monthly(*days: object, months: tuple[str, ...] = MONTHS) -> str:
    day_tags = "".join(f"<Day>{d}</Day>" for d in days)
    month_tags = "".join(f"<{m} />" for m in months)
    return f"<ScheduleByMonth><DaysOfMonth>{day_tags}</DaysOfMonth><Months>{month_tags}</Months></ScheduleByMonth>"


def rows(*triggers: str) -> list[tuple]:
    parsed = sp.consolidate(sp.parse_task(task(*triggers), PATH, tz=BRT, now=NOW))
    return [(r["repetition"], r["day_of_week"] or r["day_of_month"], r["time"]) for r in parsed]


def test_daily() -> None:
    assert rows(calendar(daily())) == [("DIARIO", None, "08:00")]


def test_weekly_one_row_per_day() -> None:
    assert rows(calendar(weekly("Monday", "Wednesday", "Friday"), start="2026-01-05T07:30:00")) == [
        ("SEMANA", "MONDAY", "07:30"),
        ("SEMANA", "WEDNESDAY", "07:30"),
        ("SEMANA", "FRIDAY", "07:30"),
    ]


def test_monthly_all_months() -> None:
    assert rows(calendar(monthly(20, 5), start="2026-01-05T09:00:00")) == [
        ("DIA", 5, "09:00"),
        ("DIA", 20, "09:00"),
    ]


def test_boot_and_logon_are_continuous() -> None:
    assert rows("<BootTrigger><Delay>PT1M</Delay></BootTrigger>") == [("CONTINUO", None, None)]
    assert rows("<LogonTrigger><UserId>RPA</UserId></LogonTrigger>") == [("CONTINUO", None, None)]


def test_lossy_triggers_are_ignored() -> None:
    assert rows(calendar(daily(2))) == []
    assert rows(calendar(weekly("Monday", interval=2))) == []
    assert rows(calendar(monthly(5, months=("January", "June")))) == []
    assert rows(calendar(monthly(5, "Last"))) == []
    assert rows(
        calendar(
            "<ScheduleByMonthDayOfWeek><Weeks><Week>1</Week></Weeks>"
            "<DaysOfWeek><Monday /></DaysOfWeek><Months><January /></Months></ScheduleByMonthDayOfWeek>"
        )
    ) == []
    assert rows(calendar(daily(), extra="<RandomDelay>PT10M</RandomDelay>")) == []


def test_non_recurring_and_event_triggers_are_ignored() -> None:
    assert rows("<TimeTrigger><StartBoundary>2026-01-01T08:00:00</StartBoundary></TimeTrigger>") == []
    assert rows("<IdleTrigger />", "<EventTrigger><Subscription>x</Subscription></EventTrigger>") == []
    assert rows("<RegistrationTrigger />", "<SessionStateChangeTrigger />") == []


def test_disabled_and_expired_triggers_are_ignored() -> None:
    assert rows(calendar(daily(), extra="<Enabled>false</Enabled>")) == []
    assert rows(calendar(daily(), extra="<EndBoundary>2026-09-01T00:00:00</EndBoundary>")) == []
    assert rows(calendar(daily(), extra="<EndBoundary>2027-01-01T00:00:00</EndBoundary>")) == [
        ("DIARIO", None, "08:00")
    ]
    # começa no futuro: task ativa, só não disparou ainda
    assert rows(calendar(daily(), start="2027-03-01T08:00:00")) == [("DIARIO", None, "08:00")]


def test_start_boundary_with_timezone_is_converted_to_local() -> None:
    assert rows(calendar(daily(), start="2026-01-01T11:00:00Z")) == [("DIARIO", None, "08:00")]
    assert rows(calendar(daily(), start="2026-01-01T08:00:00.0000000-03:00")) == [("DIARIO", None, "08:00")]


def test_bad_trigger_does_not_hide_the_good_one() -> None:  # review focus
    assert rows(calendar(daily(), start="ontem"), calendar(daily(), start="2026-01-01T09:15:00")) == [
        ("DIARIO", None, "09:15")
    ]


def test_consolidate_dedupes_sorts_and_collapses_continuous() -> None:
    assert rows(
        calendar(daily(), start="2026-01-01T09:00:00"),
        calendar(daily()),
        calendar(daily()),
        calendar(weekly("Monday")),
    ) == [("DIARIO", None, "08:00"), ("DIARIO", None, "09:00"), ("SEMANA", "MONDAY", "08:00")]
    assert rows(calendar(daily()), "<BootTrigger />") == [("CONTINUO", None, None)]


def test_consolidate_keeps_first_task_path() -> None:
    first = sp.parse_task(task(calendar(daily())), "\\A")
    second = sp.parse_task(task(calendar(daily())), "\\B")
    assert [r["task_path"] for r in sp.consolidate(first + second)] == ["\\A"]


def test_task_enabled_and_exec_actions() -> None:
    element = task(
        extra=(
            "<Settings><Enabled>false</Enabled></Settings>"
            "<Actions><Exec><Command>C:\\bots\\fat\\run.bat</Command></Exec>"
            "<Exec><Command>python.exe</Command><Arguments>main.py</Arguments>"
            "<WorkingDirectory>C:\\bots\\fat</WorkingDirectory></Exec></Actions>"
        )
    )
    assert sp.task_enabled(element) is False
    assert sp.task_enabled(task()) is True
    assert sp.exec_actions(element) == [
        sp.ExecAction("C:\\bots\\fat\\run.bat", "", ""),
        sp.ExecAction("python.exe", "main.py", "C:\\bots\\fat"),
    ]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_schedule_parser.py -q`
Expected: FAIL — `ModuleNotFoundError: jaylog.host.schedule_parser`.

- [ ] **Step 3: Implementar**

```python
# src/jaylog/host/schedule_parser.py
"""
XML de uma task do Agendador do Windows -> linhas de ``service_schedules``.

Módulo puro — sem I/O, sem ``win32`` — para que todo o mapeamento rode nos
testes em Linux. Recebe o ``<Task>`` já parseado e devolve linhas no formato do
backend (Repetição -> Granularidade -> Horário).

Regra central: um trigger é mapeado **inteiro** ou **ignorado inteiro**. O que
o modelo do backend não representa sem perda ("a cada 2 dias", "1ª segunda do
mês", atraso aleatório) não vira aproximação — some. Ver
``docs/superpowers/specs/2026-09-25-task-scheduler-schedules-design.md`` §4.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"

#: Acima disto num mesmo dia, listar horários deixa de informar: vira ``CONTINUO``.
MAX_TIMES_PER_DAY = 24

#: Ordem dos enums do backend — também é a ordem de exibição.
WEEKDAYS = ("SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY")
REPETITIONS = ("CONTINUO", "DIARIO", "SEMANA", "DIA")

_XML_WEEKDAYS = {name.capitalize(): name for name in WEEKDAYS}
_MONTHS = frozenset(
    (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
)  # fmt: skip

_DAY_MIN = 24 * 60
_WEEK_MIN = 7 * _DAY_MIN

#: Filhos de trigger que não mudam *quando* ele dispara. Qualquer outro
#: (``RandomDelay``, elementos de versões futuras do schema) deixa o horário
#: incerto ou desconhecido: o trigger inteiro é ignorado.
_NEUTRAL_CHILDREN = frozenset(
    ("StartBoundary", "EndBoundary", "Enabled", "ExecutionTimeLimit", "Repetition", "Id")
)

_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
_FRACTION_RE = re.compile(r"\.\d+")


class _Skip(Exception):
    """Trigger não representável sem perda."""


@dataclass(frozen=True)
class ExecAction:
    command: str
    arguments: str
    working_directory: str


# ----------------------------------------------------------------------
# leitura do XML
# ----------------------------------------------------------------------


def local_name(tag) -> str | None:
    """``{ns}Task`` -> ``Task``; ``None`` para comentários (cujo ``tag`` é uma função)."""
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
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _is_true(value: str | None) -> bool:
    # Ausente = habilitado: é o default do schema do Agendador.
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
    """
    ``StartBoundary``/``EndBoundary`` no relógio local, sem tzinfo.

    Sem offset o Agendador já grava hora local. Com ``Z`` ou offset ("sincronizar
    entre fusos") converte para o fuso da máquina (``tz=None``) — é nele que o bot
    dispara. A fração de segundo sai porque o ``fromisoformat`` do 3.10 não aceita
    as 7 casas que o Agendador às vezes grava.
    """
    cleaned = _FRACTION_RE.sub("", text.strip())
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    moment = datetime.fromisoformat(cleaned)
    if moment.tzinfo is not None:
        moment = moment.astimezone(tz).replace(tzinfo=None)
    return moment


def parse_duration(text: str) -> timedelta:
    """Subconjunto ISO 8601 que o Agendador grava: ``PT30M``, ``PT10H``, ``P1D``, ``P1DT2H``."""
    match = _DURATION_RE.match(text)
    if not match or text in ("P", "PT"):
        raise _Skip(f"duração inválida: {text}")
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


# ----------------------------------------------------------------------
# linhas
# ----------------------------------------------------------------------


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


def _hhmm(minute_of_day: int) -> str:
    return f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}"


def _weekdays(schedule: ET.Element) -> list[str]:
    container = _find(schedule, "DaysOfWeek")
    names = [local_name(child.tag) for child in (container if container is not None else [])]
    days = [_XML_WEEKDAYS[name] for name in names if name in _XML_WEEKDAYS]
    if not days or len(days) != len([n for n in names if n]):
        raise _Skip("DaysOfWeek vazio ou desconhecido")
    return days


def _month_days(schedule: ET.Element) -> list[int] | None:
    """Dias do mês, ou ``None`` se o trigger tem perda (meses parciais, ``Last``)."""
    months = _find(schedule, "Months")
    if months is not None:
        if {local_name(child.tag) for child in months} - {None} != _MONTHS:
            return None
    container = _find(schedule, "DaysOfMonth")
    texts = [(day.text or "").strip() for day in (container if container is not None else [])]
    if not texts or any(not t.isdigit() for t in texts):
        # "Last" não tem dia fixo: 28, 29, 30 ou 31 conforme o mês.
        return None
    days = [int(t) for t in texts]
    if any(d < 1 or d > 31 for d in days):
        raise _Skip("dia do mês fora de 1..31")
    return days


def _repetition(trigger: ET.Element) -> tuple[int, timedelta | None] | None:
    """``(intervalo em minutos, duração)``; duração ``None`` = indefinida. ``None`` = sem repetição."""
    rep = _find(trigger, "Repetition")
    if rep is None:
        return None
    interval_text = _text(rep, "Interval")
    if not interval_text:
        return None
    interval = parse_duration(interval_text)
    if interval <= timedelta(0) or interval.total_seconds() % 60:
        raise _Skip("intervalo de repetição inválido")
    duration_text = _text(rep, "Duration")
    duration = parse_duration(duration_text) if duration_text else None
    return int(interval.total_seconds() // 60), duration


def parse_trigger(
    trigger: ET.Element, task_path: str, *, tz: tzinfo | None = None, now: datetime | None = None
) -> list[dict]:
    """Linhas de um trigger. Levanta em XML inesperado; ``parse_task`` trata como ignorado."""
    kind = local_name(trigger.tag)
    if not _is_true(_text(trigger, "Enabled")):
        return []
    end = _text(trigger, "EndBoundary")
    if end and parse_boundary(end, tz) <= (now or datetime.now()):
        return []
    if kind in ("BootTrigger", "LogonTrigger"):
        # Iniciado junto com a máquina/sessão: o bot fica de pé, não tem horário.
        return _continuous(task_path)
    if kind not in ("TimeTrigger", "CalendarTrigger"):
        return []

    schedule = None
    for child in trigger:
        name = local_name(child.tag)
        if name is None:
            continue
        if name.startswith("ScheduleBy"):
            schedule = child
        elif name not in _NEUTRAL_CHILDREN:
            raise _Skip(f"elemento {name} no trigger")

    start_text = _text(trigger, "StartBoundary")
    if not start_text:
        raise _Skip("sem StartBoundary")
    start = parse_boundary(start_text, tz)
    base = start.hour * 60 + start.minute
    rep = _repetition(trigger)

    if kind == "TimeTrigger":
        # "Uma vez" só é recorrente com repetição indefinida — tratada na Task 3.
        if rep is None or rep[1] is not None:
            return []
        return _expand_indefinite(base, rep[0], task_path)

    if schedule is None:
        raise _Skip("CalendarTrigger sem ScheduleBy*")
    schedule_kind = local_name(schedule.tag)

    if schedule_kind == "ScheduleByDay":
        if int(_text(schedule, "DaysInterval") or "1") != 1:
            return []
        return _expand_daily(base, rep, task_path)
    if schedule_kind == "ScheduleByWeek":
        if int(_text(schedule, "WeeksInterval") or "1") != 1:
            return []
        return _expand_weekly(base, _weekdays(schedule), rep, task_path)
    if schedule_kind == "ScheduleByMonth":
        days = _month_days(schedule)
        if days is None:
            return []
        return _expand_monthly(base, days, rep, task_path)
    # ScheduleByMonthDayOfWeek ("1ª segunda do mês") e desconhecidos
    return []


def _expand_daily(base: int, rep, task_path: str) -> list[dict]:
    if rep is not None:
        raise _Skip("repetição: ver Task 3")
    return [_row("DIARIO", task_path, time=_hhmm(base))]


def _expand_weekly(base: int, days: list[str], rep, task_path: str) -> list[dict]:
    if rep is not None:
        raise _Skip("repetição: ver Task 3")
    return [_row("SEMANA", task_path, day_of_week=day, time=_hhmm(base)) for day in days]


def _expand_monthly(base: int, days: list[int], rep, task_path: str) -> list[dict]:
    if rep is not None:
        raise _Skip("repetição: ver Task 3")
    return [_row("DIA", task_path, day_of_month=day, time=_hhmm(base)) for day in days]


def _expand_indefinite(base: int, interval: int, task_path: str) -> list[dict]:
    raise _Skip("repetição: ver Task 3")


def parse_task(
    task: ET.Element, task_path: str, *, tz: tzinfo | None = None, now: datetime | None = None
) -> list[dict]:
    """Linhas de todos os triggers da task. Um trigger ruim não esconde os outros."""
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
            # _Skip, data/inteiro inválido, XML fora do esperado: ignorar só
            # este trigger é seguro porque nada dele é enviado pela metade.
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
    """
    União sem duplicatas (``task_path`` do primeiro visto), em ordem de exibição.

    Qualquer ``CONTINUO`` engole o resto: "contínuo e também às 08:00" confunde
    mais do que informa.
    """
    for row in rows:
        if row["repetition"] == "CONTINUO":
            return [row]
    seen: set[tuple] = set()
    unique: list[dict] = []
    for row in rows:
        key = (row["repetition"], row["day_of_week"], row["day_of_month"], row["time"])
        if key in seen:
            continue
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
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_schedule_parser.py -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS; ruff limpo (se o format reclamar, rodar `uv run ruff format src tests` e conferir o diff).

- [ ] **Step 5: Commit**

```bash
git add src/jaylog/host/schedule_parser.py tests/test_schedule_parser.py
git commit -m "Add Task Scheduler XML parser for non-repeating triggers"
```

---

### Task 3: `schedule_parser` — expansão de repetição

**Files:**
- Modify: `src/jaylog/host/schedule_parser.py` (substituir `_expand_daily`, `_expand_weekly`, `_expand_monthly`, `_expand_indefinite`; acrescentar `_offsets`)
- Test: `tests/test_schedule_parser.py`

**Interfaces:**
- Consumes: tudo da Task 2 (mesmas assinaturas `_expand_*(base, …, rep, task_path)`, `rep = (interval_min, duration | None) | None`).
- Produces: nada novo público.

- [ ] **Step 1: Testes que falham** (acrescentar)

```python
def repeat(interval: str, duration: str | None = None) -> str:
    dur = f"<Duration>{duration}</Duration>" if duration else ""
    return f"<Repetition><Interval>{interval}</Interval>{dur}<StopAtDurationEnd>false</StopAtDurationEnd></Repetition>"


def test_daily_repetition_expands_into_times() -> None:
    result = rows(calendar(daily(), extra=repeat("PT30M", "PT10H")))
    assert len(result) == 20
    assert result[0] == ("DIARIO", None, "08:00")
    assert result[-1] == ("DIARIO", None, "17:30")


def test_daily_repetition_across_midnight_wraps() -> None:
    assert rows(calendar(daily(), start="2026-01-01T22:00:00", extra=repeat("PT1H", "PT4H"))) == [
        ("DIARIO", None, "00:00"),
        ("DIARIO", None, "01:00"),
        ("DIARIO", None, "22:00"),
        ("DIARIO", None, "23:00"),
    ]


def test_daily_repetition_longer_than_a_day_is_the_union() -> None:
    # 08:00 a cada 5 h por 36 h: as cadeias de dias seguidos se sobrepõem, mas
    # o conjunto de horários de um dia é sempre o mesmo.
    assert [t for _, _, t in rows(calendar(daily(), extra=repeat("PT5H", "PT36H")))] == [
        "04:00", "08:00", "09:00", "13:00", "14:00", "18:00", "19:00", "23:00",
    ]  # fmt: skip


def test_weekly_repetition_crosses_into_next_weekday() -> None:
    assert rows(calendar(weekly("Monday"), start="2026-01-05T22:00:00", extra=repeat("PT1H", "PT4H"))) == [
        ("SEMANA", "MONDAY", "22:00"),
        ("SEMANA", "MONDAY", "23:00"),
        ("SEMANA", "TUESDAY", "00:00"),
        ("SEMANA", "TUESDAY", "01:00"),
    ]
    assert rows(calendar(weekly("Saturday"), start="2026-01-03T23:30:00", extra=repeat("PT1H", "PT2H"))) == [
        ("SEMANA", "SUNDAY", "00:30"),
        ("SEMANA", "SATURDAY", "23:30"),
    ]


def test_weekly_cap_is_per_calendar_day() -> None:
    # seg 22:00 a cada 1 h por 30 h: 2 na segunda, 24 na terça, 4 na quarta -> não é CONTINUO
    result = rows(calendar(weekly("Monday"), start="2026-01-05T22:00:00", extra=repeat("PT1H", "PT30H")))
    assert len(result) == 30
    assert sum(1 for _, day, _ in result if day == "TUESDAY") == 24


def test_monthly_repetition_same_day_expands() -> None:
    result = rows(calendar(monthly(10), start="2026-01-10T06:00:00", extra=repeat("PT2H", "PT12H")))
    assert result == [("DIA", 10, t) for t in ("06:00", "08:00", "10:00", "12:00", "14:00", "16:00")]


def test_monthly_repetition_across_midnight_is_ignored() -> None:
    assert rows(calendar(monthly(30), start="2026-01-30T23:00:00", extra=repeat("PT1H", "PT2H"))) == []


def test_more_than_24_times_a_day_is_continuous() -> None:
    hourly = rows(calendar(daily(), extra=repeat("PT1H", "PT24H")))
    assert len(hourly) == 24  # exatamente no teto: continua expandido
    assert [t for _, _, t in hourly][:2] == ["00:00", "01:00"]
    assert rows(calendar(daily(), extra=repeat("PT30M", "P1D"))) == [("CONTINUO", None, None)]
    assert rows(calendar(daily(), extra=repeat("PT55M", "PT24H"))) == [("CONTINUO", None, None)]


def test_indefinite_repetition() -> None:
    once = "<TimeTrigger><StartBoundary>2026-01-01T08:00:00</StartBoundary>{}</TimeTrigger>"
    hourly = rows(once.format(repeat("PT1H")))
    assert len(hourly) == 24 and hourly[0] == ("DIARIO", None, "00:00")
    assert rows(once.format(repeat("PT15M"))) == [("CONTINUO", None, None)]
    assert rows(once.format(repeat("PT5H"))) == []  # 24 h não é múltiplo de 5 h: horário deriva
    assert rows(once.format(repeat("PT1H", "PT3H"))) == []  # uma vez, duração finita: não recorre


def test_indefinite_repetition_on_calendar_trigger() -> None:  # review focus
    result = rows(calendar(daily(), extra=repeat("PT6H")))
    assert result == [("DIARIO", None, t) for t in ("02:00", "08:00", "14:00", "20:00")]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_schedule_parser.py -q`
Expected: FAIL nos testes novos (triggers com repetição hoje dão `[]`).

- [ ] **Step 3: Implementar** — substituir os quatro `_expand_*` e acrescentar `_offsets`:

```python
#: Guarda contra ``Duration`` absurda com intervalo de 1 min (P365D = 525 mil
#: offsets). Muito antes disso o teto por dia já resolveu para ``CONTINUO``.
_MAX_OFFSETS = 100_000


def _offsets(interval: int, duration: timedelta):
    """Minutos ``k·I`` desde o disparo, enquanto ``k·I < D``."""
    k = 0
    while timedelta(minutes=k * interval) < duration:
        if k >= _MAX_OFFSETS:
            raise _Skip("repetição longa demais")
        yield k * interval
        k += 1


def _expand_indefinite(base: int, interval: int, task_path: str) -> list[dict]:
    """
    Repetição sem ``Duration``: a partir do primeiro disparo roda a cada
    ``interval`` para sempre, então é um horário diário — se 24 h for múltiplo do
    intervalo. Senão o horário muda a cada dia e não há como representar.
    """
    if _DAY_MIN / interval > MAX_TIMES_PER_DAY:
        return _continuous(task_path)
    if _DAY_MIN % interval:
        return []
    return [
        _row("DIARIO", task_path, time=_hhmm((base + k * interval) % _DAY_MIN))
        for k in range(_DAY_MIN // interval)
    ]


def _expand_daily(base: int, rep, task_path: str) -> list[dict]:
    if rep is None:
        return [_row("DIARIO", task_path, time=_hhmm(base))]
    interval, duration = rep
    if duration is None:
        return _expand_indefinite(base, interval, task_path)
    # Módulo 24 h e união: exato mesmo com D > 24 h, porque o trigger recomeça
    # todo dia e o conjunto de horários de qualquer dia é o mesmo.
    minutes: set[int] = set()
    for offset in _offsets(interval, duration):
        minutes.add((base + offset) % _DAY_MIN)
        if len(minutes) > MAX_TIMES_PER_DAY:
            return _continuous(task_path)
    return [_row("DIARIO", task_path, time=_hhmm(m)) for m in sorted(minutes)]


def _expand_weekly(base: int, days: list[str], rep, task_path: str) -> list[dict]:
    if rep is None:
        return [_row("SEMANA", task_path, day_of_week=day, time=_hhmm(base)) for day in days]
    interval, duration = rep
    if duration is None:
        return _expand_indefinite(base, interval, task_path)
    # Mesmo raciocínio do diário, módulo uma semana: o horário que passa da
    # meia-noite cai no dia da semana seguinte (sábado -> domingo).
    slots: dict[str, set[int]] = {}
    for day in days:
        start = WEEKDAYS.index(day) * _DAY_MIN + base
        for offset in _offsets(interval, duration):
            minute_of_week = (start + offset) % _WEEK_MIN
            weekday = WEEKDAYS[minute_of_week // _DAY_MIN]
            bucket = slots.setdefault(weekday, set())
            bucket.add(minute_of_week % _DAY_MIN)
            if len(bucket) > MAX_TIMES_PER_DAY:
                return _continuous(task_path)
    return [
        _row("SEMANA", task_path, day_of_week=weekday, time=_hhmm(m))
        for weekday, minutes in slots.items()
        for m in sorted(minutes)
    ]


def _expand_monthly(base: int, days: list[int], rep, task_path: str) -> list[dict]:
    if rep is None:
        return [_row("DIA", task_path, day_of_month=day, time=_hhmm(base)) for day in days]
    interval, duration = rep
    if duration is None:
        return _expand_indefinite(base, interval, task_path)
    times: list[int] = []
    for offset in _offsets(interval, duration):
        if base + offset >= _DAY_MIN:
            # Dia 30 + 1 é 31 ou 1 conforme o mês: sem representação exata.
            return []
        times.append(base + offset)
    if len(times) > MAX_TIMES_PER_DAY:
        return _continuous(task_path)
    return [_row("DIA", task_path, day_of_month=day, time=_hhmm(m)) for day in days for m in times]
```

Remover do `parse_trigger` o comentário "— tratada na Task 3".

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_schedule_parser.py -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS; ruff limpo.

- [ ] **Step 5: Commit**

```bash
git add src/jaylog/host/schedule_parser.py tests/test_schedule_parser.py
git commit -m "Expand Task Scheduler repetition intervals into schedule times"
```

---

### Task 4: `detect_schedule` — leitura, matching e orquestração

**Files:**
- Create: `src/jaylog/host/detect_schedule.py`
- Create: `tests/test_detect_schedule.py`

**Interfaces:**
- Consumes: `schedule_parser.{local_name, task_enabled, exec_actions, parse_task, consolidate, ExecAction}` (Tasks 2–3); `detect_git.entrypoint_path`, `detect_git._popen_kwargs` (Task 1 / existente); `win32.is_windows`, `win32.oem_codepage` (Task 1); `detect_execution.detect_execution`, `detect_execution.TASK_SCHEDULER`, `detect_execution.ExecutionInfo`.
- Produces:
  ```python
  MAX_ROWS: int = 500
  @dataclass(frozen=True) class ScheduledTask: path: str; element: ET.Element
  def decode_output(raw: bytes) -> str
  def split_tasks(text: str) -> list[ScheduledTask]           # levanta ET.ParseError
  def query_tasks(timeout: float) -> list[ScheduledTask] | None
  def matches_entrypoint(actions: list[ExecAction], entrypoint: str, *, read_file=None) -> bool
  def collect_schedules(*, timeout: float = 10.0, query=None, read_file=None, now=None) -> list[dict] | None   # @safe
  ```

- [ ] **Step 1: Testes que falham**

```python
# tests/test_detect_schedule.py
import subprocess
from datetime import datetime

from jaylog.host import detect_execution, detect_git, detect_schedule, win32
from jaylog.host.schedule_parser import ExecAction

NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ENTRY = r"C:\bots\fat\main.py"
DAILY = (
    "<Triggers><CalendarTrigger><StartBoundary>2026-01-01T08:00:00</StartBoundary>"
    "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>"
)


def task_xml(command: str, *, arguments: str = "", workdir: str = "", enabled: bool = True, triggers: str = DAILY) -> str:
    return (
        f'<Task version="1.2" xmlns="{NS}">{triggers}'
        f"<Settings><Enabled>{str(enabled).lower()}</Enabled></Settings>"
        f"<Actions><Exec><Command>{command}</Command><Arguments>{arguments}</Arguments>"
        f"<WorkingDirectory>{workdir}</WorkingDirectory></Exec></Actions></Task>"
    )


RUN_BAT = r"C:\bots\fat\run.bat"
# Sem barra invertida dentro de `{}`: em Python < 3.12 isso é SyntaxError na f-string.
OUTPUT = (
    '<?xml version="1.0" encoding="UTF-16"?>\r\n<Tasks>\r\n'
    "  <!-- \\Microsoft\\Windows\\Defrag\\ScheduledDefrag -->\r\n"
    f"  {task_xml('defrag.exe')}\r\n"
    "  <!-- \\RPA\\Faturamento -->\r\n"
    f"  {task_xml(RUN_BAT)}\r\n"
    "  <!-- \\RPA\\Desligada -->\r\n"
    f"  {task_xml(RUN_BAT, enabled=False)}\r\n"
    "</Tasks>\r\n"
)


def fake_run(monkeypatch, *, stdout: bytes = b"", returncode: int = 0, exc: Exception | None = None) -> list:
    calls: list = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=b"")

    monkeypatch.setattr(detect_schedule.subprocess, "run", run)
    return calls


# ---- decodificação e split ------------------------------------------------


def test_decode_output_handles_oem_utf8_and_utf16(monkeypatch) -> None:
    monkeypatch.setattr(win32, "oem_codepage", lambda: 850)
    assert detect_schedule.decode_output("C:\\Automações".encode("cp850")) == "C:\\Automações"
    assert detect_schedule.decode_output("C:\\Automações".encode()) == "C:\\Automações"
    assert detect_schedule.decode_output("C:\\Automações".encode("utf-16")) == "C:\\Automações"


def test_split_tasks_pairs_each_task_with_the_preceding_comment() -> None:
    tasks = detect_schedule.split_tasks(OUTPUT)
    assert [t.path for t in tasks] == [
        "\\Microsoft\\Windows\\Defrag\\ScheduledDefrag",
        "\\RPA\\Faturamento",
        "\\RPA\\Desligada",
    ]


def test_split_tasks_ignores_comments_inside_a_task() -> None:  # review focus
    inner = task_xml("x.exe").replace("<Settings>", "<!-- gerado pelo instalador --><Settings>")
    tasks = detect_schedule.split_tasks(f"<Tasks><!-- \\RPA\\X -->{inner}</Tasks>")
    assert [t.path for t in tasks] == ["\\RPA\\X"]


def test_split_tasks_accepts_output_without_tasks_wrapper() -> None:
    tasks = detect_schedule.split_tasks(f"<!-- \\RPA\\X -->{task_xml('x.exe')}")
    assert [t.path for t in tasks] == ["\\RPA\\X"]


# ---- query_tasks -----------------------------------------------------------


def test_query_tasks_drops_microsoft_and_disabled(monkeypatch) -> None:
    monkeypatch.setattr(win32, "oem_codepage", lambda: 850)
    calls = fake_run(monkeypatch, stdout=OUTPUT.encode("cp850"))

    tasks = detect_schedule.query_tasks(7.0)

    assert [t.path for t in tasks] == ["\\RPA\\Faturamento"]
    args, kwargs = calls[0]
    assert args == ["schtasks", "/query", "/xml"]
    assert kwargs["timeout"] == 7.0
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_query_tasks_returns_none_on_failure(monkeypatch) -> None:
    fake_run(monkeypatch, returncode=1)
    assert detect_schedule.query_tasks(1.0) is None
    fake_run(monkeypatch, exc=subprocess.TimeoutExpired("schtasks", 1.0))
    assert detect_schedule.query_tasks(1.0) is None
    fake_run(monkeypatch, exc=FileNotFoundError("schtasks"))
    assert detect_schedule.query_tasks(1.0) is None
    fake_run(monkeypatch, stdout=b"<Tasks><Task>")
    assert detect_schedule.query_tasks(1.0) is None


# ---- matching --------------------------------------------------------------


def match(*actions: ExecAction, entry: str = ENTRY, bat: str | None = "python main.py") -> bool:
    return detect_schedule.matches_entrypoint(list(actions), entry, read_file=lambda _path: bat)


def test_script_relative_to_start_in() -> None:
    assert match(ExecAction("python.exe", "main.py", r"C:\bots\fat"))
    assert match(ExecAction(r"C:\bots\fat\.venv\Scripts\python.exe", r"main.py --prod", r"C:\bots\fat"))
    assert not match(ExecAction("python.exe", "outro.py", r"C:\bots\fat"))
    assert not match(ExecAction("python.exe", "main.py", ""))  # sem "Iniciar em": roda em system32


def test_script_absolute_quoted_and_case_insensitive() -> None:
    assert match(ExecAction("python.exe", r'"C:\Bots\Fat\MAIN.py" --prod', ""))


def test_script_with_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("BOTS", r"C:\bots")
    assert match(ExecAction("python.exe", r"%BOTS%\fat\main.py", ""))


def test_workdir_with_quotes_and_trailing_slash() -> None:  # review focus
    assert match(ExecAction("python.exe", "main.py", '"C:\\bots\\fat\\"'))
    assert match(ExecAction(r"run.bat", "", '"C:\\bots\\fat\\"'))


def test_batch_file_in_entrypoint_folder() -> None:
    assert match(ExecAction(r"C:\bots\fat\run.bat", "", ""))
    assert match(ExecAction(r"C:\bots\fat\run.cmd", "", ""), bat="call .venv\\Scripts\\activate\r\nMAIN.PY")
    assert not match(ExecAction(r"C:\bots\fat\run_outro.bat", "", ""), bat="python outro.py")
    assert match(ExecAction(r"C:\bots\fat\run.bat", "", ""), bat=None)  # ilegível: casa pela pasta


def test_batch_file_elsewhere_with_start_in() -> None:
    assert match(ExecAction(r"C:\scripts\run_fat.bat", "", r"C:\bots\fat"))
    assert not match(ExecAction(r"C:\scripts\run_fat.bat", "", r"C:\scripts"))


def test_any_action_can_match() -> None:  # review focus
    assert match(ExecAction("cmd.exe", "/c echo oi", ""), ExecAction("python.exe", "main.py", r"C:\bots\fat"))


def test_frozen_executable() -> None:
    assert match(ExecAction(r"C:\bots\fat\bot.exe", "", ""), entry=r"C:\bots\fat\bot.exe")


# ---- collect_schedules -----------------------------------------------------


def scheduler_process(monkeypatch, mode: str = detect_execution.TASK_SCHEDULER) -> None:
    monkeypatch.setattr(win32, "is_windows", lambda: True)
    monkeypatch.setattr(
        detect_execution,
        "detect_execution",
        lambda: detect_execution.ExecutionInfo(mode, None, 0, 10, "svchost.exe"),
    )
    monkeypatch.setattr(detect_git, "entrypoint_path", lambda: ENTRY)


def tasks_query(*xml_by_path: tuple[str, str]):
    return lambda _timeout: detect_schedule.split_tasks(
        "<Tasks>" + "".join(f"<!-- {p} -->{x}" for p, x in xml_by_path) + "</Tasks>"
    )


def test_collect_returns_rows_for_matching_tasks(monkeypatch) -> None:
    scheduler_process(monkeypatch)
    query = tasks_query(
        ("\\RPA\\Faturamento", task_xml("python.exe", arguments="main.py", workdir=r"C:\bots\fat")),
        ("\\RPA\\Outro", task_xml("python.exe", arguments="outro.py", workdir=r"C:\bots\fat")),
    )

    rows = detect_schedule.collect_schedules(query=query, read_file=lambda _p: None, now=datetime(2026, 9, 25))

    assert rows == [
        {"repetition": "DIARIO", "day_of_week": None, "day_of_month": None, "time": "08:00", "task_path": "\\RPA\\Faturamento"}
    ]


def test_collect_is_none_outside_windows_or_scheduler(monkeypatch) -> None:
    called: list = []
    query = lambda _t: called.append(1) or []  # noqa: E731

    monkeypatch.setattr(win32, "is_windows", lambda: False)
    assert detect_schedule.collect_schedules(query=query) is None

    scheduler_process(monkeypatch, mode=detect_execution.INTERACTIVE)
    assert detect_schedule.collect_schedules(query=query) is None
    assert called == []


def test_collect_is_none_without_matches_or_rows(monkeypatch) -> None:
    scheduler_process(monkeypatch)
    no_match = tasks_query(("\\RPA\\Outro", task_xml("python.exe", arguments="outro.py", workdir=r"C:\bots\fat")))
    assert detect_schedule.collect_schedules(query=no_match) is None

    lossy = "<Triggers><IdleTrigger /></Triggers>"
    only_lossy = tasks_query(("\\RPA\\Fat", task_xml("python.exe", arguments="main.py", workdir=r"C:\bots\fat", triggers=lossy)))
    assert detect_schedule.collect_schedules(query=only_lossy) is None


def test_collect_warns_once_when_query_fails(monkeypatch, capsys) -> None:
    scheduler_process(monkeypatch)
    assert detect_schedule.collect_schedules(query=lambda _t: None) is None
    assert "[jaylog] schedule:" in capsys.readouterr().err


def test_collect_refuses_more_than_max_rows(monkeypatch, capsys) -> None:
    scheduler_process(monkeypatch)
    monkeypatch.setattr(detect_schedule, "MAX_ROWS", 1)
    weekly = (
        "<Triggers><CalendarTrigger><StartBoundary>2026-01-05T08:00:00</StartBoundary>"
        "<ScheduleByWeek><DaysOfWeek><Monday /><Tuesday /></DaysOfWeek><WeeksInterval>1</WeeksInterval>"
        "</ScheduleByWeek></CalendarTrigger></Triggers>"
    )
    query = tasks_query(("\\RPA\\Fat", task_xml("python.exe", arguments="main.py", workdir=r"C:\bots\fat", triggers=weekly)))

    assert detect_schedule.collect_schedules(query=query, now=datetime(2026, 9, 25)) is None
    assert "teto" in capsys.readouterr().err


def test_collect_never_raises(monkeypatch) -> None:
    scheduler_process(monkeypatch)

    def boom(_timeout):
        raise RuntimeError("inesperado")

    assert detect_schedule.collect_schedules(query=boom) is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_detect_schedule.py -q`
Expected: FAIL — `ModuleNotFoundError: jaylog.host.detect_schedule`.

- [ ] **Step 3: Implementar**

```python
# src/jaylog/host/detect_schedule.py
"""
Agendas do Agendador de Tarefas do Windows que executam **este** bot.

Três decisões que não são óbvias:

* **``schtasks /query /xml``, e não COM nem PowerShell.** Sem dependência nova
  (o jaylog fala com o Windows só por ``ctypes``/subprocess), sem os 1-2 s de
  partida do PowerShell, e o XML é o schema oficial da task — não muda com o
  idioma do Windows.

* **Encoding.** Redirecionada para pipe, a saída vem na codepage OEM (cp850 no
  pt-BR), não em UTF-8. Caminho com acento (``C:\\Automações``) decodificado
  errado nunca casaria com o entrypoint.

* **Matching por arquivo, não só por pasta.** Os bots são agendados por
  ``.bat`` na pasta do projeto (o ``.py`` não aparece na task) ou por script
  relativo com "Iniciar em". Para não atribuir ao bot a task de um vizinho da
  mesma pasta, o ``.bat`` precisa citar o nome do script — quando dá para lê-lo.

Todos os caminhos são tratados com ``ntpath``: a feature é só de Windows, e
assim os testes rodam em Linux com caminhos ``C:\\...``.
"""

from __future__ import annotations

import ntpath
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from jaylog.host import detect_execution, detect_git, schedule_parser, win32
from jaylog.host._safe import safe
from jaylog.host.schedule_parser import ExecAction

#: Mesmo teto do backend. Com 24 horários/dia por trigger não deveria ocorrer.
MAX_ROWS = 500

_BATCH_MAX_BYTES = 64 * 1024
_SCRIPT_SUFFIXES = (".py", ".pyw")
_BATCH_SUFFIXES = (".bat", ".cmd")
_XML_DECL_RE = re.compile(r"<\?xml[^>]*\?>")


@dataclass(frozen=True)
class ScheduledTask:
    path: str
    element: ET.Element


def _warn(message: str) -> None:
    print(f"[jaylog] schedule: {message}", file=sys.stderr)


# ----------------------------------------------------------------------
# leitura
# ----------------------------------------------------------------------


def decode_output(raw: bytes) -> str:
    """
    Bytes do ``schtasks`` (ou de um ``.bat``) -> texto.

    UTF-8 estrito vem antes da OEM: cobre máquinas com OEMCP=65001, e texto
    cp850 com acento nunca é UTF-8 válido — então cai na tentativa seguinte.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    candidates = ["utf-8"]
    codepage = win32.oem_codepage()
    if codepage:
        candidates.append(f"cp{codepage}")
    candidates.append("mbcs")
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def split_tasks(text: str) -> list[ScheduledTask]:
    """
    Saída de ``schtasks /query /xml`` -> tasks com o path do comentário
    (``<!-- \\Pasta\\Nome -->``) que precede cada ``<Task>``.

    Levanta ``ET.ParseError`` em XML malformado.
    """
    body = _XML_DECL_RE.sub("", text).strip()
    if not body.startswith("<Tasks"):
        body = f"<Tasks>{body}</Tasks>"
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    parser.feed(body)
    root = parser.close()

    tasks: list[ScheduledTask] = []
    path: str | None = None
    for node in root:
        if node.tag is ET.Comment:
            path = (node.text or "").strip()
        elif schedule_parser.local_name(node.tag) == "Task" and path:
            tasks.append(ScheduledTask(path, node))
            path = None
    return tasks


def query_tasks(timeout: float) -> list[ScheduledTask] | None:
    """Tasks habilitadas fora de ``\\Microsoft\\``, ou ``None`` se a leitura falhou."""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/xml"],
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            **detect_git._popen_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        tasks = split_tasks(decode_output(result.stdout))
    except ET.ParseError:
        return None
    return [
        task
        for task in tasks
        if not task.path.lower().startswith("\\microsoft\\")
        and schedule_parser.task_enabled(task.element)
    ]


# ----------------------------------------------------------------------
# matching
# ----------------------------------------------------------------------


def _clean(path: str) -> str:
    return ntpath.expandvars(path.strip().strip('"'))


def _resolve(path: str, workdir: str) -> str:
    """Absoluto, normalizado e em caixa baixa — comparável com ``==``."""
    path = _clean(path)
    if workdir and not ntpath.isabs(path):
        path = ntpath.join(_clean(workdir), path)
    return ntpath.normcase(ntpath.normpath(path))


def _tokens(arguments: str) -> list[str]:
    try:
        return shlex.split(arguments, posix=False)
    except ValueError:  # aspas desbalanceadas
        return arguments.split()


def _read_small_file(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_BATCH_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > _BATCH_MAX_BYTES:
        return None
    return decode_output(raw)


def matches_entrypoint(actions: list[ExecAction], entrypoint: str, *, read_file=None) -> bool:
    """
    Alguma action ``Exec`` executa ``entrypoint``?

    * Script (ou ``.exe`` congelado) cujo caminho, resolvido contra "Iniciar
      em", é exatamente o entrypoint.
    * ``.bat``/``.cmd`` na pasta do entrypoint (ou com "Iniciar em" nela) que
      cita o nome do script. ``.bat`` ilegível casa só pela pasta.
    """
    read_file = read_file or _read_small_file
    target = ntpath.normcase(ntpath.normpath(entrypoint))
    target_dir = ntpath.dirname(target)
    target_name = ntpath.basename(target)

    for action in actions:
        workdir = action.working_directory
        command = _resolve(action.command, workdir)
        if command == target:
            return True
        for token in _tokens(action.arguments):
            if _clean(token).lower().endswith(_SCRIPT_SUFFIXES) and _resolve(token, workdir) == target:
                return True
        if command.endswith(_BATCH_SUFFIXES):
            folders = {ntpath.dirname(command)}
            if workdir:
                folders.add(_resolve(workdir, ""))
            if target_dir in folders:
                content = read_file(command)
                if content is None or target_name in content.lower():
                    return True
    return False


# ----------------------------------------------------------------------
# orquestração
# ----------------------------------------------------------------------


@safe(default=None)
def collect_schedules(
    *, timeout: float = 10.0, query=None, read_file=None, now=None
) -> list[dict] | None:
    """
    Linhas prontas para ``POST /logs/host-schedules``, ou ``None`` = nada a enviar.

    ``None`` também quando nada casou: enviar lista vazia apagaria no backend as
    agendas do serviço por causa de uma falha de leitura. ``query``/``read_file``/
    ``now`` são injetáveis para os testes.
    """
    if not win32.is_windows():
        return None
    if detect_execution.detect_execution().mode != detect_execution.TASK_SCHEDULER:
        return None
    entrypoint = detect_git.entrypoint_path()
    if not entrypoint:
        return None

    tasks = (query or query_tasks)(timeout)
    if tasks is None:
        _warn(
            "não foi possível ler as tasks do Agendador (schtasks ausente, falhou, "
            "estourou o timeout ou devolveu XML inválido); agendas não enviadas"
        )
        return None

    rows: list[dict] = []
    for task in tasks:
        if matches_entrypoint(schedule_parser.exec_actions(task.element), entrypoint, read_file=read_file):
            rows.extend(schedule_parser.parse_task(task.element, task.path, now=now))
    rows = schedule_parser.consolidate(rows)

    if len(rows) > MAX_ROWS:
        _warn(f"{len(rows)} agendas passam do teto de {MAX_ROWS}; nada foi enviado")
        return None
    return rows or None


__all__ = [
    "MAX_ROWS",
    "ScheduledTask",
    "collect_schedules",
    "decode_output",
    "matches_entrypoint",
    "query_tasks",
    "split_tasks",
]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_detect_schedule.py -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: PASS; ruff limpo.

- [ ] **Step 5: Commit**

```bash
git add src/jaylog/host/detect_schedule.py tests/test_detect_schedule.py
git commit -m "Read Task Scheduler tasks and match them to the entrypoint"
```

---

### Task 5: Envio — reporter reaproveitado, settings e ciclo de vida

**Files:**
- Modify: `src/jaylog/host/reporter.py`
- Create: `src/jaylog/host/schedule_reporter.py`
- Modify: `src/jaylog/settings.py` (bloco novo depois de "Métricas de recursos", ~l.160)
- Modify: `src/jaylog/logger.py` (imports; `configure()` ~l.72; novo `_start_schedule_reporter`; `shutdown()` ~l.345)
- Modify: `src/jaylog/_version.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_reporter.py`, `tests/test_schedule_reporter.py` (novo), `tests/test_settings.py`, `tests/test_logger_lifecycle.py`

**Interfaces:**
- Consumes: `detect_schedule.collect_schedules(*, timeout)` (Task 4); `identity.hostname()`; `RUN_ID`.
- Produces:
  ```python
  # reporter.py
  JaylogHostReporter(..., label: str = "host", noun: str = "ambiente", one_shot: bool = False)
  _unsupported_warned: set[str]
  # schedule_reporter.py
  def payload_factory(service: str, timeout: float) -> Callable[[], dict | None]
  def start(reporter: JaylogHostReporter) -> None
  def stop(timeout: float = 2.0) -> None
  def active() -> JaylogHostReporter | None
  # settings.py
  host_schedule_enabled: bool = True
  host_schedule_http_endpoint: str | None = None
  host_schedule_timeout: float = 10.0
  effective_host_schedule_endpoint -> str | None
  ```

- [ ] **Step 1: Testes que falham**

`tests/test_reporter.py` (acrescentar; `make_reporter` já existe):

```python
import threading

from jaylog.host import reporter as reporter_module


def test_payload_none_means_nothing_to_send() -> None:
    session = FakeSession([])
    rep = JaylogHostReporter("ORDERS", "https://x/logs/host-schedules", "key", session=session, backoff=(0,), payload_factory=lambda: None)

    rep.deliver()

    assert session.calls == 0
    assert rep.sent is False and rep.fatal is False and rep.unsupported is False


def test_one_shot_thread_ends_after_first_round() -> None:
    session = FakeSession([FakeResponse(200)])
    rep = JaylogHostReporter(
        "ORDERS", "https://x/logs/host-schedules", "key",
        session=session, backoff=(0,), payload_factory=lambda: {"a": 1},
        label="schedule", one_shot=True,
    )  # fmt: skip

    rep.start()
    rep._thread.join(2)

    assert rep._thread.name == "jaylog-schedule-ORDERS"
    assert not rep._thread.is_alive()
    assert rep.sent is True


def test_unsupported_warning_is_once_per_label(capsys) -> None:
    for label in ("host", "schedule", "schedule"):
        rep = JaylogHostReporter(
            "ORDERS", "https://x/y", "key",
            session=FakeSession([FakeResponse(404)]), backoff=(0,), payload_factory=lambda: {}, label=label,
        )  # fmt: skip
        rep.deliver()

    err = capsys.readouterr().err
    assert err.count("[jaylog] host:") == 1
    assert err.count("[jaylog] schedule:") == 1
    assert reporter_module._unsupported_warned == {"host", "schedule"}
```

`tests/test_schedule_reporter.py` (novo):

```python
from jaylog.host import detect_schedule, identity, schedule_reporter
from jaylog.runtime import RUN_ID


def test_payload_factory_builds_the_body(monkeypatch) -> None:
    rows = [{"repetition": "CONTINUO", "day_of_week": None, "day_of_month": None, "time": None, "task_path": "\\X"}]
    seen: list = []
    monkeypatch.setattr(detect_schedule, "collect_schedules", lambda *, timeout: seen.append(timeout) or rows)
    monkeypatch.setattr(identity, "hostname", lambda: "VM-RPA-01")

    body = schedule_reporter.payload_factory("ORDERS", 7.0)()

    assert body == {"run_id": RUN_ID, "service": "ORDERS", "hostname": "VM-RPA-01", "schedules": rows}
    assert seen == [7.0]


def test_payload_factory_is_none_without_rows(monkeypatch) -> None:
    monkeypatch.setattr(detect_schedule, "collect_schedules", lambda *, timeout: None)
    assert schedule_reporter.payload_factory("ORDERS", 1.0)() is None
```

`tests/test_settings.py` (acrescentar):

```python
def test_host_schedule_settings_defaults_and_derived_endpoint() -> None:
    settings = JaylogSettings(app_name="ORDERS", log_http_endpoint="https://api.example/logs/add")

    assert settings.host_schedule_enabled is True
    assert settings.host_schedule_timeout == 10.0
    assert settings.effective_host_schedule_endpoint == "https://api.example/logs/host-schedules"


def test_host_schedule_endpoint_override_and_missing_log_endpoint() -> None:
    override = JaylogSettings(
        app_name="ORDERS",
        log_http_endpoint="https://api.example/logs/add",
        host_schedule_http_endpoint="https://other/schedules",
    )
    assert override.effective_host_schedule_endpoint == "https://other/schedules"
    assert JaylogSettings(app_name="ORDERS", log_http_endpoint=None).effective_host_schedule_endpoint is None
```

`tests/test_logger_lifecycle.py` (acrescentar; `_http_settings` e `_no_threads` já existem):

```python
def _no_schedule_threads(monkeypatch, *, windows: bool = True) -> list:
    from jaylog.host import win32
    from jaylog.host.reporter import JaylogHostReporter

    started: list = []
    _no_threads(monkeypatch)
    monkeypatch.setattr(win32, "is_windows", lambda: windows)
    monkeypatch.setattr(JaylogHostReporter, "start", lambda self: started.append(self))
    return started


def test_configure_starts_one_schedule_reporter_on_windows(monkeypatch) -> None:
    from jaylog.host import schedule_reporter

    started = _no_schedule_threads(monkeypatch)

    configure([_http_settings("ORDERS", host_schedule_enabled=False), _http_settings("BILLING"), _http_settings("X")])

    assert len(started) == 1
    active = schedule_reporter.active()
    assert active is started[0]
    assert active.service == "BILLING"
    assert active.endpoint == "https://api.example/logs/host-schedules"

    shutdown()
    assert schedule_reporter.active() is None


def test_no_schedule_reporter_off_windows(monkeypatch) -> None:
    from jaylog.host import schedule_reporter

    started = _no_schedule_threads(monkeypatch, windows=False)
    configure([_http_settings("ORDERS")])

    assert started == []
    assert schedule_reporter.active() is None


def test_shutdown_of_bound_logger_stops_schedule_reporter(monkeypatch) -> None:
    from jaylog.host import schedule_reporter

    _no_schedule_threads(monkeypatch)
    configure([_http_settings("ORDERS"), _http_settings("BILLING")])

    shutdown("BILLING")
    assert schedule_reporter.active() is not None
    shutdown("ORDERS")
    assert schedule_reporter.active() is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_reporter.py tests/test_schedule_reporter.py tests/test_settings.py tests/test_logger_lifecycle.py -q`
Expected: FAIL — `label`/`one_shot` inexistentes, `schedule_reporter` inexistente, settings sem `host_schedule_*`.

- [ ] **Step 3: `reporter.py`**

```python
#: Aviso de "backend sem a rota" é um só por *tipo* de envio: o de ambiente e o
#: de agendas desligam de forma independente.
_unsupported_warned: set[str] = set()


def _warn(message: str, label: str = "host") -> None:
    print(f"[jaylog] {label}: {message}", file=sys.stderr)
```

Em `__init__`, novos kwargs `label: str = "host"`, `noun: str = "ambiente"`, `one_shot: bool = False`, guardados como `self.label`, `self.noun`, `self._one_shot`. Docstring da classe: acrescentar "Também entrega as agendas do Task Scheduler (``label="schedule"``, ``one_shot=True``): mesma máquina de estados, uma rodada só."

`start()`: `name=f"jaylog-{self.label}-{self.service}"`.

`_run()`:

```python
    def _run(self) -> None:
        while not self._stop.is_set():
            self.deliver()
            if self._stop.is_set() or self._one_shot:
                return
            # dorme até um request_resend() (ou até o stop). Sem polling.
            self._wakeup.wait()
            self._wakeup.clear()
```

`_post_once()` — remover `global _unsupported_warned`; logo após montar o payload:

```python
        if payload is None:
            # nada a enviar (ex.: nenhuma task casou) — não é falha, não avisa
            return None
```

e trocar os avisos:

```python
            _warn(f"falha ao montar o payload de {self.service}: {exc}", self.label)
        ...
            if self.label not in _unsupported_warned:
                _unsupported_warned.add(self.label)
                _warn(
                    f"o backend não suporta POST {self.endpoint} (HTTP {status}); "
                    f"o registro de {self.noun} foi desativado neste processo",
                    self.label,
                )
        ...
            _warn(f"autenticação recusada em {self.endpoint} (HTTP {status})", self.label)
        ...
            _warn(
                f"payload de {self.noun} rejeitado para '{self.service}' (HTTP 422): "
                f"{_body_excerpt(response)}",
                self.label,
            )
        ...
        _warn(f"POST {self.endpoint} devolveu HTTP {status}; desistindo", self.label)
```

`tests/conftest.py`: trocar as duas linhas `reporter._unsupported_warned = False` por `reporter._unsupported_warned.clear()`.

- [ ] **Step 4: `schedule_reporter.py`**

```python
"""
Envio único, por processo, das agendas do Task Scheduler para
``POST /logs/host-schedules``.

Reaproveita o ``JaylogHostReporter`` (retry, 404/405, 401/403/422) com
``one_shot=True``: as agendas não mudam durante a execução, então não há
reenvio. Fica **fora** do registry de host de propósito — um
``x-jaylog-host-required`` pede o registro de ambiente, não as agendas.

Um por processo, como as métricas: as tasks são do processo, não de cada
``app_name``.
"""

import threading

from jaylog.host import detect_schedule, identity
from jaylog.host.reporter import JaylogHostReporter
from jaylog.runtime import RUN_ID

_STOP_JOIN_TIMEOUT = 2.0

_active: JaylogHostReporter | None = None
_lock = threading.Lock()


def payload_factory(service: str, timeout: float):
    """Coleta dentro da thread do reporter: ``configure()`` não espera o ``schtasks``."""

    def factory() -> dict | None:
        rows = detect_schedule.collect_schedules(timeout=timeout)
        if not rows:
            return None
        return {"run_id": RUN_ID, "service": service, "hostname": identity.hostname(), "schedules": rows}

    return factory


def start(reporter: JaylogHostReporter) -> None:
    global _active
    with _lock:
        previous, _active = _active, reporter
    if previous is not None and previous is not reporter:
        previous.stop()
    reporter.start()


def stop(timeout: float = _STOP_JOIN_TIMEOUT) -> None:
    global _active
    with _lock:
        current, _active = _active, None
    if current is not None:
        current.stop(timeout)


def active() -> JaylogHostReporter | None:
    with _lock:
        return _active


__all__ = ["active", "payload_factory", "start", "stop"]
```

- [ ] **Step 5: `settings.py`** (depois do validator de `host_metrics_interval`)

```python
    # ------------------------------------------------------------------
    # Agendas do Task Scheduler — enviadas uma vez por execução, só quando o
    # processo foi iniciado pelo Agendador do Windows
    # ------------------------------------------------------------------

    host_schedule_enabled: bool = True

    # Derivado de `log_http_endpoint` (`/logs/add` -> `/logs/host-schedules`) se
    # não definido.
    host_schedule_http_endpoint: str | None = None

    # Segundos para o `schtasks /query /xml`. Máquina com centenas de tasks leva
    # 1-3 s; o teto evita uma thread presa se o serviço do Agendador travar.
    host_schedule_timeout: float = 10.0

    @property
    def effective_host_schedule_endpoint(self) -> str | None:
        """URL do `POST /logs/host-schedules`: o override, ou a derivada do endpoint de log."""
        if self.host_schedule_http_endpoint:
            return self.host_schedule_http_endpoint
        if not self.log_http_endpoint:
            return None
        from jaylog.endpoints import derive_endpoint

        return derive_endpoint(self.log_http_endpoint, "host-schedules")
```

Atualizar a docstring de `endpoints.py` para citar também `/logs/host-schedules` e `JAYLOG_HOST_SCHEDULE_HTTP_ENDPOINT`.

- [ ] **Step 6: `logger.py`**

Imports: `from jaylog.host import metrics_reporter, reporter, schedule_reporter, win32`.

Em `configure()`, depois de `_start_metrics_reporter(items)`: `_start_schedule_reporter(items)`.

Nova função, depois de `_start_metrics_reporter`:

```python
def _start_schedule_reporter(items: list[JaylogSettings]) -> None:
    """
    Um envio de agendas por processo, vinculado ao primeiro item elegível.

    Só no Windows: fora dele não há Agendador, e nem vale criar a thread. Se o
    processo não veio do Agendador, a coleta devolve ``None`` e nada é enviado.
    """
    if not win32.is_windows():
        return
    for item in items:
        if not item.host_schedule_enabled:
            continue
        endpoint = item.effective_host_schedule_endpoint
        if not endpoint or not item.log_http_api_key:
            continue
        schedule_reporter.start(
            JaylogHostReporter(
                service=item.app_name,
                endpoint=endpoint,
                api_key=item.log_http_api_key,
                timeout=item.effective_host_timeout,
                proxy=item.log_http_proxy,
                verify=item.log_http_verify,
                payload_factory=schedule_reporter.payload_factory(
                    item.app_name, item.host_schedule_timeout
                ),
                label="schedule",
                noun="agendas",
                one_shot=True,
            )
        )
        return
```

Em `shutdown()`:

```python
    if name is None:
        reporter.stop_all()
        metrics_reporter.stop()
        schedule_reporter.stop()
    else:
        reporter.stop(name)
        active = metrics_reporter.active()
        if active is not None and active.service == name:
            metrics_reporter.stop()
        scheduled = schedule_reporter.active()
        if scheduled is not None and scheduled.service == name:
            schedule_reporter.stop()
```

- [ ] **Step 7: `_version.py`**

```python
#: 3: limites da máquina no payload de host + ``POST /logs/host-metrics``.
#: 4: ``POST /logs/host-schedules`` (agendas do Task Scheduler do Windows).
PROTOCOL_VERSION = 4
```

- [ ] **Step 8: Rodar e ver passar**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: suíte inteira PASS; ruff limpo. Se algum teste existente comparar `PROTOCOL_VERSION == 3` (ex.: `test_host_payload.py`), atualizar para 4.

- [ ] **Step 9: Commit**

```bash
git add src/jaylog/host/reporter.py src/jaylog/host/schedule_reporter.py src/jaylog/settings.py src/jaylog/endpoints.py src/jaylog/logger.py src/jaylog/_version.py tests/
git commit -m "Send Task Scheduler schedules once per process on Windows"
```

---

### Task 6: Documentação e versão

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml` (`version = "0.3.0a5"`), `uv.lock` (via `uv lock`)
- Modify (repo `../jaylog-book`): `docs/producao/agendas-do-task-scheduler.md` (novo), `zensical.toml` (nav, depois de "Métricas de Recursos"), `docs/configuracao/variaveis-de-ambiente.md`

- [ ] **Step 1: README** — depois do parágrafo de métricas, um parágrafo: "Quando o bot é iniciado pelo Agendador de Tarefas do Windows, o jaylog lê as tasks ativas que executam o entrypoint (script com "Iniciar em" ou `.bat` na pasta do projeto) e envia as agendas — Repetição, Granularidade e Horário — uma vez por execução para `POST /logs/host-schedules`. O backend substitui as agendas sincronizadas anteriores do serviço. Triggers que o modelo não representa sem perda são ignorados; veja a documentação."

- [ ] **Step 2: jaylog-book** — página `docs/producao/agendas-do-task-scheduler.md` com: quando roda (Windows + modo `task_scheduler`); como a task é associada ao bot (formatos B e D, `.bat` precisa citar o script); tabela da §4.2 do spec (cenários mapeados); lista da §4.5 (ignorados); teto de 24 horários/dia → Contínuo; Boot/Logon → Contínuo; o que acontece em falha (nada é enviado, aviso `[jaylog] schedule:`); variáveis `JAYLOG_HOST_SCHEDULE_ENABLED`, `JAYLOG_HOST_SCHEDULE_HTTP_ENDPOINT`, `JAYLOG_HOST_SCHEDULE_TIMEOUT`. Acrescentar as três variáveis em `variaveis-de-ambiente.md` no mesmo formato das `JAYLOG_HOST_METRICS_*`, e a entrada de nav `{ "Agendas do Task Scheduler" = "producao/agendas-do-task-scheduler.md" }`.

- [ ] **Step 3: Versão**

Run: `sed -i 's/^version = "0.3.0a4"/version = "0.3.0a5"/' pyproject.toml && uv lock && uv run pytest -q`
Expected: `uv.lock` atualiza só a versão do próprio pacote; testes PASS.

- [ ] **Step 4: Commit** (dois repos)

```bash
git add README.md pyproject.toml uv.lock
git commit -m "Document Task Scheduler schedules and bump to 0.3.0a5"
cd ../jaylog-book && git switch -c feat/task-scheduler-schedules && git add docs zensical.toml && git commit -m "Document Task Scheduler schedules"
```

---

### Task 7: Validação em Windows real (manual — exige VM Windows pt-BR)

Não é automatizável neste ambiente. Pendência do spec §3.2 (encoding) e §7 (fixtures reais).

- [ ] **Step 1: Preparar** — numa VM Windows com o jaylog instalado do branch (`uv pip install -e .`) e backend de dev com o plano do backend aplicado, criar em `C:\Automações\fat\` um `main.py` que chama `configure()` + um log, um `run.bat` (`call .venv\Scripts\activate` + `python main.py`), e três tasks em `\RPA\`: (a) `.bat`, semanal seg/qua 07:30; (b) `python.exe main.py` com "Iniciar em" `C:\Automações\fat`, diário 08:00 repetindo a cada 30 min por 10 h; (c) mensal "último dia" (deve ser ignorada).

- [ ] **Step 2: Executar** — `schtasks /run /tn "\RPA\<a>"` e `"\RPA\<b>"`. Esperado: `service_schedules` do serviço com `source='TASK_SCHEDULER'` = 2 linhas SEMANA + 20 DIARIO (união das duas tasks), `task_path`/`hostname` preenchidos; nenhum aviso `[jaylog] schedule:` no log de saída; rodar de novo não duplica.

- [ ] **Step 3: Fixture real** — `schtasks /query /xml > tests\fixtures\schtasks\ptbr_full.bin` (bytes crus, sem conversão) e acrescentar em `tests/test_detect_schedule.py`:

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "schtasks"


def test_real_ptbr_output_decodes_and_splits(monkeypatch) -> None:
    monkeypatch.setattr(win32, "oem_codepage", lambda: 850)
    text = detect_schedule.decode_output((FIXTURES / "ptbr_full.bin").read_bytes())
    paths = [t.path for t in detect_schedule.split_tasks(text)]
    assert any(p.startswith("\\RPA\\") for p in paths)
    assert "Automações" in text
```

Run: `uv run pytest tests/test_detect_schedule.py -q` → PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/schtasks tests/test_detect_schedule.py
git commit -m "Add real pt-BR schtasks output fixture"
```
