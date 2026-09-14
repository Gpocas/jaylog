"""
Em que commit/branch o código deste processo está.

Três pontos que não são óbvios:

* **De onde buscar o repositório.** ``os.getcwd()`` está errado exatamente para
  a população que interessa: uma tarefa do Agendador sem "Iniciar em" roda com
  ``cwd = C:\\Windows\\system32``. A busca parte do diretório do *entrypoint*
  (``__main__.__file__``, ou ``sys.executable`` se congelado).

* **Redação de credencial é obrigatória.** Bots clonados com PAT embutido na URL
  (``https://user:ghp_xxx@github.com/...``) vazariam um token vivo para o
  Postgres e para o dashboard. É a linha de maior severidade desta feature.

* **O ambiente do subprocesso.** ``GIT_TERMINAL_PROMPT=0`` e
  ``GCM_INTERACTIVE=never`` garantem que nenhuma chamada trave pedindo senha nem
  abra janela do Credential Manager. ``GIT_OPTIONAL_LOCKS=0`` impede o
  ``git status`` de reescrever ``.git/index`` — num repositório hospedado em
  share de rede isso transforma 50 ms em segundos e ainda disputa lock com um
  ``git pull`` concorrente. No Windows, ``CREATE_NO_WINDOW`` evita que cada
  chamada pisque um console preto sob ``pythonw`` ou sob um serviço.
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from jaylog.host._safe import safe

_CREATE_NO_WINDOW = 0x08000000

#: ``//qualquer-coisa@`` entre o esquema e o host é credencial. Cobre
#: ``//user:token@``, ``//token@`` e ``//user@``.
_CREDENTIAL_RE = re.compile(r"//[^/@\s]*@")


@dataclass(frozen=True)
class GitInfo:
    available: bool | None = None
    version: str | None = None
    repo: bool | None = None
    root: str | None = None
    branch: str | None = None
    commit: str | None = None
    commit_short: str | None = None
    dirty: bool | None = None
    remote_url: str | None = None


#: Nada foi apurado — diferente de ``available=False`` ("apuramos: não tem git").
GIT_UNKNOWN = GitInfo()


def redact_remote_url(url: str) -> str:
    """
    Remove credencial embutida da URL do remote.

    ``https://user:ghp_abc@github.com/o/r.git`` -> ``https://github.com/o/r.git``

    Também remove o ``git@`` inofensivo de URLs ``ssh://`` — perder o nome de
    usuário é barato perto de vazar um token.
    """
    return _CREDENTIAL_RE.sub("//", url)


def entrypoint_dir() -> str | None:
    """
    Diretório do script (ou do ``.exe``) que iniciou o processo.

    É o ponto de partida da busca pelo repositório e também o campo
    ``entrypoint`` do payload.
    """
    try:
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.executable))
        main = sys.modules.get("__main__")
        main_file = getattr(main, "__file__", None)
        if main_file:
            return os.path.dirname(os.path.abspath(main_file))
    except Exception:
        return None
    return None


def git_search_dir(override: Path | str | None = None) -> str:
    if override:
        return str(override)
    return entrypoint_dir() or os.getcwd()


def _git_env() -> dict:
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
    )
    return env


def _popen_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def _run_git(args: list[str], cwd: str, timeout: float) -> str | None:
    """stdout do comando, ou ``None`` se o git faltou, falhou ou estourou o timeout."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=_git_env(),
            **_popen_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        # git ausente do PATH, cwd inexistente, timeout
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


@safe(default=GIT_UNKNOWN)
def detect_git(
    *,
    enabled: bool = True,
    git_dir: Path | str | None = None,
    timeout: float = 3.0,
    dirty_enabled: bool = True,
    remote_enabled: bool = True,
) -> GitInfo:
    """
    Degradação, em camadas:

    * binário ausente -> ``available=False`` e o resto ``None``;
    * fora de repositório -> ``available=True, repo=False``;
    * *detached HEAD* -> ``branch=None`` com ``commit`` preenchido. É o caso de
      deploy por tag, e não pode ser lido como "sem git".
    """
    if not enabled:
        return GIT_UNKNOWN

    cwd = git_search_dir(git_dir)
    if not os.path.isdir(cwd):
        cwd = os.getcwd()

    version_out = _run_git(["--version"], cwd, timeout)
    if version_out is None:
        return GitInfo(available=False)

    version = version_out.removeprefix("git version ").strip() or None

    root = _run_git(["rev-parse", "--show-toplevel"], cwd, timeout)
    if root is None:
        return GitInfo(available=True, version=version, repo=False)

    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, timeout)
    if branch == "HEAD":  # detached
        branch = None

    commit = _run_git(["rev-parse", "HEAD"], cwd, timeout)
    commit_short = commit[:7] if commit else None

    dirty = None
    if dirty_enabled:
        status = _run_git(["status", "--porcelain"], cwd, timeout)
        if status is not None:
            dirty = bool(status)

    remote_url = None
    if remote_enabled:
        raw_remote = _run_git(["remote", "get-url", "origin"], cwd, timeout)
        if raw_remote:
            remote_url = redact_remote_url(raw_remote)

    return GitInfo(
        available=True,
        version=version,
        repo=True,
        root=os.path.normpath(root),
        branch=branch,
        commit=commit,
        commit_short=commit_short,
        dirty=dirty,
        remote_url=remote_url,
    )


__all__ = [
    "GitInfo",
    "GIT_UNKNOWN",
    "detect_git",
    "redact_remote_url",
    "entrypoint_dir",
    "git_search_dir",
]
