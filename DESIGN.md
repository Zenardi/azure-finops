# Design

## Goals

Turn raw Azure cost + telemetry into (1) clear spend visualizations by resource,
resource type and region, and (2) prioritized, explainable right-sizing /
shutdown recommendations, with an optional **guarded** path to execute them —
all pluggable on the AI provider and surfaced on Grafana.

## Data flow (one "run")

`collect` (inventory + cost [+ metrics + advisor]) → `analyze` (rollups + FinOps
rules) → `recommend` (AI reconciliation + executive summary) → `store` (Postgres),
orchestrated by `orchestrator.run_pipeline()` and driven by the Typer CLI or the
APScheduler loop. Each run writes a `runs` audit row and is fully idempotent
(all fact writes are `INSERT … ON CONFLICT DO UPDATE`, deduped within the batch).

## Two-credential model (least privilege)

- **Read SP** (collection): Reader + Cost Management Reader + Monitoring Reader
  (+ Log Analytics Reader for memory). Built by `auth.read_credential()`.
- **Write SP** (remediation): a separate custom role limited to compute/network
  write on the allowed resource groups. Built by `auth.write_credential()`.

`DefaultAzureCredential` falls back to Managed Identity / `az` CLI when SP env
vars are absent, so the same code runs locally, in containers, and in Azure.

## Storage model (Postgres / TimescaleDB)

`resource_id` is the join key everywhere. Fact tables use natural composite keys
so they can become TimescaleDB hypertables (partition column must be in every
unique index); on plain Postgres they are ordinary tables. `init_db()` promotes
them best-effort and (re)creates the Grafana views — each optional step in its
own transaction so a missing extension degrades gracefully.

- `resources` (dimension) · `cost_snapshots` (fact, daily, hypertable on
  `usage_date`) · `utilization_samples` (fact, hypertable on `ts`) ·
  `utilization_rollups` · `recommendations` · `remediation_actions` (audit) ·
  `advisor_recommendations` · `ai_summaries` · `runs`.
- Grafana views: `v_cost_by_resource`, `v_cost_by_type`, `v_cost_by_region`,
  `v_latest_recommendations`, `v_savings_by_category`.

## Grafana: two complementary datasources

- **Postgres** — the curated, historical, AI-enriched cost/recommendation layer
  (cost by type/region/resource, daily trend, recommendations, savings). This is
  the path that makes Cost Management data + AI output visible, since Grafana has
  no native Cost Management support.
- **Azure Monitor (native)** — live, high-resolution raw metrics + Log Analytics
  drill-down for a selected resource. Data-links on `resource_id` let a user jump
  from a cost/recommendation row to that resource's live metrics.

## Cost collection notes

The Cost Management Query API caps groupings at 2 dimensions, so the live path
groups by `ResourceId` + `ServiceName` and the orchestrator enriches
resource_type/location from the inventory (joined on lower-cased resource_id).
Default metric is **Amortized** for right-sizing (reservations / savings plans
distort Actual for a single resource). Cost data lags ~8–24h and is non-final
until invoice — treated as estimates. `nextLink` pagination and 429/5xx backoff
(`resilience.with_retry`, honouring `Retry-After`/`x-ms-ratelimit-*`).

## FinOps heuristics (Phase 2)

Gate on `data_completeness ≥ 0.8`. Merge/dedupe with Azure Advisor; AI reconciles.
- **Shutdown/idle VM (deallocate):** cpu_p95 < 3% and cpu_max < 5% and low net/IO
  while running. Savings = compute only (disks keep billing).
- **Downsize VM:** cpu_p95 < 40% (and mem_p95 < 50% when available, else CPU-only
  at lower confidence). Savings = price(current) − price(target) via the Azure
  Retail Prices API.
- **Idle/orphaned:** unattached disk, unassociated public IP, empty App Service
  plan → delete. All figures are estimates.

## AI layer (Phase 3)

Provider abstraction (`ai/base.AIProvider`) with Anthropic (`claude-opus-4-8`,
adaptive thinking, strict-JSON + tolerant parse) and an OpenAI-compatible
implementation (local models via `AI_BASE_URL`), chosen by config. Input is
**aggregated** (rollups + cost + candidate flags + advisor signal), never raw
samples; large inventories are capped to top-N by cost and map-reduced; resource
names/tags are sanitized (prompt-injection surface); results cached per `run_id`.
Output is a pydantic-validated list of prioritized recommendations (action, risk,
confidence, est. savings, rationale, caveats) + an executive summary.

## Remediation (Phase 5, guarded)

Dry-run is default. Flow: recommendation `approved` (UI) → dry-run preview →
guardrails (`REMEDIATION_ENABLED`, allow-list RG, `finops:exclude` tag,
staleness/idempotency) → real action via the write SP → every attempt recorded in
`remediation_actions`. Actions: VM deallocate, VM resize, delete unattached disk,
delete idle public IP.

## Key risks

Cost API throttling/latency (handled by backoff + Amortized default); metric
retention ~93 days (PT1H aggregates); **memory needs Azure Monitor Agent or Log
Analytics** — downsize degrades to CPU-only with a caveat when absent; AI token
cost on large inventories (rollups + cap + map-reduce + cache); savings accuracy
(disks keep billing on shutdown; idle Standard IPs bill unattached — all
estimates); secrets via `.env`/`env_file`, Grafana Azure Monitor SP via
`secureJsonData`, never logged.
