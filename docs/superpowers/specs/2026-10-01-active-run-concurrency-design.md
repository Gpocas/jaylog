# Execução ativa com execuções simultâneas (adendo)

- **Data:** 2026-10-01
- **Status:** rascunho
- **Repositórios:** `backend-nn-analytics`, `frontend-analytics-logging`, `jaylog-book`.
  `jaylog` (cliente) não muda.
- **Altera:** `2026-10-01-active-run-host-design.md`, seções 3 (decisões), 4 (contrato) e 10
  (riscos). Onde houver conflito, este adendo vale. As duas mudanças ainda estão nas
  branches `feat/active-run-host`, sem merge.

## 1. Problema

Quatro pontos deixados em aberto depois da primeira entrega:

1. **Badge "desatualizado"** da lista usa a versão do jaylog do **último log**, e não a da
   execução ativa.
2. **Rótulo "Execução ativa"** aparece também para serviço parado: o texto afirma "rodando"
   quando só sabemos "a mais recente".
3. **Execuções simultâneas** do mesmo `(service, hostname, username)`: a ativa é a que
   mandou sinal por último, então alterna entre elas, e no reinício o último sinal do
   processo antigo vence o novo. Robôs agendados sobrepõem execuções com frequência.
4. **`/logs/host-stats`** (e as outras leituras "sem `run_id`") escolhem a execução só por
   `last_seen_at`, divergindo do critério da execução ativa.

## 2. Regra única (backend)

- **Atividade** de uma execução = `GREATEST(last_seen_at, last_heartbeat_at)`.
- **Viva** = atividade nos últimos `STOP_LIMIT_MINUTES` (15). O corte é calculado no app
  (`new Date()`) e enviado como `timestamp`, e não com `now()` do banco: os escritores
  gravam `new Date()` em UTC e o `now()` depende do fuso da sessão.
- **Ativa** = entre as vivas, a de `started_at` mais recente; se nenhuma estiver viva, a de
  maior atividade. Desempate final: `log_hosts.id` decrescente.
  Dentro do mesmo `(service, hostname, username)` o relógio é o da mesma máquina, então
  `started_at` é comparável.
- A regra mora em **um helper** (`src/lib/activeRun.ts`) e é usada por `/logs/last`,
  `/logs/hosts`, `/logs/host-stats` e pelo teto de `/logs/host-metrics`. Ninguém mais
  ordena `log_hosts` por conta própria para escolher "a execução do grupo".

## 3. Contrato HTTP

`GET /logs/last` — por grupo, além de `active_run_id` (agora pela regra da seção 2):

```jsonc
{
  "active_run_id": "<uuid>|null",
  "active_jaylog_version": "0.3.0a9|null",   // jaylog_version do host da execução ativa
  "live_run_count": 2                        // execuções vivas do grupo; 0 sem nenhuma
}
```

`GET /logs/hosts` ganha filtros opcionais: `hostname`, `username` (igualdade exata) e
`live` (somente execuções vivas). Com `all_runs=true` a resposta vem ordenada com a
**ativa primeiro**, depois as demais vivas por `started_at` decrescente.
`GET /logs/:id` continua sem esses campos.

## 4. Frontend

- **Badge (item 1):** `active_jaylog_version ?? jaylog_version`, em um helper puro
  `effectiveJaylogVersion(log)`.
- **Página de detalhe:** além de `active_run_id`, copia `live_run_count` e
  `last_activity_at` da linha de `/logs/last` (mesmo mecanismo). Isso também faz o
  `StatusBadge` do detalhe usar a atividade real (hoje usa só o `log_timestamp`).
- **Rótulo (item 2):** execução ativa exibida → "Execução ativa" se `isStopped(log) !== true`,
  senão "Última execução".
- **Simultâneas (item 3):** se `live_run_count > 1`, a aba Ambiente mostra "Há N outras
  execuções ativas neste host." e um seletor (grupo de botões `radiogroup`, lista vinda de
  `/logs/hosts?service_id&hostname&username&all_runs=true&live=true`). Cada opção mostra
  início e PID; a primeira (ativa) vem marcada. Escolher outra troca o host exibido, com o
  rótulo "Execução simultânea". O estado `selectedRunId` fica em `LogDetail` e vale para a
  aba Ambiente **e** para a aba Recursos, para as duas falarem da mesma execução. Volta ao
  padrão quando o serviço ou a execução ativa mudam.

## 5. Fora de escopo

Separar cada processo em linha própria do dashboard; mudar o `STOP_LIMIT_MINUTES`; exibir
serviços que nunca logaram; cliente `jaylog`.

## 6. Testes

- **Backend:** `liveCutoff` (unitário) e roteiro ponta a ponta num Postgres local **com
  fuso `America/Sao_Paulo`**: (a) duas vivas, a de início mais recente vence mesmo com o
  sinal da outra mais novo; (b) reinício com sobreposição; (c) nenhuma viva → maior
  atividade; (d) viva vence morta mesmo que a morta tenha iniciado depois, com fronteira de
  10 min vs 20 min; (e) grupo sem host → `null`/`0`; (f) filtros e ordem de `/logs/hosts`;
  (g) `host-stats` conta o python da ativa.
- **Frontend:** helper da versão, merge na página de detalhe, rótulo vivo/parado, aviso e
  seletor (troca de host e de métricas).

## 7. Riscos conhecidos

- Robô sem `heartbeat()` e sem logs deixa de contar como "vivo" após 15 min, mesmo rodando
  (consistente com o "parado" do dashboard).
- `started_at` errado (relógio da máquina fora do ar) pode eleger a execução errada entre
  vivas da mesma máquina; improvável e corrigível reiniciando.
- `/logs/hosts?live=true` varre `log_hosts` filtrando por serviço/host/usuário; o índice
  `log_hosts_service_host_user_seen_idx` cobre o prefixo.
- O CTE de `/logs/last` continua varrendo todo `log_hosts` (já registrado como ponto de
  medição em produção).
