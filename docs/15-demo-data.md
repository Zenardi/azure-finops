# 15 · Demo Data & Seeding

Two ways to put data in front of you. Use the built-in `make seed` for a quick
Azure-only smoke test, or the bundled **multi-cloud demo seed** to light up every
dashboard across all three clouds.

## Option A — `make seed` (quick, Azure-only)

```bash
make seed
```

Runs one mock Azure cost pipeline (`cli run --mock`): populates assets, cost,
recommendations, and the AI summary for the default Azure subscription. It creates
**no policies and no AWS/GCP accounts**, so the Compliance and cross-cloud views
stay empty. Good for a fast look at the FinOps/cost side.

## Option B — the multi-cloud demo seed (recommended)

A committed example script, [`docs/examples/seed_demo.py`](examples/seed_demo.py),
populates the whole platform offline (`FINOPS_MOCK=1`).

**Run it** (stack must be up — `make up`):

```bash
docker compose cp docs/examples/seed_demo.py backend:/tmp/seed_demo.py
docker compose exec -e PYTHONPATH=/app backend python /tmp/seed_demo.py
```

> **Why `PYTHONPATH=/app`?** Running a script by path puts the script's own
> directory on `sys.path`, not the container workdir where the `cloudwarden`
> package is installed. `PYTHONPATH=/app` makes the import resolve.

**What it creates:**

| | |
|-|-|
| **3 cloud accounts** | Azure + AWS + GCP (Azure onboarded first = default) |
| **~17 assets** | 7 Azure · 5 AWS · 5 GCP, in AssetDB with `created` events + relationships |
| **Azure cost pipeline** | cost rows + rightsizing/idle recommendations + AI summary |
| **9 governance policies** | 3 per cloud, a realistic compliant/non-compliant mix |
| **Execution history** | 3 backdated runs per policy, incl. 2 injected failures |

It's idempotent-ish: re-running reuses existing accounts/policies and **appends
another round of execution history** (handy for building up a time series).

**Expected output** (roughly):

```
=== POSTURE BY PROVIDER ===
  aws    compliant=1 non_compliant=2 violations=2 evaluated=3
  azure  compliant=1 non_compliant=2 violations=4 evaluated=3
  gcp    compliant=1 non_compliant=2 violations=2 evaluated=3
=== EXECUTION HEALTH BY PROVIDER ===
  aws    total=9 succeeded=8 failed=1 success_rate=0.8889
  azure  total=9 succeeded=9 failed=0 success_rate=1.0
  gcp    total=9 succeeded=8 failed=1 success_rate=0.8889
```

## How the demo achieves a realistic mix

The script is a good reference for how the platform's pieces fit together:

- **Onboards** all three clouds via `repo.upsert_subscription(..., provider=…)`.
- Runs the **Azure** cost pipeline with `run_one_subscription(AZURE_ID, mock=True)`
  (the cost pipeline is Azure-centric).
- **Ingests** AWS/GCP fixture assets directly through each provider's
  `collect_assets(...)`, then `upsert_assets` + `append_asset_event` +
  `build_relationships` — mirroring what the ingest endpoints do.
- Evaluates each policy through the **real (mock-backed) provider engine** to get
  matches, so posture reflects genuine policy evaluation.
- Fakes a **compliant** policy per cloud (`force_zero`, or a resource type absent
  from the fixtures → 0 matches) so posture shows both green and red rows.
- **Backdates** executions across ~12 days and injects two failures so
  execution-health shows a success rate below 100%.

## Verifying the seed

```bash
curl -s localhost:8000/api/subscriptions | jq 'length'          # 3
curl -s localhost:8000/api/governance/posture?provider=aws | jq '.by_provider'
curl -s -X POST localhost:8000/api/assets/query \
  -H 'Content-Type: application/json' -d '{"filters":[],"limit":100}' | jq 'length'
```

Or just open the UI: **Subscriptions** (3 clouds), **Assets** (flip the Cloud
filter), **Compliance** (posture by provider), **Executions** (27 runs).

## Resetting

```bash
docker compose down -v      # wipe all volumes
make up && make initdb      # fresh stack
# then re-run Option A or B
```
