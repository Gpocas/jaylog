# Métricas de recursos do host (CPU, memória, disco) — cliente jaylog

- **Data:** 2026-09-25
- **Status:** aprovado em conversa, aguardando revisão do spec escrito
- **Repositórios:** `jaylog` (este spec) e `backend-nn-analytics`
  (`docs/superpowers/specs/2026-09-25-host-metrics-design.md` — **fonte de verdade do
  contrato HTTP**; as seções de contrato abaixo são cópia e devem seguir aquele arquivo)

## 1. Objetivo

Enquanto o processo roda, coletar a cada **1 minuto** o uso de CPU, memória e disco da
**máquina** e do **processo**, e enviar ao backend para exibição em gráfico. Antes disso,
informar os **limites** da máquina (CPUs, RAM total, disco total) junto com o registro de
ambiente já existente.

Critérios de sucesso:

1. Com `configure()` e endpoint HTTP configurado, sem nenhuma variável nova, o bot passa a
   enviar uma amostra por minuto.
2. Nenhuma falha de coleta ou de rede afeta o bot (mesma garantia do host reporting).
3. Backend fora do ar por até 1 h não perde amostras; backend antigo desliga o coletor
   com um único aviso.

Fora de escopo: métricas de rede, GPU, alertas, envio de métricas por logger individual.

## 2. Decisões tomadas

| Tema                    | Decisão                                                                                  |
|-------------------------|------------------------------------------------------------------------------------------|
| Disco do processo       | Não existe "% de disco do processo": mede-se **I/O** (bytes lidos/escritos no intervalo) |
| Disco do host           | % e bytes usados do volume do **entrypoint**                                             |
| Dependência             | **`psutil` obrigatório** em `dependencies`                                              |
| Granularidade           | 60 s, alinhada ao relógio (uma amostra por minuto cheio)                                 |
| Unidade de coleta       | **Uma thread por processo**, não por `app_name` — CPU/RAM são do processo              |
| Transporte              | Endpoint dedicado `POST /logs/host-metrics`, lote de amostras                            |
| Limites                 | Campos novos no `HostInfo` → `POST /logs/host` já existente                             |
| Retenção                | 48 h, responsabilidade do backend                                                        |

## 3. Componentes

### 3.1 `jaylog/host/metrics.py` — leitura

Sem estado de rede, sem thread. Duas peças:

**`collect_limits(disk_path) -> Limits`**, com `@safe`:

- `cpu_count`: `psutil.cpu_count(logical=True)`
- `memory_total_bytes`: `psutil.virtual_memory().total`
- `disk_total_bytes`: `psutil.disk_usage(disk_path).total`
- `disk_path`: a **raiz do volume** que contém `disk_path` (ex.: `C:\`, `/`), para o
  dashboard mostrar "C:\" e não o caminho do projeto.

`disk_path` de entrada = `detect_git.entrypoint_dir()` (fallback `os.getcwd()`). Mesmo
motivo do git: numa tarefa do Agendador sem "Iniciar em", o `cwd` é
`C:\Windows\system32`, que mede o volume errado quando o bot roda em `D:`.

**`class MetricsSampler`**: guarda a leitura anterior e produz uma amostra por chamada.

- `sample() -> dict | None`: devolve `None` na **primeira** chamada (só estabelece a base
  — CPU e I/O são deltas, e a primeira leitura não pode sair como 0%); nas seguintes,
  devolve o dicionário de wire (seção 4.2) com `sampled_at` em UTC ISO-8601.
- Cada campo é calculado numa função `@safe(default=None)` própria: um campo que falha vira
  `null`, o resto da amostra segue.

Cálculos:

- **`host_cpu_pct`**: delta de `psutil.cpu_times()` guardado pelo próprio sampler —
  `busy = total - idle` (`- iowait` onde existir), `pct = Δbusy / Δtotal * 100`. **Não**
  usar `psutil.cpu_percent(interval=None)`: a base dele é global ao módulo psutil, e se o
  bot também chamar `cpu_percent()` as duas leituras se corrompem.
- **`host_mem_used_bytes`**: `total - available` (o "Em uso" do Gerenciador de Tarefas;
  `virtual_memory().used` tem semântica diferente por plataforma).
  **`host_mem_pct`**: `used / total * 100`.
- **`host_disk_*`**: `psutil.disk_usage(disk_path)` → `used`, `percent`.
- **Árvore do processo**: `psutil.Process()` + `children(recursive=True)`. Bots RPA abrem
  Chrome/Excel; o custo real está nos filhos.
  Estado anterior por PID: `{pid: (create_time, cpu_seconds, io_read, io_write)}`.
  Para cada processo da árvore atual:
  - PID presente no estado anterior **com o mesmo `create_time`** → contribui o delta;
  - PID novo com `create_time` **posterior** à amostra anterior → nasceu no intervalo,
    contribui o valor total;
  - PID novo com `create_time` anterior (já existia, primeira vez visto) → só entra na base;
  - processo que morre durante a leitura (`NoSuchProcess`, `AccessDenied`) → ignorado.
  - PIDs que sumiram (filho morreu no intervalo) perdem a contribuição do último
    intervalo — subestimação aceita e documentada no código.
- **`proc_cpu_pct`**: `Σ Δcpu_seconds / (Δwall * cpu_count) * 100`, limitado a 0–100.
- **`proc_mem_bytes`**: `Σ memory_info().rss` da árvore. **`proc_mem_pct`**:
  `proc_mem_bytes / memory_total_bytes * 100`.
- **`proc_io_read_bytes` / `proc_io_write_bytes`**: `Σ Δio_counters().read_bytes/
  write_bytes`. Delta negativo → `null`. Plataforma sem `io_counters` (macOS) → `null`.
  Nome `io` e não `disk`: no Windows o contador soma toda E/S (arquivo, rede, dispositivo).

### 3.2 `jaylog/host/metrics_reporter.py` — `JaylogMetricsReporter`

Mesmo desenho do `JaylogHostReporter` (thread daemon, `session`/relógio injetáveis para
teste, headers `x-api-key`, `x-jaylog-version`, `x-jaylog-protocol`, `x-jaylog-run-id`,
proxy e `verify` das settings).

Ciclo de vida:

- **Um por processo.** Singleton de módulo (`start(...)`, `stop(timeout)`), não um
  registry por serviço.
- Vinculado ao **primeiro** item de `configure()` que tenha métricas habilitadas, endpoint
  e api key: dele vêm `service`, endpoint, api key, proxy, verify e timeout.
- `configure()` chama `shutdown()` antes (comportamento atual) → o coletor é parado e
  recriado; o buffer pendente é descartado (aceito: acontece só em reconfiguração).
- `shutdown()` sem nome → para o coletor. `shutdown(name)` → para só se `name` for o
  serviço vinculado.
- `stop()`: coleta **uma amostra final** e tenta um POST, dentro do mesmo teto de 2 s do
  `_STOP_JOIN_TIMEOUT`, para a execução não terminar com um buraco no último minuto.

Loop:

1. Instancia `MetricsSampler` e chama `sample()` uma vez (base).
2. Dorme até o próximo múltiplo de `interval` no relógio de parede
   (`interval - time.time() % interval`), via `self._stop.wait(...)`. Alinhar ao relógio
   dá exatamente uma amostra por bucket de 1 min no backend.
3. `sample()` → acrescenta ao buffer `deque(maxlen=60)` (1 h de backend fora; estourou,
   descarta as mais antigas).
4. POST do buffer inteiro (≤ 60 itens; backend aceita até 120).
5. Volta ao passo 2.

Tratamento da resposta:

| Resultado                          | Ação                                                                   |
|------------------------------------|------------------------------------------------------------------------|
| 2xx                                | Esvazia o buffer                                                       |
| 2xx com `x-jaylog-host-required: 1`| Esvazia o buffer e chama `reporter.request_resend(service)`            |
| Rede/timeout, 429, 5xx             | Mantém o buffer, tenta no próximo ciclo (sem backoff extra: o ciclo já é de 60 s) |
| 404, 405                           | Desliga no processo, aviso único no stderr (backend anterior à rota)   |
| 401, 403, 422, demais 4xx          | Desliga no processo, aviso no stderr com trecho do corpo               |

**Sem psutil utilizável** (`ImportError` ou falha no primeiro `sample()`): aviso único no
stderr e o coletor não sobe. O host reporting segue normal.

### 3.3 Settings (`JaylogSettings`)

| Variável                         | Campo                        | Padrão | Observação                                         |
|----------------------------------|------------------------------|--------|----------------------------------------------------|
| `JAYLOG_HOST_METRICS_ENABLED`    | `host_metrics_enabled`       | `true` | Só vale se `host_report_enabled` também for `true` |
| `JAYLOG_HOST_METRICS_INTERVAL`   | `host_metrics_interval`      | `60`   | Segundos; validador `>= 10`                        |
| `JAYLOG_HOST_METRICS_HTTP_ENDPOINT` | `host_metrics_http_endpoint` | `None` | Override; senão derivado                           |

- `effective_host_metrics_endpoint`: override, ou `derive_endpoint(log_http_endpoint,
  "host-metrics")`.
- `endpoints.py`: generalizar `derive_host_endpoint` para
  `derive_endpoint(log_endpoint, segment)`, mantendo `derive_host_endpoint` como atalho
  (`segment="host"`) — mesma regra de path, query e fragmento.
- Timeout do POST: `log_http_timeout`.
- Por que métricas dependem de `host_report_enabled`: sem registro de host o backend não
  tem como ligar o `run_id` a hostname/username, e as amostras nunca apareceriam no gráfico.

### 3.4 Limites no `HostInfo`

`models.py` ganha, na seção "identidade da máquina", `cpu_count: int | None`,
`memory_total_bytes: int | None`, `disk_total_bytes: int | None`, `disk_path: str | None`.
`collectors._collect` preenche via `metrics.collect_limits(...)`. `HostInfo.minimal()` não
muda (continuam `None`).

`_version.PROTOCOL_VERSION`: **2 → 3**.

### 3.5 Integração em `logger.py`

- `configure()`: após `_start_host_reporters(items)`, `_start_metrics_reporter(items)`.
- `shutdown()`: parar o coletor conforme a regra do 3.2.
- `jaylog/host/__init__.py`: exportar o que for público, seguindo o padrão atual.

## 4. Contrato HTTP (cópia — fonte de verdade no spec do backend)

### 4.1 `POST /logs/host` — campos novos

`cpu_count`, `memory_total_bytes`, `disk_total_bytes` (inteiros) e `disk_path` (string),
todos anuláveis e **enviados como `null`**, nunca omitidos.

### 4.2 `POST /logs/host-metrics`

```json
{
  "run_id": "<RUN_ID>",
  "service": "<app_name vinculado>",
  "samples": [
    {
      "sampled_at": "2026-09-25T12:01:00+00:00",
      "host_cpu_pct": 80.1,
      "host_mem_used_bytes": 3221225472,
      "host_mem_pct": 37.5,
      "host_disk_used_bytes": 7516192768,
      "host_disk_pct": 70.0,
      "proc_cpu_pct": 12.4,
      "proc_mem_bytes": 314572800,
      "proc_mem_pct": 3.7,
      "proc_io_read_bytes": 1048576,
      "proc_io_write_bytes": 524288
    }
  ]
}
```

Todos os campos presentes em toda amostra; ausência de medida = `null`. Percentuais em
0–100 com até 1 casa decimal; bytes inteiros. Respostas: 200 `{inserted}`, 401, 422 —
tabela de tratamento no 3.2.

## 5. Testes (pytest, CI Linux + Windows, 3.10 e 3.13)

`tests/test_metrics.py` — psutil mockado, sem dormir:

- primeira `sample()` devolve `None`; segunda devolve todas as chaves do contrato;
- `host_cpu_pct` a partir de dois `cpu_times` sintéticos;
- `proc_cpu_pct` normalizado por `cpu_count` e limitado a 100;
- soma dos filhos; filho que nasce no intervalo conta inteiro; filho pré-existente visto
  pela primeira vez só entra na base; filho que levanta `NoSuchProcess` é ignorado;
- delta de I/O negativo → `null`; `io_counters` ausente → `null`;
- um campo levantando exceção não derruba os outros;
- `collect_limits` devolve a raiz do volume em `disk_path`.

`tests/test_metrics_reporter.py` — `session` fake e relógio injetado, no estilo de
`test_reporter.py`:

- 2xx esvazia o buffer; falha de rede/5xx/429 mantém e o próximo POST leva o lote inteiro;
- buffer limitado a 60, descartando as mais antigas;
- 404/405 desliga com aviso único; 401/403/422 desliga;
- `x-jaylog-host-required: 1` chama `request_resend(service)`;
- `stop()` envia a amostra final;
- cálculo do sleep até o próximo múltiplo de `interval`.

Ajustes em testes existentes:

- `test_endpoints.py`: `derive_endpoint(..., "host-metrics")` com query, fragmento, barra
  final e URL sem path.
- `test_settings.py`: padrões e validador de `host_metrics_interval`, endpoint efetivo.
- `test_host_payload.py`: os 4 campos de limite presentes (e `null` quando a coleta falha).
- `test_logger_lifecycle.py`: `configure()` sobe um único coletor com dois `app_name`;
  `shutdown()` o para; `host_metrics_enabled=false` não sobe.

Verificação manual: `example_app.py` contra o backend local por 3 minutos → 2 amostras
no banco (a primeira leitura é só base), `GET /logs/host-metrics` com os limites
preenchidos.

## 6. Documentação e versão

- `README.md`: menção curta às métricas e às 3 variáveis; detalhe no jaylog-book.
- `pyproject.toml`: `psutil>=5.9.6,<8` (5.9.6 é a primeira com wheel `cp37-abi3`, que cobre 3.10–3.13 no Windows e no Linux sem compilar) em `dependencies`; versão `0.3.0a4`.

## 7. Ordem de entrega

Backend primeiro (ver spec do backend, seção 10). Este cliente contra um backend antigo
funciona — o coletor desliga no 404 com um aviso e os limites são descartados pelo
backend — mas não gera dados.
