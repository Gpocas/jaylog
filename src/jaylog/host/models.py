"""
``HostInfo``: o retrato do ambiente desta execução.

Plano de propósito — cada campo vira uma coluna de ``log_hosts``, então o
``model_dump(mode="json")`` é literalmente o corpo do POST.

Todo campo de métrica é ``None``-ável e é **enviado como ``null``**, nunca
omitido: o backend precisa distinguir "o cliente não soube responder"
(``is_windows_service`` fora do Windows, git fora de repositório) de "campo
ausente porque o cliente é antigo".
"""


from pydantic import BaseModel

from jaylog._version import PROTOCOL_VERSION, __version__
from jaylog.runtime import RUN_ID, STARTED_AT


class HostInfo(BaseModel):
    # --- identidade da execução (nunca nulos) ---
    run_id: str = RUN_ID
    service: str | None = None
    protocol_version: int = PROTOCOL_VERSION
    jaylog_version: str = __version__
    started_at: str = STARTED_AT
    collected_at: str | None = None

    # --- identidade da máquina ---
    hostname: str | None = None
    username: str | None = None
    ipv4: str | None = None

    # --- sistema operacional ---
    os_system: str | None = None
    os_release: str | None = None
    os_version: str | None = None
    machine: str | None = None

    # --- como o processo foi iniciado ---
    execution_mode: str | None = None
    execution_detail: str | None = None
    session_id: int | None = None
    process_id: int | None = None
    parent_process_name: str | None = None

    # --- interpretador ---
    python_version: str | None = None
    python_implementation: str | None = None
    python_executable: str | None = None
    python_frozen: bool | None = None

    # --- ambiente virtual ---
    venv_active: bool | None = None
    venv_kind: str | None = None
    venv_path: str | None = None

    # --- git ---
    git_available: bool | None = None
    git_version: str | None = None
    git_repo: bool | None = None
    git_root: str | None = None
    git_branch: str | None = None
    git_commit: str | None = None
    git_commit_short: str | None = None
    git_commit_msg: str | None = None
    git_dirty: bool | None = None
    git_remote_url: str | None = None

    # --- localização ---
    cwd: str | None = None
    entrypoint: str | None = None

    @classmethod
    def minimal(cls) -> "HostInfo":
        """
        O mínimo publicável quando a coleta falha por inteiro.

        Garante que o dashboard ainda receba uma linha com ``run_id`` e a
        identidade da máquina — sem ela, os logs daquela execução ficariam com
        ``host_id`` NULL para sempre e ninguém saberia nem que o processo subiu.
        """
        from jaylog.host import identity

        return cls(
            hostname=identity.hostname(),
            username=identity.username(),
            ipv4=identity.ipv4(),
        )


__all__ = ["HostInfo"]
