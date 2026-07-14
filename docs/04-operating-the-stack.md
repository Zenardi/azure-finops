# 4 · Operating the Stack

Day-2 operations: the commands, the lifecycle, and how to run the platform in
one-shot vs scheduled modes.

## Makefile targets

Run `make` (or `make help`) to list them. The full set:

| Target | Runs | Use for |
|--------|------|---------|
| `make up` | `docker compose up -d --build` | Start the full stack (db + backend + grafana + frontend). |
| `make up-core` | same, minus frontend | db + backend + grafana only. |
| `make up-all` | alias for `up` | — |
| `make down` | `docker compose down` | Stop the stack (**keeps** volumes). |
| `make logs` | `docker compose logs -f` | Tail all service logs. |
| `make initdb` | CLI `initdb` in a one-off container | Create/upgrade schema (tables, hypertables, views). |
| `make seed` | CLI `run --mock` in a one-off container | One mock Azure pipeline run. |
| `make run-mock` | local CLI `run --mock` | Run the pipeline against a Postgres at `localhost:5432` (needs local Python). |
| `make install` / `install-dev` | pip install | Backend runtime / dev deps (local). |
| `make lint` | `ruff check backend` | Lint. |
| `make fmt` | `ruff format backend` | Format. |
| `make test` | `pytest` | Offline unit tests (no DB/Azure). |
| `make coverage` | `pytest --cov` w/ 95% gate | Full suite (spins ephemeral Postgres via testcontainers; needs Docker). |

## The CLI (`python -m cloudwarden.cli`)

The backend container's entrypoint. Commands:

| Command | What it does |
|---------|--------------|
| `initdb` | Create/upgrade the database schema. |
| `run [--mock]` | Run the cost pipeline once **per enabled subscription** (collect → analyze → recommend → AI summary → store). `--mock` uses fixtures. |
| `run-policies [--mock]` | Execute every enabled policy against every enabled subscription (**pull mode**). |
| `scheduler` | Long-running: runs the pipeline and policy execution on their own intervals (see below). |
| `api` | Serve the FastAPI app via uvicorn (this is the default compose `command`). |

Run an arbitrary command in the running backend container:

```bash
docker compose exec backend python -m cloudwarden.cli run-policies --mock
```

Or as a one-off container (like `make seed` does):

```bash
docker compose run --rm backend python -m cloudwarden.cli run --mock
```

## One-shot vs scheduled

The `backend` service defaults to `command: ["api"]` (serve the HTTP API). To run
the platform on a timer instead, switch it to the scheduler:

```yaml
# docker-compose.yml → services.backend
command: ["scheduler"]
```

The scheduler runs three independent loops (cadences from `.env`):

| Loop | Interval env | Default |
|------|--------------|---------|
| Cost pipeline (all subscriptions) | `RUN_INTERVAL_SECONDS` | `86400` (daily) |
| Pull-mode policy execution | `POLICY_RUN_INTERVAL_SECONDS` | `86400` |
| Governance CSV report (if enabled) | `GOVERNANCE_REPORT_INTERVAL_SECONDS` | `86400` |

> Typically you run **two** backend replicas: one with `["api"]` (serving UI/API)
> and one with `["scheduler"]` (background runs). Both share the same DB.

You can also trigger runs on demand without the scheduler:

- `POST /api/runs?mock=true` — cost pipeline (all enabled subscriptions, or
  `&subscription_id=…` for one).
- `POST /api/bindings/{id}/run` — pull-mode policy run for a binding.
- The web UI **Runs** page has a *Trigger run (mock)* button.

## Health & observability

| Check | How |
|-------|-----|
| Service status | `docker compose ps` |
| API liveness | `GET http://localhost:8000/health` → `{status:"ok", sources:{…}}` |
| API docs | `http://localhost:8000/docs` (also the compose healthcheck target) |
| Grafana health | `GET http://localhost:3000/api/health` |
| Logs (one service) | `docker compose logs -f backend` |
| DB shell | `docker compose exec db psql -U finops -d finops` |

## Data lifecycle

State lives in three named Docker volumes:

| Volume | Holds |
|--------|-------|
| `pgdata` | The entire Postgres/TimescaleDB database. |
| `appdata` | Backend `/data` — exports, generated reports, artifacts. |
| `grafana-data` | Grafana's own state (users, prefs). |

**Backup the database:**

```bash
docker compose exec -T db pg_dump -U finops finops > finops-backup.sql
```

**Restore:**

```bash
docker compose exec -T db psql -U finops -d finops < finops-backup.sql
```

**Full reset (wipe everything):**

```bash
docker compose down -v          # removes pgdata, appdata, grafana-data
make up && make initdb          # rebuild fresh, then re-seed
```

## Upgrading

1. `git pull`
2. `make up` — `--build` rebuilds the backend/frontend images.
3. `make initdb` — `init_db()` is additive (creates any new tables/views); it does
   not drop existing data.

## Local development

For working on the code (not just running it):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt
make lint         # ruff
make test         # offline unit tests (no DB/Azure)
make coverage     # full suite + 95% coverage gate (needs Docker for testcontainers)
make run-mock     # run the pipeline against a local Postgres on :5432
```

**Quality gates** (enforced in CI — `.github/workflows/ci.yml`):
- Line coverage must stay **≥ 95%** (`fail_under=95`).
- Ruff lint + format.
- Trivy image scan fails on any fixable HIGH/CRITICAL CVE.

Mirror these locally before committing.
