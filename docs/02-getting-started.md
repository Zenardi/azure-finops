# 2 · Getting Started (Quickstart)

This gets you from a clean checkout to a **fully populated, clickable platform in
mock mode** — no cloud account required.

## Prerequisites

- **Docker** with **Compose v2** (the `docker compose` subcommand). That's the only
  hard requirement to run the stack.
- Ports **3000**, **3001**, **8000**, **5432** free on the host.
- (For local dev / tests only) Python 3.11+ and a virtualenv — see
  [04 · Operating the Stack](04-operating-the-stack.md#local-development).

## 1. Configure

```bash
cp .env.example .env      # ships with FINOPS_MOCK=1 (offline mode)
```

The default `.env` runs everything against bundled fixtures — no Azure/AWS/GCP
credentials needed. See [03 · Configuration](03-configuration.md) to go live.

> `.env` is **gitignored** — never commit it. `.env.example` is the committed
> template.

## 2. Start the stack

```bash
make up                   # = docker compose up -d --build
```

This builds and starts all four services: `db`, `backend`, `grafana`, `frontend`.
First run pulls images and builds the backend/frontend — give it a couple of
minutes. Use `make up-core` to skip the frontend (db + backend + grafana only).

Check everything is healthy:

```bash
docker compose ps         # all services should be "Up"/"healthy"
```

## 3. Initialize the database (first run)

```bash
make initdb               # create tables, hypertables, and SQL views
```

> `make seed` and the backend API also call `init_db()` on startup, so this is
> usually implicit — but running it explicitly the first time makes failures
> obvious.

## 4. Load data

**Option A — quick single-cloud smoke test:**

```bash
make seed                 # one mock Azure pipeline run
```

This runs a single mock **Azure** cost pipeline (assets + cost + recommendations
+ AI summary). It does **not** create any policies or AWS/GCP accounts, so the
Compliance and cross-cloud views stay empty.

**Option B — full multi-cloud demo (recommended for exploring):**

Use the bundled demo seed to populate **all three clouds**, **9 governance
policies**, **posture by provider**, and **execution history**. See
[15 · Demo Data & Seeding](15-demo-data.md) for the one-line command and exactly
what it creates.

## 5. Open the platform

| Surface | URL | Notes |
|---------|-----|-------|
| **Web UI** | http://localhost:3001 | Start here — the full app |
| **API + Swagger** | http://localhost:8000/docs | Interactive OpenAPI docs |
| **API health** | http://localhost:8000/health | Liveness + data-source snapshot |
| **Grafana** | http://localhost:3000 | Anonymous viewing on; edit login `admin` / `admin` |

## 6. Suggested first tour

1. **Subscriptions** (`/subscriptions`) — the onboarded cloud accounts.
2. **Assets** (`/assets`) — flip the **Cloud** provider dropdown; click a row to
   drill into config, relationships, and change history.
3. **Compliance** (`/compliance`) — posture by provider; click a non-compliant
   policy to see the exact flagged resources.
4. **Policies** (`/policies`) — the Cloud Custodian policy editor with inline
   validation and version history.
5. **Executions** (`/executions`) — pull-mode run history with filters.
6. **Costs** + **Recommendations** — the FinOps side (savings, approve/remediate).
7. **Grafana** — the *Cost*, *Recommendations*, *Posture*, and *Execution Health*
   dashboards, each with a provider template variable.

Full page-by-page walkthrough: [05 · Web UI Guide](05-web-ui-guide.md).

## 7. Stop / reset

```bash
make down                 # stop the stack (keeps data volumes)
docker compose down -v    # stop AND wipe all data (pgdata/appdata/grafana-data)
```

After a `-v` reset, repeat from step 2 (`make up` → `make initdb` → seed).

## Live mode (real clouds)

To point at real Azure/AWS/GCP instead of fixtures, set `FINOPS_MOCK=0` and supply
credentials — see [03 · Configuration](03-configuration.md) and
[06 · Multi-Cloud Onboarding](06-multi-cloud-onboarding.md).
