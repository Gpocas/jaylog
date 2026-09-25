# Agendas do Task Scheduler do Windows — cliente jaylog

- **Data:** 2026-09-25
- **Status:** aprovado em conversa, aguardando revisão do spec escrito
- **Repositórios:** `jaylog` (este spec), `backend-nn-analytics`
  (`docs/superpowers/specs/2026-09-25-task-scheduler-schedules-design.md` — **fonte de
  verdade do contrato HTTP**, schema e front; a seção 6 abaixo é cópia e deve seguir
  aquele arquivo) e `frontend-analytics-logging`

## 1. Objetivo

Quando o processo foi iniciado pelo Agendador de Tarefas do Windows, ler as tasks
**ativas** que executam o entrypoint do bot, converter seus triggers para o modelo de
`service_schedules` do backend (Repetição → Granularidade → Horário) e enviar num único
POST. O backend substitui atomicamente as agendas sincronizadas anteriores do serviço.

Critérios de sucesso:

1. Com `configure()` e endpoint HTTP configurado, sem variável nova, um bot agendado envia
   suas agendas uma vez por execução.
2. Nenhuma falha (schtasks, permissão, XML, rede) afeta ou atrasa o bot.
3. Só é enviado o que é representado **sem perda**; triggers não representáveis são
   ignorados por inteiro.
4. Falha de leitura nunca apaga agendas no backend (não se envia lista vazia).

Fora de escopo: cron/systemd, tasks que não executam o entrypoint, monitoramento de
execuções esperadas.

## 2. Decisões tomadas

| Tema                    | Decisão                                                                                     |
|-------------------------|---------------------------------------------------------------------------------------------|
| Quando coletar          | Só com `sys.platform == "win32"` **e** `detect_execution().mode == "task_scheduler"`       |
| Leitura das tasks       | `schtasks /query /xml` via subprocess (sem dependência nova; XML independe do idioma)      |
| Parser                  | No jaylog; o backend recebe linhas normalizadas                                             |
| Formatos de action      | `.bat`/`.cmd` que chama o script (B) e script relativo com "Iniciar em" (D)                |
| Trigger com perda       | **Ignorado inteiro** — nunca aproximado                                                     |
| Repetição por intervalo | Expandida em horários; **> 24 horários/dia no trigger → `CONTINUO`**                       |
| Boot/Logon              | `CONTINUO`                                                                                  |
| Envio                   | Reaproveita `JaylogHostReporter` (mesma máquina de estados de retry)                       |
| Unidade de envio        | **Uma vez por processo**, vinculado ao primeiro item elegível (como as métricas)           |
| Lista vazia             | Não envia                                                                                   |
| Protocolo               | `PROTOCOL_VERSION = 4`                                                                      |

## 3. Componentes

### 3.1 `host/detect_git.py` — `entrypoint_path()`

Novo helper ao lado de `entrypoint_dir()`: caminho absoluto do arquivo do entrypoint
(`sys.executable` se frozen, senão `__main__.__file__`), ou `None`. Uso interno — **não**
entra no `HostInfo`. `entrypoint_dir()` passa a derivar dele.

### 3.2 `host/detect_schedule.py` — coleta e matching

```python
@dataclass(frozen=True)
class ScheduledTask:
    path: str                 # "\RPA\Faturamento"
    element: ET.Element       # <Task> já parseado

def query_tasks(timeout: float) -> list[ScheduledTask] | None: ...
def matches_entrypoint(task: ScheduledTask, entrypoint: str) -> bool: ...
def collect_schedules(*, timeout: float) -> list[dict] | None: ...   # @safe(default=None)
```

**`query_tasks`**

- `subprocess.run(["schtasks", "/query", "/xml"], timeout=…, stdin=DEVNULL,
  capture_output=True, creationflags=CREATE_NO_WINDOW)` — mesmo padrão de
  `detect_git._run_git` (reaproveitar `_popen_kwargs`).
- **Encoding:** a saída redirecionada vem, em geral, na codepage OEM (cp850 em pt-BR).
  Decodificar tentando, em ordem: BOM UTF-16; UTF-8 estrito (cobre OEMCP=65001, e cp850
  com acento nunca é UTF-8 válido); `cp{GetOEMCP()}` (novo `win32.oem_codepage()` via
  ctypes); `mbcs`; por fim UTF-8 com `replace`. Só então parsear. Validar numa VM Windows
  real com task/caminho acentuado.
- A saída é `<Tasks>` com um comentário `<!-- \Pasta\Nome -->` antes de cada `<Task>`.
  Parsear com `ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))` e associar cada
  `<Task>` ao comentário anterior. Namespace
  `http://schemas.microsoft.com/windows/2004/02/mit/task`.
- Descartar tasks sob `\Microsoft\` e tasks com `Settings/Enabled = false`.
- Retorna `None` em: executável ausente, returncode ≠ 0, timeout, XML malformado.

**`matches_entrypoint`** — verdadeiro se **alguma** `Actions/Exec` casar. Antes de
comparar: `os.path.expandvars`, remover aspas, resolver relativos contra
`WorkingDirectory`, `os.path.normcase(os.path.abspath(…))`.

- **Formato D (script direto):** o primeiro token de `Arguments` que termine em
  `.py`/`.pyw`, resolvido contra `WorkingDirectory`, é igual a `entrypoint_path()`.
  Cobre também `Command = python.exe` com `Arguments = C:\bots\x\main.py`. Frozen:
  `Command` resolvido igual a `sys.executable`.
- **Formato B (`.bat`/`.cmd`):** `Command` termina em `.bat`/`.cmd` **e** o diretório do
  `.bat` ou o `WorkingDirectory` é igual a `entrypoint_dir()` **e**, se o `.bat` for
  legível (≤ 64 KiB, decodificado como no `query_tasks`), seu conteúdo menciona
  `basename(entrypoint_path())` (comparação case-insensitive). `.bat` ilegível →
  casa só pelo diretório.

**`collect_schedules`** — orquestra: sem Windows/scheduler/entrypoint → `None`; lê as
tasks; filtra por `matches_entrypoint`; aplica `schedule_parser.parse_task` em cada uma;
consolida (seção 4.4). Resultado vazio → `None`.

### 3.3 `host/schedule_parser.py` — XML → linhas

Módulo **puro** (sem I/O, sem `win32`), testável no Linux:

```python
def parse_task(task: ET.Element, task_path: str, *, tz=None) -> list[dict]: ...
def consolidate(rows: list[dict]) -> list[dict]: ...
```

Cada linha: `{"repetition", "day_of_week", "day_of_month", "time", "task_path"}`, com
`time` em `"HH:MM"` ou `None`. Regras na seção 4. Exceção em um trigger → o trigger é
ignorado (os demais seguem).

### 3.4 Envio — `host/reporter.py` + `host/schedule_reporter.py`

`JaylogHostReporter` ganha dois parâmetros, sem mudar o comportamento atual:

- `label: str = "host"` — prefixo do nome da thread (`jaylog-{label}-{service}`) e das
  mensagens (`[jaylog] {label}: …`). A flag `_unsupported_warned` passa a ser por `label`.
- `one_shot: bool = False` — após a primeira rodada de `deliver()`, a thread termina em
  vez de esperar `request_resend()`.

E o `payload_factory` pode devolver `None` = "nada a enviar": `_post_once` retorna `None`
sem marcar `fatal` nem avisar.

`host/schedule_reporter.py` guarda o reporter de agendas como singleton de módulo
(`start(item)` / `stop()`), **fora** do registry de host — um `x-jaylog-host-required` não
deve reenviar agendas. Payload factory:

```python
def factory() -> dict | None:
    rows = detect_schedule.collect_schedules(timeout=settings.host_schedule_timeout)
    if not rows:
        return None
    return {"run_id": RUN_ID, "service": settings.app_name,
            "hostname": identity.hostname(), "schedules": rows}
```

A coleta roda **dentro da thread** do reporter: `configure()` não espera o `schtasks`.

### 3.5 Settings, endpoints e ciclo de vida

- `JaylogSettings`:
  - `host_schedule_enabled: bool = True`
  - `host_schedule_http_endpoint: str | None = None` → `effective_host_schedule_endpoint`
    derivado com `derive_endpoint(log_http_endpoint, "host-schedules")`
  - `host_schedule_timeout: float = 10.0` (timeout do `schtasks`)
- `logger.py`: `_start_schedule_reporter(items)` chamado em `configure()` após
  `_start_metrics_reporter`; primeiro item com `host_schedule_enabled`, endpoint e
  `log_http_api_key`. `shutdown()` chama `schedule_reporter.stop()`.
- `_version.py`: `PROTOCOL_VERSION = 4` com comentário
  `4: POST /logs/host-schedules (agendas do Task Scheduler)`.

## 4. Mapeamento de triggers

### 4.1 Filtros

- Trigger com `Enabled = false` → ignorado.
- `EndBoundary` no passado → ignorado. `StartBoundary` futuro → mantido.
- **Horário `T`** = hora:minuto de `StartBoundary` no relógio local. Boundary com `Z` ou
  offset ("sincronizar entre fusos") → convertido para o fuso local da máquina.

### 4.2 Triggers mapeados

| # | Cenário                                              | XML                                             | Resultado                                              |
|---|------------------------------------------------------|-------------------------------------------------|--------------------------------------------------------|
| 1 | Diário às 08:00                                      | `ScheduleByDay`, `DaysInterval = 1`             | `DIARIO 08:00`                                         |
| 2 | Semanal seg/qua/sex às 07:30                         | `ScheduleByWeek`, `WeeksInterval = 1`           | `SEMANA MONDAY/WEDNESDAY/FRIDAY 07:30` (3 linhas)      |
| 3 | Mensal dias 5 e 20 às 09:00, todos os meses          | `ScheduleByMonth`, 12 meses                     | `DIA 5 09:00`, `DIA 20 09:00`                          |
| 4 | Diário 08:00, repetir a cada 30 min por 10 h         | `ScheduleByDay` + `Repetition`                  | `DIARIO` 08:00 … 17:30 (20 linhas)                     |
| 5 | Semanal seg 22:00, a cada 1 h por 4 h                | `ScheduleByWeek` + `Repetition`                 | `SEMANA MONDAY 22:00, 23:00`, `SEMANA TUESDAY 00:00, 01:00` |
| 6 | Mensal dia 10 06:00, a cada 2 h por 12 h             | `ScheduleByMonth` + `Repetition`                | `DIA 10` 06:00 … 16:00 (6 linhas)                      |
| 7 | "Uma vez" 08:00, a cada 1 h indefinidamente          | `TimeTrigger` + `Repetition` sem `Duration`     | `DIARIO` 08:00, 09:00 … 07:00 (24 linhas)              |
| 8 | Diário, a cada 30 min por 24 h                       | `ScheduleByDay` + `Repetition`                  | 48 horários > 24 → `CONTINUO`                          |
| 9 | Ao iniciar o computador / ao fazer logon             | `BootTrigger` / `LogonTrigger`                  | `CONTINUO`                                             |

### 4.3 Expansão de repetição

Para um trigger com `Repetition/Interval = I` e `Duration = D` (ISO 8601; ausente ou
vazio = indefinido):

- Offsets `k·I` para `k ≥ 0` enquanto `k·I < D`; horários `T + k·I`.
- **Contagem por dia > 24 → o trigger inteiro vira `CONTINUO`.** "Por dia" = maior número
  de horários que caem num mesmo dia de calendário (ex.: seg 22:00 a cada 1 h por 30 h
  gera 2 na segunda, 24 na terça, 4 na quarta → 24, não vira `CONTINUO`).
- `DIARIO` (`D` finito, qualquer tamanho): horários tomados módulo 24 h e unidos. É exato
  porque o trigger recomeça todo dia — inclusive com `D > 24 h`, em que as cadeias de dias
  seguidos se sobrepõem.
- `SEMANA` (`D` finito): o mesmo, módulo 7 dias a partir de cada dia marcado; horário que
  passa da meia-noite vai para o dia da semana seguinte (`SATURDAY` → `SUNDAY`).
- `DIA`: repetição que passa da meia-noite → **trigger ignorado** (dia 30 + 1 cai em 31 ou
  em 1 conforme o mês).
- **Indefinido** (`Duration` ausente, em qualquer trigger): roda a cada `I` para sempre a
  partir do primeiro disparo. Se `24 h / I > 24` → `CONTINUO`; se `24 h` é múltiplo de `I`
  → `DIARIO` com os `24 h / I` horários a partir de `T`; senão (horário deriva a cada dia)
  → ignorado.
- `TimeTrigger` ("uma vez") só é mapeado com repetição indefinida (regra acima).

### 4.4 Consolidação

- União das linhas de todas as tasks e triggers casados, sem duplicatas por
  `(repetition, day_of_week, day_of_month, time)`; `task_path` do primeiro visto.
- Se houver qualquer `CONTINUO`, o resultado é **só** `[CONTINUO]` (com o `task_path` de
  quem o gerou).
- Ordenação estável: repetição (ordem do enum), dia, horário.
- Teto de 500 linhas: acima disso (não deveria ocorrer com o teto de 24/dia) → não envia
  e avisa.

### 4.5 Ignorados (trigger inteiro)

- `DaysInterval > 1`, `WeeksInterval > 1`.
- `ScheduleByMonth` com subconjunto de meses, ou com `<Day>Last</Day>` (mesmo junto com
  outros dias).
- `ScheduleByMonthDayOfWeek` ("1ª segunda do mês").
- `TimeTrigger` sem repetição ou com repetição de duração finita (não recorrente).
- `IdleTrigger`, `EventTrigger`, `RegistrationTrigger`, `SessionStateChangeTrigger`.
- `RandomDelay` ("atrasar aleatoriamente") em trigger de horário — o horário real é incerto.
- Qualquer outro elemento desconhecido no trigger. São neutros (não mudam quando dispara):
  `StartBoundary`, `EndBoundary`, `Enabled`, `ExecutionTimeLimit`, `Repetition` e o
  `ScheduleBy*`. `Boot`/`Logon` viram `CONTINUO` sem olhar os filhos (`Delay`, `UserId`).

## 5. Tratamento de erros

- Tudo roda na thread do reporter; coleta é `@safe`. Qualquer falha de coleta → factory
  devolve `None` → nada é enviado, com **um** aviso `[jaylog] schedule: …` no stderr
  (schtasks ausente/timeout, returncode ≠ 0, XML malformado).
- Sem permissão para tasks de outros usuários: o `schtasks` lista só o que o usuário do
  processo enxerga; o resultado segue normalmente com o que houver.
- Envio: máquina de estados do `JaylogHostReporter` — 2xx enviado; 404/405 desliga com
  aviso único; 401/403/422 fatal com trecho do corpo; 429/5xx/rede com backoff.

## 6. Contrato HTTP (cópia — ver spec do backend)

`POST {derive(log_http_endpoint, "host-schedules")}`, headers usuais do jaylog, corpo:

```json
{
  "run_id": "uuid",
  "service": "BOT_FATURAMENTO",
  "hostname": "VM-RPA-01",
  "schedules": [
    { "repetition": "SEMANA", "day_of_week": "MONDAY", "day_of_month": null,
      "time": "07:30", "task_path": "\\RPA\\Faturamento" }
  ]
}
```

`schedules` com 1..500 itens. 200 `{deleted, inserted}`; 422 serviço desconhecido ou linha
incoerente; 401 key inválida. O backend substitui só as linhas `TASK_SCHEDULER` do serviço,
em transação.

## 7. Testes

Todos rodam no Linux (CI ubuntu + windows), com `monkeypatch` em `win32`/`subprocess`
como em `tests/test_detect_execution.py`.

- `tests/fixtures/schtasks/*.xml` — XML **exportado de um Windows real** para cada cenário
  da 4.2 e cada caso da 4.5, mais uma saída completa de `schtasks /query /xml` (com
  comentários de path e tasks `\Microsoft\`).
- `tests/test_schedule_parser.py` — cenários 1–9, expansão (meia-noite em
  `DIARIO`/`SEMANA`/`DIA`, teto 24, indefinido múltiplo/não múltiplo de 24 h), filtros
  (desabilitado, `EndBoundary` vencido, fuso), ignorados, consolidação.
- `tests/test_detect_schedule.py` — split por comentário, descarte `\Microsoft\` e
  desabilitadas, decodificação cp850, matching B/D (`.bat` que cita/não cita o script,
  `.bat` ilegível, aspas, `%VAR%`, `normcase`, relativo, duas tasks na mesma pasta),
  gatilho só em `task_scheduler` + win32, `None` em timeout/returncode/XML inválido.
- `tests/test_reporter.py` — `label` na thread/mensagens, `_unsupported_warned` por label,
  `one_shot`, factory `None` não envia nem marca fatal.
- `tests/test_settings.py` / `tests/test_endpoints.py` — endpoint derivado e override.
- `tests/test_logger_lifecycle.py` — start em `configure()`, stop em `shutdown()`.

## 8. Documentação e versão

- `README.md` e jaylog-book: seção "Agendas do Task Scheduler" (o que é coletado, quando,
  formatos suportados, o que é ignorado, variáveis `JAYLOG_HOST_SCHEDULE_*`).
- Versão `0.3.0a5`, `PROTOCOL_VERSION = 4`.

## 9. Ordem de entrega

Backend → front → jaylog (ver spec do backend, seção 8). Neste repo:

1. `entrypoint_path()` + `win32.oem_codepage()`.
2. `schedule_parser` (puro) com fixtures.
3. `detect_schedule` (query + matching + orquestração).
4. `JaylogHostReporter` (`label`, `one_shot`, factory `None`) + `schedule_reporter`.
5. Settings/endpoint/ciclo de vida + `PROTOCOL_VERSION`.
6. Docs e versão.
