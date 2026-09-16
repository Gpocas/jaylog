"""
Como este processo foi iniciado: serviço do Windows, tarefa agendada, systemd,
cron, container, shell interativo — ou ``unknown``.

**Enum, não dois booleanos.** ``is_windows_service=False, is_task=False`` não
distingue *"verificamos e é interativo"* de *"não conseguimos determinar"*.
Numa frota de RPAs essa é a diferença entre um bot que deixou de ser tarefa
agendada e um bot cuja detecção foi bloqueada pelo antivírus — os dois são
incidentes, mas de tipos opostos. O enum também absorve ``systemd``/``cron``/
``container`` sem coluna nova no backend.

``execution_detail`` carrega a cadeia crua de processos. Sem ela, uma
classificação errada (ver o caso ``svchost.exe`` abaixo) é indiagnosticável.
"""

import os
import sys
from dataclasses import dataclass

from jaylog.host import win32
from jaylog.host._safe import safe

INTERACTIVE = "interactive"
WINDOWS_SERVICE = "windows_service"
TASK_SCHEDULER = "task_scheduler"
SYSTEMD = "systemd"
CRON = "cron"
CONTAINER = "container"
UNKNOWN = "unknown"

EXECUTION_MODES = (
    INTERACTIVE,
    WINDOWS_SERVICE,
    TASK_SCHEDULER,
    SYSTEMD,
    CRON,
    CONTAINER,
    UNKNOWN,
)

_DETAIL_MAX = 500
_CRON_COMMS = {"cron", "crond"}
_WINDOWS_SCHEDULER_ANCESTORS = {"svchost.exe", "taskeng.exe", "taskhostw.exe"}


@dataclass(frozen=True)
class ExecutionInfo:
    mode: str
    detail: str | None
    session_id: int | None
    process_id: int
    parent_process_name: str | None


def _join(chain: list[str]) -> str:
    return " < ".join(chain)[:_DETAIL_MAX]


def _self_name() -> str:
    try:
        return os.path.basename(sys.executable) or "python"
    except Exception:
        return "python"


def _detect_windows(pid: int) -> ExecutionInfo:
    table = win32.process_table()
    sid = win32.session_id()
    chain = win32.ancestry(pid, table)

    own = table.get(pid, (0, ""))[1] or _self_name()
    parent = chain[0] if chain else None
    lowered = [name.lower() for name in chain]

    if not chain:
        # Sem cadeia legível não dá para afirmar nada. Chamar isto de
        # "interactive" apagaria exatamente o caso que motivou o enum: o
        # Toolhelp bloqueado por antivírus ficaria idêntico a um shell.
        reason = (
            "sessão 0 e cadeia de processos ilegível"
            if sid == 0
            else "cadeia de processos indisponível"
        )
        return ExecutionInfo(UNKNOWN, f"{own} ({reason})"[:_DETAIL_MAX], sid, pid, None)

    detail = _join([own, *chain])

    if any(name in _WINDOWS_SCHEDULER_ANCESTORS for name in lowered):
        # Win7 / 2008R2 usa taskeng.exe; versões mais novas podem usar
        # taskhostw.exe ou iniciar o executável a partir do svchost que hospeda
        # o serviço Schedule. Esses marcadores podem estar separados do Jaylog
        # por cmd.exe, python.exe ou outro launcher, portanto toda a cadeia deve
        # ser inspecionada. A heurística de svchost pode incluir processos
        # ativados por WMI/COM; `detail` preserva a evidência para diagnóstico.
        mode = TASK_SCHEDULER
    elif "services.exe" in lowered:
        # O Service Control Manager pode iniciar diretamente o programa ou um
        # wrapper como NSSM, que por sua vez cria cmd/python e outros filhos.
        # Logo, services.exe não precisa ser o pai imediato do processo atual.
        mode = WINDOWS_SERVICE
    else:
        mode = INTERACTIVE

    return ExecutionInfo(mode, detail, sid, pid, parent)


def _read_comm(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def _unix_ancestry(pid: int) -> list[str]:
    """Nomes dos ancestrais via ``/proc``, do pai para cima. Vazio fora do Linux."""
    chain: list[str] = []
    seen = {pid}
    current = pid
    for _ in range(win32.MAX_ANCESTRY_DEPTH):
        try:
            with open(f"/proc/{current}/stat", encoding="utf-8", errors="replace") as handle:
                stat = handle.read()
        except OSError:
            break
        # o comm entre parênteses pode conter espaços e ')': cortar pelo último
        close = stat.rfind(")")
        if close == -1:
            break
        fields = stat[close + 2 :].split()
        if len(fields) < 2:
            break
        ppid = int(fields[1])
        if ppid <= 0 or ppid in seen:
            break
        name = _read_comm(ppid)
        if name is None:
            break
        seen.add(ppid)
        chain.append(name)
        current = ppid
    return chain


def _stdin_is_tty() -> bool:
    for stream in (sys.stdin, sys.stdout):
        try:
            if stream is not None and stream.isatty():
                return True
        except (AttributeError, ValueError, OSError):
            continue
    return False


def _detect_unix(pid: int) -> ExecutionInfo:
    ppid = os.getppid()
    chain = _unix_ancestry(pid)
    parent = chain[0] if chain else _read_comm(ppid)
    detail = _join([_self_name(), *chain]) if chain else None

    if os.environ.get("INVOCATION_ID") and os.path.isdir("/run/systemd/system"):
        mode = SYSTEMD
    elif os.path.exists("/.dockerenv"):
        mode = CONTAINER
    elif parent is not None and parent.lower() in _CRON_COMMS:
        mode = CRON
    elif _stdin_is_tty():
        mode = INTERACTIVE
    else:
        mode = UNKNOWN

    return ExecutionInfo(mode, detail, None, pid, parent)


@safe(default=None)
def _detect() -> ExecutionInfo | None:
    pid = os.getpid()
    if win32.is_windows():
        return _detect_windows(pid)
    return _detect_unix(pid)


def detect_execution() -> ExecutionInfo:
    """Nunca levanta: no pior caso devolve ``unknown`` com o PID."""
    info = _detect()
    if info is None:
        return ExecutionInfo(UNKNOWN, None, None, os.getpid(), None)
    return info


__all__ = [
    "ExecutionInfo",
    "detect_execution",
    "EXECUTION_MODES",
    "INTERACTIVE",
    "WINDOWS_SERVICE",
    "TASK_SCHEDULER",
    "SYSTEMD",
    "CRON",
    "CONTAINER",
    "UNKNOWN",
]
