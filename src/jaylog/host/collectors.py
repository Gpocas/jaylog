"""
``collect_host_info()``: junta todos os detectores num ``HostInfo``.

Memoizado por processo e thread-safe. O custo da coleta (~100-500 ms: um
snapshot Toolhelp mais até seis subprocessos ``git``) é pago **uma vez**, na
thread de startup — nunca na thread do chamador de ``configure()``.

Os parâmetros de git vêm da primeira chamada: dois ``app_name`` no mesmo
processo compartilham o mesmo ambiente, então coletar duas vezes só gastaria
tempo para produzir o mesmo retrato.
"""

import os
import platform
import threading
from datetime import datetime, timezone
from pathlib import Path

from jaylog.host import detect_execution, detect_git, detect_python, detect_venv, identity
from jaylog.host._safe import safe
from jaylog.host.models import HostInfo

_lock = threading.Lock()
_cached: HostInfo | None = None


@safe(default=(None, None, None, None))
def _os_info() -> tuple[str | None, str | None, str | None, str | None]:
    uname = platform.uname()
    return (
        uname.system or None,
        uname.release or None,
        uname.version or None,
        uname.machine or None,
    )


@safe(default=None)
def _cwd() -> str | None:
    return os.getcwd()


@safe(default=None)
def _collect(
    git_enabled: bool,
    git_dir: Path | str | None,
    git_timeout: float,
    git_dirty_enabled: bool,
    git_remote_enabled: bool,
) -> HostInfo:
    os_system, os_release, os_version, machine = _os_info()
    execution = detect_execution.detect_execution()
    python = detect_python.detect_python()
    venv = detect_venv.detect_venv()
    git = detect_git.detect_git(
        enabled=git_enabled,
        git_dir=git_dir,
        timeout=git_timeout,
        dirty_enabled=git_dirty_enabled,
        remote_enabled=git_remote_enabled,
    )

    return HostInfo(
        collected_at=datetime.now(timezone.utc).isoformat(),
        hostname=identity.hostname(),
        username=identity.username(),
        ipv4=identity.ipv4(),
        os_system=os_system,
        os_release=os_release,
        os_version=os_version,
        machine=machine,
        execution_mode=execution.mode,
        execution_detail=execution.detail,
        session_id=execution.session_id,
        process_id=execution.process_id,
        parent_process_name=execution.parent_process_name,
        python_version=python.version,
        python_implementation=python.implementation,
        python_executable=python.executable,
        python_frozen=python.frozen,
        venv_active=venv.active,
        venv_kind=venv.kind,
        venv_path=venv.path,
        git_available=git.available,
        git_version=git.version,
        git_repo=git.repo,
        git_root=git.root,
        git_branch=git.branch,
        git_commit=git.commit,
        git_commit_short=git.commit_short,
        git_dirty=git.dirty,
        git_remote_url=git.remote_url,
        cwd=_cwd(),
        entrypoint=detect_git.entrypoint_dir(),
    )


def collect_host_info(
    *,
    git_enabled: bool = True,
    git_dir: Path | str | None = None,
    git_timeout: float = 3.0,
    git_dirty_enabled: bool = True,
    git_remote_enabled: bool = True,
) -> HostInfo:
    """
    Retrato do ambiente, coletado uma vez por processo.

    Nunca levanta: se o ``@safe`` de backstop devolver ``None``, cai para
    ``HostInfo.minimal()``, que ainda carrega ``run_id`` e a identidade da
    máquina.
    """
    global _cached
    with _lock:
        if _cached is None:
            _cached = (
                _collect(git_enabled, git_dir, git_timeout, git_dirty_enabled, git_remote_enabled)
                or HostInfo.minimal()
            )
        return _cached


def reset_cache() -> None:
    """Limpa a memoização. Existe para os testes; não use em produção."""
    global _cached
    with _lock:
        _cached = None


__all__ = ["collect_host_info", "reset_cache"]
