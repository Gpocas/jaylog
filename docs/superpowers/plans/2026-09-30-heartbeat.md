# Heartbeat explícito (`jaylog.heartbeat()`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Um serviço em loop que chama `jaylog.heartbeat()` aparece como ativo (e não é excluído por inatividade) mesmo sem emitir logs; quem não chama mantém o comportamento atual.

**Architecture:** O cliente guarda um contador de beats por serviço e uma thread dedicada (`jaylog-heartbeat`, uma por processo, criada na 1ª chamada) envia `POST /logs/heartbeat {run_id, service}` a cada ciclo, com endpoint e chave de cada serviço. O backend carimba `log_hosts.last_heartbeat_at` com a hora do servidor e passa a expor `last_activity_at = GREATEST(último log, último heartbeat)`; os crons de 45 dias usam a mesma regra. O frontend calcula "parado" sobre `last_activity_at`, com fallback para `log_timestamp`.

**Tech Stack:** Python 3.10–3.13 + pytest + ruff + `uv` (repo `jaylog`); Bun + Elysia + Drizzle + Postgres (`backend-nn-analytics`); Bun + React + `bun:test` (`frontend-analytics-logging`); MkDocs/Zensical (`jaylog-book`).

**Spec:** `docs/superpowers/specs/2026-09-30-heartbeat-design.md` (neste repositório). Leia o spec antes de começar; este plano o implementa na ordem de entrega da seção 8.

**Repositórios (caminhos absolutos):**

| Sigla | Caminho | Partes |
|-------|---------|--------|
| `BE`  | `/home/gpocas/projects/backend-nn-analytics` | Tasks 1–5 |
| `JL`  | `/home/gpocas/misc/jaylog` | Tasks 6–9 |
| `FE`  | `/home/gpocas/projects/frontend-analytics-logging` | Tasks 10–11 |
| `BK`  | `/home/gpocas/misc/jaylog-book` | Task 12 |

Cada repositório recebe a branch `feat/heartbeat`, criada na primeira task dele, e commits próprios.

## Global Constraints

- `PROTOCOL_VERSION = 5`; pacote `0.3.0a8` (`pyproject.toml` + `uv.lock`).
- `host_heartbeat_interval`: padrão `60`, mínimo `10` segundos; `host_heartbeat_enabled`: padrão `True`; só vale junto com `host_report_enabled`.
- `heartbeat()` nunca bloqueia, nunca levanta e nunca faz rede na thread do chamador.
- Thread dedicada `jaylog-heartbeat`, daemon, **uma por processo**, criada **na 1ª chamada** de `heartbeat()`; nunca em `configure()`.
- `heartbeat(service: str | None = None)`: `None` = primeira chave de `_settings_registry` (a mesma regra do `get_logger()` sem nome); serviço desconhecido ou inelegível = no-op.
- Endpoint, API key, proxy, TLS e timeout **do item** de cada serviço, enviados **por requisição**.
- Contrato: `POST /logs/heartbeat` com corpo `{run_id, service}`; **o servidor carimba a hora**; 200 `{updated: 0|1}`; `x-jaylog-host-required: 1` quando `updated = 0`; **422 (nunca 404)** para serviço desconhecido; 404/405 do lado do cliente desativam só aquele serviço.
- `log_hosts.last_heartbeat_at`: `timestamp` **sem fuso**, escrito com `new Date()` do drizzle (mesmo caminho de `last_seen_at`), sem índice novo. Não reaproveitar `last_seen_at`.
- `STOP_LIMIT_MINUTES = 15` e `INACTIVE_SERVICE_DAYS = 45` não mudam.
- Comentários explicam o *porquê*, em português, no ponto da decisão (convenção dos dois repositórios).
- Commits terminam com a linha `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (segundo `-m`).
- **`BE/.env` aponta `DATABASE_URL` para um Postgres remoto (`34.122.100.238`). Nenhum comando desta implementação pode rodar contra ele.** `db:generate` só escreve arquivos e é seguro; `db:migrate` e os scripts de verificação só rodam com `DATABASE_URL` sobrescrito para o Postgres local descartável da Task 5.

## Review Focus

Entradas e falhas que o spec implica e que mais provavelmente quebram na prática. Cada linha tem o teste na task dona do código.

1. `heartbeat()` antes de `configure()`, depois de `shutdown()` ou com serviço inexistente: no-op silencioso, sem exceção e sem thread (Task 8).
2. `heartbeat()` chamado em loop apertado (milhares de vezes entre dois ciclos): um único POST por ciclo, sem crescer memória (Task 7).
3. Backend antigo ou fora do ar: 404/405/4xx desativa só o serviço afetado e o bot segue; erro de rede/429/5xx mantém o beat pendente (Task 7).
4. Serviço silencioso cujo último log é de uma execução antiga e cujo heartbeat é da execução atual (mesmo `service/hostname/username`): `/logs/last` devolve `last_activity_at` recente (Task 3, verificação da Task 5).
5. Serviço ou host que só manda heartbeat: não é avisado, excluído nem apagado pelos crons (Task 4, verificação da Task 5).
6. `configure()` chamado duas vezes (reconfiguração): beats e alvos antigos não vazam para a nova configuração (Task 8).

---

## Parte 1 — Backend (`BE`)

> Convenção do repositório: `bun test` cobre só código puro em `src/lib/`; **não existe harness de banco**. As Tasks 1–4 são validadas por `bun run typecheck` + `bun test` (nada pode regredir) e a Task 5 exercita rota, `/logs/last` e crons de ponta a ponta contra um Postgres local descartável.

### Task 1: Coluna `last_heartbeat_at` e migration

**Files:**
- Modify: `BE/src/db/schema/log_hosts.ts` (após `last_seen_at`, linha ~54)
- Create: `BE/src/db/migrations/00NN_*.sql` + atualização de `BE/src/db/migrations/meta/` (geradas)

**Interfaces:**
- Produces: `logHosts.last_heartbeat_at` (`Date | null`) no schema drizzle; coluna `log_hosts.last_heartbeat_at timestamp NULL` no banco.

- [ ] **Step 1: Criar a branch**

```bash
cd /home/gpocas/projects/backend-nn-analytics
git switch -c feat/heartbeat
```

- [ ] **Step 2: Adicionar a coluna ao schema**

Em `src/db/schema/log_hosts.ts`, logo depois de `last_seen_at: ...`:

```ts
    // Último `jaylog.heartbeat()` da execução, com a hora do servidor. Coluna
    // própria, e não `last_seen_at`: aquela é indexada (cada escrita a
    // reindexaria) e significa "última sincronização do host". Sem índice aqui,
    // o UPDATE por (run_id, service) é HOT. Mesmo tipo e mesmo caminho de
    // escrita (`new Date()`) de `last_seen_at`/`log_timestamp`: o GREATEST entre
    // os dois não depende do fuso da sessão.
    last_heartbeat_at: timestamp("last_heartbeat_at"),
```

- [ ] **Step 3: Gerar a migration**

Run: `bun db:generate`
Expected: cria um `src/db/migrations/00NN_<nome>.sql` e atualiza `meta/_journal.json` e o snapshot. (`drizzle.config.ts` só lê `DATABASE_URL` para montar as credenciais; `generate` não conecta ao banco.)

- [ ] **Step 4: Conferir o SQL gerado**

Run: `ls -t src/db/migrations/*.sql | head -1 | xargs cat`
Expected: exatamente `ALTER TABLE "log_hosts" ADD COLUMN "last_heartbeat_at" timestamp;`. Se vier qualquer outra instrução, **pare**: o schema divergiu do banco por outro motivo e isso precisa de revisão antes de seguir.

- [ ] **Step 5: Typecheck e testes**

Run: `bun run typecheck && bun test`
Expected: sem erros; todos os testes existentes passam.

- [ ] **Step 6: Commit**

```bash
git add src/db/schema/log_hosts.ts src/db/migrations
git commit -m "feat(db): add log_hosts.last_heartbeat_at" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 2: `POST /logs/heartbeat`

**Files:**
- Modify: `BE/src/routes/logs.ts` (nova rota entre `POST /host-metrics` e `POST /host-schedules`, dentro do mesmo `.guard({}, ...)` atrás do `apiKeyPlugin`)

**Interfaces:**
- Consumes: `logHosts.last_heartbeat_at` (Task 1); `httpError`/`errorSchema` de `../lib/errors` (já importados no arquivo).
- Produces: `POST /logs/heartbeat` — corpo `{run_id: uuid, service: string}`; 200 `{updated: number}`; 422 `errorSchema`; header `x-jaylog-host-required: 1` quando `updated = 0`.

- [ ] **Step 1: Inserir a rota**

Em `src/routes/logs.ts`, imediatamente antes de

```ts
      .post(
        "/host-schedules",
```

inserir:

```ts
      .post(
        "/heartbeat",
        async ({ body, set }) => {
          const [service] = await db.select({ id: services.id }).from(services).where(eq(services.name, body.service)).limit(1);
          // 422, nunca 404: o jaylog lê 404/405 como "backend não tem a rota" e
          // desativa o heartbeat desse serviço pelo resto do processo.
          if (!service) return httpError(set, 422, "Service not found");

          // Hora do servidor, não do cliente: bots rodam em VMs com relógio à
          // deriva e o limite de "parado" é de só 15 min. O erro do carimbo no
          // recebimento é de no máximo um intervalo de envio (~60 s).
          const updated = await db
            .update(logHosts)
            .set({ last_heartbeat_at: new Date() })
            .where(and(eq(logHosts.run_id, body.run_id), eq(logHosts.service, service.id)))
            .returning({ id: logHosts.id });

          // Sem registro de host o beat não tem onde ficar: mesmo sinal do /add e
          // do /host-metrics para o cliente reenviar o POST /logs/host.
          if (updated.length === 0) set.headers["x-jaylog-host-required"] = "1";
          return { updated: updated.length };
        },
        {
          body: t.Object({ run_id: t.String({ format: "uuid" }), service: t.String({ minLength: 1 }) }),
          response: { 200: t.Object({ updated: t.Number() }), 422: errorSchema },
          detail: { tags: ["Logs"] },
        },
      )
```

- [ ] **Step 2: Typecheck e testes**

Run: `bun run typecheck && bun test`
Expected: sem erros (`src/lib/openapi.test.ts` segue passando).

- [ ] **Step 3: Commit**

```bash
git add src/routes/logs.ts
git commit -m "feat(logs): add POST /logs/heartbeat" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 3: `last_activity_at` em `GET /logs/last`

**Files:**
- Modify: `BE/src/routes/logs.ts` — `logLastSchema` (linha ~56) e o handler de `"/last"` (linha ~318)

**Interfaces:**
- Consumes: `logHosts.last_heartbeat_at` (Task 1).
- Produces: cada item de `GET /logs/last` ganha `last_activity_at: Date` = `GREATEST(log_timestamp, maior last_heartbeat_at de log_hosts com o mesmo service/hostname/username)`.

- [ ] **Step 1: Declarar o campo no schema de resposta**

Em `logLastSchema`, logo depois de `log_timestamp:        t.Date(),`:

```ts
  // Mais recente entre o último log e o último heartbeat da instância. É a base
  // do "parado" no dashboard; `log_timestamp` segue sendo o último *log*.
  last_activity_at:     t.Date(),
```

- [ ] **Step 2: Reescrever a consulta do handler**

No handler `"/last"`, substituir `const rows = await db.selectDistinctOn(...)...` por (mantendo `serviceFilter` como está e copiando integralmente a lista de campos existente, acrescida de `last_activity_at`):

```ts
          // Último heartbeat por instância, agregado UMA vez em CTE e juntado ao
          // log. Um subselect correlacionado na lista de colunas rodaria por linha
          // de log (até 1000 por grupo) antes do DISTINCT ON. Casa por
          // (service, hostname, username), não por `host_id`: no serviço
          // silencioso, o último log é de uma execução antiga e o `host_id` dele
          // não é o da execução que está mandando heartbeat.
          const heartbeats = db.$with("heartbeats").as(
            db
              .select({
                service: logHosts.service,
                hostname: logHosts.hostname,
                username: logHosts.username,
                last_heartbeat_at: sql<Date>`max(${logHosts.last_heartbeat_at})`.as("last_heartbeat_at"),
              })
              .from(logHosts)
              .where(isNotNull(logHosts.last_heartbeat_at))
              .groupBy(logHosts.service, logHosts.hostname, logHosts.username),
          );

          const rows = await db
            .with(heartbeats)
            .selectDistinctOn([logs.service, logs.hostname, logs.username], {
              id: logs.id,
              /* ...todos os campos que já existem hoje, sem alteração... */
              run_id: logs.run_id,
              // GREATEST ignora NULL: sem heartbeat, vale só o log. `mapWith` usa o
              // decodificador da coluna para devolver `Date`, como `log_timestamp`
              // (o driver entrega `timestamp` como string crua).
              last_activity_at: sql<Date>`GREATEST(${logs.log_timestamp}, ${heartbeats.last_heartbeat_at})`.mapWith(logs.log_timestamp),
            })
            .from(logs)
            .innerJoin(services, eq(logs.service, services.id))
            .innerJoin(serviceOwner, eq(services.owner, serviceOwner.id))
            // leftJoin: serviços anteriores ao vínculo ainda têm company/sector nulos.
            .leftJoin(companies, eq(services.company_id, companies.id))
            .leftJoin(sectors, eq(services.sector_id, sectors.id))
            .leftJoin(
              heartbeats,
              and(
                eq(heartbeats.service, logs.service),
                eq(heartbeats.hostname, logs.hostname),
                eq(heartbeats.username, logs.username),
              ),
            )
            .where(serviceFilter)
            .orderBy(logs.service, logs.hostname, logs.username, desc(logs.log_timestamp));
```

Adicionar `isNotNull` ao import de `drizzle-orm` na linha 2 do arquivo (`import { and, desc, eq, gte, ilike, inArray, isNotNull, isNull, lte, or, sql, type SQL } from "drizzle-orm";`). O `return rows.map(...)` com `service_stop_limit` fica igual.

- [ ] **Step 3: Typecheck e testes**

Run: `bun run typecheck && bun test`
Expected: sem erros. Se o typecheck reclamar de `db.with(...).selectDistinctOn`, conferir a versão do `drizzle-orm` instalada (`bun pm ls drizzle-orm`); `WithBuilder` expõe `selectDistinctOn` nas versões recentes. Se reclamar de `mapWith(logs.log_timestamp)`, trocar por `mapWith((value) => new Date(`${value}+0000`))`, que é o que o decodificador da coluna faz para `timestamp` sem fuso.

- [ ] **Step 4: Commit**

```bash
git add src/routes/logs.ts
git commit -m "feat(logs): expose last_activity_at on /logs/last" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 4: Crons de 45 dias consideram o heartbeat

**Files:**
- Create: `BE/src/jobs/serviceActivity.ts`
- Modify: `BE/src/jobs/cleanup.ts` (`cleanupLogHosts`, `cleanupInactiveServices` — 2 consultas)
- Modify: `BE/src/jobs/deletionWarnings.ts` (`warnInactiveServices` — 2 consultas)

**Interfaces:**
- Consumes: `log_hosts.last_heartbeat_at` (Task 1).
- Produces: `SERVICE_LAST_ACTIVITY` (fragmento `sql`) exigindo os aliases `s` (services) e `l` (LEFT JOIN logs) numa consulta com `GROUP BY s.id, s.created_at`. Usado pelas 4 consultas.

- [ ] **Step 1: Criar o fragmento compartilhado**

`src/jobs/serviceActivity.ts`:

```ts
import { sql } from "drizzle-orm";

/**
 * Última atividade de um serviço: o mais recente entre o último log e o último
 * heartbeat de qualquer execução dele, caindo para `created_at` quando não há
 * nenhum dos dois. É a regra do `cleanup-inactive-services` e do
 * `warn-inactive-services` — as quatro consultas usam este fragmento para o
 * aviso e a exclusão nunca discordarem (aviso de 38 dias, exclusão aos 45).
 *
 * Exige os aliases `s` (services) e `l` (LEFT JOIN logs) e `GROUP BY s.id,
 * s.created_at`. O heartbeat entra por subselect agregado, e não por um segundo
 * LEFT JOIN, que multiplicaria as linhas de log por execução. `GREATEST` ignora
 * NULL no Postgres: sem heartbeat, o resultado é só a regra antiga.
 */
export const SERVICE_LAST_ACTIVITY = sql`GREATEST(
  COALESCE(MAX(l.log_timestamp), s.created_at),
  (SELECT MAX(h.last_heartbeat_at) FROM log_hosts h WHERE h.service = s.id)
)`;
```

- [ ] **Step 2: `cleanupInactiveServices` (2 consultas)**

Em `src/jobs/cleanup.ts`: `import { SERVICE_LAST_ACTIVITY } from "./serviceActivity";`.

Na consulta de identificação:

```ts
  const inactiveServices = await execRows<{ id: string; name: string }>(sql`
    SELECT s.id, s.name
    FROM services s
    LEFT JOIN logs l ON l.service = s.id
    GROUP BY s.id, s.name, s.created_at
    HAVING ${SERVICE_LAST_ACTIVITY} < NOW() - (${INACTIVE_SERVICE_DAYS} || ' days')::interval
  `);
```

Atualizar o comentário acima dela: "sem logs **nem heartbeat** há mais de 45 dias OU criados há mais de 45 dias sem nenhum dos dois".

Na subconsulta do `INSERT ... SELECT` de `service_deletion_warnings`, trocar `COALESCE(MAX(l.log_timestamp), s.created_at) AS last_activity` por `${SERVICE_LAST_ACTIVITY} AS last_activity`.

- [ ] **Step 3: `cleanupLogHosts`**

```ts
/**
 * Remove execuções antigas que já não têm nenhum log que dependa delas. Um host
 * que só manda heartbeat (serviço em loop silencioso) não tem logs e já não
 * ressincroniza o registro: sem a checagem de `last_heartbeat_at` ele seria
 * apagado aos 45 dias e recriado no próximo `x-jaylog-host-required`.
 */
export async function cleanupLogHosts() {
  await db.execute(sql`
    DELETE FROM log_hosts h
    WHERE h.last_seen_at < NOW() - INTERVAL '45 days'
      AND (h.last_heartbeat_at IS NULL OR h.last_heartbeat_at < NOW() - INTERVAL '45 days')
      AND NOT EXISTS (SELECT 1 FROM logs l WHERE l.host_id = h.id)
  `);
}
```

- [ ] **Step 4: `warnInactiveServices` (2 consultas)**

Em `src/jobs/deletionWarnings.ts`: `import { SERVICE_LAST_ACTIVITY } from "./serviceActivity";`. Nas duas subconsultas (o `INSERT ... FROM (...) a` e o `UPDATE ... FROM (...) a`), trocar `COALESCE(MAX(l.log_timestamp), s.created_at) AS last_activity` por `${SERVICE_LAST_ACTIVITY} AS last_activity`. O `GROUP BY` do `INSERT` (`s.id, s.name, s.created_at`) e o do `UPDATE` (`s.id, s.created_at`) já satisfazem o requisito do fragmento.

- [ ] **Step 5: Typecheck e testes**

Run: `bun run typecheck && bun test`
Expected: sem erros.

- [ ] **Step 6: Commit**

```bash
git add src/jobs
git commit -m "feat(jobs): count heartbeat as activity in 45-day cleanup and warnings" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 5: Verificação de ponta a ponta no Postgres local

**Files:**
- Create (NÃO commitar): `BE/tmp-verify-heartbeat.ts`

**Interfaces:**
- Consumes: Tasks 1–4.

- [ ] **Step 1: Subir um Postgres local descartável e migrar**

```bash
docker run -d --name hb-verify-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=hb -p 55432:5432 postgres:18
export HB_DB=postgresql://postgres:pw@localhost:55432/hb
DATABASE_URL=$HB_DB DATABASE_SSL=false bun db:migrate
```

Expected: o `db:migrate` aplica todas as migrations, inclusive a da Task 1, sem erro. Se o container ainda não aceitar conexões, aguardar ~5 s e repetir. **Conferir que a URL impressa/usada é `localhost:55432`**.

- [ ] **Step 2: Escrever o script de verificação**

`tmp-verify-heartbeat.ts` (recusa rodar fora do Postgres local):

```ts
import assert from "node:assert/strict";
import { app } from "./src/app";
import { db, execRows } from "./src/db";
import { apiKeys, logHosts, logs, services, sessions, users } from "./src/db/schema";
import { cleanupInactiveServices, cleanupLogHosts } from "./src/jobs/cleanup";
import { warnInactiveServices } from "./src/jobs/deletionWarnings";
import { sql } from "drizzle-orm";

if (!/localhost:55432/.test(process.env.DATABASE_URL ?? "")) {
  throw new Error(`recusado: DATABASE_URL não é o Postgres local de verificação (${process.env.DATABASE_URL})`);
}

const sha = (v: string) => new Bun.CryptoHasher("sha256").update(v).digest("hex");
const ago = (ms: number) => new Date(Date.now() - ms);
const MIN = 60_000, DAY = 86_400_000;
const RUN_OLD = "11111111-1111-4111-8111-111111111111";
const RUN_NEW = "22222222-2222-4222-8222-222222222222";
const RUN_NOHOST = "33333333-3333-4333-8333-333333333333";

const [owner] = await db.insert(users).values({ name: "verify", email: "v@x.com", password: "x", profile: "admin" }).returning();
await db.insert(sessions).values({ user_id: owner!.id, token_hash: sha("tok"), expires_at: new Date(Date.now() + DAY) });
await db.insert(apiKeys).values({ name: "verify", key_hash: sha("apikey") });

const mkService = async (name: string, createdDaysAgo = 0) =>
  (await db.insert(services).values({ name, owner: owner!.id, created_at: ago(createdDaysAgo * DAY) }).returning())[0]!;
const hostBase = (service: string, run_id: string) =>
  ({ run_id, service, protocol_version: 5, jaylog_version: "0.3.0a8", hostname: "H1", username: "U1" });
const beat = (run_id: string, service: string, key = "apikey") =>
  app.handle(new Request("http://localhost/logs/heartbeat", {
    method: "POST",
    headers: { "content-type": "application/json", "x-api-key": key },
    body: JSON.stringify({ run_id, service }),
  }));

// --- rota -------------------------------------------------------------------
const hb = await mkService("VERIFY-HB");
await db.insert(logHosts).values({ ...hostBase(hb.id, RUN_OLD), last_seen_at: ago(3 * 60 * MIN) });
await db.insert(logHosts).values(hostBase(hb.id, RUN_NEW));
await db.insert(logs).values({ service: hb.id, hostname: "H1", username: "U1", ipv4: "1.1.1.1", log_level: "INFO", log_message: "antigo", log_timestamp: ago(2 * 60 * MIN), run_id: RUN_OLD });

let res = await beat(RUN_NEW, "VERIFY-HB");
assert.equal(res.status, 200);
assert.deepEqual(await res.json(), { updated: 1 });

res = await beat(RUN_NOHOST, "VERIFY-HB");
assert.equal(res.status, 200);
assert.equal(res.headers.get("x-jaylog-host-required"), "1");
assert.deepEqual(await res.json(), { updated: 0 });

assert.equal((await beat(RUN_NEW, "NAO-EXISTE")).status, 422);
assert.equal((await beat(RUN_NEW, "VERIFY-HB", "errada")).status, 401);
console.log("OK rota /logs/heartbeat");

// --- /logs/last: log de 2 h atrás + heartbeat agora (outra execução, mesma instância)
res = await app.handle(new Request("http://localhost/logs/last", { headers: { authorization: "Bearer tok" } }));
assert.equal(res.status, 200);
const rows = (await res.json()) as { service: string; log_timestamp: string; last_activity_at: string }[];
const row = rows.find((r) => r.service === hb.id)!;
assert.ok(Date.now() - new Date(row.log_timestamp).getTime() > 100 * MIN, "log_timestamp segue sendo o do último log");
assert.ok(Date.now() - new Date(row.last_activity_at).getTime() < 2 * MIN, "last_activity_at reflete o heartbeat");
console.log("OK /logs/last last_activity_at");

// sem heartbeat: last_activity_at == log_timestamp
const plain = await mkService("VERIFY-PLAIN");
await db.insert(logs).values({ service: plain.id, hostname: "H2", username: "U2", ipv4: "1.1.1.1", log_level: "INFO", log_message: "x", log_timestamp: ago(5 * MIN) });
res = await app.handle(new Request("http://localhost/logs/last", { headers: { authorization: "Bearer tok" } }));
const plainRow = ((await res.json()) as { service: string; log_timestamp: string; last_activity_at: string }[]).find((r) => r.service === plain.id)!;
assert.equal(plainRow.last_activity_at, plainRow.log_timestamp);
console.log("OK /logs/last sem heartbeat");

// --- crons -------------------------------------------------------------------
const stale = await mkService("VERIFY-STALE", 50);
await db.insert(logs).values({ service: stale.id, hostname: "H3", username: "U3", ipv4: "1.1.1.1", log_level: "INFO", log_message: "x", log_timestamp: ago(50 * DAY) });
const silent = await mkService("VERIFY-SILENT", 50); // só heartbeat, nenhum log
await db.insert(logHosts).values({ ...hostBase(silent.id, "44444444-4444-4444-8444-444444444444"), last_seen_at: ago(50 * DAY), last_heartbeat_at: new Date() });
const orphanHost = "55555555-5555-4555-8555-555555555555";
await db.insert(logHosts).values({ ...hostBase(plain.id, orphanHost), hostname: "H9", last_seen_at: ago(50 * DAY) });

await warnInactiveServices();
const warned = await execRows<{ service_name: string }>(sql`SELECT service_name FROM service_deletion_warnings WHERE status = 'PENDING'`);
assert.ok(warned.some((w) => w.service_name === "VERIFY-STALE"), "serviço parado aos 50 dias é avisado");
assert.ok(!warned.some((w) => w.service_name === "VERIFY-SILENT"), "serviço só com heartbeat não é avisado");

await cleanupInactiveServices();
const names = (await db.select({ name: services.name }).from(services)).map((s) => s.name);
assert.ok(!names.includes("VERIFY-STALE"), "serviço parado é excluído");
assert.ok(names.includes("VERIFY-SILENT"), "serviço só com heartbeat sobrevive");

await cleanupLogHosts();
const runs = (await db.select({ run_id: logHosts.run_id }).from(logHosts)).map((h) => h.run_id);
assert.ok(runs.includes("44444444-4444-4444-8444-444444444444"), "host com heartbeat recente sobrevive");
assert.ok(!runs.includes(orphanHost), "host antigo, sem heartbeat e sem logs, é apagado");
console.log("OK crons");
process.exit(0);
```

- [ ] **Step 3: Rodar contra o Postgres local**

Run: `DATABASE_URL=$HB_DB DATABASE_SSL=false bun tmp-verify-heartbeat.ts`
Expected: imprime `OK rota /logs/heartbeat`, `OK /logs/last last_activity_at`, `OK /logs/last sem heartbeat`, `OK crons` e encerra com código 0. Ruído de conexão com o Redis (`ECONNREFUSED`) é esperado e irrelevante: a fila do `/logs/add` não é usada aqui. Se `last_activity_at` vier como string sem `Z` ou o teste de `/logs/last` devolver 500 de validação de resposta, é o `mapWith` da Task 3: aplicar a alternativa descrita no Step 3 daquela task e repetir.

- [ ] **Step 4: Limpar**

```bash
docker rm -f hb-verify-pg
rm tmp-verify-heartbeat.ts
git status --short
```

Expected: `git status` limpo (nada a commitar nesta task).

---

## Parte 2 — Cliente (`JL`)

### Task 6: Configurações, endpoint derivado e protocolo 5

**Files:**
- Modify: `JL/src/jaylog/settings.py` (após o bloco de métricas, ~linha 175; validador junto de `validate_host_metrics_interval`, ~linha 206)
- Modify: `JL/src/jaylog/_version.py`
- Modify: `JL/src/jaylog/endpoints.py` (docstring)
- Test: `JL/tests/test_settings.py`, `JL/tests/test_endpoints.py`, `JL/tests/test_host_payload.py`

**Interfaces:**
- Produces: `JaylogSettings.host_heartbeat_enabled: bool = True`, `host_heartbeat_interval: float = 60`, `host_heartbeat_http_endpoint: str | None = None`, propriedade `effective_host_heartbeat_endpoint -> str | None`; `PROTOCOL_VERSION == 5`.

- [ ] **Step 1: Criar a branch**

```bash
cd /home/gpocas/misc/jaylog
git switch -c feat/heartbeat
```

- [ ] **Step 2: Escrever os testes que falham**

Em `tests/test_settings.py`, ao final:

```python
def test_host_heartbeat_settings_defaults_and_derived_endpoint() -> None:
    settings = JaylogSettings(app_name="ORDERS", log_http_endpoint="https://api.example/logs/add")

    assert settings.host_heartbeat_enabled is True
    assert settings.host_heartbeat_interval == 60
    assert settings.effective_host_heartbeat_endpoint == "https://api.example/logs/heartbeat"


def test_host_heartbeat_endpoint_override_and_missing_log_endpoint() -> None:
    override = JaylogSettings(
        app_name="ORDERS",
        log_http_endpoint="https://api.example/logs/add",
        host_heartbeat_http_endpoint="https://other/beat",
    )
    assert override.effective_host_heartbeat_endpoint == "https://other/beat"

    assert (
        JaylogSettings(app_name="ORDERS", log_http_endpoint=None).effective_host_heartbeat_endpoint
        is None
    )


def test_host_heartbeat_interval_has_a_floor() -> None:
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError, match="JAYLOG_HOST_HEARTBEAT_INTERVAL"):
        JaylogSettings(app_name="ORDERS", host_heartbeat_interval=5)
```

Em `tests/test_endpoints.py`, ao final:

```python
def test_derives_heartbeat_endpoint() -> None:
    from jaylog.endpoints import derive_endpoint

    assert derive_endpoint("https://api/logs/add", "heartbeat") == "https://api/logs/heartbeat"
    assert derive_endpoint("https://api", "heartbeat") == "https://api/heartbeat"
```

Em `tests/test_host_payload.py`, trocar o teste do protocolo:

```python
def test_protocol_version_is_5() -> None:
    from jaylog._version import PROTOCOL_VERSION

    assert PROTOCOL_VERSION == 5
    assert HostInfo().protocol_version == 5
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `uv run pytest tests/test_settings.py tests/test_endpoints.py tests/test_host_payload.py -q`
Expected: FAIL (`AttributeError`/`ValidationError` dos campos novos e `assert 4 == 5`). O teste de `derive_endpoint` já passa: a função é genérica.

- [ ] **Step 4: Implementar**

Em `src/jaylog/settings.py`, logo depois de `effective_host_metrics_endpoint` (antes do bloco "Agendas do Task Scheduler"):

```python
    # ------------------------------------------------------------------
    # Heartbeat explícito — `jaylog.heartbeat()`, chamado pelo loop do usuário
    # ------------------------------------------------------------------

    # Só vale junto com `host_report_enabled`: o backend credita o beat à linha de
    # `log_hosts` da execução, que só existe depois do registro de host.
    host_heartbeat_enabled: bool = True

    # Segundos entre envios. Só sai POST se houve `heartbeat()` desde o último
    # envio: o intervalo limita a taxa, não cria beats. Acima de ~15 min o serviço
    # aparece como parado entre envios (limite do dashboard).
    host_heartbeat_interval: float = 60

    # Derivado de `log_http_endpoint` (`/logs/add` -> `/logs/heartbeat`) se não
    # definido.
    host_heartbeat_http_endpoint: str | None = None

    @property
    def effective_host_heartbeat_endpoint(self) -> str | None:
        """URL do `POST /logs/heartbeat`: o override, ou a derivada do endpoint de log."""
        if self.host_heartbeat_http_endpoint:
            return self.host_heartbeat_http_endpoint
        if not self.log_http_endpoint:
            return None
        from jaylog.endpoints import derive_endpoint

        return derive_endpoint(self.log_http_endpoint, "heartbeat")
```

E, junto de `validate_host_metrics_interval`:

```python
    @field_validator("host_heartbeat_interval", mode="after")
    @classmethod
    def validate_host_heartbeat_interval(cls, v: float) -> float:
        if v < 10:
            raise ValueError("JAYLOG_HOST_HEARTBEAT_INTERVAL deve ser de pelo menos 10 segundos")
        return v
```

Em `src/jaylog/_version.py`, acrescentar à docstring do `PROTOCOL_VERSION` e subir o valor:

```python
#: 4: ``POST /logs/host-schedules`` (agendas do Task Scheduler do Windows).
#: 5: ``POST /logs/heartbeat`` (sinal explícito de que o loop do serviço progride).
PROTOCOL_VERSION = 5
```

Em `src/jaylog/endpoints.py`, na docstring do módulo, acrescentar `/logs/heartbeat` à lista de segmentos e `JAYLOG_HOST_HEARTBEAT_HTTP_ENDPOINT` à lista de overrides.

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest -q && uv run ruff check .`
Expected: toda a suíte passa; ruff sem erros.

- [ ] **Step 6: Commit**

```bash
git add src/jaylog/settings.py src/jaylog/_version.py src/jaylog/endpoints.py tests
git commit -m "feat(heartbeat): settings, derived endpoint and protocol 5" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 7: `JaylogHeartbeatReporter`

**Files:**
- Create: `JL/src/jaylog/host/heartbeat_reporter.py`
- Test: `JL/tests/test_heartbeat_reporter.py`

**Interfaces:**
- Consumes: `PROTOCOL_VERSION`, `__version__` de `jaylog._version`; `RUN_ID` de `jaylog.runtime`; `jaylog.host.reporter.request_resend(service) -> bool`; `jaylog.diagnostics.emit(source, message)`.
- Produces:
  - `HeartbeatTarget(service, endpoint, api_key, interval=60.0, timeout=5.0, proxy=None, verify=False)` (dataclass frozen)
  - `JaylogHeartbeatReporter(*, session=None, request_resend=None, autostart=True)` com `register(targets)`, `beat(service) -> bool`, `remove(service)`, `deliver(deadline=None)`, `stop(timeout=2.0)`, `_run(stop_event)`, propriedades `targets -> dict`, `beats -> dict`, `interval -> float`
  - funções de módulo delegando ao singleton `_reporter`: `register(targets)`, `beat(service) -> bool`, `remove(service)`, `stop(timeout=2.0)`, `targets() -> dict`

- [ ] **Step 1: Escrever os testes que falham**

`tests/test_heartbeat_reporter.py`:

```python
import threading

import pytest

from jaylog._version import PROTOCOL_VERSION
from jaylog.host.heartbeat_reporter import HeartbeatTarget, JaylogHeartbeatReporter
from jaylog.runtime import RUN_ID


class FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


class FakeSession:
    def __init__(self, responses=(), on_post=None) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.on_post = on_post

    def post(self, url, json=None, **kwargs):
        self.calls.append({"url": url, "json": json, **kwargs})
        if self.on_post is not None:
            self.on_post()
        response = self.responses.pop(0) if self.responses else FakeResponse(200)
        if isinstance(response, Exception):
            raise response
        return response


def target(service: str = "ORDERS", **overrides) -> HeartbeatTarget:
    values = {
        "service": service,
        "endpoint": f"https://api/{service}/logs/heartbeat",
        "api_key": f"key-{service}",
        **overrides,
    }
    return HeartbeatTarget(**values)


def make(targets=None, responses=(), *, resend=None, on_post=None):
    session = FakeSession(responses, on_post)
    reporter = JaylogHeartbeatReporter(
        session=session,
        request_resend=resend or (lambda service: True),
        autostart=False,
    )
    reporter.register(targets if targets is not None else [target()])
    return reporter, session


def test_beat_for_unknown_service_is_a_noop():
    reporter, session = make()

    assert reporter.beat("OUTRO") is False
    reporter.deliver()

    assert session.calls == []
    assert reporter._thread is None


def test_posts_contract_with_per_request_credentials():
    reporter, session = make([target()], [FakeResponse(200)])

    assert reporter.beat("ORDERS") is True
    reporter.deliver()

    call = session.calls[0]
    assert call["url"] == "https://api/ORDERS/logs/heartbeat"
    assert call["json"] == {"run_id": RUN_ID, "service": "ORDERS"}
    assert call["headers"]["x-api-key"] == "key-ORDERS"
    assert call["headers"]["x-jaylog-run-id"] == RUN_ID
    assert call["headers"]["x-jaylog-protocol"] == str(PROTOCOL_VERSION)


def test_each_service_uses_its_own_endpoint_and_key():
    reporter, session = make([target("ORDERS"), target("BILLING", api_key="outra")])

    reporter.beat("ORDERS")
    reporter.beat("BILLING")
    reporter.deliver()

    by_service = {c["json"]["service"]: c for c in session.calls}
    assert by_service["ORDERS"]["headers"]["x-api-key"] == "key-ORDERS"
    assert by_service["BILLING"]["headers"]["x-api-key"] == "outra"
    assert by_service["BILLING"]["url"] == "https://api/BILLING/logs/heartbeat"


def test_target_network_settings_are_sent_per_request():
    reporter, session = make([target(proxy="http://proxy:3128", verify="/ca.pem", timeout=7.0)])

    reporter.beat("ORDERS")
    reporter.deliver()

    call = session.calls[0]
    assert call["proxies"] == {"http": "http://proxy:3128", "https": "http://proxy:3128"}
    assert call["verify"] == "/ca.pem"
    assert call["timeout"] == 7.0


def test_no_post_without_a_new_beat():
    reporter, session = make()

    reporter.beat("ORDERS")
    reporter.deliver()
    reporter.deliver()

    assert len(session.calls) == 1


def test_many_beats_between_cycles_produce_a_single_post():
    reporter, session = make()

    for _ in range(10_000):
        reporter.beat("ORDERS")
    reporter.deliver()

    assert len(session.calls) == 1
    assert reporter.beats == {"ORDERS": 10_000}


def test_beat_arriving_during_post_stays_pending():
    holder = {}
    reporter, session = make(on_post=lambda: holder["r"].beat("ORDERS"))
    holder["r"] = reporter

    reporter.beat("ORDERS")
    reporter.deliver()
    assert len(session.calls) == 1

    session.on_post = None
    reporter.deliver()
    assert len(session.calls) == 2
    reporter.deliver()
    assert len(session.calls) == 2


@pytest.mark.parametrize("failure", [FakeResponse(500), FakeResponse(429), ConnectionError("fora")])
def test_transient_failure_keeps_beat_pending(failure):
    reporter, session = make(responses=[failure, FakeResponse(200)])

    reporter.beat("ORDERS")
    reporter.deliver()
    reporter.deliver()
    assert len(session.calls) == 2

    reporter.deliver()
    assert len(session.calls) == 2
    assert reporter.beat("ORDERS") is True  # não foi desativado


@pytest.mark.parametrize("status", [404, 405])
def test_old_backend_disables_only_that_service(status, capsys):
    reporter, _ = make(
        [target("ORDERS"), target("BILLING")],
        [FakeResponse(status), FakeResponse(200)],
    )

    reporter.beat("ORDERS")
    reporter.beat("BILLING")
    reporter.deliver()

    assert reporter.beat("ORDERS") is False
    assert reporter.beat("BILLING") is True
    err = capsys.readouterr().err
    assert "ORDERS" in err and str(status) in err
    assert "BILLING" not in err


def test_other_4xx_disables_with_body_excerpt(capsys):
    reporter, _ = make(responses=[FakeResponse(401, text="chave inválida")])

    reporter.beat("ORDERS")
    reporter.deliver()

    assert reporter.beat("ORDERS") is False
    assert "chave inválida" in capsys.readouterr().err


def test_host_required_header_requests_resend_for_that_service():
    asked: list[str] = []
    reporter, _ = make(
        responses=[FakeResponse(200, {"x-jaylog-host-required": "1"})],
        resend=lambda service: asked.append(service) or True,
    )

    reporter.beat("ORDERS")
    reporter.deliver()

    assert asked == ["ORDERS"]


def test_first_beat_starts_the_thread_and_stop_sends_pending():
    session = FakeSession()
    reporter = JaylogHeartbeatReporter(session=session, request_resend=lambda s: True)
    reporter.register([target(interval=60.0)])
    assert reporter._thread is None

    reporter.beat("ORDERS")
    assert reporter._thread is not None
    reporter.stop()

    assert len(session.calls) == 1
    assert reporter.targets == {}
    assert reporter._thread is None


def test_stop_flushes_a_pending_beat_when_no_thread_is_running():
    reporter, session = make(responses=[FakeResponse(200)])

    reporter.beat("ORDERS")
    reporter.stop()

    assert len(session.calls) == 1


def test_loop_ends_when_every_target_is_disabled():
    reporter, _ = make(responses=[FakeResponse(404)])
    reporter.beat("ORDERS")

    reporter._run(threading.Event())  # ficaria esperando para sempre se não terminasse

    assert reporter.beat("ORDERS") is False


def test_loop_delivers_once_and_ends_when_stopped():
    reporter, session = make(responses=[FakeResponse(200)])
    reporter.beat("ORDERS")
    stop = threading.Event()
    stop.set()

    reporter._run(stop)

    assert len(session.calls) == 1


def test_register_discards_previous_state():
    reporter, session = make()
    reporter.beat("ORDERS")

    reporter.register([target("ORDERS")])
    reporter.deliver()

    assert session.calls == []
    assert reporter.beats == {}


def test_remove_drops_only_that_target():
    reporter, session = make([target("ORDERS"), target("BILLING")])
    reporter.beat("ORDERS")
    reporter.beat("BILLING")

    reporter.remove("ORDERS")
    reporter.deliver()

    assert [c["json"]["service"] for c in session.calls] == ["BILLING"]


def test_interval_comes_from_the_first_target():
    reporter, _ = make([target("ORDERS", interval=30.0), target("BILLING", interval=90.0)])

    assert reporter.interval == 30.0
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_heartbeat_reporter.py -q`
Expected: FAIL com `ModuleNotFoundError: jaylog.host.heartbeat_reporter`.

- [ ] **Step 3: Implementar**

`src/jaylog/host/heartbeat_reporter.py`:

```python
"""
``jaylog.heartbeat()``: sinal explícito de que o loop do usuário está progredindo.

**Uma thread por processo, um alvo por serviço.** A thread é criada na 1ª chamada
de ``beat()`` (quem não usa heartbeat não paga thread) e percorre os serviços com
beat pendente a cada ciclo. O estado é um contador por serviço, não um horário:
quem carimba a hora é o backend, então relógio de VM à deriva não atrapalha, e um
beat que chega durante o POST continua pendente (o contador lido *antes* do POST é
o que se marca como enviado).

**Endpoint e chave por serviço.** Cada ``JaylogSettings`` tem a própria URL e API
key; o POST usa as do item daquele serviço, passadas por requisição — a sessão não
fixa ``x-api-key`` como a de métricas faz, porque lá o destino é um só.

Sem buffer e sem backoff próprio: o ciclo já é de 60 s, e o único beat que importa
é o mais recente. Falha transitória só mantém o beat pendente.
"""

import sys
import threading
import time
import warnings
from dataclasses import dataclass

import requests
import urllib3

from jaylog._version import PROTOCOL_VERSION, __version__
from jaylog.diagnostics import emit as debug
from jaylog.host import reporter as host_reporter
from jaylog.runtime import RUN_ID

_STOP_JOIN_TIMEOUT = 2.0
_DEFAULT_INTERVAL = 60.0


def _warn(message: str) -> None:
    print(f"[jaylog] heartbeat: {message}", file=sys.stderr)


@dataclass(frozen=True)
class HeartbeatTarget:
    """Destino do heartbeat de um serviço: tudo vem do ``JaylogSettings`` dele."""

    service: str
    endpoint: str
    api_key: str
    interval: float = _DEFAULT_INTERVAL
    timeout: float = 5.0
    proxy: str | None = None
    #: seguro por padrão; quem decide o valor real é o `log_http_verify` do item,
    #: passado por `configure()` (hoje `False` por compatibilidade, `True` na 0.4.0)
    verify: bool | str = True


class JaylogHeartbeatReporter:
    """
    ``session`` e ``request_resend`` são injetáveis para os testes cobrirem a
    máquina de estados sem rede. ``autostart=False`` permite dirigir ``deliver()``
    à mão, sem a thread.
    """

    def __init__(self, *, session=None, request_resend=None, autostart: bool = True) -> None:
        self._session = session
        self._request_resend = request_resend or host_reporter.request_resend
        self._autostart = autostart
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        #: um Event por thread: um `stop()` que não espera a thread presa num POST
        #: não pode deixar um Event novo e "limpo" nas mãos dela.
        self._stop_event: threading.Event | None = None
        self._targets: dict[str, HeartbeatTarget] = {}
        self._beats: dict[str, int] = {}
        self._sent: dict[str, int] = {}
        self._disabled: set[str] = set()
        self._ignored: set[str] = set()

    # ------------------------------------------------------------------
    # estado
    # ------------------------------------------------------------------

    @property
    def targets(self) -> dict[str, HeartbeatTarget]:
        with self._lock:
            return dict(self._targets)

    @property
    def beats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._beats)

    @property
    def interval(self) -> float:
        """Vale o intervalo do primeiro alvo registrado (um por processo)."""
        with self._lock:
            for target in self._targets.values():
                return target.interval
        return _DEFAULT_INTERVAL

    def register(self, targets: list[HeartbeatTarget]) -> None:
        """Substitui os alvos e descarta todo o estado anterior."""
        with self._lock:
            self._targets = {t.service: t for t in targets}
            self._beats.clear()
            self._sent.clear()
            self._disabled.clear()
            self._ignored.clear()

    def remove(self, service: str) -> None:
        with self._lock:
            self._targets.pop(service, None)
            self._beats.pop(service, None)
            self._sent.pop(service, None)
            self._disabled.discard(service)

    def beat(self, service: str) -> bool:
        """
        Registra um beat. Só incrementa um contador sob lock: sem rede e sem
        bloquear, porque roda dentro do loop do usuário. ``False`` = ignorado
        (serviço sem alvo elegível, ou já desativado).
        """
        with self._lock:
            if service not in self._targets or service in self._disabled:
                if service not in self._ignored:
                    self._ignored.add(service)
                    debug(
                        "heartbeat",
                        f"beat ignorado; serviço={service}; motivo=sem alvo elegível ou desativado",
                    )
                return False
            self._beats[service] = self._beats.get(service, 0) + 1
            if self._autostart and (self._thread is None or not self._thread.is_alive()):
                self._start_locked()
        return True

    # ------------------------------------------------------------------
    # thread
    # ------------------------------------------------------------------

    def _start_locked(self) -> None:
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(self._stop_event,),
            name="jaylog-heartbeat",
            daemon=True,
        )
        self._thread.start()
        debug("heartbeat", f"thread iniciada; serviços={','.join(self._targets)}")

    def _run(self, stop: threading.Event) -> None:
        try:
            while True:
                self.deliver()
                if not self._has_active_targets():
                    debug("heartbeat", "thread encerrada; motivo=nenhum serviço ativo")
                    return
                if stop.wait(self.interval):
                    return
        except Exception as exc:  # pragma: no cover - _post já captura a rede
            _warn(f"thread encerrada por erro inesperado: {exc}")

    def _has_active_targets(self) -> bool:
        with self._lock:
            return any(service not in self._disabled for service in self._targets)

    def stop(self, timeout: float = _STOP_JOIN_TIMEOUT) -> None:
        """
        Para a thread e envia o que ainda estiver pendente, para a execução não
        terminar com o último beat perdido. O envio final só sai se a thread já
        terminou: disputar o estado com um POST em voo não vale o risco.
        """
        deadline = time.monotonic() + timeout
        with self._lock:
            thread, event = self._thread, self._stop_event
        if event is not None:
            event.set()
        alive = False
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
            alive = thread.is_alive()
        if not alive:
            self.deliver(deadline=deadline)
        self._reset()

    def _reset(self) -> None:
        with self._lock:
            self._thread = None
            self._stop_event = None
            self._targets = {}
            self._beats.clear()
            self._sent.clear()
            self._disabled.clear()
            self._ignored.clear()

    # ------------------------------------------------------------------
    # entrega (síncrona — os testes chamam direto)
    # ------------------------------------------------------------------

    def deliver(self, deadline: float | None = None) -> None:
        """Um POST por serviço com beat pendente; respeita ``deadline`` (monotonic)."""
        with self._lock:
            pending = [
                (target, self._beats[target.service])
                for target in self._targets.values()
                if target.service not in self._disabled
                and self._beats.get(target.service, 0) > self._sent.get(target.service, 0)
            ]
        for target, seq in pending:
            timeout = target.timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    debug("heartbeat", "envio interrompido; motivo=prazo de encerramento esgotado")
                    return
                timeout = min(timeout, remaining)
            if self._post(target, timeout):
                with self._lock:
                    if target.service in self._targets:
                        self._sent[target.service] = max(self._sent.get(target.service, 0), seq)

    def _post(self, target: HeartbeatTarget, timeout: float) -> bool:
        """``True`` = entregue. ``False`` = tentar de novo no próximo ciclo (ou desativado)."""
        if self._session is None:
            self._session = requests.Session()
        kwargs: dict = {
            "json": {"run_id": RUN_ID, "service": target.service},
            "headers": {
                "x-api-key": target.api_key,
                "x-jaylog-version": __version__,
                "x-jaylog-protocol": str(PROTOCOL_VERSION),
                "x-jaylog-run-id": RUN_ID,
            },
            "timeout": timeout,
            "verify": target.verify,
        }
        if target.proxy:
            kwargs["proxies"] = {"http": target.proxy, "https": target.proxy}

        try:
            debug(
                "heartbeat",
                f"requisição HTTP iniciada; método=POST; endpoint={target.endpoint}; "
                f"serviço={target.service}; timeout={timeout:g}s",
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                response = self._session.post(target.endpoint, **kwargs)
        except Exception as exc:
            debug(
                "heartbeat",
                f"requisição HTTP falhou; serviço={target.service}; "
                f"tipo={type(exc).__name__}; detalhe={exc}",
            )
            return False  # rede fora, DNS, timeout: o beat fica pendente

        status = response.status_code
        debug(
            "heartbeat",
            f"resposta HTTP recebida; serviço={target.service}; status={status}",
        )

        if 200 <= status < 300:
            if response.headers.get("x-jaylog-host-required") == "1":
                accepted = self._request_resend(target.service)
                debug(
                    "heartbeat",
                    f"backend solicitou sincronização do host; serviço={target.service}; "
                    f"reenvio_aceito={accepted}",
                )
            return True

        if status == 429 or status >= 500:
            return False

        with self._lock:
            self._disabled.add(target.service)
        if status in (404, 405):
            # Backend anterior à rota. O mesmo contrato do /logs/host-metrics:
            # "serviço desconhecido" é 422, nunca 404, para este caso ser inequívoco.
            _warn(
                f"o backend não suporta POST {target.endpoint} (HTTP {status}); "
                f"heartbeat de '{target.service}' desativado neste processo"
            )
        else:
            _warn(
                f"POST {target.endpoint} devolveu HTTP {status}; "
                f"heartbeat de '{target.service}' desativado: {_body_excerpt(response)}"
            )
        return False


def _body_excerpt(response, limit: int = 300) -> str:
    try:
        return response.text[:limit]
    except Exception:
        return "<corpo ilegível>"


# ----------------------------------------------------------------------
# singleton de módulo — as funções resolvem `_reporter` a cada chamada, para os
# testes poderem trocá-lo por uma instância com sessão falsa
# ----------------------------------------------------------------------

_reporter = JaylogHeartbeatReporter()


def register(targets: list[HeartbeatTarget]) -> None:
    _reporter.register(targets)


def beat(service: str) -> bool:
    return _reporter.beat(service)


def remove(service: str) -> None:
    _reporter.remove(service)


def stop(timeout: float = _STOP_JOIN_TIMEOUT) -> None:
    _reporter.stop(timeout)


def targets() -> dict[str, HeartbeatTarget]:
    return _reporter.targets


__all__ = [
    "HeartbeatTarget",
    "JaylogHeartbeatReporter",
    "beat",
    "register",
    "remove",
    "stop",
    "targets",
]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_heartbeat_reporter.py -q && uv run ruff check .`
Expected: todos os testes passam, sem `sleep` e sem rede; ruff sem erros.

- [ ] **Step 5: Commit**

```bash
git add src/jaylog/host/heartbeat_reporter.py tests/test_heartbeat_reporter.py
git commit -m "feat(heartbeat): per-service heartbeat reporter with a dedicated thread" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 8: `jaylog.heartbeat()` e integração com `configure()`/`shutdown()`

**Files:**
- Modify: `JL/src/jaylog/logger.py` (imports ~linha 14; `configure()` ~linha 111; novas funções após `_start_metrics_reporter`; `shutdown()` ~linha 505)
- Modify: `JL/src/jaylog/__init__.py`
- Test: `JL/tests/test_logger_lifecycle.py`

**Interfaces:**
- Consumes: `HeartbeatTarget`, `heartbeat_reporter.register/beat/remove/stop/targets` (Task 7); `JaylogSettings.host_heartbeat_*` e `effective_host_heartbeat_endpoint` (Task 6).
- Produces: `jaylog.heartbeat(service: str | None = None) -> None`, exportada em `jaylog/__init__.py`.

- [ ] **Step 1: Escrever os testes que falham**

Em `tests/test_logger_lifecycle.py`, ao final (reusa `_http_settings` e `_no_threads` já existentes):

```python
class _OkSession:
    """Sessão sem rede que aceita qualquer POST."""

    def post(self, *_args, **_kwargs):
        import types

        return types.SimpleNamespace(status_code=200, headers={}, text="")


def _fake_heartbeat(monkeypatch, *, autostart: bool = False):
    from jaylog.host import heartbeat_reporter
    from jaylog.host.heartbeat_reporter import JaylogHeartbeatReporter

    fake = JaylogHeartbeatReporter(
        session=_OkSession(), request_resend=lambda service: True, autostart=autostart
    )
    monkeypatch.setattr(heartbeat_reporter, "_reporter", fake)
    return fake


def _heartbeat_threads() -> list:
    return [t for t in threading.enumerate() if t.name == "jaylog-heartbeat" and t.is_alive()]


def test_configure_registers_a_heartbeat_target_per_eligible_logger(monkeypatch) -> None:
    _no_threads(monkeypatch)
    fake = _fake_heartbeat(monkeypatch)

    configure(
        [
            _http_settings("ORDERS", host_heartbeat_interval=30),
            _http_settings(
                "BILLING", log_http_endpoint="https://other/logs/add", log_http_api_key="k2"
            ),
            _http_settings("OFF", host_heartbeat_enabled=False),
            _http_settings("NOHOST", host_report_enabled=False),
            _http_settings("NOKEY", log_http_api_key=None),
        ]
    )

    targets = fake.targets
    assert sorted(targets) == ["BILLING", "ORDERS"]
    assert targets["ORDERS"].endpoint == "https://api.example/logs/heartbeat"
    assert targets["ORDERS"].interval == 30
    assert targets["BILLING"].endpoint == "https://other/logs/heartbeat"
    assert targets["BILLING"].api_key == "k2"


def test_heartbeat_defaults_to_first_registered_logger(monkeypatch) -> None:
    import jaylog

    _no_threads(monkeypatch)
    fake = _fake_heartbeat(monkeypatch)
    configure([_http_settings("ORDERS"), _http_settings("BILLING")])

    jaylog.heartbeat()
    jaylog.heartbeat("BILLING")
    jaylog.heartbeat("NOPE")

    assert fake.beats == {"ORDERS": 1, "BILLING": 1}


def test_heartbeat_is_a_silent_noop_without_configure() -> None:
    import jaylog

    jaylog.heartbeat()
    jaylog.heartbeat("ORDERS")

    assert _heartbeat_threads() == []


def test_heartbeat_is_a_silent_noop_after_shutdown(monkeypatch) -> None:
    import jaylog

    _no_threads(monkeypatch)
    fake = _fake_heartbeat(monkeypatch)
    configure(_http_settings("ORDERS"))
    shutdown()

    jaylog.heartbeat()

    assert fake.beats == {}
    assert _heartbeat_threads() == []


def test_heartbeat_never_raises_even_if_the_reporter_breaks(monkeypatch) -> None:
    import jaylog
    from jaylog.host import heartbeat_reporter

    _no_threads(monkeypatch)
    configure(_http_settings("ORDERS"))

    def boom(service):
        raise RuntimeError("quebrou")

    monkeypatch.setattr(heartbeat_reporter, "beat", boom)

    jaylog.heartbeat()  # não pode propagar para o loop do usuário


def test_heartbeat_thread_starts_on_first_call_and_stops_on_shutdown(monkeypatch) -> None:
    import jaylog

    _no_threads(monkeypatch)
    _fake_heartbeat(monkeypatch, autostart=True)
    configure(_http_settings("ORDERS"))
    assert _heartbeat_threads() == []

    jaylog.heartbeat()
    assert len(_heartbeat_threads()) == 1

    shutdown()
    assert _heartbeat_threads() == []


def test_shutdown_of_one_logger_removes_only_its_heartbeat_target(monkeypatch) -> None:
    _no_threads(monkeypatch)
    fake = _fake_heartbeat(monkeypatch)
    configure([_http_settings("ORDERS"), _http_settings("BILLING")])

    shutdown("BILLING")
    assert sorted(fake.targets) == ["ORDERS"]

    shutdown()
    assert fake.targets == {}


def test_reconfigure_discards_heartbeat_state(monkeypatch) -> None:
    import jaylog

    _no_threads(monkeypatch)
    fake = _fake_heartbeat(monkeypatch)
    configure(_http_settings("ORDERS"))
    jaylog.heartbeat()
    assert fake.beats == {"ORDERS": 1}

    configure(_http_settings("ORDERS"))

    assert fake.beats == {}
    assert sorted(fake.targets) == ["ORDERS"]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_logger_lifecycle.py -q`
Expected: FAIL (`AttributeError: module 'jaylog' has no attribute 'heartbeat'` e alvos não registrados).

- [ ] **Step 3: Implementar em `logger.py`**

Imports (linhas ~14-16):

```python
from jaylog.host import heartbeat_reporter, metrics_reporter, reporter, schedule_reporter, win32
...
from jaylog.host.heartbeat_reporter import HeartbeatTarget
```

Em `configure()`, logo depois de `_start_metrics_reporter(items)`:

```python
    _register_heartbeat_targets(items)
```

Depois de `_start_metrics_reporter(...)` (antes de `_start_schedule_reporter`):

```python
def _register_heartbeat_targets(items: list[JaylogSettings]) -> None:
    """
    Um alvo por serviço elegível, cada um com o endpoint e a chave do seu item.

    Não cria thread nem faz rede: a thread só nasce na 1ª chamada de
    ``heartbeat()``, então quem não usa o recurso não paga nada por ele.
    """
    targets: list[HeartbeatTarget] = []
    for item in items:
        if not (item.host_report_enabled and item.host_heartbeat_enabled):
            diagnostics.emit(
                "heartbeat",
                f"alvo não registrado; serviço={item.app_name}; "
                "motivo=coleta de host ou heartbeat desativado",
            )
            continue
        endpoint = item.effective_host_heartbeat_endpoint
        if not endpoint or not item.log_http_api_key:
            diagnostics.emit(
                "heartbeat",
                f"alvo não registrado; serviço={item.app_name}; "
                "motivo=endpoint ou credencial ausente",
            )
            continue
        targets.append(
            HeartbeatTarget(
                service=item.app_name,
                endpoint=endpoint,
                api_key=item.log_http_api_key,
                interval=item.host_heartbeat_interval,
                timeout=item.log_http_timeout,
                proxy=item.log_http_proxy,
                verify=item.log_http_verify,
            )
        )
    heartbeat_reporter.register(targets)


def heartbeat(service: str | None = None) -> None:
    """
    Avisa que o loop do serviço está progredindo, mesmo sem emitir logs.

    Chame **dentro do loop**, uma vez por iteração::

        while True:
            do_work()
            jaylog.heartbeat()

    É barata (incrementa um contador) e nunca bloqueia nem levanta: o envio ao
    backend acontece numa thread própria, no máximo uma vez por
    ``host_heartbeat_interval``. O backend passa a tratar o serviço como ativo
    pelo mais recente entre o último log e o último heartbeat.

    Sem argumento vale para o **primeiro** logger registrado (a mesma regra do
    ``get_logger()`` sem nome); com ``service="BILLING"``, para aquele
    ``app_name``. Antes de ``configure()``, para um serviço desconhecido ou com o
    heartbeat desativado, não faz nada.

    Não chame de uma thread separada que continue viva com o loop travado: isso
    anularia o sinal.
    """
    try:
        name = service if service is not None else next(iter(_settings_registry), None)
        if name is None:
            diagnostics.emit("heartbeat", "beat ignorado; motivo=configure() ainda não foi chamado")
            return
        heartbeat_reporter.beat(name)
    except Exception:  # noqa: BLE001 - o loop do usuário nunca quebra por causa do heartbeat
        pass
```

Em `shutdown()`, no ramo `if name is None:` acrescentar `heartbeat_reporter.stop()` (depois de `metrics_reporter.stop()`), e no ramo `else:` acrescentar `heartbeat_reporter.remove(name)`:

```python
    if name is None:
        reporter.stop_all()
        metrics_reporter.stop()
        heartbeat_reporter.stop()
        schedule_reporter.stop()
    else:
        reporter.stop(name)
        heartbeat_reporter.remove(name)
        active = metrics_reporter.active()
        ...
```

Em `src/jaylog/__init__.py`:

```python
from jaylog.logger import configure, get_logger, heartbeat, shutdown
...
__all__ = [
    "configure",
    "get_logger",
    "heartbeat",
    "shutdown",
    ...
]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest -q && uv run ruff check .`
Expected: toda a suíte passa (inclusive os testes de ciclo de vida antigos) e o ruff não reclama.

- [ ] **Step 5: Commit**

```bash
git add src/jaylog/logger.py src/jaylog/__init__.py tests/test_logger_lifecycle.py
git commit -m "feat(heartbeat): expose jaylog.heartbeat() and wire it into configure/shutdown" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 9: Bump de versão e verificação final do cliente

**Files:**
- Modify: `JL/pyproject.toml` (`version`), `JL/uv.lock`
- Modify: `JL/docs/superpowers/specs/2026-09-30-heartbeat-design.md` (status)

- [ ] **Step 1: Subir a versão**

Em `pyproject.toml`: `version = "0.3.0a8"`. Depois:

```bash
uv lock
git diff --stat
```

Expected: só `pyproject.toml` e `uv.lock` mudam (uma linha cada, como no bump `0.3.0a6`).

- [ ] **Step 2: Suíte e lint completos**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: tudo verde. Se o `format --check` apontar os arquivos novos, rodar `uv run ruff format .` e conferir que só eles mudaram.

- [ ] **Step 3: Teste manual contra o dashboard local (opcional, exige backend da Parte 1 rodando)**

```python
# /tmp/hb_loop.py
import time, jaylog
from jaylog import JaylogSettings, configure

configure(JaylogSettings(app_name="VERIFY-HB", log_http_endpoint="http://localhost:3000/logs/add",
                         log_http_api_key="<chave local>", host_heartbeat_interval=10))
while True:
    jaylog.heartbeat()
    time.sleep(1)
```

Expected: nenhuma linha de log nova, e `last_heartbeat_at` do `log_hosts` dessa execução avançando a cada ~10 s.

- [ ] **Step 4: Atualizar o status do spec e commitar**

No cabeçalho do spec, trocar `Status: aprovado em conversa, aguardando revisão do spec escrito` por `Status: implementado (cliente 0.3.0a8, protocolo 5)`.

```bash
git add pyproject.toml uv.lock docs/superpowers/specs/2026-09-30-heartbeat-design.md docs/superpowers/plans/2026-09-30-heartbeat.md
git commit -m "bump to version 0.3.0a8" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Parte 3 — Frontend (`FE`)

### Task 10: `isStopped` sobre `last_activity_at`

**Files:**
- Modify: `FE/src/pages/Dashboard/interfaces.ts` (`Log`, `LogHost`)
- Modify: `FE/src/pages/Dashboard/utils.ts:66-69`
- Test: `FE/src/pages/Dashboard/utils.test.ts`

**Interfaces:**
- Consumes: campo `last_activity_at` de `GET /logs/last` (Task 3).
- Produces: `Log.last_activity_at?: string`; `LogHost.last_heartbeat_at?: string | null`; `isStopped(log)` usando `last_activity_at ?? log_timestamp`. Os consumidores (`GroupedView`, `LogModal`, tabela e filtro parado/ativo de `Dashboard/index.tsx`) **não mudam**.

> Desvio do spec: o spec cita `lib/schemas.ts`, mas o frontend não tem schema Zod para logs (só a interface TypeScript); nada a fazer lá.

- [ ] **Step 1: Criar a branch**

```bash
cd /home/gpocas/projects/frontend-analytics-logging
git switch -c feat/heartbeat
```

- [ ] **Step 2: Escrever os testes que falham**

Em `src/pages/Dashboard/utils.test.ts`: acrescentar `setSystemTime` e `afterEach` ao import de `bun:test`, `isStopped` ao import de `./utils`, `import type { Log } from "./interfaces";`, e ao final do arquivo:

```ts
describe("isStopped", () => {
  const LIMIT = 15;

  function logAt(overrides: Partial<Log>): Log {
    return {
      id: "log-1",
      service: "svc-1",
      hostname: "h1",
      username: "u1",
      ipv4: "10.0.0.1",
      is_exception: false,
      log_level: "INFO",
      log_message: "m",
      log_timestamp: new Date(NOW - 60 * MIN).toISOString(),
      service_stop_limit: LIMIT,
      ...overrides,
    };
  }

  beforeEach(() => setSystemTime(new Date(NOW)));
  afterEach(() => setSystemTime());

  test("sem limite de parada não há veredito", () => {
    expect(isStopped(logAt({ service_stop_limit: undefined }))).toBeNull();
  });

  test("backend antigo (sem last_activity_at): vale o último log", () => {
    expect(isStopped(logAt({ log_timestamp: new Date(NOW - 20 * MIN).toISOString() }))).toBe(true);
    expect(isStopped(logAt({ log_timestamp: new Date(NOW - 5 * MIN).toISOString() }))).toBe(false);
  });

  test("log antigo com heartbeat recente: ativo", () => {
    const log = logAt({
      log_timestamp: new Date(NOW - 120 * MIN).toISOString(),
      last_activity_at: new Date(NOW - 1 * MIN).toISOString(),
    });
    expect(isStopped(log)).toBe(false);
  });

  test("log e heartbeat antigos: parado", () => {
    const log = logAt({
      log_timestamp: new Date(NOW - 120 * MIN).toISOString(),
      last_activity_at: new Date(NOW - 30 * MIN).toISOString(),
    });
    expect(isStopped(log)).toBe(true);
  });
});
```

(Adaptar os imports do topo do arquivo: `import { test, expect, describe, beforeEach, afterEach, setSystemTime } from "bun:test";`.)

- [ ] **Step 3: Rodar e ver falhar**

Run: `bun test src/pages/Dashboard/utils.test.ts`
Expected: FAIL no caso "log antigo com heartbeat recente" (hoje devolve `true`) e erro de tipo nos campos novos.

- [ ] **Step 4: Implementar**

Em `src/pages/Dashboard/interfaces.ts`, na interface `Log`, depois de `log_timestamp: string;`:

```ts
	/** Mais recente entre o último log e o último heartbeat da instância. */
	last_activity_at?: string;
```

Na interface `LogHost`, depois de `last_seen_at: string;`:

```ts
	/** Último `jaylog.heartbeat()` da execução; ausente em backend antigo. */
	last_heartbeat_at?: string | null;
```

Em `src/pages/Dashboard/utils.ts`:

```ts
export function isStopped(log: Log): boolean | null {
  if (log.service_stop_limit == null) return null;
  // `last_activity_at` já é o mais recente entre o último log e o último
  // heartbeat; sem ele (backend antigo) vale o último log, como sempre valeu.
  const lastActivity = log.last_activity_at ?? log.log_timestamp;
  return Date.now() - new Date(lastActivity).getTime() > log.service_stop_limit * 60_000;
}
```

- [ ] **Step 5: Rodar e ver passar**

Run: `bun test && bun run typecheck`
Expected: toda a suíte passa e o typecheck não reclama.

- [ ] **Step 6: Commit**

```bash
git add src/pages/Dashboard/interfaces.ts src/pages/Dashboard/utils.ts src/pages/Dashboard/utils.test.ts
git commit -m "feat(dashboard): derive stopped status from last_activity_at" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

### Task 11: "Último heartbeat" na aba de ambiente

**Files:**
- Modify: `FE/src/pages/Dashboard/components/LogEnvironmentTab.tsx` (seção "Identificação", após "Visto por último", linha ~45)
- Test: `FE/src/pages/Dashboard/components/LogModal.test.tsx` (bloco `describe("LogDetail / ambiente da execução")`, linha ~240)

**Interfaces:**
- Consumes: `LogHost.last_heartbeat_at` (Task 10).

- [ ] **Step 1: Escrever os testes que falham**

Dentro de `describe("LogDetail / ambiente da execução", ...)`, depois do primeiro `test`:

```tsx
	test("mostra o último heartbeat da execução", async () => {
		getSpy.mockImplementation(async (path: string) => {
			if (path.startsWith("/logs/hosts")) {
				return [{ ...EXECUTION_HOST, last_heartbeat_at: "2026-08-30T10:02:30Z" }] as never;
			}
			return [] as never;
		});

		renderWithProviders(<LogDetail log={EXECUTION_LOG} onClose={() => {}} />);
		fireEvent.mouseDown(screen.getByRole("tab", { name: "Ambiente da execução" }), { button: 0 });

		await waitFor(() => expect(screen.getByText("Último heartbeat")).toBeTruthy());
		expect(screen.getByText("30/08/2026 10:02:30")).toBeTruthy();
	});

	test("execução sem heartbeat diz que nenhum foi recebido", async () => {
		getSpy.mockImplementation(async (path: string) => {
			if (path.startsWith("/logs/hosts")) return [EXECUTION_HOST] as never;
			return [] as never;
		});

		renderWithProviders(<LogDetail log={EXECUTION_LOG} onClose={() => {}} />);
		fireEvent.mouseDown(screen.getByRole("tab", { name: "Ambiente da execução" }), { button: 0 });

		await waitFor(() => expect(screen.getByText("Último heartbeat")).toBeTruthy());
		expect(screen.getByText("Nenhum recebido")).toBeTruthy();
	});
```

O texto "Nenhum recebido" (e não "Não informado") é de propósito: o primeiro teste do bloco usa `getByText("Não informado")` e falharia com dois campos iguais.

- [ ] **Step 2: Rodar e ver falhar**

Run: `bun test src/pages/Dashboard/components/LogModal.test.tsx`
Expected: FAIL nos dois testes novos (`Último heartbeat` não existe).

- [ ] **Step 3: Implementar**

Em `LogEnvironmentTab.tsx`, logo depois do campo "Visto por último":

```tsx
        <Field
          label="Último heartbeat"
          value={host.last_heartbeat_at ? displayDate(host.last_heartbeat_at) : "Nenhum recebido"}
        />
```

- [ ] **Step 4: Rodar e ver passar**

Run: `bun test && bun run typecheck`
Expected: toda a suíte passa, inclusive o teste antigo de `"Não informado"`.

- [ ] **Step 5: Commit**

```bash
git add src/pages/Dashboard/components/LogEnvironmentTab.tsx src/pages/Dashboard/components/LogModal.test.tsx
git commit -m "feat(dashboard): show last heartbeat in the environment tab" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Parte 4 — Documentação (`BK`)

### Task 12: Página, variáveis de ambiente e API no jaylog-book

**Files:**
- Create: `BK/docs/producao/heartbeat.md`
- Modify: `BK/zensical.toml` (navegação, seção "Produção")
- Modify: `BK/docs/configuracao/variaveis-de-ambiente.md` (após a linha de `JAYLOG_HOST_METRICS_HTTP_ENDPOINT`, ~linha 33)
- Modify: `BK/docs/referencia/api.md` (nova seção após `shutdown()`; ajustar o texto do `shutdown()`)

- [ ] **Step 1: Criar a branch**

```bash
cd /home/gpocas/misc/jaylog-book
git switch -c feat/heartbeat
```

- [ ] **Step 2: Criar `docs/producao/heartbeat.md`**

````markdown
# Heartbeat de Serviço

A partir da 0.3.0a8, você pode avisar o backend de que o loop do seu serviço está **progredindo**, mesmo quando ele não emite nenhum log. Sem isso, o painel decide que um serviço está parado só pelo horário do último log: um bot em loop silencioso aparece como "Parado" em 15 minutos e, sem logs por 45 dias, é excluído por inatividade.

## Como usar

Chame `jaylog.heartbeat()` **dentro do loop**, uma vez por iteração:

```python
import jaylog
from jaylog import JaylogSettings, configure

configure(JaylogSettings(app_name="meu-bot"))

while True:
    processar_fila()
    jaylog.heartbeat()
```

A chamada é barata (só incrementa um contador) e nunca bloqueia nem levanta exceção. O envio acontece numa thread própria, no máximo uma vez por `JAYLOG_HOST_HEARTBEAT_INTERVAL` (60 s por padrão) e só se houve `heartbeat()` desde o último envio.

O backend passa a considerar o serviço ativo pelo **mais recente** entre o último log e o último heartbeat. Quem não chama `heartbeat()` continua exatamente como antes: vale o último log.

!!! warning "Coloque a chamada onde o trabalho acontece"

    O heartbeat existe para detectar loop **travado**. Se você o chamar de uma thread separada que continua viva enquanto o loop principal está preso, o serviço parecerá saudável sem estar.

## Vários serviços no mesmo processo

Sem argumento, `heartbeat()` vale para o **primeiro** logger registrado em `configure()` (a mesma regra do `get_logger()` sem nome). Para outro serviço, informe o `app_name`:

```python
configure([JaylogSettings(app_name="ORDERS"), JaylogSettings(app_name="BILLING")])

jaylog.heartbeat()            # ORDERS
jaylog.heartbeat("BILLING")   # BILLING
```

Cada serviço usa o endpoint e a API key do próprio `JaylogSettings`. Um nome desconhecido, ou um serviço com o heartbeat desativado, é ignorado sem erro.

## Configuração

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `JAYLOG_HOST_HEARTBEAT_ENABLED` | `true` | Liga o heartbeat. Só vale se `JAYLOG_HOST_REPORT_ENABLED` também estiver ligado |
| `JAYLOG_HOST_HEARTBEAT_INTERVAL` | `60` | Segundos entre envios (mínimo `10`) |
| `JAYLOG_HOST_HEARTBEAT_HTTP_ENDPOINT` | derivado | Override do endpoint; por padrão `/logs/add` vira `/logs/heartbeat` |

!!! note

    O painel considera um serviço parado após 15 minutos sem atividade. Um `JAYLOG_HOST_HEARTBEAT_INTERVAL` próximo ou acima disso faria o serviço aparecer como parado entre um envio e outro.

## Quando o backend não suporta

Se o backend responder 404/405 (versão anterior à rota) ou rejeitar a chave, o jaylog avisa **uma vez** no `stderr` e desativa o heartbeat **daquele serviço** pelo resto do processo. O restante do jaylog não é afetado. Falhas de rede e respostas 429/5xx só mantêm o beat pendente para o próximo ciclo.

Ao encerrar (`shutdown()` ou fim do processo), um último envio sai se houver beat pendente.
````

- [ ] **Step 3: Navegação**

Em `zensical.toml`, na seção "Produção", depois da linha de "Métricas de Recursos":

```toml
    { "Heartbeat de Serviço" = "producao/heartbeat.md" },
```

- [ ] **Step 4: Variáveis de ambiente**

Em `docs/configuracao/variaveis-de-ambiente.md`, depois da linha de `JAYLOG_HOST_METRICS_HTTP_ENDPOINT`, mantendo o alinhamento da tabela:

```markdown
| `JAYLOG_HOST_HEARTBEAT_ENABLED` | NÃO          | `true`    | Permite o envio de `jaylog.heartbeat()` (exige `HOST_REPORT_ENABLED`)   |
| `JAYLOG_HOST_HEARTBEAT_INTERVAL` | NÃO         | `60`      | Segundos entre envios de heartbeat (mínimo `10`)                        |
| `JAYLOG_HOST_HEARTBEAT_HTTP_ENDPOINT` | NÃO    | derivado  | Override para o endpoint de heartbeat; por padrão `/logs/add` vira `/logs/heartbeat` |
```

- [ ] **Step 5: Referência da API**

Em `docs/referencia/api.md`, depois da seção `shutdown()`, acrescentar:

````markdown
## `heartbeat(service=None)`

Avisa o backend de que o loop do serviço está progredindo, mesmo sem emitir logs. Chame dentro do loop; é barata e nunca levanta. Sem argumento vale para o primeiro logger registrado; com `service="BILLING"`, para aquele `app_name`. Veja [Heartbeat de Serviço](../producao/heartbeat.md).

```python
import jaylog

while True:
    processar_fila()
    jaylog.heartbeat()
```
````

E no texto do `shutdown()`, trocar "e o coletor de métricas de recursos (que envia uma última amostra)" por "o coletor de [métricas de recursos](../producao/metricas-de-recursos.md) (que envia uma última amostra) e o [heartbeat](../producao/heartbeat.md) (que envia o último beat pendente)".

- [ ] **Step 6: Conferir o build e commitar**

Run: `git diff --stat` (4 arquivos: 1 novo + 3 editados). Se o repositório tiver o binário do Zensical instalado (`zensical build`), rodar e conferir que o build não emite aviso de link quebrado; se não houver, conferir os links relativos a olho.

```bash
git add docs zensical.toml
git commit -m "docs: add heartbeat page, env vars and API reference" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Cobertura do spec**

| Spec | Task |
|------|------|
| §3.1 API pública (`heartbeat`, resolução de `service`, no-op) | 8 (+ testes de 7) |
| §3.2 Configuração (3 campos, validador, endpoint derivado) | 6 |
| §3.3 Reporter (alvos, contador, thread, envio, respostas, `stop`) | 7 |
| §3.4 Versões (protocolo 5, `0.3.0a8`) | 6, 9 |
| §3.5 Threads por processo (só na 1ª chamada) | 7 (`test_first_beat_starts_the_thread...`), 8 |
| §4.1 Migration e schema | 1 |
| §4.2 `POST /logs/heartbeat` | 2 |
| §4.3 `/logs/last` com `last_activity_at` | 3 |
| §4.4 Crons (`cleanupInactiveServices`, `warnInactiveServices`, `cleanupLogHosts`) | 4 |
| §5 Contrato HTTP | 2, 7 |
| §6 Frontend (`isStopped`, tipos, aba de ambiente) | 10, 11 (`lib/schemas.ts` descartado: não existe schema de log) |
| §7 Testes | 6–8, 10, 11; backend pela verificação da Task 5 |
| §8 Ordem de entrega | Partes 1 → 2 → 3 |
| Documentação (não listada no spec) | 12 |

**2. Placeholders:** nenhum "TBD"/"TODO". A única instrução com elipse é copiar a lista de campos já existente do handler `/last` na Task 3 (marcada com comentário explícito), porque repeti-la duplicaria ~30 linhas sem mudança.

**3. Consistência de tipos:** `HeartbeatTarget`, `JaylogHeartbeatReporter.{register,beat,remove,deliver,stop,_run,targets,beats,interval}`, funções de módulo `register/beat/remove/stop/targets`, `SERVICE_LAST_ACTIVITY`, `last_heartbeat_at`, `last_activity_at` e `effective_host_heartbeat_endpoint` têm o mesmo nome e assinatura em todas as tasks que os usam.

**4. Review Focus:** cada linha tem teste na task dona (1→T8, 2→T7, 3→T7, 4→T3+T5, 5→T4+T5, 6→T8).
