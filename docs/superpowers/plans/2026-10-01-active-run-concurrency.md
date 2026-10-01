# Execução ativa com execuções simultâneas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "execução ativa" do dashboard deixa de alternar entre execuções simultâneas, a badge de versão e o rótulo passam a refletir a execução ativa de verdade, e `host-stats` usa o mesmo critério; quando há mais de uma execução viva, a aba Ambiente avisa e deixa escolher qual ver.

**Architecture:** O backend ganha um helper único (`src/lib/activeRun.ts`) com a regra: *viva* = atividade nos últimos 15 min; *ativa* = entre as vivas a de `started_at` mais recente, senão a de maior atividade. `/logs/last` devolve `active_run_id` (nova regra), `active_jaylog_version` e `live_run_count`; `/logs/hosts` ganha filtros `hostname`, `username`, `live` e ordena a ativa primeiro. O frontend usa esses campos na badge, no rótulo e num seletor de execuções simultâneas, cujo estado vale para as abas Ambiente e Recursos.

**Tech Stack:** Bun + Elysia + Drizzle + Postgres (`backend-nn-analytics`); Bun + React + `bun:test` (`frontend-analytics-logging`); MkDocs/Zensical (`jaylog-book`).

**Spec:** `docs/superpowers/specs/2026-10-01-active-run-concurrency-design.md` (neste repositório); adendo de `2026-10-01-active-run-host-design.md`. Leia os dois antes de começar.

**Repositórios (caminhos absolutos).** As três branches `feat/active-run-host` já existem, com a primeira entrega commitada e sem merge: **continue nelas**, não crie branch nova.

| Sigla | Caminho | Tasks |
|-------|---------|-------|
| `BE`  | `/home/gpocas/projects/backend-nn-analytics` | 1–3 |
| `FE`  | `/home/gpocas/projects/frontend-analytics-logging` | 4–5 |
| `BK`  | `/home/gpocas/misc/jaylog-book` | 6 |

O cliente `jaylog` não muda. Os caminhos nas tasks são relativos à raiz de cada repositório.

## Global Constraints

- **Sem migration, sem rota nova, sem cron novo, sem bump de protocolo ou de pacote.** `PROTOCOL_VERSION` continua `5`.
- **Atividade** = `GREATEST(last_seen_at, last_heartbeat_at)`; **viva** = atividade `>=` agora − `STOP_LIMIT_MINUTES` (15, de `src/db/schema/services.ts`, **não** duplicar o número).
- **Ativa** = entre as vivas, a de `started_at` mais recente (`NULLS LAST`); sem nenhuma viva, a de maior atividade; desempate final `log_hosts.id` decrescente.
- O corte de "viva" é calculado no app e enviado como `${cutoff.toISOString()}::timestamp`. **Nunca** usar `now()` do banco nem passar `Date` cru em `sql\`\``: ambos dependem do fuso da sessão. Os escritores gravam `new Date()` em UTC.
- Ninguém além do helper `src/lib/activeRun.ts` ordena `log_hosts` para escolher "a execução do grupo" (`/logs/last`, `/logs/hosts`, `/logs/host-stats`, teto de `/logs/host-metrics`).
- `GET /logs/:id` **não** muda.
- `/logs/last` ganha **apenas** `active_jaylog_version` (anulável), `live_run_count` (inteiro, 0 sem nenhuma) e a nova regra de `active_run_id`; nenhum campo existente muda ou sai.
- `/logs/hosts` ganha **apenas** os filtros opcionais `hostname`, `username` e `live`; o comportamento sem eles não muda (exceto a ordem: a ativa primeiro dentro de cada grupo).
- Frontend: sem `active_run_id` cai em `run_id`; sem `live_run_count` ou com `<= 1`, não há aviso nem seletor; cores só por tokens semânticos (`test/palette-guard.test.ts`).
- Comentários explicam o *porquê*, em português, no ponto da decisão.
- Commits terminam com a linha `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (segundo `-m`).
- **`BE/.env` aponta `DATABASE_URL` para um Postgres remoto (`34.122.100.238`). Nenhum comando desta implementação pode rodar contra ele.** `bun run typecheck` e `bun test` rodam com `DATABASE_URL=postgresql://x:x@localhost:1/x`; `db:migrate` e o script da Task 3 só rodam com `DATABASE_URL` sobrescrito para o Postgres local descartável (`localhost:55432`, **com fuso `America/Sao_Paulo`**).

## Review Focus

Falhas que o spec implica e que mais provavelmente quebram na prática. Cada linha tem o teste na task dona do código.

1. **Reinício com sobreposição:** o processo antigo manda um último heartbeat *depois* de a nova execução se registrar; a nova continua ativa (Task 3, cenário B).
2. **Viva vs. morta:** uma execução viva e mais antiga vence uma morta que iniciou depois, com a fronteira 10 min × 20 min num banco em fuso `America/Sao_Paulo` (Task 3, cenário D).
3. **Nenhuma viva:** vale a de maior atividade, e não a de maior `started_at` (Task 3, cenário C).
4. **Grupo sem hosts:** `active_run_id`/`active_jaylog_version` nulos, `live_run_count` 0, e a badge cai no `jaylog_version` do log (Tasks 3 e 4).
5. **Serviço que só manda heartbeat, aberto pela página de detalhe:** o `StatusBadge` mostra *running* e o rótulo é "Execução ativa", porque a página copia `last_activity_at` de `/logs/last` (Task 4).
6. **Serviço parado com uma única execução:** rótulo "Última execução", sem aviso nem seletor (Task 5).
7. **`live_run_count > 1` mas a lista de execuções falha ou ainda não chegou:** a aba mostra o host da execução ativa normalmente, sem quebrar e sem seletor (Task 5).

---

## Parte 1 — Backend (`BE`)

> Convenção do repositório: `bun test` cobre só código puro em `src/lib/`; não existe harness de banco. As Tasks 1–2 são validadas por `bun run typecheck` + `bun test`; a Task 3 exercita tudo de ponta a ponta num Postgres local descartável.

### Task 1: Helper da regra e `/logs/last`

**Files:**
- Create: `BE/src/lib/activeRun.ts`
- Create: `BE/src/lib/activeRun.test.ts`
- Modify: `BE/src/routes/logs.ts` (`logLastSchema` ~linhas 47-80; CTE `activeRuns` e select do handler de `/last` ~linhas 336-420; importar o helper no topo)

**Interfaces:**
- Produces (usado pela Task 2): `liveCutoff(now?: Date): Date`, `isLiveRun(cutoff: Date): SQL<boolean>`, `activeRunOrder(cutoff: Date): SQL[]` — todos de `BE/src/lib/activeRun.ts`.
- Produces (usado pelas Tasks 4–5): em cada item de `GET /logs/last`: `active_run_id: string | null`, `active_jaylog_version: string | null`, `live_run_count: number`.

- [ ] **Step 1: Conferir o baseline**

```bash
cd /home/gpocas/projects/backend-nn-analytics
git branch --show-current   # feat/active-run-host
DATABASE_URL=postgresql://x:x@localhost:1/x bun run typecheck && DATABASE_URL=postgresql://x:x@localhost:1/x bun test
```

Expected: branch correta, typecheck limpo, 17 testes passando.

- [ ] **Step 2: Escrever o teste que falha**

`src/lib/activeRun.test.ts`:

```ts
import { describe, expect, test } from "bun:test";
import { STOP_LIMIT_MINUTES } from "../db/schema/services";
import { liveCutoff } from "./activeRun";

describe("liveCutoff", () => {
  test("é o instante dado menos a mesma janela do 'parado' do dashboard", () => {
    const now = new Date("2026-10-01T13:00:00.000Z");
    expect(liveCutoff(now).getTime()).toBe(now.getTime() - STOP_LIMIT_MINUTES * 60_000);
  });

  test("sem argumento usa o relógio do app", () => {
    const before = Date.now();
    const cutoff = liveCutoff().getTime();
    const after = Date.now();
    expect(cutoff).toBeGreaterThanOrEqual(before - STOP_LIMIT_MINUTES * 60_000);
    expect(cutoff).toBeLessThanOrEqual(after - STOP_LIMIT_MINUTES * 60_000);
  });
});
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `DATABASE_URL=postgresql://x:x@localhost:1/x bun test src/lib/activeRun.test.ts`
Expected: FAIL (módulo `./activeRun` não existe).

- [ ] **Step 4: Criar o helper**

`src/lib/activeRun.ts`:

```ts
import { sql, type SQL } from "drizzle-orm";
import { logHosts } from "../db/schema";
import { STOP_LIMIT_MINUTES } from "../db/schema/services";

/**
 * Sinal mais recente de uma execução: o registro/reenvio do host ou o heartbeat.
 * `GREATEST` ignora NULL, então sem heartbeat vale `last_seen_at`.
 */
const activity = sql`GREATEST(${logHosts.last_seen_at}, ${logHosts.last_heartbeat_at})`;

/** Uma execução é "viva" se deu sinal dentro da mesma janela do "parado" do dashboard. */
export function liveCutoff(now: Date = new Date()): Date {
  return new Date(now.getTime() - STOP_LIMIT_MINUTES * 60_000);
}

/**
 * O corte vai como literal `timestamp` (UTC), e não como `Date` cru nem com
 * `now()`: as colunas são `timestamp` sem fuso e os escritores gravam `new Date()`
 * em UTC. `Date` cru ou `now()` passariam pelo fuso da sessão do banco e
 * deslocariam a fronteira em horas. O cast de uma string com `Z` para `timestamp`
 * descarta o fuso, que é o que queremos.
 */
function cutoffSql(cutoff: Date): SQL {
  return sql`${cutoff.toISOString()}::timestamp`;
}

export function isLiveRun(cutoff: Date): SQL<boolean> {
  return sql<boolean>`(${activity} >= ${cutoffSql(cutoff)})`;
}

/**
 * ORDER BY que põe a execução ativa do grupo primeiro. Usar depois de
 * `service, hostname, username` num `DISTINCT ON`, ou sozinho para ler um grupo.
 *
 * 1. vivas antes das mortas;
 * 2. entre as vivas, a que INICIOU por último. É o que impede a alternância entre
 *    execuções simultâneas e o "último heartbeat do processo antigo vence o novo"
 *    no reinício. Dentro do mesmo host o relógio de `started_at` é o da mesma
 *    máquina, então é comparável;
 * 3. sem nenhuma viva (o CASE vale NULL para todas), a de maior atividade;
 * 4. desempate determinístico pelo id (uuidv7).
 */
export function activeRunOrder(cutoff: Date): SQL[] {
  return [
    sql`${isLiveRun(cutoff)} DESC`,
    sql`CASE WHEN ${isLiveRun(cutoff)} THEN ${logHosts.started_at} END DESC NULLS LAST`,
    sql`${activity} DESC`,
    sql`${logHosts.id} DESC`,
  ];
}
```

- [ ] **Step 5: Rodar e ver passar**

Run: `DATABASE_URL=postgresql://x:x@localhost:1/x bun test src/lib/activeRun.test.ts`
Expected: 2 testes passando. Se o import de `../db/schema` tentar abrir conexão com o banco, trocar por importar só `logHosts` de `../db/schema/log_hosts`.

- [ ] **Step 6: Usar o helper em `/logs/last`**

Em `src/routes/logs.ts`, importar no topo (junto dos outros imports de `../lib/...`):

```ts
import { activeRunOrder, isLiveRun, liveCutoff } from "../lib/activeRun";
```

Em `logLastSchema`, logo depois de `active_run_id: t.Nullable(t.String()),`:

```ts
  // jaylog_version do host da execução ativa. A badge "desatualizado" da lista
  // lê este antes do `jaylog_version` do último log, que pode ser de uma
  // execução anterior ainda sem logs.
  active_jaylog_version: t.Nullable(t.String()),
  // Execuções vivas da instância (atividade dentro do limite de "parado"); 0 sem
  // nenhuma. Acima de 1 o dashboard avisa e oferece escolher qual ver.
  live_run_count:       t.Number(),
```

Atualizar o comentário do campo `active_run_id` no schema para: `// Execução ativa do grupo: entre as vivas a que iniciou por último, senão a de maior atividade (ver lib/activeRun.ts).`

No handler de `/last`, trocar o bloco do CTE `activeRuns` (o comentário e o `db.$with("active_runs")...`) por:

```ts
          // Execução ativa por instância (regra em lib/activeRun.ts). Sem filtro de
          // heartbeat: uma execução que nunca chamou jaylog.heartbeat() concorre por
          // `last_seen_at`. O count em janela enxerga TODAS as execuções do grupo
          // (a janela é avaliada antes do DISTINCT ON), então `live_run_count`
          // independe de qual linha o DISTINCT ON mantém.
          const cutoff = liveCutoff();
          const activeRuns = db.$with("active_runs").as(
            db
              .selectDistinctOn([logHosts.service, logHosts.hostname, logHosts.username], {
                service: logHosts.service,
                hostname: logHosts.hostname,
                username: logHosts.username,
                active_run_id: sql<string>`${logHosts.run_id}`.as("active_run_id"),
                active_jaylog_version: sql<string>`${logHosts.jaylog_version}`.as("active_jaylog_version"),
                live_run_count: sql<number>`(count(*) FILTER (WHERE ${isLiveRun(cutoff)}) OVER (PARTITION BY ${logHosts.service}, ${logHosts.hostname}, ${logHosts.username}))::int`.as("live_run_count"),
              })
              .from(logHosts)
              .orderBy(logHosts.service, logHosts.hostname, logHosts.username, ...activeRunOrder(cutoff)),
          );
```

Na lista de colunas do select, logo depois de `active_run_id: activeRuns.active_run_id,`:

```ts
              active_jaylog_version: activeRuns.active_jaylog_version,
              // Grupo sem nenhum host: o LEFT JOIN devolve NULL, e o contrato diz 0.
              live_run_count: sql<number>`coalesce(${activeRuns.live_run_count}, 0)`.mapWith(Number),
```

- [ ] **Step 7: Typecheck e suíte**

Run: `DATABASE_URL=postgresql://x:x@localhost:1/x bun run typecheck && DATABASE_URL=postgresql://x:x@localhost:1/x bun test`
Expected: typecheck limpo e todos os testes passando (17 anteriores + 2 novos). Se o typecheck reclamar do spread `...activeRunOrder(cutoff)` em `.orderBy`, passar `...activeRunOrder(cutoff)` depois dos três campos (a assinatura aceita `(SQL | Column)[]`).

- [ ] **Step 8: Commit**

```bash
git add src/lib/activeRun.ts src/lib/activeRun.test.ts src/routes/logs.ts
git commit -m "feat(logs): pick the active run by newest start among live runs" -m "A ativa deixava de ser estável com execuções simultâneas (alternava pelo último sinal, e no reinício o heartbeat do processo antigo vencia o novo). Agora vale a execução viva que iniciou por último, e /logs/last devolve active_jaylog_version e live_run_count." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `/logs/hosts` com filtros e o mesmo critério em `host-stats`/`host-metrics`

**Files:**
- Modify: `BE/src/routes/logs.ts` (handlers `GET /host-metrics` — a ordenação do teto —, `GET /hosts` e `GET /host-stats`)

**Interfaces:**
- Consumes: `liveCutoff`, `isLiveRun`, `activeRunOrder` (Task 1).
- Produces (usado pela Task 5): `GET /logs/hosts?service_id&hostname&username&all_runs=true&live=true` → hosts vivos do grupo, a ativa primeiro.

- [ ] **Step 1: `GET /hosts`**

No handler `/hosts`, depois de `if (query.run_id) filters.push(...)`:

```ts
          const cutoff = liveCutoff();
          if (query.hostname) filters.push(eq(logHosts.hostname, query.hostname));
          if (query.username) filters.push(eq(logHosts.username, query.username));
          // Só as execuções vivas: alimenta o seletor de execuções simultâneas.
          if (query.live) filters.push(isLiveRun(cutoff));
```

Trocar o `.orderBy(logHosts.service, logHosts.hostname, logHosts.username, desc(logHosts.last_seen_at))` por:

```ts
            // A ativa primeiro dentro de cada grupo (mesma regra do /logs/last), e não
            // a de last_seen_at mais recente.
            .orderBy(logHosts.service, logHosts.hostname, logHosts.username, ...activeRunOrder(cutoff))
```

No schema `query` da rota `/hosts`, acrescentar:

```ts
            hostname: t.Optional(t.String()),
            username: t.Optional(t.String()),
            live: t.Optional(looseBoolean),
```

- [ ] **Step 2: `GET /host-stats`**

Trocar o `.orderBy(logHosts.service, logHosts.hostname, logHosts.username, desc(logHosts.last_seen_at))` por:

```ts
            // Uma execução por instância, a mesma que o dashboard chama de ativa;
            // por last_seen_at as estatísticas contariam outra execução que a tela.
            .orderBy(logHosts.service, logHosts.hostname, logHosts.username, ...activeRunOrder(liveCutoff()))
```

- [ ] **Step 3: `GET /host-metrics` (teto de recursos)**

Na consulta de `limits`, trocar `.orderBy(desc(logHosts.last_seen_at))` por `.orderBy(...activeRunOrder(liveCutoff()))`, com o comentário: `// Sem run_id, o teto exibido é o da execução ativa (mesma regra do dashboard).`

- [ ] **Step 4: Limpar imports e checar**

Se `desc` não for mais usado em nenhum ponto de `logs.ts`, remover do import de `drizzle-orm` (provavelmente ainda é usado: confira com `grep -n "desc(" src/routes/logs.ts`).

Run: `DATABASE_URL=postgresql://x:x@localhost:1/x bun run typecheck && DATABASE_URL=postgresql://x:x@localhost:1/x bun test`
Expected: typecheck limpo, 19 testes passando.

- [ ] **Step 5: Commit**

```bash
git add src/routes/logs.ts
git commit -m "feat(logs): filter /logs/hosts by instance and align host-stats with the active run" -m "/logs/hosts aceita hostname, username e live e devolve a execução ativa primeiro; host-stats e o teto de host-metrics escolhem a execução pela mesma regra do dashboard." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Verificação de ponta a ponta no Postgres local (fuso São Paulo)

**Files:**
- Create (NÃO commitar): `BE/tmp-verify-active-run-2.ts`

**Interfaces:**
- Consumes: Tasks 1–2.

- [ ] **Step 1: Subir um Postgres local descartável com fuso `America/Sao_Paulo` e migrar**

```bash
cd /home/gpocas/projects/backend-nn-analytics
docker run -d --name arc-verify-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=arc -p 55432:5432 postgres:18 -c timezone=America/Sao_Paulo
export ARC_DB=postgresql://postgres:pw@localhost:55432/arc
sleep 5
docker exec arc-verify-pg psql -U postgres -d arc -c "SHOW TimeZone"
DATABASE_URL=$ARC_DB DATABASE_SSL=false bun db:migrate
```

Expected: `SHOW TimeZone` imprime `America/Sao_Paulo` e as migrations aplicam sem erro. **Conferir que a URL usada é `localhost:55432`.** Se a porta estiver ocupada, `docker ps -a` e remover o container de verificação antigo.

- [ ] **Step 2: Escrever o script**

`tmp-verify-active-run-2.ts` (recusa rodar fora do Postgres local):

```ts
import assert from "node:assert/strict";
import { app } from "./src/app";
import { db } from "./src/db";
import { logHosts, logs, services, sessions, users } from "./src/db/schema";

if (!/localhost:55432/.test(process.env.DATABASE_URL ?? "")) {
  throw new Error(`recusado: DATABASE_URL não é o Postgres local de verificação (${process.env.DATABASE_URL})`);
}

const sha = (v: string) => new Bun.CryptoHasher("sha256").update(v).digest("hex");
const SEC = 1000, MIN = 60 * SEC, HOUR = 60 * MIN, DAY = 24 * HOUR;
const ago = (ms: number) => new Date(Date.now() - ms);
const uuid = (n: number) => {
  const h = n.toString(16);
  return `${h.repeat(8)}-${h.repeat(4)}-4${h.repeat(3)}-8${h.repeat(3)}-${h.repeat(12)}`;
};
const [R1, R2, R3, R4, R5, R6, R7, R8, R9, R10] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(uuid);

const [owner] = await db.insert(users).values({ name: "verify", email: "v@x.com", password: "x", profile: "admin" }).returning();
await db.insert(sessions).values({ user_id: owner!.id, token_hash: sha("tok"), expires_at: new Date(Date.now() + DAY) });

const mkService = async (name: string) => (await db.insert(services).values({ name, owner: owner!.id }).returning())[0]!;
// last_seen_at e started_at sempre explícitos: o default do banco (now()) usa o fuso
// da sessão, justamente o que este roteiro quer provar que a regra não usa.
const mkHost = (service: string, run_id: string, extra: Partial<typeof logHosts.$inferInsert> = {}) =>
  db.insert(logHosts).values({
    run_id, service, protocol_version: 5, jaylog_version: "0.3.0a9", hostname: "H1", username: "U1",
    started_at: new Date(), last_seen_at: new Date(), ...extra,
  });
const mkLog = (service: string, run_id: string | null) =>
  db.insert(logs).values({ service, hostname: "H1", username: "U1", ipv4: "1.1.1.1", log_level: "INFO", log_message: "x", log_timestamp: ago(30 * MIN), run_id });
const get = (path: string) => app.handle(new Request(`http://localhost${path}`, { headers: { authorization: "Bearer tok" } }));

type Row = { service: string; active_run_id: string | null; active_jaylog_version: string | null; live_run_count: number };
const lastRow = async (serviceId: string) => {
  const res = await get("/logs/last");
  assert.equal(res.status, 200);
  const row = ((await res.json()) as Row[]).find((r) => r.service === serviceId);
  assert.ok(row, "o grupo aparece em /logs/last");
  return row;
};
const runIds = async (serviceId: string, extra = "") => {
  const res = await get(`/logs/hosts?service_id=${serviceId}&hostname=H1&username=U1&all_runs=true${extra}`);
  assert.equal(res.status, 200);
  return ((await res.json()) as { run_id: string }[]).map((h) => h.run_id);
};

// A: duas vivas; a de início mais recente vence mesmo com o sinal da outra mais novo.
const a = await mkService("VERIFY-A");
await mkHost(a.id, R1, { started_at: ago(2 * HOUR), last_seen_at: ago(2 * HOUR), last_heartbeat_at: ago(10 * SEC), jaylog_version: "0.3.0a5" });
await mkHost(a.id, R2, { started_at: ago(1 * HOUR), last_seen_at: ago(5 * MIN), last_heartbeat_at: ago(4 * MIN), jaylog_version: "0.3.0a9" });
await mkLog(a.id, R1);
let row = await lastRow(a.id);
assert.equal(row.active_run_id, R2, "A: vence a que iniciou por último");
assert.equal(row.active_jaylog_version, "0.3.0a9", "A: versão é a da execução ativa");
assert.equal(row.live_run_count, 2, "A: duas vivas");
assert.deepEqual(await runIds(a.id, "&live=true"), [R2, R1], "A: lista viva, ativa primeiro");
console.log("OK A duas vivas");

// B: reinício com sobreposição; o processo antigo ainda manda heartbeat e a nova acabou de registrar.
const b = await mkService("VERIFY-B");
await mkHost(b.id, R3, { started_at: ago(3 * HOUR), last_seen_at: ago(3 * HOUR), last_heartbeat_at: ago(5 * SEC) });
await mkHost(b.id, R4, { started_at: new Date(), last_seen_at: new Date() });
await mkLog(b.id, R3);
row = await lastRow(b.id);
assert.equal(row.active_run_id, R4, "B: a nova assume mesmo com o último sinal da antiga mais recente");
assert.equal(row.live_run_count, 2);
console.log("OK B reinício com sobreposição");

// C: nenhuma viva; vale a de maior atividade, não a de início mais recente.
const c = await mkService("VERIFY-C");
await mkHost(c.id, R5, { started_at: ago(5 * HOUR), last_seen_at: ago(5 * HOUR), last_heartbeat_at: ago(2 * HOUR) });
await mkHost(c.id, R6, { started_at: ago(4 * HOUR), last_seen_at: ago(3 * HOUR) });
await mkLog(c.id, R5);
row = await lastRow(c.id);
assert.equal(row.active_run_id, R5, "C: sem vivas, maior atividade");
assert.equal(row.live_run_count, 0);
assert.deepEqual(await runIds(c.id, "&live=true"), [], "C: filtro live não devolve mortas");
assert.equal((await runIds(c.id)).length, 2, "C: sem live devolve as duas");
console.log("OK C nenhuma viva");

// D: viva vence morta mesmo que a morta tenha iniciado depois. Fronteira 10 min x 20 min com o banco em São Paulo.
const d = await mkService("VERIFY-D");
await mkHost(d.id, R7, { started_at: ago(3 * HOUR), last_seen_at: ago(3 * HOUR), last_heartbeat_at: ago(10 * MIN) });
await mkHost(d.id, R8, { started_at: ago(1 * HOUR), last_seen_at: ago(20 * MIN) });
await mkLog(d.id, R7);
row = await lastRow(d.id);
assert.equal(row.active_run_id, R7, "D: viva (10 min) vence morta (20 min)");
assert.equal(row.live_run_count, 1);
assert.deepEqual(await runIds(d.id, "&live=true"), [R7]);
assert.deepEqual(await runIds(d.id), [R7, R8], "D: sem live, ativa primeiro");
console.log("OK D viva vence morta (fuso São Paulo)");

// E: grupo sem nenhum host.
const e = await mkService("VERIFY-E");
await mkLog(e.id, null);
row = await lastRow(e.id);
assert.equal(row.active_run_id, null);
assert.equal(row.active_jaylog_version, null);
assert.equal(row.live_run_count, 0);
console.log("OK E grupo sem host");

// F: host-stats conta o python da execução ativa, não o da de sinal mais recente.
const f = await mkService("VERIFY-F");
await mkHost(f.id, R9, { started_at: ago(2 * HOUR), last_seen_at: ago(2 * HOUR), last_heartbeat_at: ago(10 * SEC), python_version: "3.10" });
await mkHost(f.id, R10, { started_at: ago(1 * HOUR), last_seen_at: ago(1 * MIN), python_version: "3.12" });
const stats = (await (await get("/logs/host-stats")).json()) as { python_version: { value: string; count: number }[] };
assert.deepEqual(stats.python_version, [{ value: "3.12", count: 1 }], "F: host-stats conta a ativa");
console.log("OK F host-stats");
process.exit(0);
```

- [ ] **Step 3: Rodar contra o Postgres local**

Run: `DATABASE_URL=$ARC_DB DATABASE_SSL=false bun tmp-verify-active-run-2.ts`
Expected: imprime `OK A…`, `OK B…`, `OK C…`, `OK D…`, `OK E…`, `OK F…` e sai com código 0. Se `/logs/last` devolver 500 de validação de resposta, falta `active_jaylog_version`/`live_run_count` no `logLastSchema` (Task 1). Se o cenário D falhar só por causa da fronteira, o corte está passando pelo fuso do banco: conferir que `cutoffSql` usa `::timestamp` sobre `toISOString()`. Se o cenário A devolver ordem invertida em `runIds`, o `ORDER BY` de `/logs/hosts` não está usando `activeRunOrder`.

- [ ] **Step 4: Limpar**

```bash
docker rm -f arc-verify-pg
rm tmp-verify-active-run-2.ts
git status --short
```

Expected: `git status` limpo (nada a commitar nesta task). Guarde uma cópia do script e a saída completa do Step 3 no relatório da task, para a revisão.

---

## Parte 2 — Frontend (`FE`)

### Task 4: Badge de versão, campos novos e página de detalhe

**Files:**
- Modify: `FE/src/pages/Dashboard/interfaces.ts` (interface `Log`, após `active_run_id`)
- Modify: `FE/src/pages/Dashboard/utils.ts` (helper novo)
- Modify: `FE/src/pages/Dashboard/index.tsx` (badge, ~linhas 441-451)
- Modify: `FE/src/pages/Dashboard/LogDetailPage.tsx` (merge de campos da linha de `/logs/last`)
- Test: `FE/src/pages/Dashboard/utils.test.ts`, `FE/src/pages/Dashboard/LogDetailPage.test.tsx`

**Interfaces:**
- Consumes: `active_jaylog_version`, `live_run_count`, `last_activity_at` de `GET /logs/last` (Task 1).
- Produces (usado pela Task 5): `Log.live_run_count?: number`; o `Log` entregue ao `LogDetail` pela página já traz `active_run_id`, `live_run_count` e `last_activity_at`. Também `effectiveJaylogVersion(log)` em `utils.ts`.

- [ ] **Step 1: Conferir o baseline**

```bash
cd /home/gpocas/projects/frontend-analytics-logging
git branch --show-current   # feat/active-run-host
bun test && bun run typecheck
```

Expected: 442 testes passando, typecheck limpo.

- [ ] **Step 2: Escrever os testes que falham**

Em `utils.test.ts`, acrescentar (seguindo os imports e o estilo do arquivo; importar `effectiveJaylogVersion` de `./utils`):

```ts
describe("effectiveJaylogVersion", () => {
	test("prefere a versão da execução ativa à do último log", () => {
		expect(effectiveJaylogVersion({ active_jaylog_version: "0.3.0a9", jaylog_version: "0.3.0a5" })).toBe("0.3.0a9");
	});

	test("sem execução ativa cai na versão do último log", () => {
		expect(effectiveJaylogVersion({ active_jaylog_version: null, jaylog_version: "0.3.0a5" })).toBe("0.3.0a5");
		expect(effectiveJaylogVersion({ jaylog_version: "0.3.0a5" })).toBe("0.3.0a5");
	});

	test("sem nenhuma das duas devolve undefined", () => {
		expect(effectiveJaylogVersion({})).toBeUndefined();
	});
});
```

Em `LogDetailPage.test.tsx`, seguindo os testes de "execução ativa" que já existem no arquivo (os que ligam `useRealLogDetail = true` e respondem `/logs/last` com uma linha de mesmo `id`), acrescentar:

```tsx
	test("copia last_activity_at e live_run_count: serviço que só manda heartbeat aparece como running", async () => {
		useRealLogDetail = true;
		const hora = 60 * 60_000;
		const detail = {
			...log,
			log_timestamp: new Date(Date.now() - 2 * hora).toISOString(), // último LOG há 2 h
			service_stop_limit: 15,
		};
		const lastRow = {
			...detail,
			last_activity_at: new Date(Date.now() - 30_000).toISOString(), // heartbeat há 30 s
			active_run_id: "d5a9c0b2-8e3f-4a24-b6e9-9a7c2f3d4e5b",
			live_run_count: 1,
		};
		get.mockImplementation((path) => {
			if (path === "/logs/last") return Promise.resolve([lastRow]);
			if (path.startsWith("/logs/hosts")) return Promise.resolve([]);
			if (path.startsWith(`/logs/${LOG_ID}`)) return Promise.resolve(detail);
			return Promise.resolve([]);
		});

		renderWithProviders(<LogDetailPage />);

		await waitFor(() => expect(screen.getByText("running")).toBeDefined());
		expect(screen.queryByText("stopped")).toBeNull();
	});

	test("sem a linha em /logs/last o status segue o último log", async () => {
		useRealLogDetail = true;
		const detail = {
			...log,
			log_timestamp: new Date(Date.now() - 2 * 60 * 60_000).toISOString(),
			service_stop_limit: 15,
		};
		get.mockImplementation((path) => {
			if (path === "/logs/last") return Promise.resolve([]);
			if (path.startsWith("/logs/hosts")) return Promise.resolve([]);
			if (path.startsWith(`/logs/${LOG_ID}`)) return Promise.resolve(detail);
			return Promise.resolve([]);
		});

		renderWithProviders(<LogDetailPage />);

		await waitFor(() => expect(screen.getByText("stopped")).toBeDefined());
	});
```

Se o `StatusBadge` aparecer mais de uma vez na página real e `getByText` reclamar de múltiplos elementos, trocar por `getAllByText(...).length > 0` e manter a asserção de ausência do outro estado.

- [ ] **Step 3: Rodar e ver falhar**

Run: `bun test src/pages/Dashboard/utils.test.ts src/pages/Dashboard/LogDetailPage.test.tsx`
Expected: FAIL em `effectiveJaylogVersion` (não existe) e no teste "running" (o detalhe usa só `log_timestamp`, então mostra *stopped*). O teste "sem a linha…" deve passar desde já.

- [ ] **Step 4: Implementar**

`interfaces.ts`, na interface `Log`, logo depois de `active_run_id`:

```ts
	/**
	 * `jaylog_version` do host da execução ativa (`GET /logs/last`). A badge da
	 * lista usa este antes do `jaylog_version` do último log, que pode ser de uma
	 * execução anterior que ainda não logou.
	 */
	active_jaylog_version?: string | null;
	/**
	 * Execuções vivas da instância (atividade dentro de `service_stop_limit`
	 * minutos); 0 sem nenhuma. Acima de 1 a aba Ambiente avisa e deixa escolher.
	 */
	live_run_count?: number;
```

`utils.ts`, junto de `isStopped`:

```ts
/**
 * Versão do jaylog exibida na lista: a da execução ativa (que pode estar rodando
 * sem ter logado ainda) e, sem ela, a do último log.
 */
export function effectiveJaylogVersion(
  log: Pick<Log, "active_jaylog_version" | "jaylog_version">,
): string | undefined {
  return log.active_jaylog_version ?? log.jaylog_version;
}
```

`index.tsx`: importar `effectiveJaylogVersion` junto dos outros imports de `./utils` e, dentro do IIFE da badge, substituir:

```tsx
                            const version = effectiveJaylogVersion(log);
                            const outdated = !version || isVersionOutdated(version, jaylogLastVersion);
```

e `{log.jaylog_version ?? "?"}` por `{version ?? "?"}`.

`LogDetailPage.tsx`, no `useMemo` que monta `log`:

```tsx
  const log = useMemo(
    () =>
      detail && lastRow
        ? {
            ...detail,
            active_run_id: lastRow.active_run_id,
            live_run_count: lastRow.live_run_count,
            // O /logs/:id só traz o `log_timestamp`. Sem a atividade (log OU
            // heartbeat) o StatusBadge e o rótulo "Última execução" tratariam como
            // parado um serviço que só manda heartbeat, ao contrário da listagem.
            last_activity_at: lastRow.last_activity_at,
          }
        : detail,
    [detail, lastRow],
  );
```

- [ ] **Step 5: Rodar e ver passar**

Run: `bun test && bun run typecheck`
Expected: toda a suíte passa e typecheck limpo.

- [ ] **Step 6: Commit**

```bash
git add src/pages/Dashboard/interfaces.ts src/pages/Dashboard/utils.ts src/pages/Dashboard/utils.test.ts src/pages/Dashboard/index.tsx src/pages/Dashboard/LogDetailPage.tsx src/pages/Dashboard/LogDetailPage.test.tsx
git commit -m "feat(dashboard): version badge and status follow the active run" -m "A badge de versão da lista usa a versão da execução ativa; a página de detalhe passa a copiar live_run_count e last_activity_at de /logs/last, então o status do detalhe considera o heartbeat como a listagem." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Rótulo vivo/parado e seletor de execuções simultâneas

**Files:**
- Modify: `FE/src/lib/queryKeys.ts` (`LogLiveRunsParams` e `logs.liveRuns`)
- Modify: `FE/src/pages/Dashboard/components/LogEnvironmentTab.tsx` (props, rótulo, aviso e seletor)
- Modify: `FE/src/pages/Dashboard/components/LogModal.tsx` (estado `selectedRunId`, chamadas da aba Ambiente e da aba Recursos)
- Test: `FE/src/pages/Dashboard/components/LogModal.test.tsx`

**Interfaces:**
- Consumes: `Log.active_run_id`, `Log.live_run_count`, `isStopped(log)` (Task 4); `GET /logs/hosts?service_id&hostname&username&all_runs=true&live=true` (Task 2).
- Produces: `LogEnvironmentTab` com as props `{ serviceId, hostname, username, runId?, activeRunId?, liveRunCount?, stopped?, onSelectRun? }` (a prop `activeRun` deixa de existir).

- [ ] **Step 1: Escrever os testes que falham**

Em `LogModal.test.tsx`, junto das constantes de topo (depois de `hostCalls`), acrescentar:

```tsx
const SIBLING_RUN_ID = "e6b0d1c3-9f40-4b35-a7fa-0b8d3a4e5f6c";
const THIRD_RUN_ID = "f7c1e2d4-0a51-4c46-b80b-1c9e4b5f6a7d";

const LIVE_RUNS: LogHost[] = [
	{ ...EXECUTION_HOST, id: "h-a", run_id: ACTIVE_RUN_ID, started_at: "2026-08-30T09:58:00Z", process_id: 111 },
	{ ...EXECUTION_HOST, id: "h-b", run_id: SIBLING_RUN_ID, started_at: "2026-08-30T09:50:00Z", process_id: 222 },
	{ ...EXECUTION_HOST, id: "h-c", run_id: THIRD_RUN_ID, started_at: "2026-08-30T09:40:00Z", process_id: 333 },
];

/** Responde /logs/hosts como o backend: a lista viva com all_runs, ou o host do run_id pedido. */
const hostsResponder = async (path: string) => {
	if (path.startsWith("/logs/host-metrics")) return HOST_METRICS as never;
	if (!path.startsWith("/logs/hosts")) return [] as never;
	if (path.includes("all_runs=true")) return LIVE_RUNS as never;
	const runId = new URL(path, "http://x").searchParams.get("run_id");
	return LIVE_RUNS.filter((host) => host.run_id === runId) as never;
};

const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();
const openEnvironment = () =>
	fireEvent.mouseDown(screen.getByRole("tab", { name: "Ambiente da execução" }), { button: 0 });
```

Dentro de `describe("LogDetail / ambiente da execução", ...)`, acrescentar:

```tsx
	test("serviço parado rotula a execução como 'Última execução'", async () => {
		getSpy.mockImplementation(hostsResponder);
		renderWithProviders(
			<LogDetail
				log={{
					...EXECUTION_LOG,
					active_run_id: ACTIVE_RUN_ID,
					service_stop_limit: 15,
					last_activity_at: minutesAgo(120),
				}}
				onClose={() => {}}
			/>,
		);
		openEnvironment();

		await waitFor(() => expect(screen.getByText("Última execução")).toBeTruthy());
		expect(screen.queryByText("Execução ativa")).toBeNull();
	});

	test("serviço com atividade recente rotula a execução como 'Execução ativa'", async () => {
		getSpy.mockImplementation(hostsResponder);
		renderWithProviders(
			<LogDetail
				log={{
					...EXECUTION_LOG,
					active_run_id: ACTIVE_RUN_ID,
					service_stop_limit: 15,
					last_activity_at: minutesAgo(1),
				}}
				onClose={() => {}}
			/>,
		);
		openEnvironment();

		await waitFor(() => expect(screen.getByText("Execução ativa")).toBeTruthy());
		expect(screen.queryByText("Última execução")).toBeNull();
	});

	test("com uma única execução viva não há aviso nem seletor", async () => {
		getSpy.mockImplementation(hostsResponder);
		renderWithProviders(
			<LogDetail log={{ ...EXECUTION_LOG, active_run_id: ACTIVE_RUN_ID, live_run_count: 1 }} onClose={() => {}} />,
		);
		openEnvironment();

		await waitFor(() => expect(screen.getByText("Execução ativa")).toBeTruthy());
		expect(screen.queryByRole("radiogroup")).toBeNull();
		expect(hostCalls().some((path) => path.includes("all_runs=true"))).toBe(false);
	});

	test("com execuções simultâneas avisa e lista as vivas do host", async () => {
		getSpy.mockImplementation(hostsResponder);
		renderWithProviders(
			<LogDetail log={{ ...EXECUTION_LOG, active_run_id: ACTIVE_RUN_ID, live_run_count: 3 }} onClose={() => {}} />,
		);
		openEnvironment();

		await waitFor(() => expect(screen.getByText("Há outras 2 execuções ativas neste host.")).toBeTruthy());
		expect(hostCalls()).toContain(
			"/logs/hosts?service_id=svc-1&hostname=host-1&username=user-1&all_runs=true&live=true",
		);
		const radios = await screen.findAllByRole("radio");
		expect(radios).toHaveLength(3);
		expect(radios[0]!.getAttribute("aria-checked")).toBe("true");
		expect(radios[0]!.textContent).toContain("PID 111");
		expect(radios[0]!.textContent).toContain("ativa");
		expect(radios[1]!.getAttribute("aria-checked")).toBe("false");
	});

	test("com exatamente uma outra execução o aviso fica no singular", async () => {
		getSpy.mockImplementation(hostsResponder);
		renderWithProviders(
			<LogDetail log={{ ...EXECUTION_LOG, active_run_id: ACTIVE_RUN_ID, live_run_count: 2 }} onClose={() => {}} />,
		);
		openEnvironment();

		await waitFor(() => expect(screen.getByText("Há outra execução ativa neste host.")).toBeTruthy());
	});

	test("escolher outra execução troca o host exibido e as métricas acompanham", async () => {
		getSpy.mockImplementation(hostsResponder);
		renderWithProviders(
			<LogDetail log={{ ...EXECUTION_LOG, active_run_id: ACTIVE_RUN_ID, live_run_count: 3 }} onClose={() => {}} />,
		);
		openEnvironment();
		const radios = await screen.findAllByRole("radio");

		fireEvent.click(radios[1]!);

		await waitFor(() =>
			expect(hostCalls()).toContain(`/logs/hosts?service_id=svc-1&run_id=${SIBLING_RUN_ID}`),
		);
		await waitFor(() => expect(screen.getByText("Execução simultânea")).toBeTruthy());
		expect(screen.getAllByRole("radio")[1]!.getAttribute("aria-checked")).toBe("true");

		fireEvent.mouseDown(screen.getByRole("tab", { name: "Recursos" }), { button: 0 });
		await waitFor(() =>
			expect(getSpy).toHaveBeenCalledWith(
				`/logs/host-metrics?service=svc-1&hostname=host-1&username=user-1&run_id=${SIBLING_RUN_ID}`,
			),
		);
	});

	test("se a lista das simultâneas falha, o host da execução ativa continua aparecendo", async () => {
		getSpy.mockImplementation(async (path: string) => {
			if (path.includes("all_runs=true")) throw new Error("falhou");
			return hostsResponder(path);
		});
		renderWithProviders(
			<LogDetail log={{ ...EXECUTION_LOG, active_run_id: ACTIVE_RUN_ID, live_run_count: 3 }} onClose={() => {}} />,
		);
		openEnvironment();

		await waitFor(() => expect(screen.getByText("Python e ambiente virtual")).toBeTruthy());
		expect(screen.queryByRole("radiogroup")).toBeNull();
	});
```

Os testes antigos "mostra o ambiente da execução ativa…" e os dois de `Recursos` com `active_run_id` continuam valendo sem alteração.

- [ ] **Step 2: Rodar e ver falhar**

Run: `bun test src/pages/Dashboard/components/LogModal.test.tsx`
Expected: FAIL nos testes novos (rótulo "Última execução", aviso, seletor e troca de métricas ainda não existem).

- [ ] **Step 3: Implementar a chave de query**

`queryKeys.ts`, junto de `LogHostParams`:

```ts
export interface LogLiveRunsParams {
	serviceId: string;
	hostname: string;
	username: string;
}
```

e dentro de `logs`, depois de `host`:

```ts
		liveRuns: (params: LogLiveRunsParams) =>
			[...queryKeys.logs.all, "live-runs", params] as const,
```

- [ ] **Step 4: Implementar a aba**

Em `LogEnvironmentTab.tsx`, acrescentar, antes de `LogEnvironmentTab`, as funções de apoio:

```tsx
/**
 * Rótulo da execução exibida. "Execução ativa" só enquanto o serviço não está
 * parado (a mesma regra do StatusBadge); parado, é apenas a última que conhecemos.
 */
function runLabel(
  runId: string | null | undefined,
  activeRunId: string | null | undefined,
  stopped: boolean | null,
): string | null {
  if (!activeRunId) return null;
  if (runId !== activeRunId) return "Execução simultânea";
  return stopped === true ? "Última execução" : "Execução ativa";
}

function SimultaneousRuns({
  count,
  runs,
  selectedRunId,
  activeRunId,
  onSelect,
}: {
  count: number;
  runs: LogHost[] | undefined;
  selectedRunId: string | null | undefined;
  activeRunId: string | null | undefined;
  onSelect: (runId: string) => void;
}) {
  const others = count - 1;
  return (
    <div className="space-y-2">
      <p className="text-sm text-zinc-400">
        {others === 1
          ? "Há outra execução ativa neste host."
          : `Há outras ${others} execuções ativas neste host.`}
      </p>
      {runs && runs.length > 1 ? (
        <div role="radiogroup" aria-label="Execuções ativas neste host" className="flex flex-col gap-1">
          {runs.map((run) => {
            const checked = run.run_id === selectedRunId;
            return (
              <button
                key={run.run_id}
                type="button"
                role="radio"
                aria-checked={checked}
                onClick={() => onSelect(run.run_id)}
                className={`rounded border px-3 py-1.5 text-left text-xs ${
                  checked
                    ? "border-zinc-500 bg-zinc-800 text-zinc-100"
                    : "border-zinc-700 text-zinc-400 hover:bg-zinc-800/60"
                }`}
              >
                Iniciada em {run.started_at ? formatAbsolute(run.started_at) : "?"} · PID {run.process_id ?? "?"}
                {run.run_id === activeRunId ? " · ativa" : ""}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
```

Trocar a assinatura e o início do componente por:

```tsx
export function LogEnvironmentTab({
  serviceId,
  hostname,
  username,
  runId,
  activeRunId,
  liveRunCount = 0,
  stopped = null,
  onSelectRun,
}: {
  serviceId: string;
  hostname: string;
  username: string;
  /** Execução exibida: a escolhida no seletor, a ativa do grupo ou, sem nenhuma das duas, a do log. */
  runId?: string | null;
  /** Execução ativa do grupo (`active_run_id` de /logs/last); ausente em backend antigo ou grupo sem host. */
  activeRunId?: string | null;
  /** Execuções vivas da instância; acima de 1 aparecem o aviso e o seletor. */
  liveRunCount?: number;
  /** Serviço parado (mesma regra do StatusBadge): a ativa vira "Última execução". */
  stopped?: boolean | null;
  onSelectRun?: (runId: string) => void;
}) {
```

Depois da query `environmentQuery` existente (antes dos `return` antecipados — os hooks precisam rodar sempre), acrescentar:

```tsx
  const showSimultaneous = liveRunCount > 1 && !!onSelectRun;
  const liveRunsQuery = useQuery({
    queryKey: queryKeys.logs.liveRuns({ serviceId, hostname, username }),
    queryFn: () => {
      const params = new URLSearchParams({
        service_id: serviceId,
        hostname,
        username,
        all_runs: "true",
        live: "true",
      });
      return api.get<LogHost[]>(`/logs/hosts?${params.toString()}`);
    },
    enabled: showSimultaneous,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
```

Importar `queryKeys.logs.liveRuns` não exige import novo (o arquivo já importa `queryKeys`). Substituir o retorno final do componente por:

```tsx
  const label = runLabel(runId, activeRunId, stopped);
  return (
    <div className="space-y-6">
      {label ? (
        <p className="text-xs font-semibold uppercase tracking-wider text-success">{label}</p>
      ) : null}
      {showSimultaneous ? (
        <SimultaneousRuns
          count={liveRunCount}
          runs={liveRunsQuery.data}
          selectedRunId={runId}
          activeRunId={activeRunId}
          onSelect={onSelectRun!}
        />
      ) : null}
      <EnvironmentDetails host={host} />
    </div>
  );
```

- [ ] **Step 5: Ligar no `LogDetail`**

Em `LogModal.tsx`, junto de `infoTab`:

```tsx
  // Execução escolhida no seletor de simultâneas. Vale para as abas Ambiente e
  // Recursos: se só uma seguisse a escolha, as duas descreveriam hosts diferentes.
  // `null` = a ativa do grupo (ou, sem ela, a do log).
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const viewRunId = selectedRunId ?? log.active_run_id ?? log.run_id;
  useEffect(() => {
    setSelectedRunId(null);
  }, [log.service, log.active_run_id]);
```

Trocar a chamada da aba Ambiente (~linha 505) por:

```tsx
              <LogEnvironmentTab
                serviceId={log.service}
                hostname={log.hostname}
                username={log.username}
                runId={viewRunId}
                activeRunId={log.active_run_id}
                liveRunCount={log.live_run_count ?? 0}
                stopped={isStopped(log)}
                onSelectRun={setSelectedRunId}
              />
```

e, no `LazyLogHostMetricsTab` (~linha 1166), `runId={viewRunId}` (ajustar o comentário existente: acompanha a execução escolhida na aba Ambiente).

- [ ] **Step 6: Rodar e ver passar**

Run: `bun test && bun run typecheck`
Expected: toda a suíte passa (inclusive os testes antigos de "execução ativa" e de `Recursos`) e typecheck limpo. Se `palette-guard` reclamar de alguma classe, trocar por token semântico já usado no repositório.

- [ ] **Step 7: Commit**

```bash
git add src/lib/queryKeys.ts src/pages/Dashboard/components/LogEnvironmentTab.tsx src/pages/Dashboard/components/LogModal.tsx src/pages/Dashboard/components/LogModal.test.tsx
git commit -m "feat(dashboard): label the last run and pick among simultaneous runs" -m "Serviço parado deixa de se declarar 'Execução ativa'. Com mais de uma execução viva a aba Ambiente avisa e oferece um seletor, cuja escolha vale também para a aba Recursos." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Parte 3 — Documentação (`BK`)

### Task 6: Regra da execução ativa e execuções simultâneas no jaylog-book

**Files:**
- Modify: `BK/docs/producao/registro-de-ambiente.md` (seção "Ambiente atual sem logs", criada na primeira entrega)

**Interfaces:**
- Consumes: spec `2026-10-01-active-run-concurrency-design.md`, seções 2 e 4.

- [ ] **Step 1: Conferir a branch e ler a seção**

```bash
cd /home/gpocas/misc/jaylog-book
git branch --show-current   # feat/active-run-host
grep -n "Ambiente atual sem logs" -A12 docs/producao/registro-de-ambiente.md
```

- [ ] **Step 2: Reescrever a definição e acrescentar as simultâneas**

Substituir o parágrafo que começa com "A execução ativa é a de atividade mais recente da instância…" por:

```markdown
A **execução ativa** é, entre as execuções da instância (mesmo `service`, `hostname` e `username`) que deram sinal nos últimos 15 minutos, a que **iniciou por último**. Sinal é o último heartbeat ou, sem `jaylog.heartbeat()`, o momento em que o registro foi enviado. Se nenhuma deu sinal nesse prazo, vale a de atividade mais recente, e o dashboard a chama de "Última execução" em vez de "Execução ativa".

### Execuções simultâneas

Quando duas ou mais execuções da mesma instância estão vivas ao mesmo tempo (por exemplo, um robô agendado cuja execução anterior ainda não terminou), a aba **Ambiente** avisa quantas outras existem e deixa escolher qual ver, com início e PID de cada uma. A escolha vale também para a aba **Recursos**. A que iniciou por último é sempre a exibida por padrão, e ela não muda quando uma execução mais antiga manda heartbeat depois.
```

- [ ] **Step 3: Build**

Run: `uv run zensical build --clean`
Expected: build sem erros e sem links quebrados. `site/` é saída de build: confira com `git status --short` que ele não entra no commit.

- [ ] **Step 4: Commit**

```bash
git add docs/producao/registro-de-ambiente.md
git commit -m "docs: explain the active-run rule and simultaneous runs" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**Cobertura do spec** (`2026-10-01-active-run-concurrency-design.md`):

- §2 regra única (viva / ativa / helper compartilhado): Task 1 (helper + `/logs/last`), Task 2 (`hosts`, `host-stats`, teto de `host-metrics`).
- §3 contrato (`/logs/last` com 3 campos; `/logs/hosts` com filtros e ordem): Tasks 1 e 2; verificado na Task 3 (cenários A–F).
- §4 frontend: badge e merge da página (Task 4); rótulo, aviso, seletor e estado compartilhado com Recursos (Task 5).
- §6 testes: backend (unitário na Task 1, roteiro SP na Task 3), frontend (Tasks 4–5).
- §7 riscos: documentados; sem task.
- Documentação: Task 6.

**Placeholders:** nenhum "TBD"/"TODO". Dois pontos dependem de verificação pelo executor, ambos com instrução concreta: o import de `../db/schema` no teste do helper (alternativa dada no Step 5 da Task 1) e a unicidade do texto "running"/"stopped" na página real (alternativa dada no Step 2 da Task 4).

**Consistência de nomes/tipos:** `liveCutoff`/`isLiveRun`/`activeRunOrder` (Tasks 1–2); `active_jaylog_version`, `live_run_count` (schema, SQL, `Log`, testes); props `activeRunId`, `liveRunCount`, `stopped`, `onSelectRun`, `hostname`, `username`, `runId` de `LogEnvironmentTab` iguais na definição (Task 5 Step 4), no uso (Step 5) e nos testes; a prop antiga `activeRun` some e o único chamador é o `LogModal`.

**Riscos da execução:** a Task 3 depende de Docker e da porta 55432 livre; a Task 5 depende do teste mockar `/logs/hosts` por caminho (o `hostsResponder` cobre as três variantes de URL usadas).
