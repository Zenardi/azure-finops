# 1 · Overview & Architecture

## What CloudWarden is

**CloudWarden** is a **multi-cloud Governance-as-Code + FinOps platform** — think of it as a
self-hosted blend of *Cloud Custodian / Stacklet* (policy-driven governance and
remediation) and a *FinOps cost-optimization engine* (idle/rightsizing detection,
savings estimates, an AI-written executive summary). It covers **Azure, AWS, and
GCP** behind a single `provider` dimension, so posture, assets, and execution
health roll up per cloud and across all clouds at once.

It runs fully **offline in mock mode** (`FINOPS_MOCK=1`) using bundled fixtures —
no cloud credentials needed to explore every feature — and switches to live cloud
APIs by flipping `FINOPS_MOCK=0` and supplying credentials.

### What it does, in one paragraph

It **collects** an inventory of cloud resources, metrics, and cost; **analyzes**
them for idle/oversized resources and produces **rightsizing recommendations**
with estimated savings and an **AI executive summary**; **evaluates governance
policies** (Cloud Custodian `c7n`-style) against your resources in three modes
(scheduled pull, ad-hoc push, real-time event); tracks a full **AssetDB** with
change history and relationships; optionally **remediates** violations behind
guardrails and an approval workflow; and surfaces everything through a **Next.js
web UI**, a **FastAPI HTTP API**, and **Grafana dashboards**.

## The four services (docker-compose stack)

| Service | Image / build | Host port | Role |
|---------|---------------|-----------|------|
| `db` | `timescale/timescaledb:2.17.2-pg16` | `5432` | PostgreSQL + TimescaleDB — all state, hypertables for time series, SQL views |
| `backend` | `./backend` (FastAPI/uvicorn) | `8000` | HTTP API, orchestrator pipeline, policy engine, scheduler, CLI |
| `grafana` | `grafana/grafana:11.4.0` | `3000` | Dashboards, reads Postgres directly |
| `frontend` | `./frontend` (Next.js standalone) | **`3001`** → container `3000` | Web UI |

> **Port note:** the web UI is published on host **3001** (container 3000).
> Grafana is on **3000**. The API is on **8000**.

Named Docker volumes persist state: `pgdata` (database), `appdata` (backend
`/data` — exports, artifacts), `grafana-data` (Grafana state).

## Architecture at a glance

```
                       ┌─────────────────────────────────────────────┐
   Browser ─────────▶  │  frontend (Next.js)  :3001                   │
                       └───────────────┬─────────────────────────────┘
                                       │ HTTP (NEXT_PUBLIC_API_BASE)
                       ┌───────────────▼─────────────────────────────┐
   Grafana :3000 ────▶ │  backend (FastAPI)   :8000                   │
        │  (SQL)       │   ├─ api/main.py         HTTP surface        │
        │              │   ├─ orchestrator.py     collect→analyze→store
        │              │   ├─ custodian/          policy engine (push/pull/event)
        │              │   ├─ providers/          azure | aws | gcp    │
        │              │   ├─ analysis/ + ai/     FinOps + AI summary  │
        │              │   ├─ remediation/        guardrailed actions  │
        │              │   ├─ notify/ authz/      channels, RBAC, OIDC │
        │              │   └─ scheduler.py        cron-style runner    │
        │              └───────────────┬─────────────────────────────┘
        │                              │ SQLAlchemy (psycopg)
        └──────────────────────────────▼─────────────────────────────┐
                       │  db (TimescaleDB / Postgres) :5432           │
                       │   tables · hypertables · SQL views           │
                       └──────────────────────────────────────────────┘
```

### Backend package map (`backend/cloudwarden/`)

| Area | Modules | Purpose |
|------|---------|---------|
| Entry | `cli.py`, `orchestrator.py`, `scheduler.py`, `config.py` | CLI, pipeline, cron runner, typed settings |
| Cloud providers | `providers/azure.py · aws.py · gcp.py · registry.py` | Onboarding, asset collection, per-cloud policy eval |
| Azure collectors | `azure/inventory.py · cost.py · metrics.py · advisor.py · activitylog.py · logs.py` | Live/mocked Azure data pulls |
| FinOps analysis | `analysis/idle.py · rules.py · savings.py · rollup.py · pricing.py` | Idle/rightsizing detection, savings, rollups |
| AI | `ai/factory.py · anthropic_provider.py · openai_compatible_provider.py · prompt.py · schemas.py` | Executive summary generation |
| Governance | `custodian/engine.py · bindings.py · gitops.py · eventmode.py`, `packs/registry.py` | Policy engine, bindings, GitOps sync, packs |
| AssetDB & events | `events/ingestion.py · assetdb.py · models.py` | Real-time ingestion, asset events, relationships |
| Remediation | `remediation/executor.py · guardrails.py · approval.py` | Guardrailed actions + approvals |
| Notifications | `notify/service.py · dispatch.py` | Slack, email, Teams, Jira, ServiceNow |
| Security | `authz/rbac.py · oidc.py · audit.py · teams.py`, `auth.py` | RBAC, SSO/OIDC, audit trail |
| Storage | `storage/schema.py · db.py · repository.py` | ORM schema, engine/session, all queries |
| API | `api/main.py` | FastAPI app |

## Core concepts / glossary

- **Provider** — `azure` | `aws` | `gcp`. Source of truth is the
  `subscriptions.provider` column; every asset, policy execution, and posture row
  carries it (defaulting to `azure` for legacy rows).
- **Subscription / account** — a single onboarded cloud account: an Azure
  *subscription*, an AWS *account*, or a GCP *project*. All three live in the
  `subscriptions` table with a `provider` tag. The first one onboarded becomes the
  **default**.
- **Asset** — a cloud resource tracked in **AssetDB** with config, tags,
  relationships (e.g. disk → VM → NIC), and a change-event history.
- **Policy** — a Cloud Custodian (`c7n`-style) rule: a resource type + filters +
  actions. Stored versioned in the DB; authored in the UI, via API, or synced
  from Git.
- **Binding** — attaches a policy to a **collection** (or subscription) so it runs
  in a defined scope on a schedule (pull mode).
- **Collection** — a named group of policies (a policy set/pack) applied together.
- **Account-group** — a named group of subscriptions/accounts for scoping runs.
- **Execution** — one run of one policy against one subscription, with status
  (`succeeded`/`failed`), matched-resource count, and actions taken.
- **Posture** — compliance rollup: compliant vs non-compliant policies, violation
  counts, broken down by provider / policy / subscription / collection / control.
- **Recommendation** — a FinOps finding (stop idle / downsize) with estimated
  monthly savings and confidence.
- **Remediation** — taking a corrective action on a resource; gated by guardrails
  and (optionally) an approval workflow. Defaults to **dry-run**.

## Execution modes for policies

| Mode | Trigger | Entry point |
|------|---------|-------------|
| **Pull** | Scheduled / on-demand batch across bindings | `run-policies` CLI, scheduler, `POST /api/runs` |
| **Push** | Ad-hoc dry-run of a single policy | `POST /api/policies/{id}/dryrun` |
| **Event** | Real-time on a cloud change event | `POST /api/events/azure` (Event Grid) |

## Where to go next

- Stand it up → [02 · Getting Started](02-getting-started.md)
- Tune it → [03 · Configuration Reference](03-configuration.md)
- Day-2 operations → [04 · Operating the Stack](04-operating-the-stack.md)
- Click around → [05 · Web UI Guide](05-web-ui-guide.md)
- Load demo data → [15 · Demo Data & Seeding](15-demo-data.md)
