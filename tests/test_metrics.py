from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from jaylog.host import metrics
from jaylog.host.metrics import SAMPLE_FIELDS, MetricsSampler, collect_limits, volume_root

GB = 1024**3


class FakeError(Exception):
    pass


class FakeNoSuchProcess(FakeError):
    pass


class FakeAccessDenied(FakeError):
    pass


class FakeProc:
    def __init__(self, pid, create_time, cpu=0.0, rss=0, io=(0, 0), fail=None):
        self.pid = pid
        self.create = create_time
        self.cpu = cpu
        self.rss = rss
        self.io = io
        self.fail = fail  # exceção levantada ao ler

    @contextmanager
    def oneshot(self):
        yield

    def _check(self):
        if self.fail is not None:
            raise self.fail

    def create_time(self):
        self._check()
        return self.create

    def cpu_times(self):
        self._check()
        return SimpleNamespace(user=self.cpu, system=0.0)

    def memory_info(self):
        self._check()
        return SimpleNamespace(rss=self.rss)

    def io_counters(self):
        self._check()
        if self.io is None:
            raise AttributeError("io_counters")
        return SimpleNamespace(read_bytes=self.io[0], write_bytes=self.io[1])


class FakePsutil:
    """Só o pedaço da API do psutil que o sampler usa."""

    Error = FakeError
    NoSuchProcess = FakeNoSuchProcess
    AccessDenied = FakeAccessDenied
    ZombieProcess = FakeNoSuchProcess

    def __init__(self):
        self.cpus = 2
        self.mem_total = 8 * GB
        self.mem_available = 5 * GB
        self.disk = SimpleNamespace(total=10 * GB, used=7 * GB, percent=70.0)
        self.system = SimpleNamespace(user=0.0, system=0.0, idle=0.0)
        self.me = FakeProc(1, create_time=0.0)
        self.children = []
        self.disk_paths = []

    def cpu_count(self, logical=True):
        return self.cpus

    def cpu_times(self):
        return self.system

    def virtual_memory(self):
        return SimpleNamespace(total=self.mem_total, available=self.mem_available)

    def disk_usage(self, path):
        self.disk_paths.append(path)
        return self.disk

    def Process(self):  # noqa: N802 - espelha a API do psutil
        me = self.me
        fake = self

        class _Me:
            pid = me.pid
            oneshot = me.oneshot
            create_time = me.create_time
            cpu_times = me.cpu_times
            memory_info = me.memory_info
            io_counters = me.io_counters

            def children(self, recursive=False):
                return list(fake.children)

        return _Me()


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def make_sampler(ps=None, now=1000.0):
    ps = ps or FakePsutil()
    clock = Clock(now)
    return MetricsSampler("/data/bot", psutil_module=ps, clock=clock), ps, clock


def advance(ps, clock, *, seconds=60.0, busy=0.0, idle=0.0):
    clock.now += seconds
    ps.system = SimpleNamespace(
        user=ps.system.user + busy, system=ps.system.system, idle=ps.system.idle + idle
    )


def test_first_sample_is_baseline_only():
    sampler, _, _ = make_sampler()

    assert sampler.sample() is None


def test_second_sample_has_every_contract_field():
    sampler, ps, clock = make_sampler()
    sampler.sample()
    advance(ps, clock, busy=30, idle=90)

    sample = sampler.sample()

    assert set(sample) == {"sampled_at", *SAMPLE_FIELDS}
    assert sample["sampled_at"].endswith("+00:00")


def test_host_cpu_from_cpu_times_delta():
    sampler, ps, clock = make_sampler()
    sampler.sample()
    advance(ps, clock, busy=96, idle=24)  # 96 de 120 s de CPU ocupados

    assert sampler.sample()["host_cpu_pct"] == 80.0


def test_host_memory_uses_total_minus_available():
    sampler, ps, clock = make_sampler()
    sampler.sample()
    advance(ps, clock, busy=1, idle=1)

    sample = sampler.sample()

    assert sample["host_mem_used_bytes"] == 3 * GB
    assert sample["host_mem_pct"] == 37.5


def test_host_disk_measures_the_volume_root():
    sampler, ps, clock = make_sampler()
    sampler.sample()
    advance(ps, clock, busy=1, idle=1)

    sample = sampler.sample()

    assert sample["host_disk_used_bytes"] == 7 * GB
    assert sample["host_disk_pct"] == 70.0
    assert ps.disk_paths[-1] == volume_root("/data/bot")


def test_proc_cpu_is_normalized_by_cpu_count_and_includes_children():
    sampler, ps, clock = make_sampler()
    ps.children = [FakeProc(2, create_time=10.0, cpu=5.0)]
    sampler.sample()

    advance(ps, clock, busy=1, idle=1)
    ps.me.cpu += 30.0  # 30 s de CPU do processo
    ps.children[0].cpu += 30.0  # + 30 s do filho = 60 s em 60 s x 2 CPUs

    assert sampler.sample()["proc_cpu_pct"] == 50.0


def test_proc_cpu_is_capped_at_100():
    sampler, ps, clock = make_sampler()
    sampler.sample()
    advance(ps, clock, busy=1, idle=1)
    ps.me.cpu += 500.0

    assert sampler.sample()["proc_cpu_pct"] == 100.0


def test_child_born_during_interval_counts_in_full():
    sampler, ps, clock = make_sampler()
    sampler.sample()

    advance(ps, clock, busy=1, idle=1)
    ps.children = [FakeProc(2, create_time=clock.now - 30, cpu=12.0, io=(100, 50))]

    sample = sampler.sample()

    assert sample["proc_cpu_pct"] == 10.0  # 12 s / (60 s x 2)
    assert sample["proc_io_read_bytes"] == 100
    assert sample["proc_io_write_bytes"] == 50


def test_preexisting_child_seen_first_time_only_sets_baseline():
    sampler, ps, clock = make_sampler()
    sampler.sample()

    advance(ps, clock, busy=1, idle=1)
    # já existia antes da amostra anterior, mas só apareceu na árvore agora
    ps.children = [FakeProc(2, create_time=5.0, cpu=900.0, io=(10**9, 10**9))]

    sample = sampler.sample()

    assert sample["proc_cpu_pct"] == 0.0
    assert sample["proc_io_read_bytes"] == 0


def test_reused_pid_is_treated_as_new_process():
    sampler, ps, clock = make_sampler()
    ps.children = [FakeProc(2, create_time=10.0, cpu=100.0)]
    sampler.sample()

    advance(ps, clock, busy=1, idle=1)
    # mesmo PID, outro processo: nasceu no intervalo, com menos CPU que o antigo
    ps.children = [FakeProc(2, create_time=clock.now - 10, cpu=6.0)]

    assert sampler.sample()["proc_cpu_pct"] == 5.0


def test_child_that_vanishes_or_is_denied_is_skipped():
    sampler, ps, clock = make_sampler()
    ps.me.rss = 100
    sampler.sample()

    advance(ps, clock, busy=1, idle=1)
    ps.children = [
        FakeProc(2, create_time=clock.now - 1, fail=FakeNoSuchProcess()),
        FakeProc(3, create_time=clock.now - 1, fail=FakeAccessDenied()),
        FakeProc(4, create_time=clock.now - 1, rss=50),
    ]

    sample = sampler.sample()

    assert sample["proc_mem_bytes"] == 150
    assert sample["proc_cpu_pct"] == 0.0


def test_proc_memory_sums_rss_of_tree():
    sampler, ps, clock = make_sampler()
    ps.me.rss = GB // 4
    ps.children = [FakeProc(2, create_time=10.0, rss=GB // 2)]
    sampler.sample()
    advance(ps, clock, busy=1, idle=1)

    sample = sampler.sample()

    assert sample["proc_mem_bytes"] == 3 * GB // 4
    assert sample["proc_mem_pct"] == 9.4  # 0,75 GB de 8 GB = 9,375%


def test_io_delta():
    sampler, ps, clock = make_sampler()
    ps.me.io = (1000, 500)
    sampler.sample()

    advance(ps, clock, busy=1, idle=1)
    ps.me.io = (4000, 700)

    sample = sampler.sample()

    assert sample["proc_io_read_bytes"] == 3000
    assert sample["proc_io_write_bytes"] == 200


def test_negative_io_delta_becomes_none():
    sampler, ps, clock = make_sampler()
    ps.me.io = (1000, 500)
    sampler.sample()

    advance(ps, clock, busy=1, idle=1)
    ps.me.io = (10, 5)

    sample = sampler.sample()

    assert sample["proc_io_read_bytes"] is None
    assert sample["proc_io_write_bytes"] is None


def test_io_unavailable_on_platform_becomes_none():
    sampler, ps, clock = make_sampler()
    ps.me.io = None
    sampler.sample()
    advance(ps, clock, busy=1, idle=1)

    sample = sampler.sample()

    assert sample["proc_io_read_bytes"] is None
    assert sample["proc_cpu_pct"] == 0.0


def test_one_failing_reading_does_not_sink_the_others(monkeypatch):
    sampler, ps, clock = make_sampler()
    sampler.sample()
    advance(ps, clock, busy=96, idle=24)

    def boom(path):
        raise OSError("disco sumiu")

    monkeypatch.setattr(ps, "disk_usage", boom)

    sample = sampler.sample()

    assert sample["host_disk_pct"] is None
    assert sample["host_disk_used_bytes"] is None
    assert sample["host_cpu_pct"] == 80.0
    assert sample["host_mem_pct"] == 37.5


def test_clock_going_backwards_yields_none_cpu():
    sampler, ps, clock = make_sampler()
    sampler.sample()
    clock.now -= 5

    sample = sampler.sample()

    assert sample["proc_cpu_pct"] is None


def test_collect_limits():
    ps = FakePsutil()

    limits = collect_limits("/data/bot", psutil_module=ps)

    assert limits.cpu_count == 2
    assert limits.memory_total_bytes == 8 * GB
    assert limits.disk_total_bytes == 10 * GB
    assert limits.disk_path == volume_root("/data/bot")


def test_collect_limits_survives_failures(monkeypatch):
    ps = FakePsutil()
    monkeypatch.setattr(ps, "virtual_memory", lambda: (_ for _ in ()).throw(OSError()))

    limits = collect_limits("/data/bot", psutil_module=ps)

    assert limits.memory_total_bytes is None
    assert limits.cpu_count == 2


def test_volume_root_on_posix_is_a_mount_point(tmp_path):
    root = volume_root(str(tmp_path))

    import os

    assert os.path.ismount(root)
    assert str(tmp_path).startswith(root)


@pytest.mark.parametrize("path, expected", [("C:\\bots\\x", "C:\\"), ("D:/y", "D:\\")])
def test_volume_root_on_windows_is_the_drive(monkeypatch, path, expected):
    import ntpath

    monkeypatch.setattr(metrics, "os", SimpleNamespace(path=ntpath, sep="\\", getcwd=lambda: "C:\\"))

    assert volume_root(path) == expected
