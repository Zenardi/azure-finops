"""Scale & performance contract (issue #55, M13.5).

A repeatable load test that exercises **policy execution at scale** — running a
binding's ``N`` subscriptions × ``M`` policies through the real execution +
persistence path (``run_binding``) with an **injected, offline** runner (no c7n,
no Azure, no network) — and asserts the run stays inside a documented time and
memory budget. Throughput is recorded so the nightly job can flag regressions.

The heavy load tests are marked ``perf`` and are therefore **excluded from the
default PR run** (``addopts = -m 'not perf'`` in ``pyproject.toml``); they run in
the nightly perf CI job. The two lightweight tests below — the regression-detector
unit and the marker-exclusion guard — are intentionally *unmarked* so they run in
the default suite and keep the mechanism honest.

Scale can be dialled UP via ``PERF_SUBSCRIPTIONS`` / ``PERF_POLICIES`` env vars
(never below the ≥50×≥20 DoD floor); the budget via ``PERF_BUDGET_SECONDS`` and the
memory ceiling via ``PERF_MEMORY_CEILING_MB``. See ``backend/tests/perf/README.md``.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
import tracemalloc
from pathlib import Path

import pytest

from cloudwarden.custodian.bindings import run_binding
from cloudwarden.storage import repository as repo
from cloudwarden.storage.db import session_scope

# --------------------------------------------------------------------------- #
# Load parameters (env-overridable; the DoD floor of ≥50 subs × ≥20 policies is
# a hard minimum — env vars may only scale the load UP).
# --------------------------------------------------------------------------- #
SCALE_SUBSCRIPTIONS = max(50, int(os.environ.get("PERF_SUBSCRIPTIONS", "50")))
SCALE_POLICIES = max(20, int(os.environ.get("PERF_POLICIES", "20")))
SCALE_BUDGET_SECONDS = float(os.environ.get("PERF_BUDGET_SECONDS", "120"))
MEMORY_CEILING_BYTES = int(os.environ.get("PERF_MEMORY_CEILING_MB", "256")) * 1024 * 1024

perf = pytest.mark.perf

_SCALE_SPEC = {"policies": [{"name": "scale", "resource": "azure.vm", "actions": ["stop"]}]}


class FakeScaleRunner:
    """Offline ``CustodianRunner``: no c7n/Azure. Returns **zero matches** so the

    benchmark isolates the execution + persistence path (open row → run → record →
    finish) without pulling in the notification subsystem (which only fires on a
    match). Records nothing — throughput is timed by the caller."""

    def validate(self, spec: dict) -> dict:
        return {"valid": True, "errors": []}

    def run(self, spec: dict, subscription_id: str, credential, dry_run: bool) -> dict:
        return {"resources": [], "matched": 0, "dry_run": dry_run}

    def schema(self, resource_type: str | None = None) -> dict:
        return {"resource_types": []}


def _seed_scale_binding(n_subs: int, n_policies: int, *, dry_run: bool = True) -> int:
    """Seed a collection (n policies), an account group (n subscriptions) and a binding."""
    with session_scope() as s:
        cid = repo.create_collection(s, name="scale-coll")["id"]
        for i in range(n_policies):
            pid = repo.create_policy(
                s, name=f"scale-pol-{i}", resource_type="azure.vm", spec=_SCALE_SPEC
            )["id"]
            repo.add_policy_to_collection(s, cid, pid)
        gid = repo.create_account_group(s, name="scale-grp")["id"]
        for i in range(n_subs):
            sid = f"scale-sub-{i}"
            repo.upsert_subscription(s, subscription_id=sid, display_name=f"S-{i}")
            repo.add_subscription_to_group(s, gid, sid)
        return repo.create_binding(
            s, collection_id=cid, account_group_id=gid, dry_run=dry_run, enabled=True
        )["id"]


def record_perf_result(
    path: Path,
    *,
    name: str,
    subscriptions: int,
    policies: int,
    executions: int,
    elapsed_seconds: float,
) -> dict:
    """Write a throughput record as JSON (the nightly job uploads it as an artifact)."""
    throughput = executions / elapsed_seconds if elapsed_seconds > 0 else 0.0
    record = {
        "name": name,
        "subscriptions": subscriptions,
        "policies": policies,
        "executions": executions,
        "elapsed_seconds": round(elapsed_seconds, 4),
        "throughput_per_second": round(throughput, 2),
    }
    Path(path).write_text(json.dumps(record, indent=2, sort_keys=True))
    return record


def detect_regression(
    *, baseline_per_second: float, observed_per_second: float, tolerance: float = 0.25
) -> bool:
    """Flag a throughput regression: observed slower than baseline by more than ``tolerance``.

    A non-positive baseline (unknown / first run) never false-positives.
    """
    if baseline_per_second <= 0:
        return False
    return observed_per_second < baseline_per_second * (1 - tolerance)


def _pytest_ini_options() -> dict:
    """The root ``[tool.pytest.ini_options]`` table (found by walking up from here)."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            ini = tomllib.loads(candidate.read_text()).get("tool", {}).get("pytest", {})
            ini_options = ini.get("ini_options")
            if ini_options is not None:
                return ini_options
    raise AssertionError("no [tool.pytest.ini_options] found in any parent pyproject.toml")


def _results_dir(fallback: Path) -> Path:
    directory = Path(os.environ.get("PERF_RESULTS_DIR") or fallback)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# --------------------------------------------------------------------------- #
# Load test — a binding across ≥50 subscriptions × ≥20 policies within budget
# --------------------------------------------------------------------------- #
@perf
def test_scale_50_subscriptions_within_budget(db) -> None:
    # Arrange — a binding at (at least) the DoD scale floor.
    assert SCALE_SUBSCRIPTIONS >= 50 and SCALE_POLICIES >= 20
    expected = SCALE_SUBSCRIPTIONS * SCALE_POLICIES
    bid = _seed_scale_binding(SCALE_SUBSCRIPTIONS, SCALE_POLICIES)
    # Act — time a full binding run (one execution per policy × subscription).
    start = time.perf_counter()
    result = run_binding(bid, runner=FakeScaleRunner(), mock=True)
    elapsed = time.perf_counter() - start
    # Assert — every execution ran and the wall-clock is inside the budget.
    assert result["status"] == "completed"
    assert len(result["executions"]) == expected
    assert elapsed <= SCALE_BUDGET_SECONDS, (
        f"scale run took {elapsed:.1f}s, exceeding the {SCALE_BUDGET_SECONDS:.0f}s budget"
    )


@perf
def test_scale_throughput_recorded(db, tmp_path) -> None:
    # Arrange
    expected = SCALE_SUBSCRIPTIONS * SCALE_POLICIES
    bid = _seed_scale_binding(SCALE_SUBSCRIPTIONS, SCALE_POLICIES)
    # Act — run the load, then persist a throughput record.
    start = time.perf_counter()
    result = run_binding(bid, runner=FakeScaleRunner(), mock=True)
    elapsed = time.perf_counter() - start
    n = len(result["executions"])
    out = _results_dir(tmp_path) / "execution_scale.json"
    record = record_perf_result(
        out,
        name="execution_scale",
        subscriptions=SCALE_SUBSCRIPTIONS,
        policies=SCALE_POLICIES,
        executions=n,
        elapsed_seconds=elapsed,
    )
    # Assert — a well-formed record with a positive throughput was written to disk.
    saved = json.loads(out.read_text())
    assert saved == record
    assert saved["executions"] == n == expected
    assert saved["throughput_per_second"] > 0


@perf
def test_scale_no_memory_blowup(db) -> None:
    # Arrange
    expected = SCALE_SUBSCRIPTIONS * SCALE_POLICIES
    bid = _seed_scale_binding(SCALE_SUBSCRIPTIONS, SCALE_POLICIES)
    # Act — measure the peak Python heap while the full load runs.
    tracemalloc.start()
    try:
        result = run_binding(bid, runner=FakeScaleRunner(), mock=True)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Assert — the scaled run completes without an unbounded memory blowup.
    assert len(result["executions"]) == expected
    assert peak <= MEMORY_CEILING_BYTES, (
        f"peak heap {peak / 1e6:.0f}MB exceeds the {MEMORY_CEILING_BYTES / 1e6:.0f}MB ceiling"
    )


# --------------------------------------------------------------------------- #
# Regression detection (pure) — runs in the DEFAULT suite (unmarked)
# --------------------------------------------------------------------------- #
def test_scale_regression_detected() -> None:
    # A throughput drop beyond the tolerance IS flagged (positive)...
    assert detect_regression(baseline_per_second=100.0, observed_per_second=50.0) is True
    # ...a drop within tolerance is NOT (negative)...
    assert detect_regression(baseline_per_second=100.0, observed_per_second=95.0) is False
    # ...and an unknown/zero baseline never false-positives (edge).
    assert detect_regression(baseline_per_second=0.0, observed_per_second=10.0) is False


# --------------------------------------------------------------------------- #
# Marker hygiene — the perf gate excludes these tests from the default PR run
# --------------------------------------------------------------------------- #
def test_perf_marker_excluded_by_default() -> None:
    ini = _pytest_ini_options()
    # The marker is registered...
    assert any(m.startswith("perf") for m in ini.get("markers", []))
    # ...and the default invocation deselects it.
    assert "not perf" in ini.get("addopts", "")
