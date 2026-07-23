"""Cost allocation / showback / chargeback by tag → team (M14.5).

CloudWarden *enforces* a cost-allocation tag (via the cost / tag-compliance packs) but
never *reported spend by it*. This groups spend by an arbitrary tag key (CostCenter /
Owner / Team / env) so every dollar is attributed — and, crucially, surfaces an explicit
**unallocated** bucket for untagged spend (the thing you actually want to drive down).

Design invariants:

* **Nothing is silently dropped.** Spend with no value for the grouping key lands in an
  explicit ``unallocated`` bucket, never discarded.
* **Reconciliation.** ``allocated + unallocated == total`` always holds — the report is
  a partition of the scoped spend.
* **Tag → team.** Tag values map to the existing :mod:`teams` model, so a team-scoped
  principal sees only its own allocation (enforced at the query layer, upstream).
* **Shared-cost split.** A designated "shared" tag value can be redistributed across the
  other allocated buckets — **even** (equal) or **proportional** (by each bucket's own
  spend) — preserving the total.

The pure helpers (:func:`group_by_tag`, :func:`build_report`, :func:`split_shared`) are
unit-tested without a database on seeded rows; :func:`compute_showback` injects its data
source (the injection-safe :func:`repository.cost_by_tag`) so the read → allocate flow is
exercised offline.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# The explicit bucket untagged spend lands in — surfaced, never dropped.
UNALLOCATED = "unallocated"

# Columns for the streaming showback export (order matters — the CSV header).
SHOWBACK_EXPORT_COLUMNS = ("key", "tag_value", "team", "cost", "share", "currency")


@dataclass(frozen=True)
class Allocation:
    """One tag value's slice of the scoped spend."""

    tag_value: str  # the tag value, or ``UNALLOCATED``
    team: str | None  # the mapped team, or ``None`` (unmapped / the unallocated bucket)
    cost: float
    share: float  # fraction of the total (0..1)


@dataclass(frozen=True)
class AllocationReport:
    """A partition of scoped spend by tag value, with the unallocated bucket explicit."""

    key: str  # the tag key grouped by
    total: float
    allocated: float
    unallocated: float
    currency: str
    allocations: list[Allocation]  # every bucket, incl. unallocated, highest-cost first


def group_by_tag(rows: Iterable[dict[str, Any]], *, key: str) -> dict[str, float]:
    """Sum ``cost`` across ``rows`` grouped by each row's ``tags[key]``.

    A row whose tags lack ``key`` (or carry an empty value) lands in the
    :data:`UNALLOCATED` bucket — untagged spend is surfaced, never dropped.
    """
    out: dict[str, float] = defaultdict(float)
    for row in rows:
        tags = row.get("tags") or {}
        value = tags.get(key)
        out[value if value else UNALLOCATED] += float(row.get("cost", 0.0))
    return dict(out)


def split_shared(
    amount: float,
    targets: Sequence[str],
    *,
    method: str = "even",
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Split ``amount`` across ``targets`` — ``even`` (equal) or ``proportional``.

    ``proportional`` splits by each target's ``weights`` share; when no usable weight is
    present (missing or all-zero) it falls back to an even split rather than dividing by
    zero. An empty ``targets`` yields ``{}`` (nothing to split onto).
    """
    if not targets:
        return {}
    if method == "proportional":
        weights = weights or {}
        total_weight = sum(max(weights.get(t, 0.0), 0.0) for t in targets)
        if total_weight > 0:
            return {
                t: round(amount * max(weights.get(t, 0.0), 0.0) / total_weight, 6) for t in targets
            }
    per = amount / len(targets)
    return {t: round(per, 6) for t in targets}


def build_report(
    buckets: dict[str, float],
    *,
    key: str,
    team_map: dict[str, str] | None = None,
    currency: str = "USD",
    shared_value: str | None = None,
    split: str = "even",
) -> AllocationReport:
    """Assemble an :class:`AllocationReport` from grouped ``buckets`` (tag value → cost).

    Maps each tag value to a team via ``team_map`` (unmapped → ``None``); the
    :data:`UNALLOCATED` bucket always has no team. When ``shared_value`` names a present
    bucket, its cost is redistributed across the *other allocated* buckets (``split`` =
    ``even`` | ``proportional`` by their spend) and the shared bucket is dropped — the
    total is preserved. Guarantees ``allocated + unallocated == total``.
    """
    team_map = team_map or {}
    working = dict(buckets)

    if shared_value and shared_value in working:
        shared_cost = working.pop(shared_value)
        targets = [v for v in working if v != UNALLOCATED]
        split_amounts = split_shared(
            shared_cost, targets, method=split, weights={t: working[t] for t in targets}
        )
        for target, amount in split_amounts.items():
            working[target] = round(working[target] + amount, 6)

    total = round(sum(working.values()), 6)
    allocations: list[Allocation] = []
    allocated = 0.0
    unallocated = 0.0
    for tag_value, cost in sorted(working.items(), key=lambda kv: kv[1], reverse=True):
        cost = round(float(cost), 6)
        if tag_value == UNALLOCATED:
            unallocated += cost
            team = None
        else:
            allocated += cost
            team = team_map.get(tag_value)
        share = round(cost / total, 6) if total else 0.0
        allocations.append(Allocation(tag_value=tag_value, team=team, cost=cost, share=share))

    return AllocationReport(
        key=key,
        total=total,
        allocated=round(allocated, 6),
        unallocated=round(unallocated, 6),
        currency=currency,
        allocations=allocations,
    )


def compute_showback(
    session: Any,
    *,
    key: str,
    start: Any,
    end: Any,
    team_map: dict[str, str] | None = None,
    visible_tag_values: set[str] | None = None,
    subscription_id: str | None = None,
    shared_value: str | None = None,
    split: str = "even",
    currency: str = "USD",
) -> AllocationReport:
    """Aggregate ``cost_snapshots`` by tag ``key`` over ``[start, end]`` and allocate.

    Delegates to the injection-safe :func:`repository.cost_by_tag`; when
    ``visible_tag_values`` is a set (a team-scoped principal) only those values are
    aggregated, so the caller never sees another team's spend nor the unallocated
    bucket. ``None`` (admin / RBAC off) returns the full partition incl. unallocated.
    """
    from ..storage import repository as repo

    tag_values = list(visible_tag_values) if visible_tag_values is not None else None
    rows = repo.cost_by_tag(
        session,
        key=key,
        start=start,
        end=end,
        tag_values=tag_values,
        subscription_id=subscription_id,
    )
    buckets: dict[str, float] = defaultdict(float)
    resolved_currency = currency
    for row in rows:
        value = row["tag_value"]
        buckets[value if value else UNALLOCATED] += float(row["cost"])
        if row.get("currency"):
            resolved_currency = row["currency"]
    return build_report(
        dict(buckets),
        key=key,
        team_map=team_map,
        currency=resolved_currency,
        shared_value=shared_value,
        split=split,
    )


def report_rows(report: AllocationReport) -> list[dict[str, Any]]:
    """Flatten a report into export rows (matching :data:`SHOWBACK_EXPORT_COLUMNS`)."""
    return [
        {
            "key": report.key,
            "tag_value": a.tag_value,
            "team": a.team,
            "cost": a.cost,
            "share": a.share,
            "currency": report.currency,
        }
        for a in report.allocations
    ]


def report_public(report: AllocationReport) -> dict[str, Any]:
    """Serialize a report for the JSON API."""
    return {
        "key": report.key,
        "total": report.total,
        "allocated": report.allocated,
        "unallocated": report.unallocated,
        "currency": report.currency,
        "allocations": [
            {
                "tag_value": a.tag_value,
                "team": a.team,
                "cost": a.cost,
                "share": a.share,
            }
            for a in report.allocations
        ],
    }
