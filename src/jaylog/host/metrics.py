"""
Leitura de uso de recursos: limites da máquina e amostras periódicas.

Sem thread e sem rede — isso é do ``metrics_reporter``. Aqui só se lê o psutil
e se devolve o dicionário de wire do ``POST /logs/host-metrics``.

``psutil`` é importado **dentro** das funções, pelo mesmo motivo do ``ctypes``
em ``win32.py``: este módulo entra no caminho de ``collectors`` (e portanto de
``import jaylog``), e um psutil quebrado não pode custar o import da lib.

Toda leitura passa por ``@safe``: um campo que falha vira ``None`` e o resto da
amostra segue.
"""

import os
import time
from datetime import datetime, timezone
from typing import NamedTuple

from jaylog.host import detect_git
from jaylog.host._safe import safe

#: Campos de uma amostra, na ordem do contrato (``sampled_at`` à parte).
SAMPLE_FIELDS = (
    "host_cpu_pct",
    "host_mem_used_bytes",
    "host_mem_pct",
    "host_disk_used_bytes",
    "host_disk_pct",
    "proc_cpu_pct",
    "proc_mem_bytes",
    "proc_mem_pct",
    "proc_io_read_bytes",
    "proc_io_write_bytes",
)


class Limits(NamedTuple):
    cpu_count: int | None
    memory_total_bytes: int | None
    disk_total_bytes: int | None
    disk_path: str | None


class _ProcReading(NamedTuple):
    create_time: float
    cpu_seconds: float
    rss: int
    io_read: int | None
    io_write: int | None


def import_psutil():
    """O módulo ``psutil``, ou ``None`` se ele não carregar nesta plataforma."""
    try:
        import psutil
    except Exception:
        return None
    return psutil


def default_disk_path() -> str:
    """
    Onde medir o disco: o diretório do entrypoint, não o ``cwd``.

    Mesmo motivo da busca do git: numa tarefa do Agendador sem "Iniciar em" o
    ``cwd`` é ``C:\\Windows\\system32``, que mede o volume errado quando o bot
    roda em ``D:``.
    """
    return detect_git.entrypoint_dir() or os.getcwd()


def volume_root(path: str) -> str:
    """
    Raiz do volume que contém ``path`` (``C:\\``, ``/``, ``/mnt/dados``).

    É o que vai para o dashboard em ``disk_path``: "C:\\" diz de que disco é o
    teto; o caminho do projeto não diz.
    """
    path = os.path.abspath(path)
    drive, _ = os.path.splitdrive(path)
    if drive:
        return drive + os.sep
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def collect_limits(disk_path: str | None = None, *, psutil_module=None) -> Limits:
    """Limites da máquina — fixos durante a execução, enviados no ``POST /logs/host``."""
    ps = psutil_module or import_psutil()
    if ps is None:
        return Limits(None, None, None, None)

    root = _safe_volume_root(disk_path)

    @safe(default=None)
    def cpu_count() -> int | None:
        return ps.cpu_count(logical=True)

    @safe(default=None)
    def memory_total() -> int | None:
        return int(ps.virtual_memory().total)

    @safe(default=None)
    def disk_total() -> int | None:
        return int(ps.disk_usage(root).total) if root else None

    return Limits(cpu_count(), memory_total(), disk_total(), root)


@safe(default=None)
def _safe_volume_root(disk_path: str | None) -> str | None:
    return volume_root(disk_path or default_disk_path())


def _pct(part: float, whole: float) -> float | None:
    if not whole:
        return None
    return round(part / whole * 100, 1)


class MetricsSampler:
    """
    Produz uma amostra por chamada a ``sample()``, guardando a leitura anterior.

    CPU e E/S são deltas: a primeira chamada só estabelece a base e devolve
    ``None`` — publicá-la sairia como "0%" no gráfico.

    ``psutil_module`` e ``clock`` são injetáveis para que os testes cubram as
    contas sem depender da máquina que roda o CI.
    """

    def __init__(self, disk_path: str | None = None, *, psutil_module=None, clock=time.time):
        self._ps = psutil_module or import_psutil()
        self._clock = clock
        self._disk_root = _safe_volume_root(disk_path)
        self._prev_time: float | None = None
        self._prev_system = None
        self._prev_tree: dict[int, _ProcReading] = {}
        self._cpu_count = self._read_cpu_count()

    @property
    def available(self) -> bool:
        return self._ps is not None

    def sample(self) -> dict | None:
        if self._ps is None:
            return None

        now = self._clock()
        system = self._read_system_cpu()
        tree = self._read_tree() or {}
        memory = self._read_memory()
        disk = self._read_disk()

        sample = None
        if self._prev_time is not None:
            sample = {
                "sampled_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                **dict.fromkeys(SAMPLE_FIELDS),
            }
            sample["host_cpu_pct"] = self._host_cpu_pct(self._prev_system, system)
            if memory is not None:
                total, available = memory
                sample["host_mem_used_bytes"] = total - available
                sample["host_mem_pct"] = _pct(total - available, total)
            if disk is not None:
                sample["host_disk_used_bytes"], sample["host_disk_pct"] = disk
            self._fill_process(sample, tree, now, memory)

        self._prev_time = now
        self._prev_system = system
        self._prev_tree = tree
        return sample

    # ------------------------------------------------------------------
    # leituras (uma por recurso, cada uma isolada pelo @safe)
    # ------------------------------------------------------------------

    @safe(default=None)
    def _read_cpu_count(self) -> int | None:
        return self._ps.cpu_count(logical=True) if self._ps else None

    @safe(default=None)
    def _read_system_cpu(self) -> tuple[float, float] | None:
        """
        ``(busy, total)`` em segundos de CPU acumulados desde o boot.

        Conta própria em vez de ``psutil.cpu_percent(interval=None)``: a base
        daquele é global ao módulo psutil, e se o bot também chamar
        ``cpu_percent()`` as duas leituras se corrompem.
        """
        times = self._ps.cpu_times()
        fields = vars(times) if not hasattr(times, "_asdict") else times._asdict()
        # guest/guest_nice já estão somados em user/nice no Linux (o próprio
        # psutil os desconta do total pelo mesmo motivo).
        total = sum(fields.values()) - fields.get("guest", 0.0) - fields.get("guest_nice", 0.0)
        idle = fields.get("idle", 0.0) + fields.get("iowait", 0.0)
        return total - idle, total

    @safe(default=None)
    def _read_memory(self) -> tuple[int, int] | None:
        vm = self._ps.virtual_memory()
        # `total - available` é o "Em uso" do Gerenciador de Tarefas; `vm.used`
        # tem semântica diferente em cada plataforma.
        return int(vm.total), int(vm.available)

    @safe(default=None)
    def _read_disk(self) -> tuple[int, float] | None:
        if not self._disk_root:
            return None
        usage = self._ps.disk_usage(self._disk_root)
        return int(usage.used), round(float(usage.percent), 1)

    @safe(default=None)
    def _read_tree(self) -> dict[int, _ProcReading]:
        """
        O processo e todos os descendentes. Bots RPA abrem Chrome e Excel: o
        custo real costuma estar nos filhos, não no Python.
        """
        ps = self._ps
        me = ps.Process()
        procs = [me]
        try:
            procs += me.children(recursive=True)
        except (ps.Error, OSError):
            pass

        tree: dict[int, _ProcReading] = {}
        for proc in procs:
            try:
                with proc.oneshot():
                    times = proc.cpu_times()
                    io_read, io_write = self._read_io(proc)
                    tree[proc.pid] = _ProcReading(
                        create_time=proc.create_time(),
                        cpu_seconds=times.user + times.system,
                        rss=proc.memory_info().rss,
                        io_read=io_read,
                        io_write=io_write,
                    )
            except (ps.Error, OSError):
                # morreu entre a listagem e a leitura, ou é de outro usuário
                continue
        return tree

    def _read_io(self, proc) -> tuple[int | None, int | None]:
        try:
            io = proc.io_counters()
        except (AttributeError, NotImplementedError, self._ps.Error, OSError):
            # macOS não tem io_counters; AccessDenied pode vir só desta chamada
            return None, None
        return io.read_bytes, io.write_bytes

    # ------------------------------------------------------------------
    # contas
    # ------------------------------------------------------------------

    @staticmethod
    def _host_cpu_pct(prev, cur) -> float | None:
        if prev is None or cur is None:
            return None
        busy = cur[0] - prev[0]
        total = cur[1] - prev[1]
        if total <= 0:
            return None
        return round(min(max(busy / total * 100, 0.0), 100.0), 1)

    def _fill_process(self, sample: dict, tree: dict, now: float, memory) -> None:
        """
        Soma a árvore do processo, confrontando cada PID com a leitura anterior:

        - mesmo PID e mesmo ``create_time`` -> contribui o delta;
        - PID novo nascido depois da amostra anterior -> contribui tudo (todo o
          consumo dele cabe no intervalo);
        - PID novo que já existia (visto agora pela primeira vez) -> só entra na
          base; contar o total dele despejaria horas de CPU num minuto;
        - reuso de PID aparece como ``create_time`` diferente -> processo novo.

        Um filho que morreu no intervalo perde a contribuição desse último
        trecho: subestimação pequena, aceita em troca de não precisar de hooks
        de término de processo.
        """
        prev_time = self._prev_time
        elapsed = now - prev_time
        cpu = 0.0
        io_read = io_write = 0
        io_known = False
        io_negative = False

        for pid, cur in tree.items():
            old = self._prev_tree.get(pid)
            if old is not None and abs(old.create_time - cur.create_time) < 1e-3:
                base = old
            elif cur.create_time > prev_time:
                base = None
            else:
                continue

            cpu += cur.cpu_seconds - (base.cpu_seconds if base else 0.0)

            if cur.io_read is not None and (base is None or base.io_read is not None):
                read = cur.io_read - (base.io_read if base else 0)
                write = cur.io_write - (base.io_write if base else 0)
                if read < 0 or write < 0:
                    io_negative = True
                io_read += read
                io_write += write
                io_known = True

        cpu_count = self._cpu_count
        if elapsed > 0 and cpu_count:
            sample["proc_cpu_pct"] = round(min(max(cpu / (elapsed * cpu_count) * 100, 0.0), 100.0), 1)

        if io_known and not io_negative:
            sample["proc_io_read_bytes"] = io_read
            sample["proc_io_write_bytes"] = io_write

        if tree:
            rss = sum(reading.rss for reading in tree.values())
            sample["proc_mem_bytes"] = rss
            if memory is not None:
                sample["proc_mem_pct"] = _pct(rss, memory[0])


__all__ = [
    "SAMPLE_FIELDS",
    "Limits",
    "MetricsSampler",
    "collect_limits",
    "default_disk_path",
    "import_psutil",
    "volume_root",
]
