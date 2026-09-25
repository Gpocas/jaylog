"""Leitura das tasks do Windows e associação segura ao entrypoint atual."""

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


def decode_output(raw: bytes) -> str:
    """Decodifica saída do ``schtasks`` na OEM local, UTF-8 ou UTF-16."""
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
    """Associa cada ``Task`` ao comentário de caminho que a precede."""
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
    """Tasks ativas fora de ``\\Microsoft\\``, ou ``None`` quando a consulta falha."""
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
    except (ET.ParseError, UnicodeError):
        return None
    return [
        task
        for task in tasks
        if not task.path.lower().startswith("\\microsoft\\")
        and schedule_parser.task_enabled(task.element)
    ]


def _clean(path: str) -> str:
    return ntpath.expandvars(path.strip().strip('"'))


def _resolve(path: str, workdir: str) -> str:
    path = _clean(path)
    if workdir and not ntpath.isabs(path):
        path = ntpath.join(_clean(workdir), path)
    return ntpath.normcase(ntpath.normpath(path))


def _tokens(arguments: str) -> list[str]:
    try:
        return shlex.split(arguments, posix=False)
    except ValueError:
        return arguments.split()


def _read_small_file(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_BATCH_MAX_BYTES + 1)
    except OSError:
        return None
    return decode_output(raw) if len(raw) <= _BATCH_MAX_BYTES else None


def matches_entrypoint(actions: list[ExecAction], entrypoint: str, *, read_file=None) -> bool:
    """Retorna se qualquer ``Exec`` executa o script ou executável atual."""
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
            if (
                _clean(token).lower().endswith(_SCRIPT_SUFFIXES)
                and _resolve(token, workdir) == target
            ):
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


@safe(default=None)
def collect_schedules(
    *, timeout: float = 10.0, query=None, read_file=None, now=None
) -> list[dict] | None:
    """Coleta agendas prontas para o POST, sem nunca afetar o processo observado."""
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
        if matches_entrypoint(
            schedule_parser.exec_actions(task.element), entrypoint, read_file=read_file
        ):
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
