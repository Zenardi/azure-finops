"""M14.5 — showback / chargeback by tag → team. Tests written FIRST (TDD).

Layers, each asserting one behaviour:

* **Pure logic** (no DB): group cost by a tag key (untagged → an explicit
  ``unallocated`` bucket), reconcile allocated + unallocated to the total, map tag
  values to teams, and split shared costs (even / proportional).
* **Repository** (``db`` fixture): the injection-safe by-tag aggregation over
  ``cost_snapshots`` (the tag key is a *bound* parameter — a SQL-injection attempt
  just returns the unallocated bucket, never executes).
* **Team scoping** (``db`` fixture): a tag-value → team map, and a team-scoped
  principal that sees only its own allocation.
* **Enrichment + API** : cost rows carry inventory tags (case-insensitively); the
  showback endpoints reconcile, are RBAC-guarded, and export streaming CSV/JSON.
"""

from __future__ import annotations

import datetime as dt

_ON = dt.date(2026, 7, 20)


# --------------------------------------------------------------------------- #
# Pure logic — no database
# --------------------------------------------------------------------------- #
def test_group_by_tag_key_totals() -> None:
    from cloudwarden.analysis.allocation import group_by_tag

    rows = [
        {"tags": {"owner": "web-team"}, "cost": 60.0},
        {"tags": {"owner": "web-team"}, "cost": 40.0},
        {"tags": {"owner": "data-team"}, "cost": 25.0},
    ]
    assert group_by_tag(rows, key="owner") == {"web-team": 100.0, "data-team": 25.0}


def test_untagged_lands_in_unallocated() -> None:
    from cloudwarden.analysis.allocation import UNALLOCATED, group_by_tag

    rows = [
        {"tags": {"owner": "web-team"}, "cost": 60.0},
        {"tags": {}, "cost": 30.0},  # no tags at all
        {"tags": {"env": "prod"}, "cost": 10.0},  # tagged, but not with `owner`
        {"tags": {"owner": ""}, "cost": 5.0},  # empty value counts as untagged
    ]
    grouped = group_by_tag(rows, key="owner")
    assert grouped["web-team"] == 60.0
    assert grouped[UNALLOCATED] == 45.0  # 30 + 10 + 5 — never silently dropped


def test_allocated_plus_unallocated_equals_total() -> None:
    from cloudwarden.analysis.allocation import build_report

    buckets = {"web-team": 100.0, "data-team": 25.0, "unallocated": 45.0}
    report = build_report(buckets, key="owner")
    assert report.total == 170.0
    assert report.allocated == 125.0
    assert report.unallocated == 45.0
    # The reconciliation invariant: allocated + unallocated is exactly the total.
    assert report.allocated + report.unallocated == report.total


def test_tag_value_maps_to_team() -> None:
    from cloudwarden.analysis.allocation import build_report

    report = build_report(
        {"web-team": 100.0, "data-team": 25.0},
        key="owner",
        team_map={"web-team": "Platform", "data-team": "Data"},
    )
    by_value = {a.tag_value: a for a in report.allocations}
    assert by_value["web-team"].team == "Platform"
    assert by_value["data-team"].team == "Data"


def test_unmapped_tag_value_has_no_team() -> None:
    from cloudwarden.analysis.allocation import build_report

    report = build_report({"ghost-team": 10.0}, key="owner", team_map={"web-team": "Platform"})
    assert report.allocations[0].team is None  # mapped when known, else left unbound


def test_shared_cost_split_even_and_proportional() -> None:
    from cloudwarden.analysis.allocation import split_shared

    even = split_shared(120.0, ["a", "b", "c"], method="even")
    assert even == {"a": 40.0, "b": 40.0, "c": 40.0}

    proportional = split_shared(
        100.0, ["a", "b"], method="proportional", weights={"a": 30.0, "b": 70.0}
    )
    assert proportional == {"a": 30.0, "b": 70.0}  # split by each target's own weight


def test_split_shared_proportional_zero_weight_falls_back_to_even() -> None:
    from cloudwarden.analysis.allocation import split_shared

    # No usable weights (all zero) → an even split rather than a divide-by-zero.
    out = split_shared(50.0, ["a", "b"], method="proportional", weights={"a": 0.0, "b": 0.0})
    assert out == {"a": 25.0, "b": 25.0}


def test_split_shared_empty_targets() -> None:
    from cloudwarden.analysis.allocation import split_shared

    assert split_shared(100.0, [], method="even") == {}


def test_build_report_applies_shared_split() -> None:
    from cloudwarden.analysis.allocation import build_report

    # A "shared" bucket is redistributed across the other allocated buckets; the total
    # is preserved and the shared bucket disappears from the result.
    buckets = {"web-team": 60.0, "data-team": 40.0, "shared": 20.0}
    report = build_report(buckets, key="owner", shared_value="shared", split="even")
    by_value = {a.tag_value: a.cost for a in report.allocations}
    assert "shared" not in by_value
    assert by_value["web-team"] == 70.0  # 60 + 10
    assert by_value["data-team"] == 50.0  # 40 + 10
    assert report.total == 120.0  # redistribution moves the shared spend, preserves the total


def test_build_report_shares_sum_to_one() -> None:
    from cloudwarden.analysis.allocation import build_report

    report = build_report({"a": 30.0, "b": 70.0}, key="owner")
    assert round(sum(a.share for a in report.allocations), 6) == 1.0


def test_build_report_empty_is_zero() -> None:
    from cloudwarden.analysis.allocation import build_report

    report = build_report({}, key="owner")
    assert report.total == 0.0
    assert report.allocations == []


# --------------------------------------------------------------------------- #
# Cost enrichment — cost rows carry inventory tags
# --------------------------------------------------------------------------- #
def test_enrich_cost_attaches_inventory_tags_case_insensitively() -> None:
    from cloudwarden import models as m
    from cloudwarden.orchestrator import _enrich_cost

    resources = [
        m.ResourceRecord(
            resource_id="/SUBS/RG/providers/Microsoft.Compute/vm-1",
            subscription_id="sub",
            type="Microsoft.Compute/virtualMachines",
            name="vm-1",
            location="eastus",
            resource_group="RG",
            tags={"owner": "web-team"},
        )
    ]
    # Cost row id differs only in case — Azure resource ids are case-insensitive.
    cost = [
        m.CostRow(
            usage_date=_ON, resource_id="/subs/rg/providers/microsoft.compute/vm-1", cost=10.0
        )
    ]
    _enrich_cost(cost, resources)
    assert cost[0].tags == {"owner": "web-team"}
    assert cost[0].resource_type == "Microsoft.Compute/virtualMachines"


# --------------------------------------------------------------------------- #
# Repository — injection-safe by-tag aggregation
# --------------------------------------------------------------------------- #
def _seed(s, rows, *, on=_ON):
    """Seed ``cost_snapshots``: each row is ``(resource_id, tags, cost, subscription_id)``."""
    from cloudwarden import models as m
    from cloudwarden.storage import repository as repo

    crs = [
        m.CostRow(
            usage_date=on,
            resource_id=rid,
            subscription_id=sub,
            meter_category="Compute",
            cost=cost,
            tags=tags,
        )
        for rid, tags, cost, sub in rows
    ]
    repo.upsert_cost_snapshots(s, crs)


def test_cost_by_tag_groups_by_value(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        _seed(
            s,
            [
                ("/r1", {"owner": "web-team"}, 60.0, "sub-a"),
                ("/r2", {"owner": "web-team"}, 40.0, "sub-a"),
                ("/r3", {"owner": "data-team"}, 25.0, "sub-a"),
            ],
        )
        rows = repo.cost_by_tag(
            s, key="owner", start=_ON - dt.timedelta(days=5), end=_ON + dt.timedelta(days=1)
        )

    by_value = {r["tag_value"]: r["cost"] for r in rows}
    assert by_value["web-team"] == 100.0
    assert by_value["data-team"] == 25.0


def test_cost_by_tag_untagged_returns_null_value(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        _seed(s, [("/r1", {"owner": "web-team"}, 60.0, "sub-a"), ("/r2", {}, 30.0, "sub-a")])
        rows = repo.cost_by_tag(s, key="owner", start=_ON - dt.timedelta(days=5), end=_ON)

    by_value = {r["tag_value"]: r["cost"] for r in rows}
    assert by_value.get(None) == 30.0  # untagged → NULL tag_value (the unallocated bucket)


def test_group_by_key_is_injection_safe(db) -> None:
    from sqlalchemy import text

    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        _seed(s, [("/r1", {"owner": "web-team"}, 60.0, "sub-a")])
        # A SQL-injection attempt as the tag key: it is a BOUND parameter, so it is a
        # harmless JSONB key lookup (matching nothing) — never executed as SQL.
        rows = repo.cost_by_tag(
            s,
            key="owner'); DROP TABLE cost_snapshots; --",
            start=_ON - dt.timedelta(days=5),
            end=_ON,
        )
        # The table is intact and the spend landed in the unallocated (NULL) bucket.
        remaining = s.execute(text("SELECT COUNT(*) FROM cost_snapshots")).scalar()

    assert remaining == 1
    assert {r["tag_value"] for r in rows} == {None}


def test_cost_by_tag_subscription_filter(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        _seed(
            s,
            [
                ("/r1", {"owner": "web-team"}, 60.0, "sub-a"),
                ("/r2", {"owner": "web-team"}, 40.0, "sub-b"),
            ],
        )
        rows = repo.cost_by_tag(
            s, key="owner", start=_ON - dt.timedelta(days=5), end=_ON, subscription_id="sub-a"
        )

    # Only sub-a's spend is aggregated — the subscription filter is applied.
    assert {r["tag_value"]: r["cost"] for r in rows} == {"web-team": 60.0}


def test_cost_by_tag_team_scoped_filter(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        _seed(
            s,
            [
                ("/r1", {"owner": "web-team"}, 60.0, "sub-a"),
                ("/r2", {"owner": "data-team"}, 25.0, "sub-a"),
                ("/r3", {}, 15.0, "sub-a"),
            ],
        )
        rows = repo.cost_by_tag(
            s, key="owner", start=_ON - dt.timedelta(days=5), end=_ON, tag_values=["web-team"]
        )

    # Only the requested tag value is returned — untagged/other-team rows are excluded.
    assert {r["tag_value"] for r in rows} == {"web-team"}
    assert rows[0]["cost"] == 60.0


# --------------------------------------------------------------------------- #
# Team scoping
# --------------------------------------------------------------------------- #
def test_team_map_from_settings_parses() -> None:
    from types import SimpleNamespace

    from cloudwarden.authz import teams

    settings = SimpleNamespace(showback_team_map='{"web-team": "Platform", "data-team": "Data"}')
    assert teams.team_map_from_settings(settings) == {"web-team": "Platform", "data-team": "Data"}


def test_team_map_from_settings_malformed_is_empty() -> None:
    from types import SimpleNamespace

    from cloudwarden.authz import teams

    assert teams.team_map_from_settings(SimpleNamespace(showback_team_map="not json")) == {}
    assert teams.team_map_from_settings(SimpleNamespace(showback_team_map="[1,2]")) == {}


def test_visible_tag_values_rbac_off_returns_none(db) -> None:
    from cloudwarden.authz import teams
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        # RBAC disabled → no scoping → the caller sees every tag value.
        assert teams.visible_tag_values(s, None, team_map={"x": "T"}, rbac_enabled=False) is None


def test_visible_tag_values_anonymous_under_rbac_sees_nothing(db) -> None:
    from cloudwarden.authz import teams
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        # RBAC on but no principal → an empty set (sees nothing), never all.
        assert teams.visible_tag_values(s, None, team_map={"x": "T"}, rbac_enabled=True) == set()


def test_team_scoped_principal_sees_only_own(db) -> None:
    from cloudwarden.authz import teams
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        platform = repo.create_team(s, name="Platform")
        repo.create_team(s, name="Data")
        repo.add_team_member(s, team_id=platform["id"], principal="alice")
        team_map = {"web-team": "Platform", "data-team": "Data"}
        visible = teams.visible_tag_values(s, "alice", team_map=team_map, rbac_enabled=True)

    # Alice is on Platform → she sees only the tag values mapped to Platform.
    assert visible == {"web-team"}


# --------------------------------------------------------------------------- #
# compute_showback — DB-backed report
# --------------------------------------------------------------------------- #
def test_compute_showback_reconciles(db) -> None:
    from cloudwarden.analysis import allocation
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        _seed(
            s,
            [
                ("/r1", {"owner": "web-team"}, 60.0, "sub-a"),
                ("/r2", {"owner": "data-team"}, 25.0, "sub-a"),
                ("/r3", {}, 15.0, "sub-a"),
            ],
        )
        report = allocation.compute_showback(
            s, key="owner", start=_ON - dt.timedelta(days=5), end=_ON
        )

    assert report.total == 100.0
    assert report.allocated == 85.0
    assert report.unallocated == 15.0
    assert report.allocated + report.unallocated == report.total


def test_compute_showback_team_scoped_excludes_unallocated(db) -> None:
    from cloudwarden.analysis import allocation
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        _seed(
            s,
            [
                ("/r1", {"owner": "web-team"}, 60.0, "sub-a"),
                ("/r2", {"owner": "data-team"}, 25.0, "sub-a"),
                ("/r3", {}, 15.0, "sub-a"),
            ],
        )
        report = allocation.compute_showback(
            s,
            key="owner",
            start=_ON - dt.timedelta(days=5),
            end=_ON,
            visible_tag_values={"web-team"},
        )

    # A team-scoped view shows only the owned allocation — no unallocated bucket.
    assert {a.tag_value for a in report.allocations} == {"web-team"}
    assert report.unallocated == 0.0
    assert report.total == 60.0


# --------------------------------------------------------------------------- #
# API — read + RBAC + streaming export
# --------------------------------------------------------------------------- #
def _seed_via_api_db(s) -> None:
    _seed(
        s,
        [
            ("/r1", {"owner": "web-team"}, 60.0, "sub-a"),
            ("/r2", {"owner": "data-team"}, 25.0, "sub-a"),
            ("/r3", {}, 15.0, "sub-a"),
        ],
    )


def test_by_tag_endpoint(db) -> None:
    from fastapi.testclient import TestClient

    from cloudwarden.api.main import app
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        _seed_via_api_db(s)
    client = TestClient(app)

    resp = client.get("/api/costs/by-tag?key=owner&days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "owner"
    assert body["total"] == 100.0


def test_showback_endpoint_reconciles(db) -> None:
    from fastapi.testclient import TestClient

    from cloudwarden.api.main import app
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        _seed_via_api_db(s)
    client = TestClient(app)

    body = client.get("/api/costs/showback?key=owner&days=30").json()
    assert body["allocated"] + body["unallocated"] == body["total"] == 100.0
    assert body["unallocated"] == 15.0


def test_showback_read_requires_permission(db, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from cloudwarden.api.main import app
    from cloudwarden.authz import rbac
    from cloudwarden.config import get_settings
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    monkeypatch.setenv("RBAC_ENABLED", "1")
    get_settings.cache_clear()
    with session_scope() as s:
        rbac.seed_default_roles(s)
        repo.assign_role(s, principal="ed", role_name="editor")
    client = TestClient(app)

    assert client.get("/api/costs/showback?key=owner").status_code == 401
    assert (
        client.get("/api/costs/showback?key=owner", headers={"X-Principal": "ed"}).status_code
        == 200
    )
    get_settings.cache_clear()


def test_showback_export_streams_csv_json(db) -> None:
    from fastapi.testclient import TestClient

    from cloudwarden.api.main import app
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        _seed_via_api_db(s)
    client = TestClient(app)

    csv_resp = client.get("/api/costs/showback/export?key=owner&days=30&format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    lines = [ln for ln in csv_resp.text.splitlines() if ln]
    assert lines[0].split(",")[:2] == ["key", "tag_value"]  # header row
    assert any("web-team" in ln for ln in lines[1:])

    json_resp = client.get("/api/costs/showback/export?key=owner&days=30&format=json")
    assert json_resp.status_code == 200
    payload = json_resp.json()
    assert isinstance(payload, list)
    assert any(row["tag_value"] == "web-team" for row in payload)


def test_showback_export_rejects_bad_format(db) -> None:
    from fastapi.testclient import TestClient

    from cloudwarden.api.main import app

    client = TestClient(app)
    assert client.get("/api/costs/showback/export?key=owner&format=xml").status_code == 400
