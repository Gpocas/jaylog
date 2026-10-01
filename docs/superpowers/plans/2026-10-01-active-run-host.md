# Host da execução ativa Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O ambiente atual do serviço no dashboard (protocolo, versão do pacote, SO, Python, git) passa a vir da execução ativa, mesmo que ela não tenha emitido nenhum log; o detalhe de um log histórico continua mostrando o host do próprio `run_id`.

**Architecture:** O cliente já registra o host de cada execução no `configure()` e se recupera via `x-jaylog-host-required` (nenhum código novo lá). O backend faz `GET /logs/last` devolver `active_run_id` por grupo `(service, hostname, username)`: a linha de `log_hosts` com maior `GREATEST(last_seen_at, last_heartbeat_at)`. O modal do frontend passa `active_run_id ?? run_id` para a aba de ambiente, que já consome `GET /logs/hosts?service_id&run_id`.

**Tech Stack:** Bun + Elysia + Drizzle + Postgres (`backend-nn-analytics`); Bun + React + `bun:test` (`frontend-analytics-logging`); Python 3.10–3.13 + pytest + ruff + `uv` (`jaylog`); MkDocs/Zensical (`jaylog-book`).

**Spec:** `docs/superpowers/specs/2026-10-01-active-run-host-design.md` (neste repositório). Leia o spec antes de começar; este plano o implementa na ordem de entrega da seção 9.

**Repositórios (caminhos absolutos):**

| Sigla | Caminho | Tasks |
|-------|---------|-------|
| `BE`  | `/home/gpocas/projects/backend-nn-analytics` | 1–2 |
| `FE`  | `/home/gpocas/projects/frontend-analytics-logging` | 3 |
| `JL`  | `/home/gpocas/misc/jaylog` | 4 |
| `BK`  | `/home/gpocas/misc/jaylog-book` | 5 |

Cada repositório recebe a branch `feat/active-run-host`, criada na primeira task dele, e commits próprios.

## Global Constraints

- **Sem migration, sem rota nova, sem cron novo, sem bump de protocolo ou de pacote.** `PROTOCOL_VERSION` continua `5`.
- `GET /logs/last` ganha **apenas** `active_run_id: string | null`; nenhum campo existente muda ou sai. `run_id` continua sendo o do **último log**.
- Execução ativa = linha de `log_hosts` do grupo `(service, hostname, username)` com maior `GREATEST(last_seen_at, last_heartbeat_at)` (`GREATEST` ignora `NULL`); desempate por `log_hosts.id` decrescente (uuidv7, ordenável por tempo).
- A execução ativa **não** exige heartbeat: execução sem `last_heartbeat_at` concorre por `last_seen_at`.
- O `active_run_id` é calculado **uma vez por grupo** em CTE e juntado *depois* do `DISTINCT ON` dos logs (um subselect correlacionado rodaria por linha de log, até 1000 por grupo). Casamento por igualdade exata de `service/hostname/username`, como o CTE `heartbeats` existente.
- `GET /logs/:id` **não** muda: o detalhe de um log histórico mostra o host do `run_id` dele.
- Frontend: sem `active_run_id` (backend antigo, ou grupo sem host) cai em `run_id`, como hoje.
- Comentários explicam o *porquê*, em português, no ponto da decisão (convenção dos repositórios).
- Commits terminam com a linha `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (segundo `-m`).
- **`BE/.env` aponta `DATABASE_URL` para um Postgres remoto (`34.122.100.238`). Nenhum comando desta implementação pode rodar contra ele.** `db:migrate` e o script de verificação só rodam com `DATABASE_URL` sobrescrito para o Postgres local descartável da Task 2.

## Review Focus

Entradas e falhas que o spec implica e que mais provavelmente quebram na prática. Cada linha tem o teste na task dona do código.

1. Serviço que já logou na execução antiga e cuja execução nova está silenciosa: `active_run_id` é o da nova e `run_id` continua o do log antigo (Task 2, cenário A).
2. Execução antiga ainda com heartbeat fresco e execução nova sem heartbeat: vence a de maior `GREATEST`, não a de `last_seen_at` mais recente (Task 2, cenário B).
3. Grupo com logs e **nenhuma** linha em `log_hosts` (cliente pré-protocolo 2, ou host nunca entregue): `active_run_id` é `null`, a linha do grupo continua aparecendo e o frontend cai em `run_id` (Tasks 2 e 3).
4. Log legado **sem** `run_id` mas com `active_run_id`: a aba de ambiente busca o host da execução ativa em vez de mostrar "Contexto de execução indisponível" (Task 3).
5. Detalhe de um log vindo de `GET /logs/:id`: continua no host do próprio `run_id` e sem o rótulo "Execução ativa" (Tasks 2 e 3).
6. Serviço silencioso sem nenhum log depois do restart do backend: o `x-jaylog-host-required` devolvido pelo `/logs/heartbeat` acorda o `JaylogHostReporter` registrado, sem log algum no processo (Task 4).

---

## Parte 1 — Backend (`BE`)

> Convenção do repositório: `bun test` cobre só código puro em `src/lib/`; **não existe harness de banco**. A Task 1 é validada por `bun run typecheck` + `bun test` (nada pode regredir) e a Task 2 exercita `/logs/last`, `/logs/hosts` e `/logs/:id` de ponta a ponta contra um Postgres local descartável.

### Task 1: `active_run_id` em `GET /logs/last`

**Files:**
- Modify: `BE/src/routes/logs.ts` (`logLastSchema` linhas 47-77; handler de `/last` linhas 322-411)

**Interfaces:**
- Consumes: `logHosts` (`run_id`, `service`, `hostname`, `username`, `last_seen_at`, `last_heartbeat_at`, `id`) já importado em `logs.ts`; `desc`, `and`, `eq`, `sql` já importados.
- Produces: campo `active_run_id: string | null` em cada item de `GET /logs/last`, consumido pela Task 3.

- [ ] **Step 1: Criar a branch e conferir o baseline**

```bash
cd /home/gpocas/projects/backend-nn-analytics
git checkout -b feat/active-run-host
bun run typecheck && bun test
```

Expected: typecheck sem erros e a suíte passando (é o baseline; qualquer falha aqui já existia).

- [ ] **Step 2: Acrescentar o campo ao schema de resposta**

Em `logLastSchema`, logo depois de `last_activity_at: t.Date(),` (e do comentário dele):

```ts
  // Execução ativa do grupo: a linha de log_hosts de maior GREATEST(last_seen_at,
  // last_heartbeat_at). Diverge de `run_id` (o do último log) justamente quando há
  // uma execução nova e silenciosa, que ainda não logou. É por ele que o dashboard
  // busca o ambiente atual em /logs/hosts.
  active_run_id:        t.Nullable(t.String()),
```

- [ ] **Step 3: Criar o CTE da execução ativa e juntá-lo**

No handler de `/last`, logo depois do CTE `heartbeats` (termina na linha ~353), acrescentar:

```ts
          // Execução ativa por instância. Sem filtro de heartbeat: uma execução que
          // nunca chamou jaylog.heartbeat() concorre por `last_seen_at`, gravado no
          // configure() e a cada reenvio de host. DISTINCT ON + ORDER BY GREATEST
          // escolhe uma linha por grupo; o desempate pelo id (uuidv7) torna a
          // escolha determinística quando duas execuções empatam no instante.
          const activeRuns = db.$with("active_runs").as(
            db
              .selectDistinctOn([logHosts.service, logHosts.hostname, logHosts.username], {
                service: logHosts.service,
                hostname: logHosts.hostname,
                username: logHosts.username,
                active_run_id: sql<string>`${logHosts.run_id}`.as("active_run_id"),
              })
              .from(logHosts)
              .orderBy(
                logHosts.service,
                logHosts.hostname,
                logHosts.username,
                sql`GREATEST(${logHosts.last_seen_at}, ${logHosts.last_heartbeat_at}) DESC`,
                desc(logHosts.id),
              ),
          );
```

Trocar `.with(heartbeats)` por `.with(heartbeats, activeRuns)`.

Na lista de colunas, depois de `last_activity_at: ...,`:

```ts
              active_run_id: activeRuns.active_run_id,
```

Depois do `.leftJoin(heartbeats, ...)` existente, antes do `.where(serviceFilter)`:

```ts
            .leftJoin(
              activeRuns,
              and(
                eq(activeRuns.service, logs.service),
                eq(activeRuns.hostname, logs.hostname),
                eq(activeRuns.username, logs.username),
              ),
            )
```

- [ ] **Step 4: Typecheck e suíte**

Run: `bun run typecheck && bun test`
Expected: sem erros de tipo e a mesma suíte do Step 1 passando. Se o typecheck reclamar do tipo de `active_run_id` na resposta (`string | null` vs schema), o `leftJoin` já o torna anulável; `logLastSchema` aceita `t.Nullable(t.String())`. Se reclamar de `sql<string>\`${logHosts.run_id}\`.as(...)`, usar `logHosts.run_id` direto no CTE e ler `activeRuns.run_id` (renomeando a chave na lista de colunas para `active_run_id`).

- [ ] **Step 5: Commit**

```bash
git add src/routes/logs.ts
git commit -m "feat(logs): expose active_run_id per instance in /logs/last" -m "A aba de ambiente do dashboard lia o host do último log, então uma execução nova e silenciosa continuava exibindo o ambiente da anterior. O campo aponta a execução de maior GREATEST(last_seen_at, last_heartbeat_at)." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Verificação de ponta a ponta no Postgres local

**Files:**
- Create (NÃO commitar): `BE/tmp-verify-active-run.ts`

**Interfaces:**
- Consumes: Task 1.

- [ ] **Step 1: Subir um Postgres local descartável e migrar**

```bash
cd /home/gpocas/projects/backend-nn-analytics
docker run -d --name arh-verify-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=arh -p 55432:5432 postgres:18
export ARH_DB=postgresql://postgres:pw@localhost:55432/arh
DATABASE_URL=$ARH_DB DATABASE_SSL=false bun db:migrate
```

Expected: todas as migrations aplicam sem erro. Se o container ainda não aceitar conexões, aguardar ~5 s e repetir. **Conferir que a URL usada é `localhost:55432`.** Se a porta 55432 estiver ocupada (resto de uma verificação anterior), `docker rm -f hb-verify-pg` e repetir.

- [ ] **Step 2: Escrever o script de verificação**

`tmp-verify-active-run.ts` (recusa rodar fora do Postgres local):

```ts
import assert from "node:assert/strict";
import { eq } from "drizzle-orm";
import { app } from "./src/app";
import { db } from "./src/db";
import { logHosts, logs, services, sessions, users } from "./src/db/schema";

if (!/localhost:55432/.test(process.env.DATABASE_URL ?? "")) {
  throw new Error(`recusado: DATABASE_URL não é o Postgres local de verificação (${process.env.DATABASE_URL})`);
}

const sha = (v: string) => new Bun.CryptoHasher("sha256").update(v).digest("hex");
const ago = (ms: number) => new Date(Date.now() - ms);
const MIN = 60_000, HOUR = 60 * MIN, DAY = 24 * HOUR;
const uuid = (n: number) => `${String(n).repeat(8)}-${String(n).repeat(4)}-4${String(n).repeat(3)}-8${String(n).repeat(3)}-${String(n).repeat(12)}`;
const RUN_OLD = uuid(1), RUN_NEW = uuid(2);
const RUN_HB = uuid(3), RUN_QUIET = uuid(4);
const RUN_E1 = uuid(5), RUN_E2 = uuid(6);

const [owner] = await db.insert(users).values({ name: "verify", email: "v@x.com", password: "x", profile: "admin" }).returning();
await db.insert(sessions).values({ user_id: owner!.id, token_hash: sha("tok"), expires_at: new Date(Date.now() + DAY) });

const mkService = async (name: string) =>
  (await db.insert(services).values({ name, owner: owner!.id }).returning())[0]!;
const mkHost = (service: string, run_id: string, extra: Partial<typeof logHosts.$inferInsert> = {}) =>
  db.insert(logHosts).values({ run_id, service, protocol_version: 5, jaylog_version: "0.3.0a9", hostname: "H1", username: "U1", ...extra });
const mkLog = (service: string, run_id: string | null, when: Date) =>
  db.insert(logs).values({ service, hostname: "H1", username: "U1", ipv4: "1.1.1.1", log_level: "INFO", log_message: "x", log_timestamp: when, run_id });
const get = (path: string) => app.handle(new Request(`http://localhost${path}`, { headers: { authorization: "Bearer tok" } }));

type Row = { service: string; run_id: string | null; active_run_id: string | null };
const lastRow = async (serviceId: string) => {
  const res = await get("/logs/last");
  assert.equal(res.status, 200);
  const row = ((await res.json()) as Row[]).find((r) => r.service === serviceId);
  assert.ok(row, "o grupo aparece em /logs/last");
  return row;
};

// A: execução nova e silenciosa. O último log é da antiga (protocolo 4); a nova (protocolo 5) só tem host.
const a = await mkService("VERIFY-A");
await mkHost(a.id, RUN_OLD, { protocol_version: 4, last_seen_at: ago(3 * HOUR) });
await mkHost(a.id, RUN_NEW);
await mkLog(a.id, RUN_OLD, ago(2 * HOUR));
let row = await lastRow(a.id);
assert.equal(row.run_id, RUN_OLD, "run_id segue sendo o do último log");
assert.equal(row.active_run_id, RUN_NEW, "active_run_id é a execução nova, sem logs");
let hosts = (await (await get(`/logs/hosts?service_id=${a.id}&run_id=${row.active_run_id}`)).json()) as { protocol_version: number }[];
assert.equal(hosts[0]!.protocol_version, 5, "o host da execução ativa é o de protocolo 5");
console.log("OK A execução nova e silenciosa");

// B: heartbeat fresco numa execução com last_seen antigo vence last_seen mais recente sem heartbeat.
const b = await mkService("VERIFY-B");
await mkHost(b.id, RUN_HB, { last_seen_at: ago(3 * HOUR), last_heartbeat_at: new Date() });
await mkHost(b.id, RUN_QUIET, { last_seen_at: ago(1 * HOUR) });
await mkLog(b.id, RUN_QUIET, ago(30 * MIN));
row = await lastRow(b.id);
assert.equal(row.active_run_id, RUN_HB, "vence a de maior GREATEST(last_seen_at, last_heartbeat_at)");
console.log("OK B heartbeat vence last_seen");

// C: logs sem nenhuma linha em log_hosts.
const c = await mkService("VERIFY-C");
await mkLog(c.id, null, ago(5 * MIN));
row = await lastRow(c.id);
assert.equal(row.active_run_id, null, "sem host, active_run_id é null");
console.log("OK C grupo sem log_hosts");

// E: duas execuções sem heartbeat; a de last_seen_at mais recente vence.
const e = await mkService("VERIFY-E");
await mkHost(e.id, RUN_E1, { last_seen_at: ago(2 * HOUR) });
await mkHost(e.id, RUN_E2, { last_seen_at: ago(1 * HOUR) });
await mkLog(e.id, RUN_E1, ago(90 * MIN));
row = await lastRow(e.id);
assert.equal(row.active_run_id, RUN_E2, "sem heartbeat, vale last_seen_at");
console.log("OK E fallback em last_seen_at");

// D: o detalhe de um log não ganha active_run_id (histórico não se mistura com estado atual).
const [logA] = await db.select({ id: logs.id }).from(logs).where(eq(logs.service, a.id));
const detail = (await (await get(`/logs/${logA!.id}`)).json()) as Record<string, unknown>;
assert.equal(detail.run_id, RUN_OLD);
assert.ok(!("active_run_id" in detail), "GET /logs/:id não expõe active_run_id");
console.log("OK D detalhe do log inalterado");
process.exit(0);
```

- [ ] **Step 3: Rodar contra o Postgres local**

Run: `DATABASE_URL=$ARH_DB DATABASE_SSL=false bun tmp-verify-active-run.ts`
Expected: imprime `OK A…`, `OK B…`, `OK C…`, `OK E…`, `OK D…` e encerra com código 0. Ruído de conexão com o Redis (`ECONNREFUSED`) é esperado e irrelevante aqui. Se `/logs/last` devolver 500 de validação de resposta, o campo novo não está no `logLastSchema` (Task 1, Step 2). Se `active_run_id` vier `undefined` em vez de `null`, a coluna não entrou na lista de colunas do `select` (Task 1, Step 3). Se o `uuid()` do script for rejeitado como uuid inválido, trocar por literais `crypto.randomUUID()`.

- [ ] **Step 4: Plano de execução (só olhar)**

```bash
docker exec arh-verify-pg psql -U postgres -d arh -c "EXPLAIN SELECT DISTINCT ON (service, hostname, username) run_id FROM log_hosts ORDER BY service, hostname, username, GREATEST(last_seen_at, last_heartbeat_at) DESC, id DESC;"
```

Expected: um `Sort` + `Unique` sobre `log_hosts`. Em produção o volume é de uma linha por execução com limpeza em 45 dias; **não criar índice** a menos que a tabela real já esteja na casa das centenas de milhares de linhas (registrar no PR e decidir à parte).

- [ ] **Step 5: Limpar**

```bash
docker rm -f arh-verify-pg
rm tmp-verify-active-run.ts
git status --short
```

Expected: `git status` limpo (nada a commitar nesta task).

---

## Parte 2 — Frontend (`FE`)

### Task 3: Aba de ambiente usa a execução ativa

**Files:**
- Modify: `FE/src/pages/Dashboard/interfaces.ts` (interface `Log`, após `run_id`, linha ~38)
- Modify: `FE/src/pages/Dashboard/components/LogEnvironmentTab.tsx` (props e retorno final)
- Modify: `FE/src/pages/Dashboard/components/LogModal.tsx` (linha ~505)
- Test: `FE/src/pages/Dashboard/components/LogModal.test.tsx` (bloco `describe("LogDetail / ambiente da execução")`, linha ~240)

**Interfaces:**
- Consumes: `active_run_id` de `GET /logs/last` (Task 1).
- Produces: `Log.active_run_id?: string | null`; `LogEnvironmentTab` ganha a prop opcional `activeRun?: boolean`.

- [ ] **Step 1: Criar a branch e conferir o baseline**

```bash
cd /home/gpocas/projects/frontend-analytics-logging
git checkout -b feat/active-run-host
bun test && bun run typecheck
```

Expected: suíte e typecheck passando.

- [ ] **Step 2: Escrever os testes que falham**

Em `LogModal.test.tsx`, logo abaixo da constante `EXECUTION_HOST` (ou junto das outras constantes de topo), acrescentar:

```tsx
const ACTIVE_RUN_ID = "d5a9c0b2-8e3f-4a24-b6e9-9a7c2f3d4e5b";

const hostCalls = () =>
	getSpy.mock.calls
		.map(([path]: unknown[]) => String(path))
		.filter((path: string) => path.startsWith("/logs/hosts"));
```

Dentro de `describe("LogDetail / ambiente da execução", ...)`, depois do teste "execução sem heartbeat diz que nenhum foi recebido":

```tsx
	test("mostra o ambiente da execução ativa, e não o do último log", async () => {
		getSpy.mockImplementation(async (path: string) => {
			if (path.startsWith("/logs/hosts")) {
				return [{ ...EXECUTION_HOST, run_id: ACTIVE_RUN_ID, protocol_version: 5 }] as never;
			}
			return [] as never;
		});

		renderWithProviders(
			<LogDetail log={{ ...EXECUTION_LOG, active_run_id: ACTIVE_RUN_ID }} onClose={() => {}} />,
		);
		fireEvent.mouseDown(screen.getByRole("tab", { name: "Ambiente da execução" }), { button: 0 });

		await waitFor(() => expect(screen.getByText("Execução ativa")).toBeTruthy());
		expect(hostCalls()).toEqual([`/logs/hosts?service_id=svc-1&run_id=${ACTIVE_RUN_ID}`]);
	});

	test("sem active_run_id usa a execução do log e não rotula como ativa", async () => {
		getSpy.mockImplementation(async (path: string) => {
			if (path.startsWith("/logs/hosts")) return [EXECUTION_HOST] as never;
			return [] as never;
		});

		renderWithProviders(<LogDetail log={EXECUTION_LOG} onClose={() => {}} />);
		fireEvent.mouseDown(screen.getByRole("tab", { name: "Ambiente da execução" }), { button: 0 });

		await waitFor(() => expect(screen.getByText("Python e ambiente virtual")).toBeTruthy());
		expect(hostCalls()).toEqual([`/logs/hosts?service_id=svc-1&run_id=${EXECUTION_LOG.run_id}`]);
		expect(screen.queryByText("Execução ativa")).toBeNull();
	});

	test("active_run_id nulo cai no run_id do log", async () => {
		getSpy.mockImplementation(async (path: string) => {
			if (path.startsWith("/logs/hosts")) return [EXECUTION_HOST] as never;
			return [] as never;
		});

		renderWithProviders(
			<LogDetail log={{ ...EXECUTION_LOG, active_run_id: null }} onClose={() => {}} />,
		);
		fireEvent.mouseDown(screen.getByRole("tab", { name: "Ambiente da execução" }), { button: 0 });

		await waitFor(() => expect(screen.getByText("Python e ambiente virtual")).toBeTruthy());
		expect(hostCalls()).toEqual([`/logs/hosts?service_id=svc-1&run_id=${EXECUTION_LOG.run_id}`]);
		expect(screen.queryByText("Execução ativa")).toBeNull();
	});

	test("log legado sem run_id mas com execução ativa busca o ambiente ativo", async () => {
		getSpy.mockImplementation(async (path: string) => {
			if (path.startsWith("/logs/hosts")) {
				return [{ ...EXECUTION_HOST, run_id: ACTIVE_RUN_ID }] as never;
			}
			return [] as never;
		});

		renderWithProviders(
			<LogDetail log={{ ...SAMPLE_LOG, active_run_id: ACTIVE_RUN_ID }} onClose={() => {}} />,
		);
		fireEvent.mouseDown(screen.getByRole("tab", { name: "Ambiente da execução" }), { button: 0 });

		await waitFor(() => expect(screen.getByText("Execução ativa")).toBeTruthy());
		expect(screen.queryByText("Contexto de execução indisponível.")).toBeNull();
		expect(hostCalls()).toEqual([`/logs/hosts?service_id=svc-1&run_id=${ACTIVE_RUN_ID}`]);
	});
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `bun test src/pages/Dashboard/components/LogModal.test.tsx`
Expected: FAIL nos quatro testes novos (o primeiro e o quarto por não existir "Execução ativa"; o segundo e o terceiro podem passar já, o que é esperado: protegem o fallback). Erro de tipo em `active_run_id` não impede `bun test`, mas aparece no typecheck do Step 5.

- [ ] **Step 4: Implementar**

`interfaces.ts`, na interface `Log`, logo depois de `run_id`:

```ts
	/**
	 * Execução ativa da instância (`GET /logs/last`): a de maior atividade em
	 * `log_hosts`, mesmo sem logs. Diverge de `run_id` quando há uma execução nova
	 * e silenciosa. Ausente em `GET /logs/:id`, que é o histórico de um log.
	 */
	active_run_id?: string | null;
```

`LogEnvironmentTab.tsx`, assinatura:

```tsx
export function LogEnvironmentTab({
  serviceId,
  runId,
  activeRun = false,
}: {
  serviceId: string;
  runId?: string | null;
  /** `runId` é a execução ativa do serviço, e não a de um log específico. */
  activeRun?: boolean;
}) {
```

E o retorno final (última linha do componente) passa a:

```tsx
  return (
    <div className="space-y-6">
      {activeRun ? (
        <p className="text-xs font-semibold uppercase tracking-wider text-emerald-400">
          Execução ativa
        </p>
      ) : null}
      <EnvironmentDetails host={host} />
    </div>
  );
```

`LogModal.tsx` (~linha 505):

```tsx
              <LogEnvironmentTab
                serviceId={log.service}
                runId={log.active_run_id ?? log.run_id}
                activeRun={!!log.active_run_id}
              />
```

- [ ] **Step 5: Rodar e ver passar**

Run: `bun test && bun run typecheck`
Expected: toda a suíte passa, inclusive "não consulta hosts para log legado sem run_id" (esse `SAMPLE_LOG` não tem `active_run_id`) e o teste antigo de `"Não informado"`.

- [ ] **Step 6: Commit**

```bash
git add src/pages/Dashboard/interfaces.ts src/pages/Dashboard/components/LogEnvironmentTab.tsx src/pages/Dashboard/components/LogModal.tsx src/pages/Dashboard/components/LogModal.test.tsx
git commit -m "feat(dashboard): show the active run's environment" -m "O modal recebe a linha de /logs/last; a aba de ambiente passa a buscar o host de active_run_id (cai em run_id sem ele). O detalhe de um log vindo de /logs/:id segue no host do próprio run_id." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Parte 3 — Cliente (`JL`)

### Task 4: Testes de regressão do registro de host sem logs

Nenhum código de produção muda. Os dois testes travam o comportamento de que backend e frontend agora dependem.

**Files:**
- Test: `JL/tests/test_logger_lifecycle.py` (fim do arquivo)
- Test: `JL/tests/test_heartbeat_reporter.py` (após `test_host_required_header_requests_resend_for_that_service`, linha ~193)

**Interfaces:**
- Consumes: `logger._start_host_reporters` (`logger.py:166`), `reporter.register/request_resend` (`host/reporter.py`), `JaylogHeartbeatReporter` / `HeartbeatTarget` (`host/heartbeat_reporter.py`).

- [ ] **Step 1: Criar a branch**

```bash
cd /home/gpocas/misc/jaylog
git checkout -b feat/active-run-host
uv run pytest -q
```

Expected: baseline verde.

- [ ] **Step 2: Escrever os dois testes**

Em `tests/test_logger_lifecycle.py`, no fim:

```python
def test_configure_registers_the_host_before_any_log_is_emitted(monkeypatch) -> None:
    from jaylog.host.metrics_reporter import JaylogMetricsReporter

    started: list = []

    class _FakeHostReporter:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.service = kwargs["service"]

        def start(self) -> None:
            started.append(self)

        def stop(self, timeout: float = 0.0) -> None:
            pass

    monkeypatch.setattr(logger_module, "JaylogHostReporter", _FakeHostReporter)
    monkeypatch.setattr(JaylogMetricsReporter, "start", lambda self: None)

    configure(_http_settings("ORDERS"))

    # nenhum get_logger()/log foi chamado: o dashboard só enxerga o ambiente da
    # execução ativa porque o registro sai do configure(), e não do 1º log
    assert [r.kwargs["service"] for r in started] == ["ORDERS"]
    assert started[0].kwargs["endpoint"] == "https://api.example/logs/host"
```

Em `tests/test_heartbeat_reporter.py`, depois de `test_host_required_header_requests_resend_for_that_service`:

```python
def test_host_required_from_heartbeat_wakes_the_registered_host_reporter_without_any_log():
    from jaylog.host import reporter as host_registry
    from jaylog.host.reporter import JaylogHostReporter

    host = JaylogHostReporter(service="ORDERS", endpoint="https://api/ORDERS/logs/host", api_key="k")
    host.sent = True  # o backend perdeu o registro depois de uma entrega bem-sucedida
    host_registry.register(host)
    try:
        session = FakeSession([FakeResponse(200, {"x-jaylog-host-required": "1"})])
        reporter = JaylogHeartbeatReporter(session=session, autostart=False)  # request_resend real
        reporter.register([target()])

        reporter.beat("ORDERS")
        reporter.deliver()

        assert host.sent is False
        assert host._wakeup.is_set()
    finally:
        host_registry.stop_all()
```

- [ ] **Step 3: Rodar**

Run: `uv run pytest tests/test_logger_lifecycle.py tests/test_heartbeat_reporter.py -v`
Expected: PASS nos dois testes novos. **Eles não devem falhar:** descrevem o comportamento atual. Se algum falhar, a premissa do spec (seção 2, "o que já existe") está errada: parar e reportar antes de qualquer outra mudança. Para provar que não são vácuos, quebrar de propósito e restaurar: comentar `reporter.register(host_reporter)` e `host_reporter.start()` em `_start_host_reporters` (o 1º teste deve falhar) e trocar `request_resend` por `lambda s: False` no construtor padrão do `JaylogHeartbeatReporter` (o 2º deve falhar). `git checkout src` desfaz.

- [ ] **Step 4: Lint e suíte completa**

Run: `uv run ruff check . && uv run pytest -q`
Expected: sem avisos do ruff e a suíte inteira passando.

- [ ] **Step 5: Commit**

```bash
git add tests/test_logger_lifecycle.py tests/test_heartbeat_reporter.py
git commit -m "test(host): pin host registration without logs and resend on heartbeat" -m "O dashboard passa a depender de que toda execução registre o host no configure() e de que o heartbeat reacione o reenvio quando o backend perde o registro. Nenhum código de produção muda." -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Parte 4 — Documentação (`BK`)

### Task 5: Registro de ambiente sem logs no jaylog-book

**Files:**
- Modify: `BK/docs/producao/registro-de-ambiente.md` (parágrafo da linha 7, e nova seção antes de `## Próximo passo`, linha 64)

**Interfaces:**
- Consumes: comportamento descrito no spec (seções 2 e 3).

- [ ] **Step 1: Criar a branch**

```bash
cd /home/gpocas/misc/jaylog-book
git checkout -b feat/active-run-host
```

- [ ] **Step 2: Escrever a seção**

Antes de `## Próximo passo`, inserir:

```markdown
## Ambiente atual sem logs

O registro de ambiente sai do `configure()`, não do primeiro log. Por isso o dashboard mostra o ambiente da **execução ativa** (protocolo, versão do pacote, Python, Git…) mesmo que ela ainda não tenha emitido nenhum log. O detalhe de um log antigo continua mostrando o ambiente da execução que o emitiu.

A execução ativa é a de atividade mais recente da instância (mesmo `service`, `hostname` e `username`): o último heartbeat ou, sem `jaylog.heartbeat()`, o momento em que o registro foi enviado.

!!! tip "Serviço silencioso"
    Se o backend perder o registro enquanto o serviço está sem logar (restart, limpeza de dados), quem faz o jaylog reenviar o ambiente é o [heartbeat](heartbeat.md): a resposta do backend pede o reenvio. Serviços que nem logam nem chamam `jaylog.heartbeat()` só se recuperam no próximo restart.
```

O `!!! tip` segue o padrão de `docs/producao/secrets.md`; o título entre aspas segue o de `docs/producao/heartbeat.md`.

- [ ] **Step 3: Build da documentação**

Run: o comando de build do repositório (ver `README.md` do book; tipicamente `uv run zensical build` ou `uv run mkdocs build --strict`).
Expected: build sem erros e sem links quebrados. `site/` é saída de build: conferir com `git status --short` que ele não entra no commit (se não estiver no `.gitignore`, não o adicione).

- [ ] **Step 4: Commit**

```bash
git add docs/producao/registro-de-ambiente.md
git commit -m "docs: document the active run's environment without logs" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**Cobertura do spec** (seções do `2026-10-01-active-run-host-design.md`):

- §2 "O que já existe": travado pelos testes da Task 4.
- §3 decisões e §4 contrato (`active_run_id`): Task 1 (schema + query) e Task 2 (cenários A–E).
- §5 backend: Task 1; "Sem rota/migration/cron novo" está em Global Constraints.
- §6 frontend: Task 3 (interface, prop, modal). O detalhe via `/logs/:id` permanece sem mudança de código; é coberto pelo cenário D (Task 2) e pelo teste "sem active_run_id" (Task 3).
- §7 cliente (testes + documentação): Tasks 4 e 5.
- §9 ordem de entrega: BE → FE → JL → BK.
- §10 riscos: duas instâncias vivas no mesmo grupo é risco herdado e documentado no spec, sem task.

**Divergência já resolvida:** o spec foi ajustado para o rótulo único "Execução ativa" (não existe "anterior": sem `active_run_id` o componente não sabe se o host exibido é o atual).

**Placeholders:** nenhum "TBD"/"TODO"; todo passo de código traz o código. Os comandos de build do book (`uv run zensical build --clean`) vêm do README dele.

**Consistência de tipos/nomes:** `active_run_id` (BE schema, CTE, FE `Log`), `activeRun` (prop), `ACTIVE_RUN_ID` e `hostCalls()` (testes) usados de forma idêntica entre as tasks.
