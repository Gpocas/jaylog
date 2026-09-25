# Host Metrics (jaylog) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Coletar CPU/memória/disco do host e do processo a cada minuto e enviar ao `POST /logs/host-metrics`; enviar os limites da máquina no `POST /logs/host`.

**Architecture:** `host/metrics.py` (leitura pura, psutil, `@safe`) + `host/metrics_reporter.py`
(thread daemon única por processo, buffer + POST) + integração em `configure()`/`shutdown()`.

**Tech Stack:** Python 3.10–3.13, psutil, requests, pydantic-settings, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-host-metrics-design.md` (contrato: spec do backend)

## Global Constraints

- `psutil>=5.9.6,<8` em `dependencies`; versão do pacote `0.3.0a4`; `PROTOCOL_VERSION = 3`.
- Nenhuma exceção de coleta/rede escapa para o chamador.
- Toda amostra carrega todas as chaves do contrato; ausência = `None`.
- Buffer `deque(maxlen=60)`; intervalo padrão 60 s, mínimo 10 s; alinhado ao relógio.
- Variáveis: `JAYLOG_HOST_METRICS_ENABLED`, `JAYLOG_HOST_METRICS_INTERVAL`, `JAYLOG_HOST_METRICS_HTTP_ENDPOINT`.
- Comentários e mensagens em português, no estilo dos módulos `host/`.

## Review Focus

- Bot que abre e fecha filhos (Chrome) entre amostras → CPU/IO não negativos nem absurdos.
- `AccessDenied` ao ler um filho (processo de outro usuário/elevado no Windows) → ignorado, resto segue.
- `configure()` chamado duas vezes → um único coletor vivo.
- `shutdown()` com backend fora do ar → não bloqueia além de ~2 s.
- Dois `app_name` no mesmo `configure()` → um coletor, vinculado ao primeiro elegível.

---

### Task 1: `metrics.py` — limites e sampler

**Files:** Modify `pyproject.toml`; Create `src/jaylog/host/metrics.py`, `tests/test_metrics.py`

**Produces:**
```python
SAMPLE_FIELDS: tuple[str, ...]  # ordem do contrato, sem sampled_at
class Limits(NamedTuple): cpu_count: int|None; memory_total_bytes: int|None; disk_total_bytes: int|None; disk_path: str|None
def default_disk_path() -> str
def collect_limits(disk_path: str | None = None) -> Limits
class MetricsSampler:
    def __init__(self, disk_path: str | None = None, *, psutil_module=None, clock=time.time): ...
    def sample(self) -> dict | None
```
- [ ] Testes com psutil fake (spec §5); falham → implementar → passam → commit.

### Task 2: limites no `HostInfo`

**Files:** Modify `models.py`, `collectors.py`, `_version.py`; Test `tests/test_host_payload.py`
- [ ] 4 campos novos, preenchidos via `collect_limits()`; `PROTOCOL_VERSION = 3`. Teste → commit.

### Task 3: endpoint e settings

**Files:** Modify `endpoints.py`, `settings.py`; Test `tests/test_endpoints.py`, `tests/test_settings.py`
- [ ] `derive_endpoint(log_endpoint, segment)`; `derive_host_endpoint` vira atalho.
- [ ] `host_metrics_enabled`, `host_metrics_interval` (validador `>= 10`), `host_metrics_http_endpoint`, `effective_host_metrics_endpoint`. Testes → commit.

### Task 4: `JaylogMetricsReporter`

**Files:** Create `src/jaylog/host/metrics_reporter.py`, `tests/test_metrics_reporter.py`

**Produces:**
```python
class JaylogMetricsReporter:
    def __init__(self, service, endpoint, api_key, *, interval=60.0, timeout=5.0, proxy=None, verify=False, session=None, sampler=None, clock=time.time): ...
    buffer: deque; disabled: bool
    def start(self) -> None; def stop(self, timeout=2.0) -> None
    def collect(self) -> None     # uma amostra -> buffer
    def flush(self) -> bool       # POST do buffer; True se entregue
def start(reporter) -> None; def stop(timeout=2.0) -> None; def active() -> JaylogMetricsReporter | None
def seconds_until_next(interval: float, now: float) -> float
```
- [ ] Testes (spec §5) → implementar → commit.

### Task 5: integração no `logger.py`

**Files:** Modify `logger.py`, `tests/conftest.py`; Test `tests/test_logger_lifecycle.py`
- [ ] `_start_metrics_reporter(items)` após os host reporters; `shutdown()` para conforme spec §3.2. Testes → commit.

### Task 6: docs, versão, verificação

- [ ] README, `pyproject.toml` versão `0.3.0a4`, `uv lock`. `uv run ruff check .`, `uv run pytest`. Commit.
