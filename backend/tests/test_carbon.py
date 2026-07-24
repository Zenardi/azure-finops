"""M14.16 — carbon / emissions footprint (sustainability). Tests written FIRST (TDD).

CloudWarden governs cost but has no carbon dimension. This layer collects each
provider's sustainability data (Azure Emissions Impact Dashboard, AWS Customer Carbon
Footprint Tool, GCP Carbon Footprint export), **normalizes every figure to a single
unit — grams CO2-equivalent (gCO2e) — with the provider source recorded**, attributes
emissions to resources where the provider reports at resource grain (keeping
service/region grain otherwise, never fabricating per-resource precision), and ties
idle/orphan waste detectors to their **wasted emissions** alongside wasted spend.

Everything is an **estimate** carrying a methodology caveat — never presented as fact.

Layers under test:
* **Normalization + attribution + waste** (pure, no DB): unit conversion to gCO2e,
  per-resource attribution when available, service-grain kept when not, wasted-emissions
  tie-in. Positive AND negative (empty source → no rows; service-grain-only → kept).
* **Collectors** (injected/mock clients): each provider's recorded fixture normalizes to
  the same ``EmissionRow`` shape; the live path is out of mock scope.
* **Orchestrator + repository + API** (``db`` fixture): collect emissions per account,
  persist a snapshot, read a summary + by-resource trend, provider-filterable.
"""

from __future__ import annotations

import datetime as dt

from cloudwarden.analysis import carbon
from cloudwarden.models import EmissionRow, Recommendation, ResourceRecord

# --------------------------------------------------------------------------- #
# Builders — hand-built rows keep the pure tests isolated & repeatable.
# --------------------------------------------------------------------------- #
_TODAY = dt.date(2026, 7, 1)


def _raw(
    *,
    resource_id: str | None = None,
    service_name: str | None = None,
    location: str | None = None,
    grain: str | None = None,
    amount: float = 1.0,
    unit: str | None = None,
) -> dict:
    row: dict = {"usage_date": _TODAY.isoformat(), "amount": amount}
    if resource_id is not None:
        row["resource_id"] = resource_id
    if service_name is not None:
        row["service_name"] = service_name
    if location is not None:
        row["location"] = location
    if grain is not None:
        row["grain"] = grain
    if unit is not None:
        row["unit"] = unit
    return row


def _resource(resource_id: str, *, rtype: str = "microsoft.compute/disks") -> ResourceRecord:
    return ResourceRecord(
        resource_id=resource_id,
        name=resource_id.rsplit("/", 1)[-1],
        type=rtype,
        location="westeurope",
        resource_group="rg-app",
        subscription_id="00000000-0000-0000-0000-000000000000",
    )


# --------------------------------------------------------------------------- #
# Unit conversion — normalize every provider unit to a single unit (gCO2e).
# --------------------------------------------------------------------------- #
def test_to_gco2e_converts_tonnes_kilograms_grams() -> None:
    # Arrange / Act / Assert — 1 t = 1e6 g, 1 kg = 1e3 g, metric ton == tonne.
    assert carbon.to_gco2e(1.0, "tco2e") == 1_000_000.0
    assert carbon.to_gco2e(1.0, "kgco2e") == 1_000.0
    assert carbon.to_gco2e(2.5, "g") == 2.5
    assert carbon.to_gco2e(1.0, "mtco2e") == 1_000_000.0


def test_to_gco2e_unknown_unit_treated_as_grams() -> None:
    # An unrecognized unit falls back to grams (factor 1.0) rather than crashing a run.
    assert carbon.to_gco2e(7.0, "widgets") == 7.0


# --------------------------------------------------------------------------- #
# Collectors — normalize each provider's fixture (mock / injected client) to gCO2e
# --------------------------------------------------------------------------- #
def test_azure_emissions_normalized_to_gco2e() -> None:
    from cloudwarden.providers.carbon import azure

    rows = azure.collect_carbon()
    assert rows and all(isinstance(r, EmissionRow) for r in rows)
    assert all(r.provider == "azure" for r in rows)
    # Every row carries a positive gCO2e figure and records the provider source.
    assert all(r.gco2e > 0 for r in rows)
    assert all("Emissions Impact" in r.source for r in rows)


def test_aws_emissions_normalized_to_gco2e() -> None:
    from cloudwarden.providers.carbon import aws

    rows = aws.collect_carbon()
    assert rows and all(r.provider == "aws" for r in rows)
    assert all(r.gco2e > 0 for r in rows)
    assert all("Carbon Footprint" in r.source for r in rows)


def test_gcp_emissions_normalized_to_gco2e() -> None:
    from cloudwarden.providers.carbon import gcp

    rows = gcp.collect_carbon()
    assert rows and all(r.provider == "gcp" for r in rows)
    assert all(r.gco2e > 0 for r in rows)
    assert all("Carbon Footprint" in r.source for r in rows)


def test_collect_carbon_accepts_injected_client() -> None:
    from cloudwarden.providers.carbon import azure

    class _Fake:
        def emissions(self) -> dict:
            return {
                "unit": "kgco2e",
                "source": "fake",
                "rows": [_raw(resource_id="/r/x", grain="resource", amount=3.0)],
            }

    rows = azure.collect_carbon(client=_Fake())
    assert len(rows) == 1
    assert rows[0].gco2e == 3000.0  # 3 kg → 3000 g


def test_collect_carbon_freshens_days_ago_to_recent_date() -> None:
    # A fixture row keyed by ``days_ago`` (not an absolute date) always lands inside the
    # query window regardless of when CI runs — mirrors the mock cost collector.
    from cloudwarden.providers.carbon import azure

    class _Fake:
        def emissions(self) -> dict:
            return {
                "unit": "g",
                "source": "fake",
                "rows": [{"days_ago": 2, "resource_id": "/r/x", "grain": "resource", "amount": 5}],
            }

    rows = azure.collect_carbon(client=_Fake())
    assert rows[0].usage_date == dt.date.today() - dt.timedelta(days=2)


# --------------------------------------------------------------------------- #
# The seven named TDD cases (issue #149)
# --------------------------------------------------------------------------- #
def test_per_resource_attribution_when_available() -> None:
    # Arrange — a resource-grain emission row + the matching inventory resource.
    rid = "/subscriptions/s/resourceGroups/rg-app/providers/Microsoft.Compute/disks/d1"
    rows = carbon.normalize([_raw(resource_id=rid, grain="resource", amount=2.0)], provider="azure")
    resources = [_resource(rid, rtype="microsoft.compute/disks")]
    # Act — attribute against inventory.
    attributed = carbon.attribute(rows, resources)
    # Assert — the row is bound to the resource and enriched with its type.
    assert len(attributed) == 1
    assert attributed[0].resource_id == rid
    assert attributed[0].grain == "resource"
    assert attributed[0].resource_type == "microsoft.compute/disks"


def test_service_grain_kept_when_no_resource_detail() -> None:
    # Arrange — a service-grain row (provider gave no resource id).
    rows = carbon.normalize(
        [_raw(service_name="Azure Monitor", grain="service", amount=1.0)], provider="azure"
    )
    # Act — attribute against a (different) inventory that cannot match it.
    attributed = carbon.attribute(rows, [_resource("/r/other")])
    # Assert — grain is preserved; no per-resource precision is fabricated.
    assert len(attributed) == 1
    assert attributed[0].grain == "service"
    assert attributed[0].resource_id is None
    assert attributed[0].resource_type is None
    assert attributed[0].service_name == "Azure Monitor"


def test_idle_resource_reports_wasted_emissions() -> None:
    # Arrange — an idle/orphan recommendation on a resource that has emissions.
    rid = "/subscriptions/s/resourceGroups/rg-app/providers/Microsoft.Compute/disks/orphan"
    rows = carbon.normalize([_raw(resource_id=rid, grain="resource", amount=0.5)], provider="azure")
    rec = Recommendation(
        resource_id=rid,
        category="delete_orphan",
        action="delete_disk",
        est_monthly_savings=12.0,
        currency="EUR",
    )
    # Act
    wasted = carbon.wasted_emissions([rec], rows)
    # Assert — wasted emissions surface alongside the wasted spend.
    assert len(wasted) == 1
    assert wasted[0].resource_id == rid
    assert wasted[0].wasted_gco2e == 500_000.0  # 0.5 t → 500,000 g
    assert wasted[0].wasted_monthly_cost == 12.0
    assert wasted[0].currency == "EUR"


def test_empty_source_yields_no_rows() -> None:
    # Arrange / Act — an empty emissions source normalizes to nothing.
    assert carbon.normalize([], provider="azure") == []


def test_normalize_defaults_missing_date_to_today() -> None:
    # A row with no usage_date falls back to today rather than crashing normalization.
    rows = carbon.normalize([{"resource_id": "/r/a", "amount": 1.0}], provider="azure")
    assert rows[0].usage_date == dt.date.today()


# --------------------------------------------------------------------------- #
# Negative / robustness — never fabricate, never mislabel, never crash
# --------------------------------------------------------------------------- #
def test_normalize_skips_zero_emission_rows() -> None:
    rows = carbon.normalize(
        [_raw(resource_id="/r/a", amount=0.0), _raw(resource_id="/r/b", amount=1.0)],
        provider="aws",
    )
    assert [r.resource_id for r in rows] == ["/r/b"]


def test_normalize_infers_grain_from_resource_id() -> None:
    # No explicit grain: a resource id → resource grain; otherwise service grain.
    with_id = carbon.normalize([_raw(resource_id="/r/a", amount=1.0)], provider="gcp")
    without_id = carbon.normalize([_raw(service_name="BigQuery", amount=1.0)], provider="gcp")
    assert with_id[0].grain == "resource"
    assert without_id[0].grain == "service"
    assert without_id[0].resource_id is None


def test_normalize_retargets_account_placeholder() -> None:
    rid = "arn:aws:ec2:us-east-1:123456789012:instance/i-1"
    rows = carbon.normalize(
        [_raw(resource_id=rid, grain="resource", amount=1.0, unit="mtco2e")],
        provider="aws",
        account_id="999900001111",
    )
    assert "999900001111" in rows[0].resource_id
    assert rows[0].account_id == "999900001111"


def test_normalize_uses_provider_source_default_when_row_omits_it() -> None:
    rows = carbon.normalize([_raw(resource_id="/r/a", amount=1.0)], provider="gcp")
    assert "Carbon Footprint" in rows[0].source


def test_attribute_leaves_unmatched_resource_grain_unenriched() -> None:
    # A resource-grain row with no matching inventory keeps its grain but no resource_type.
    rows = carbon.normalize(
        [_raw(resource_id="/r/ghost", grain="resource", amount=1.0)], provider="azure"
    )
    attributed = carbon.attribute(rows, [_resource("/r/other")])
    assert attributed[0].grain == "resource"
    assert attributed[0].resource_type is None


def test_wasted_emissions_ignores_resource_without_emissions() -> None:
    rec = Recommendation(resource_id="/r/no-emissions", category="idle_ip", action="delete_ip")
    assert carbon.wasted_emissions([rec], []) == []


def test_wasted_emissions_ignores_service_grain_rows() -> None:
    # Service-grain emissions can't be attributed to a resource → no wasted-emissions line.
    rows = carbon.normalize([_raw(service_name="S3", grain="service", amount=1.0)], provider="aws")
    rec = Recommendation(resource_id="/r/x", category="delete_orphan", action="delete_disk")
    assert carbon.wasted_emissions([rec], rows) == []


# --------------------------------------------------------------------------- #
# Summary — total gCO2e with the source recorded + the methodology caveat
# --------------------------------------------------------------------------- #
def test_summarize_totals_by_provider_and_service_with_caveat() -> None:
    rows = carbon.normalize(
        [_raw(resource_id="/r/a", service_name="VM", amount=1.0)], provider="azure"
    ) + carbon.normalize(
        [_raw(service_name="S3", grain="service", amount=2.0, unit="mtco2e")], provider="aws"
    )
    summary = carbon.summarize(rows)
    # azure 1 t = 1e6 g; aws 2 mt = 2e6 g → 3e6 g total.
    assert summary["total_gco2e"] == 3_000_000.0
    providers = {p["provider"]: p["gco2e"] for p in summary["by_provider"]}
    assert providers == {"azure": 1_000_000.0, "aws": 2_000_000.0}
    assert summary["sources"]  # provider sources recorded
    assert "estimate" in summary["methodology"].lower()


def test_summarize_empty_is_zero() -> None:
    summary = carbon.summarize([])
    assert summary["total_gco2e"] == 0.0
    assert summary["by_provider"] == []


# --------------------------------------------------------------------------- #
# Provider seam — collect_carbon dispatches per registered cloud
# --------------------------------------------------------------------------- #
def test_provider_collect_carbon_dispatch() -> None:
    from cloudwarden.providers import registry

    for name in ("aws", "azure", "gcp"):
        rows = registry.get(name).collect_carbon()
        assert rows and all(r.provider == name for r in rows)


def test_orchestrator_collect_carbon_isolates_provider_failure() -> None:
    from cloudwarden.azure.context import AccountContext
    from cloudwarden.orchestrator import collect_carbon

    bogus = AccountContext(account_id="x", provider="nope")
    assert collect_carbon([bogus]) == []


def test_orchestrator_carbon_snapshot_summarizes() -> None:
    from cloudwarden.orchestrator import carbon_snapshot

    snap = carbon_snapshot(["azure"])
    assert snap["summary"]["total_gco2e"] > 0
    assert snap["rows"]


# --------------------------------------------------------------------------- #
# Repository + orchestrator persistence (DB fixture)
# --------------------------------------------------------------------------- #
def test_run_carbon_persists_snapshots(db) -> None:
    from cloudwarden.orchestrator import run_carbon
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    counts = run_carbon(["azure"])
    assert counts["carbon_rows"] > 0
    with session_scope() as s:
        summary = repo.carbon_summary(s, days=30, provider="azure")
    assert summary["total_gco2e"] > 0


def test_upsert_carbon_snapshots_is_idempotent(db) -> None:
    from cloudwarden.orchestrator import run_carbon
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    run_carbon(["azure"])
    with session_scope() as s:
        first = repo.carbon_summary(s, days=30, provider="azure")["total_gco2e"]
    run_carbon(["azure"])  # re-collect the same fixture
    with session_scope() as s:
        second = repo.carbon_summary(s, days=30, provider="azure")["total_gco2e"]
    assert first == second  # upsert on the natural key — no duplication


def test_carbon_by_resource_returns_resource_grain_rows(db) -> None:
    from cloudwarden.orchestrator import run_carbon
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    run_carbon(["azure"])
    with session_scope() as s:
        rows = repo.carbon_by_resource(s, limit=50, provider="azure")
    assert rows and all(r["resource_id"] for r in rows)
    assert all(float(r["gco2e"]) > 0 for r in rows)


def test_upsert_carbon_snapshots_empty_is_noop(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    with session_scope() as s:
        assert repo.upsert_carbon_snapshots(s, []) == 0


# --------------------------------------------------------------------------- #
# API — GET summary / by-resource, POST collect, provider filter (DB fixture)
# --------------------------------------------------------------------------- #
def _client():
    from fastapi.testclient import TestClient

    from cloudwarden.api.main import app

    return TestClient(app)


def test_api_carbon_summary_empty_before_collect(db) -> None:
    resp = _client().get("/api/carbon/summary")
    assert resp.status_code == 200
    assert resp.json()["total_gco2e"] == 0.0


def test_api_carbon_collect_then_summary(db) -> None:
    client = _client()
    collected = client.post("/api/carbon/collect", params={"provider": "azure"})
    assert collected.status_code == 200
    assert collected.json()["carbon_rows"] > 0

    summary = client.get("/api/carbon/summary", params={"provider": "azure"}).json()
    assert summary["total_gco2e"] > 0
    assert summary["sources"]
    assert "estimate" in summary["methodology"].lower()


def test_api_carbon_by_resource(db) -> None:
    client = _client()
    client.post("/api/carbon/collect", params={"provider": "azure"})
    rows = client.get("/api/carbon/by-resource", params={"provider": "azure"}).json()
    assert rows and all(r["resource_id"] for r in rows)


def test_api_carbon_collect_invalid_provider(db) -> None:
    resp = _client().post("/api/carbon/collect", params={"provider": "nope"})
    assert resp.status_code == 400


def test_api_carbon_summary_provider_filter_isolates_cloud(db) -> None:
    client = _client()
    client.post("/api/carbon/collect", params={"provider": "azure"})
    # Filtering to a cloud with no collected emissions yields zero — not azure's total.
    aws_only = client.get("/api/carbon/summary", params={"provider": "aws"}).json()
    assert aws_only["total_gco2e"] == 0.0
