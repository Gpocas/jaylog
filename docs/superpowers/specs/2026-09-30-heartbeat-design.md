# Heartbeat explícito de serviço — `jaylog.heartbeat()`

- **Data:** 2026-09-30
- **Status:** implementado (cliente 0.3.0a8, protocolo 5)
- **Repositórios:** `jaylog` (este spec, cliente), `backend-nn-analytics` (rota, coluna, crons)
  e `frontend-analytics-logging` (status no dashboard). Este arquivo cobre os três; como nos
  specs anteriores, a seção 5 (contrato HTTP) é a fonte de verdade do contrato.

## 1. Objetivo

Hoje "serviço parado" é decidido só pelo último `log_timestamp`. Um serviço em loop que
não emite logs aparece como parado em 15 min e, sem logs por 45 dias, é excluído pelo cron
`cleanup-inactive-services`. O desenvolvedor passa a poder dizer "o loop está progredindo"
chamando `jaylog.heartbeat()`; o backend usa esse sinal junto com os logs.

Critérios de sucesso:

1. Um serviço que chama `heartbeat()` a cada iteração do loop aparece como **ativo** no
   dashboard sem emitir nenhum log, e não é avisado nem excluído por inatividade.
2. Quem **não** chama `heartbeat()` mantém exatamente o comportamento atual (fallback
   para o último `log_timestamp`).
3. Se o loop travar, o serviço volta a aparecer como parado após o limite de 15 min
   (o heartbeat é chamado **pelo loop**, não por uma thread automática).
4. `heartbeat()` nunca bloqueia, nunca levanta e nunca faz rede na thread do chamador.

Fora de escopo: sinal de fim de execução; intervalo esperado de bots agendados; mudar o
limite de 15 min (`STOP_LIMIT_MINUTES`); usar as métricas de CPU/memória como sinal de vida;
exibir no dashboard serviços que só enviam heartbeat e nunca logaram.

## 2. Decisões tomadas

| Tema                     | Decisão                                                                                     |
|--------------------------|---------------------------------------------------------------------------------------------|
| Sinal                    | Só o `heartbeat()` **explícito** conta. Métricas não entram no status                        |
| Regra de atividade       | `max(último log_timestamp, último heartbeat)`; sem heartbeat, só o log (como hoje)           |
| Thread                   | **Dedicada** (`jaylog-heartbeat`), **uma por processo**, iniciada **na 1ª chamada**          |
| Escopo do beat           | **Por serviço** (`app_name`), não por processo                                               |
| API                      | `heartbeat(service: str \| None = None)`; `None` = 1ª configuração **registrada**            |
| Rede                     | Endpoint, chave, proxy e TLS **do item** de cada serviço, enviados por requisição            |
| Transporte               | `POST /logs/heartbeat` `{run_id, service}`, um POST por serviço com beat pendente            |
| Hora do beat             | **Carimbada pelo servidor** no recebimento; o cliente não envia horário                      |
| Persistência             | Coluna `last_heartbeat_at` em `log_hosts` (uma linha por `(run_id, service)`)                |
| Intervalo                | `host_heartbeat_interval`, padrão 60 s, mínimo 10 s, independente das métricas               |
| Limite de "parado"       | Continua 15 min, global                                                                      |

## 3. Cliente (`jaylog`)

### 3.1 API pública

```python
import jaylog

while True:
    do_work()
    jaylog.heartbeat()                  # 1º serviço registrado
    # jaylog.heartbeat("BILLING")       # serviço específico
```

`heartbeat` é exportada em `jaylog/__init__.py` junto de `configure`, `get_logger` e
`shutdown`. Comportamento:

- Incrementa em memória, sob lock, o contador de beats do serviço (seção 3.3) e retorna.
  Sem rede, sem I/O, sem horário: quem carimba a hora é o servidor.
- Resolve `service=None` para a **primeira chave de `_settings_registry`** (mesma regra do
  `get_logger()` sem nome). Serviço sem configuração, inelegível (sem endpoint/chave,
  `host_report_enabled` ou `host_heartbeat_enabled` desligados) ou chamada antes de
  `configure()` → **no-op** com mensagem em `diagnostics`, nunca exceção.
- Na **primeira** chamada elegível do processo, inicia a thread (seção 3.3).

### 3.2 Configuração (`settings.py`)

| Campo                          | Padrão | Observação                                                      |
|--------------------------------|--------|-----------------------------------------------------------------|
| `host_heartbeat_enabled`       | `True` | Só vale com `host_report_enabled` (o backend liga `run_id` a host) |
| `host_heartbeat_interval`      | `60`   | Validador: mínimo 10 s, como `host_metrics_interval`            |
| `host_heartbeat_http_endpoint` | `None` | Override; senão `effective_host_heartbeat_endpoint` deriva `/logs/add` → `/logs/heartbeat` via `derive_endpoint(..., "heartbeat")` |

O intervalo é por processo: vale o do **primeiro item registrado**, como o das métricas.
Um valor perto ou acima de 15 min faria o serviço aparecer como parado entre envios;
documentar no README, sem travar o valor.

### 3.3 `jaylog/host/heartbeat_reporter.py`

Mesmo padrão de `metrics_reporter.py`: classe injetável (`session`, `clock`, `wait`) e
singleton de módulo `start/stop/active` sob lock.

- **Alvos:** `configure()` registra, para cada item elegível, um `_Target(service,
  endpoint, api_key, proxy, verify, timeout)` no módulo. Não cria thread nem faz rede.
  `shutdown()`/nova `configure()` limpam alvos e estado de beats.
- **Estado:** `dict[app_name, beat_seq]` — um contador por serviço, incrementado por
  `heartbeat()`, mais `sent_seq` por serviço. Pendente = `beat_seq > sent_seq`.
- **Loop da thread** (`jaylog-heartbeat`, daemon):
  `while not stop: enviar_pendentes(); stop.wait(interval)`. A primeira rodada é imediata,
  para a execução nova ficar ativa sem esperar um intervalo.
- **Envio:** por serviço pendente, `POST target.endpoint` com `json={run_id, service}` e
  headers `x-api-key`, `x-jaylog-version`, `x-jaylog-protocol`, `x-jaylog-run-id` **por
  requisição** (a sessão não fixa chave). Proxy/verify/timeout do alvo.
- **Resposta:**
  - 2xx → `sent_seq = beat_seq` lido **antes** do POST (um beat que chegou durante o POST
    continua pendente). Se `x-jaylog-host-required: 1` →
    `host_reporter.request_resend(service)`.
  - Erro de rede, 429 ou ≥ 500 → mantém pendente; tenta no próximo ciclo. Sem buffer nem
    backoff próprio: o ciclo já é de 60 s, e o beat útil é sempre o mais recente.
  - 404/405 → desativa **aquele serviço** e avisa uma vez no stderr (backend sem a rota).
  - Outro 4xx → desativa aquele serviço com aviso e trecho do corpo.
  - Todos os serviços desativados → a thread encerra.
- **`stop()`:** sinaliza, dá `join` e, se a thread terminou, faz um último envio dos
  pendentes (mesmo cuidado do `metrics_reporter.stop`: não disputa com um POST em voo).
  Chamado por `shutdown()`.
- Todos os eventos passam por `diagnostics.emit("heartbeat", ...)`.

### 3.4 Versões

`PROTOCOL_VERSION = 5` (`5: POST /logs/heartbeat`), comentário em `_version.py`. Bump de
pacote para a próxima pré-release (`0.3.0a8`) no plano de implementação.

### 3.5 Threads por processo

Antes: `2N + 1` (+1 no Windows enquanto as agendas rodam). Depois, **só** para quem chama
`heartbeat()`: `2N + 2`. Quem não usa não paga nada.

## 4. Backend (`backend-nn-analytics`)

### 4.1 Migration e schema

`log_hosts.last_heartbeat_at timestamp NULL`, **mesmo tipo e mesmo caminho de escrita de
`last_seen_at`** (`new Date()` pelo drizzle, e não `now()` no SQL). É o que permite
`GREATEST` com `logs.log_timestamp` (também `timestamp` sem fuso) sem conversão de fuso de
sessão. Sem índice novo: o `UPDATE` é pela chave única `(run_id, service)` e a coluna fora
de índice permite atualização HOT. `last_seen_at` **não** é reaproveitado: é indexado e
significa "última sincronização do host".

### 4.2 `POST /logs/heartbeat` (atrás do `apiKeyPlugin`)

- Corpo `{run_id: uuid, service: string}`.
- Busca `services` por `name`. Inexistente → **422** (nunca 404: a lib lê 404/405 como
  "backend sem a rota").
- `UPDATE log_hosts SET last_heartbeat_at = new Date() WHERE run_id = ? AND service = ?`.
- 0 linhas afetadas (host ainda não registrado): 200 `{updated: 0}` com header
  `x-jaylog-host-required: 1`. Caso contrário 200 `{updated: 1}`.
- Sem fila BullMQ: uma escrita pequena por serviço por minuto.

### 4.3 `/logs/last`

Passa a devolver `last_activity_at = GREATEST(log_timestamp do último log, MAX(last_heartbeat_at))`.
O heartbeat é casado por `(service, hostname, username)` — **não** por `host_id`: no caso
que interessa, o serviço silencioso, o último log é de uma execução antiga e o `host_id` dele
não é o da execução atual. Igualdade exata, como o agrupamento do próprio `DISTINCT ON`;
`logs.hostname` e `log_hosts.hostname` vêm da mesma `identity.hostname()` do cliente.
O agregado de heartbeat é calculado **uma vez por grupo** e juntado *depois* do
`DISTINCT ON` (um subselect correlacionado rodaria por linha de log, até 1000 por grupo).
`service_stop_limit` segue igual. `GET /logs/:id` não muda.

### 4.4 Crons

- `cleanupInactiveServices` e `warnInactiveServices`: `last_activity =
  GREATEST(MAX(log_timestamp), MAX(last_heartbeat_at), created_at)`, com o heartbeat por
  subselect agregado por serviço (um segundo `LEFT JOIN` multiplicaria linhas).
  `last_activity_at` gravado em `service_deletion_warnings` passa a refletir isso.
- `cleanupLogHosts`: só apaga host com `last_seen_at` antigo **e** `last_heartbeat_at`
  nulo ou antigo (45 dias) e sem logs. Sem isso, um host que só manda heartbeat seria
  apagado e recriado a cada ciclo de `x-jaylog-host-required`.

## 5. Contrato HTTP

```
POST /logs/heartbeat
x-api-key, x-jaylog-version, x-jaylog-protocol, x-jaylog-run-id
{ "run_id": "<uuid>", "service": "<app_name>" }

200 { "updated": 0|1 }   [+ x-jaylog-host-required: 1 quando updated = 0]
401 chave inválida   422 serviço desconhecido
```

Backend antigo responde 404/405 → o cliente desativa o heartbeat daquele serviço com um
aviso. O resto do jaylog não é afetado.

## 6. Frontend (`frontend-analytics-logging`)

- `Dashboard/utils.ts`: `isStopped` usa `log.last_activity_at ?? log.log_timestamp`. O
  fallback cobre backend antigo e serviços sem heartbeat. Consumidores (`GroupedView`,
  `LogModal`, tabela e filtro parado/ativo do `Dashboard/index.tsx`) não mudam.
- `Dashboard/interfaces.ts`: `Log.last_activity_at` e `LogHost.last_heartbeat_at` opcionais
  (o frontend não tem schema Zod para logs; só a interface TypeScript).
- Colunas "Último log" e "Tempo" seguem em `log_timestamp`.
- `LogEnvironmentTab`: campo "Último heartbeat" ao lado de "Visto por último"
  (requer `last_heartbeat_at` no payload do host).

## 7. Testes

- **Cliente:** `heartbeat()` no-op antes de `configure()`, para serviço desconhecido e
  inelegível; resolução de `service=None`; thread só nasce na 1ª chamada; beat durante o
  POST continua pendente; 2xx/429/5xx/404/4xx por serviço; um serviço desativado não para
  o outro; endpoint e chave por serviço (dois itens com backends distintos); `stop()` envia
  o pendente; `configure()` repetido limpa estado. Tudo com `session`/`clock` injetados,
  sem rede e sem `sleep`.
- **Backend:** rota (422, `updated` 0/1, header de host requerido); `/logs/last` com log
  antigo + heartbeat recente, sem heartbeat, e heartbeat de outra execução do mesmo
  `(service, hostname, username)`; crons (serviço só com heartbeat não é avisado nem
  excluído; host com heartbeat recente não é apagado).
- **Frontend:** `isStopped` com log antigo + `last_activity_at` recente → ativo; sem o
  campo → comportamento atual; aba de ambiente.

## 8. Ordem de entrega

1. Backend (migration, rota, `/logs/last`, crons) — compatível com clientes atuais.
2. Cliente `jaylog` 0.3.0a8 — contra backend antigo degrada para "heartbeat desativado".
3. Frontend — o fallback permite subir antes ou depois do backend.

## 9. Riscos conhecidos

- **Esquecer de chamar `heartbeat()`** não é detectável; o serviço cai no fallback de log.
  É o trade-off assumido da opção explícita.
- **`heartbeat()` no lugar errado** (fora do loop, ou numa thread separada que continua
  viva com o loop travado) anula o sinal. Documentar no README com exemplo.
- **Serviço só com heartbeat, sem nenhum log**, não aparece no dashboard (o `/logs/last`
  parte de `logs`). Aceito.
- **Hostname com caixa diferente** entre `logs` e `log_hosts` quebraria o casamento da
  seção 4.3. Hoje ambos vêm da mesma função; revisar se isso mudar.
- **Bots agendados** continuam "parados" entre execuções; o heartbeat não resolve isso.
