# Scale & performance tests (M13.5)

A repeatable **load test** that proves the platform executes governance *at scale*:
it runs a binding across **N subscriptions × M policies** through the real
execution + persistence path (`run_binding`) and asserts the run stays inside a
documented **time** and **memory** budget. Throughput is recorded so the nightly
job can flag regressions.

Everything is **offline and deterministic**: an injected `FakeScaleRunner`
replaces Cloud Custodian / Azure (no c7n, no network), and the DB is a throwaway
Postgres started by the `db` fixture (testcontainers). No live cloud is touched.

## What it measures

| Test | Behaviour asserted |
|------|--------------------|
| `test_scale_50_subscriptions_within_budget` | A full binding run over **≥50 subs × ≥20 policies** (≥1000 executions) completes within the time budget. |
| `test_scale_throughput_recorded` | The run's throughput (executions/sec) is written as a JSON record (uploaded as a CI artifact). |
| `test_scale_no_memory_blowup` | Peak Python heap during the scaled run stays under the memory ceiling. |
| `test_scale_regression_detected` | The throughput regression detector flags a drop beyond tolerance (pure unit — runs in the default suite). |
| `test_perf_marker_excluded_by_default` | The `perf` marker + `addopts` genuinely exclude the heavy tests from the default run (pure guard — runs in the default suite). |

## The budget

| Parameter | Default | Env override | Rationale |
|-----------|---------|--------------|-----------|
| Subscriptions | `50` | `PERF_SUBSCRIPTIONS` (floor 50) | DoD scale floor; env may only scale **up**. |
| Policies | `20` | `PERF_POLICIES` (floor 20) | DoD scale floor; env may only scale **up**. |
| Time budget | `120s` | `PERF_BUDGET_SECONDS` | A **regression ceiling**, not an SLA. A full 1000-execution run is ~7s locally; 120s (~17×) absorbs CI variance while still catching an O(N²)/missing-index/per-call-sleep blowup. |
| Memory ceiling | `256 MB` | `PERF_MEMORY_CEILING_MB` | Peak `tracemalloc` heap; catches unbounded accumulation. |
| Regression tolerance | `25%` | (arg to `detect_regression`) | Throughput below `baseline × (1 − tolerance)` is a regression. |

Results are written to `$PERF_RESULTS_DIR/execution_scale.json` (falls back to the
test's `tmp_path`); the nightly CI job sets `PERF_RESULTS_DIR` and uploads it.

## Running

The heavy load tests are marked `perf` and **excluded from the default PR run**
(`addopts = -m 'not perf'` in the root `pyproject.toml`). Run them explicitly:

```bash
# Requires Docker (testcontainers spins up Postgres).
make perf
# or directly:
pytest backend/tests/perf -m perf -v
# scale it up:
PERF_SUBSCRIPTIONS=100 PERF_POLICIES=40 pytest backend/tests/perf -m perf
```

The two pure guard/unit tests run automatically in the default suite (`pytest`)
and in the `backend` CI job, so the exclusion mechanism and the regression
detector stay covered on every PR.

## CI

A dedicated **`perf`** job in `.github/workflows/ci.yml` runs **nightly**
(cron) and on manual `workflow_dispatch`. It is **skipped on pull requests**
(non-blocking on PRs) and **blocks on regression nightly** — a budget breach
fails the job. It uploads `execution_scale.json` as a `perf-results` artifact.
