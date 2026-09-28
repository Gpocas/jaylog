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

from jaylog.diagnostics import emit as debug
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
    commit_msg: str | None = None
    commit_datetime: str | None = None
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


def entrypoint_path() -> str | None:
    """Arquivo que iniciou o processo: ``__main__``, ``argv[0]`` ou .exe frozen."""
    try:
        if getattr(sys, "frozen", False):
            path = os.path.abspath(sys.executable)
            debug("entrypoint", f"executável congelado identificado; caminho={path}")
            return path
        main = sys.modules.get("__main__")
        main_file = getattr(main, "__file__", None)
        if main_file:
            path = os.path.abspath(main_file)
            debug("entrypoint", f"arquivo principal identificado por __main__; caminho={path}")
            return path
        # Em launchers que executam o script via ``runpy`` ou substituem o
        # módulo ``__main__``, ``__file__`` pode não sobreviver. ``argv[0]``
        # preserva o arquivo original nesse caso. ``-c`` e ``-`` não são
        # arquivos e não podem ser associados a uma task com segurança.
        argv = getattr(sys, "argv", ())
        argv0 = argv[0] if argv else None
        if argv0 and argv0 not in ("-c", "-"):
            path = os.path.abspath(argv0)
            debug("entrypoint", f"arquivo principal identificado por argv[0]; caminho={path}")
            return path
    except Exception as exc:
        debug(
            "entrypoint",
            f"identificação falhou; tipo={type(exc).__name__}; detalhe={exc}",
        )
        return None
    debug("entrypoint", "arquivo principal não identificado")
    return None


def entrypoint_dir() -> str | None:
    """
    Diretório do script (ou do ``.exe``) que iniciou o processo.

    É o ponto de partida da busca pelo repositório e também o campo
    ``entrypoint`` do payload.
    """
    path = entrypoint_path()
    return os.path.dirname(path) if path else None


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
        debug(
            "git",
            f"subprocesso iniciado; comando=git {' '.join(args)}; diretório={cwd}; "
            f"timeout={timeout:g}s",
        )
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=_git_env(),
            **_popen_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        debug(
            "git",
            f"subprocesso falhou; comando=git {' '.join(args)}; "
            f"tipo={type(exc).__name__}; detalhe={exc}",
        )
        # git ausente do PATH, cwd inexistente, timeout
        return None
    if result.returncode != 0:
        debug(
            "git",
            f"subprocesso rejeitado; comando=git {' '.join(args)}; "
            f"código_de_saída={result.returncode}",
        )
        return None
    debug("git", f"subprocesso concluído; comando=git {' '.join(args)}; código_de_saída=0")
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
        debug("git", "detecção não iniciada; motivo=funcionalidade desativada")
        return GIT_UNKNOWN

    cwd = git_search_dir(git_dir)
    debug("git", f"detecção iniciada; diretório_de_busca={cwd}")
    if not os.path.isdir(cwd):
        cwd = os.getcwd()

    version_out = _run_git(["--version"], cwd, timeout)
    if version_out is None:
        debug("git", "detecção concluída; git_disponível=False")
        return GitInfo(available=False)

    version = version_out.removeprefix("git version ").strip() or None

    root = _run_git(["rev-parse", "--show-toplevel"], cwd, timeout)
    if root is None:
        debug("git", "detecção concluída; git_disponível=True; repositório=False")
        return GitInfo(available=True, version=version, repo=False)

    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, timeout)
    if branch == "HEAD":  # detached
        branch = None

    commit = _run_git(["rev-parse", "HEAD"], cwd, timeout)
    commit_short = commit[:7] if commit else None
    commit_msg = _run_git(["log", "-1", "--format=%s"], cwd, timeout)
    commit_datetime = _run_git(["log", "-1", "--format=%cI"], cwd, timeout)

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

    info = GitInfo(
        available=True,
        version=version,
        repo=True,
        root=os.path.normpath(root),
        branch=branch,
        commit=commit,
        commit_short=commit_short,
        commit_msg=commit_msg,
        commit_datetime=commit_datetime,
        dirty=dirty,
        remote_url=remote_url,
    )
    debug(
        "git",
        f"detecção concluída; git_disponível=True; repositório=True; "
        f"branch={branch or '(detached)'}; commit={commit_short or '(indisponível)'}; "
        f"dirty={dirty}",
    )
    return info


__all__ = [
    "GitInfo",
    "GIT_UNKNOWN",
    "detect_git",
    "redact_remote_url",
    "entrypoint_path",
    "entrypoint_dir",
    "git_search_dir",
]
