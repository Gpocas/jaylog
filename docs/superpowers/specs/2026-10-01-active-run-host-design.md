# Host da execução ativa — ambiente atual independente de logs

- **Data:** 2026-10-01
- **Status:** rascunho
- **Repositórios:** `backend-nn-analytics` (consulta) e `frontend-analytics-logging` (exibição).
  `jaylog` (cliente) **não muda de código**: só testes de regressão; a nota de documentação vai para o `jaylog-book`.
  Este arquivo cobre os três; a seção 4 (contrato HTTP) é a fonte de verdade.
- **Depende de:** `2026-09-30-heartbeat-design.md` (protocolo 5, `last_heartbeat_at`,
  `x-jaylog-host-required` na resposta do heartbeat).

> Código lido em `/home/gpocas/projects/backend-nn-analytics` (`src/routes/logs.ts`,
> `src/db/schema/log_hosts.ts`) e `/home/gpocas/projects/frontend-analytics-logging`
> (`src/pages/Dashboard/`). `GET /logs/hosts?service_id&run_id` já existe e é o que a aba de
> ambiente consome; por isso `/logs/last` só precisa dizer **qual** execução é a ativa.

## 1. Problema

O dashboard mostra o ambiente do serviço (versão do pacote, protocolo, SO, Python, git…) a
partir do host ligado ao **último log** (`logs.host_id`). Consequências:

- Um serviço que subiu com o jaylog novo mas ainda não logou continua exibindo o host da
  execução antiga (por exemplo protocolo 4, mesmo já rodando o 5).
- Um serviço silencioso por muito tempo nunca atualiza o ambiente exibido.
- Mostrar "como o serviço roda agora" e "como estava quando este log foi emitido" é a mesma
  consulta, e não deveriam ser.

O cliente já registra o host de cada execução sem depender de logs (seção 2). O que falta é
o backend/UI **lerem** esse registro como estado atual.

## 2. O que já existe (não muda)

| Peça | Onde | Comportamento |
|---|---|---|
| Registro no startup | `logger.configure()` → `_start_host_reporters` → `JaylogHostReporter` | `POST /logs/host` numa thread daemon por serviço, com backoff (~10 min de insistência) |
| Reenvio sob demanda | `host/reporter.py::request_resend` | Dorme num `Event`; acorda ao ver `x-jaylog-host-required: 1`; debounce de 30 s |
| Gatilho pelo log | `handlers/http_handler.py` | Resposta de `/logs/add` com o header → `request_resend(service)` |
| Gatilho pelo heartbeat | `host/heartbeat_reporter.py:280` | Resposta 2xx de `/logs/heartbeat` com o header → `request_resend(service)` |
| Backend | `POST /logs/heartbeat` | `updated: 0` (host da run desconhecido) → devolve `x-jaylog-host-required: 1` |

Ou seja: **toda execução cria sua linha em `log_hosts` (chave `(run_id, service)`) no
`configure()`, com ou sem logs**, e se recupera sozinha de perda de dados no backend desde
que o serviço chame `heartbeat()` ou logue. Um `HostSyncReporter` periódico foi considerado
e descartado: `protocol_version`, versão do pacote e SO são fixos durante a execução, e o
heartbeat já renova a presença.

## 3. Decisões

| Tema | Decisão |
|---|---|
| Unidade de "ativa" | Por grupo do dashboard: `(service, hostname, username)` |
| Execução ativa | A linha de `log_hosts` do grupo com maior `GREATEST(last_seen_at, last_heartbeat_at)` (`GREATEST` ignora `NULL`) |
| Fallback sem `heartbeat()` | Sem heartbeat, vale `last_seen_at`, que é gravado no `configure()` e a cada reenvio. A execução mais recente a ter iniciado ganha |
| Contrato | `GET /logs/last` ganha `active_run_id` (uuid, anulável) por grupo; nada é removido. O host vem do `GET /logs/hosts` que já existe |
| Histórico | `GET /logs/:id` e o modal de um log continuam mostrando o host do `host_id` daquele log |
| Cliente | Sem mudança de código nem de protocolo (continua 5, sem bump de pacote) |
| Cadência de sync | Só startup + `host-required`. Sem reenvio periódico |

Fora de escopo: reenvio periódico de host; exibir serviços que nunca logaram (o `/logs/last`
parte de `logs`, como já aceito no spec de heartbeat); mudar `STOP_LIMIT_MINUTES`; mudar a
regra de `last_activity_at`.

## 4. Contrato HTTP

`GET /logs/last` — cada item de grupo passa a incluir:

```jsonc
{
  // ...campos atuais (último log, last_activity_at, run_id do último log, etc.)...
  "active_run_id": "<uuid>"   // null se o grupo não tem nenhuma linha em log_hosts
}
```

`run_id` continua sendo o do **último log**; `active_run_id` é a execução ativa. Os dois
divergem justamente no caso que importa: execução nova e silenciosa. O host da execução
ativa é lido com o `GET /logs/hosts?service_id=<id>&run_id=<active_run_id>` que a aba de
ambiente já usa. Nenhuma rota nova e nenhum payload de host duplicado em cada grupo.

Compatibilidade: campo novo e opcional. Frontend antigo o ignora; backend antigo não o
envia e o frontend novo cai em `run_id` (seção 6).

## 5. Backend (`backend-nn-analytics`)

- **Query (`src/routes/logs.ts`, `/logs/last`):** o CTE `heartbeats` já agrega
  `log_hosts` por `(service, hostname, username)`. Ele passa a também escolher a execução
  ativa por grupo, com `DISTINCT ON (service, hostname, username)` ordenado por
  `GREATEST(last_seen_at, last_heartbeat_at) DESC` (`GREATEST` ignora `NULL`), e a juntar
  `active_run_id` ao log **depois** do `DISTINCT ON` dos logs, mesma regra de desempenho
  do heartbeat. O CTE atual filtra `last_heartbeat_at IS NOT NULL`; a execução ativa
  **não** pode ter esse filtro (execução sem heartbeat também conta), então é um CTE à parte.
- **Índice:** já existe `log_hosts_service_host_user_seen_idx (service, hostname, username,
  last_seen_at)`. O `ORDER BY GREATEST(...)` não o usa, mas o volume é pequeno (uma linha
  por execução, limpa em 45 dias). Só indexar se o `EXPLAIN` mostrar custo real.
- **Sem rota nova, sem migration, sem cron novo.** `cleanupLogHosts` já protege hosts com
  heartbeat recente. Host sem heartbeat e sem log por 45 dias é apagado, e nesse prazo
  `cleanupInactiveServices` já exclui o serviço: coerente.
- **Schema OpenAPI:** `logLastSchema` ganha `active_run_id: t.Nullable(t.String())`.

## 6. Frontend (`frontend-analytics-logging`)

- `Dashboard/interfaces.ts`: `Log.active_run_id?: string | null`.
- `LogDetailPage.tsx`: as linhas do dashboard navegam para `/logs/:id`, e `GET /logs/:id` não
  traz `active_run_id` (e não deve: seção 3). A página reaproveita a query do dashboard
  (`queryKeys.logs.last()`), acha a linha com `row.id === log.id` e entrega o log com
  `active_run_id` ao `LogDetail`. Deep link frio busca `/logs/last` uma vez; enquanto não
  chega (ou se falhar, ou se o log não for a última linha do grupo) vale o `run_id` do log.
  A página não espera essa query para renderizar.
- `LogModal.tsx` (`LogDetail`): `<LogEnvironmentTab runId={log.active_run_id ?? log.run_id} />` e
  a aba de métricas usam o mesmo `active_run_id ?? run_id`, para as duas abas falarem da mesma
  execução. Um log que não é a última linha do seu grupo (histórico) segue no host do próprio
  `run_id`.
- `LogEnvironmentTab` ganha a prop opcional `activeRun`; quando `true`, mostra o rótulo
  "Execução ativa", para o usuário não confundir o estado atual com o histórico. O modal a
  passa quando há `active_run_id`. Não existe rótulo "anterior": sem `active_run_id` o
  componente não sabe se o host exibido é o atual.
- `isStopped` não muda.

## 7. Cliente (`jaylog`)

Nenhuma mudança funcional. Para travar o comportamento de que o resto depende:

- Teste: `configure()` dispara o `POST /logs/host` sem nenhum log emitido (já coberto em
  parte; garantir o caso explícito "zero logs").
- Teste: resposta do heartbeat com `x-jaylog-host-required: 1` aciona `request_resend`
  mesmo sem logs no processo.
- Documentação (jaylog-book, `producao/registro-de-ambiente.md`; o README do repo só aponta
  para o book): registrar que o ambiente atual no dashboard vem do startup, e que chamar
  `heartbeat()` é o que faz o serviço se recuperar sozinho se o backend perder o registro
  enquanto ele está silencioso.

## 8. Testes

- **Backend** (sem harness de banco no repo: verificação ponta a ponta no Postgres local,
  como no plano de heartbeat): `/logs/last` com (a) log antigo + execução nova sem logs →
  `active_run_id` é o da nova; (b) duas execuções do mesmo grupo, a mais antiga com
  heartbeat mais recente → vence a de maior `GREATEST`; (c) grupo sem linha em `log_hosts`
  → `active_run_id: null`; (d) `last_heartbeat_at` nulo → vale `last_seen_at`;
  (e) `GET /logs/:id` inalterado.
- **Frontend:** o modal busca `/logs/hosts` com `active_run_id` quando presente e com o
  `run_id` do log quando ausente; `LogDetail` vindo de `/logs/:id` segue usando o `run_id`
  do próprio log; rótulo "Execução ativa" só quando há `active_run_id`.

## 9. Ordem de entrega

1. Backend: campo `active_run_id` em `/logs/last` (aditivo, compatível com tudo).
2. Frontend: aceita o campo opcional e mantém o fallback; pode subir antes ou depois.
3. Cliente (testes) e `jaylog-book` (documentação), a qualquer momento.

## 10. Riscos conhecidos

- **Duas instâncias vivas do mesmo `(service, hostname, username)`:** a de heartbeat mais
  recente vira "ativa" e o host exibido pode alternar entre elas. O dashboard já agrupa
  nesse nível, então a ambiguidade é herdada; documentar.
- **Backend fora do ar no startup por mais de ~10 min:** o `JaylogHostReporter` desiste do
  backoff e dorme até um `host-required`. Se o serviço também não chama `heartbeat()` nem
  loga, o host só aparece quando algum sinal chegar. Aceito: é o mesmo trade-off da opção
  de heartbeat explícito.
- **Serviço que nunca logou** continua fora do dashboard (parte de `logs`).
- **Hostname/username com caixa diferente** entre `logs` e `log_hosts` quebraria o
  casamento; hoje ambos vêm da mesma `identity.hostname()`.
