"""Carbon / emissions footprint — normalize, attribute, waste tie-in (M14.16).

Pure over injected data: each provider's collector hands raw emissions rows and this
module (a) **normalizes every figure to a single unit — grams CO2e (gCO2e)** — with the
provider source recorded, (b) **attributes** emissions to inventory resources where the
provider reports at resource grain, keeping service/region grain otherwise — never
fabricating per-resource precision the provider didn't give, and (c) ties idle/orphan
**waste** detectors to their wasted emissions alongside wasted spend.

Every figure is a provider-reported **estimate** carrying a methodology caveat — never
presented as measured fact (the platform's honesty principle).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..models import EmissionRow, Recommendation, ResourceRecord, WastedEmission

# Provider native emission units -> grams CO2e. Providers report in different units:
# Azure's Emissions Impact Dashboard in tonnes (tCO2e), AWS's Customer Carbon Footprint
# Tool in metric tons (MTCO2e == tonne), GCP's Carbon Footprint export in kilograms — so
# every figure is converted to grams and stored in one unit.
UNIT_TO_GRAMS: dict[str, float] = {
    "g": 1.0,
    "gco2e": 1.0,
    "gco2": 1.0,
    "grams": 1.0,
    "kg": 1_000.0,
    "kgco2e": 1_000.0,
    "kgco2": 1_000.0,
    "t": 1_000_000.0,
    "tco2e": 1_000_000.0,
    "tonne": 1_000_000.0,
    "tonnes": 1_000_000.0,
    "mt": 1_000_000.0,
    "mtco2e": 1_000_000.0,  # metric ton == tonne
}

# Each provider's canonical native unit (the unit its dashboard reports in) — used to
# interpret an amount when neither the envelope nor the row states a unit: Azure's Emissions
# Impact Dashboard reports tonnes, AWS's CCFT metric tons, GCP's export kilograms.
PROVIDER_DEFAULT_UNITS: dict[str, str] = {
    "azure": "tco2e",
    "aws": "mtco2e",
    "gcp": "kgco2e",
}

# The provider dashboard each emissions figure is sourced from — recorded on every row.
PROVIDER_SOURCES: dict[str, str] = {
    "azure": "Azure Emissions Impact Dashboard",
    "aws": "AWS Customer Carbon Footprint Tool",
    "gcp": "GCP Carbon Footprint (BigQuery export)",
}

# Placeholder account ids embedded in the recorded fixtures, retargeted to the onboarded
# account on ingest (mirrors the identity collectors) so multi-account runs stay distinct.
_PLACEHOLDER_ACCOUNTS: dict[str, str] = {
    "azure": "00000000-0000-0000-0000-000000000000",
    "aws": "123456789012",
    "gcp": "example-project-123456",
}

# The honesty caveat carried on every summary — emissions are estimates, not measurements.
METHODOLOGY_CAVEAT = (
    "Emissions are provider-reported estimates (Azure Emissions Impact Dashboard / AWS "
    "Customer Carbon Footprint Tool / GCP Carbon Footprint), normalized to grams CO2e "
    "(gCO2e). Provider methodologies (location- vs market-based), boundaries and reporting "
    "lag differ, so treat these as directional estimates — never as measured fact."
)


def to_gco2e(amount: float, unit: str | None) -> float:
    """Convert ``amount`` in a provider ``unit`` to grams CO2e (unknown unit -> grams)."""
    factor = UNIT_TO_GRAMS.get((unit or "gco2e").strip().lower(), 1.0)
    return float(amount) * factor


def _retarget(value: str | None, provider: str, account_id: str | None) -> str | None:
    """Rewrite a fixture's placeholder account segment to the onboarded account."""
    placeholder = _PLACEHOLDER_ACCOUNTS.get(provider)
    if value and account_id and placeholder and account_id != placeholder:
        return value.replace(placeholder, account_id)
    return value


def _parse_date(value: Any) -> dt.date:
    if not value:
        return dt.date.today()
    return dt.date.fromisoformat(str(value)[:10])


def normalize(
    rows: list[dict[str, Any]],
    *,
    provider: str,
    account_id: str | None = None,
    unit: str | None = None,
    source: str | None = None,
) -> list[EmissionRow]:
    """Normalize raw provider emissions rows to gCO2e ``EmissionRow``s (source recorded).

    ``unit`` / ``source`` are the envelope defaults a row may override. A zero/empty
    source yields **no** rows — an empty emissions source is never a fabricated zero-row.
    Grain is inferred from the presence of a resource id when not stated, and is never
    over-stated: a row with no resource id stays at service grain (``resource_id=None``)."""
    default_source = source or PROVIDER_SOURCES.get(provider, "")
    default_unit = unit or PROVIDER_DEFAULT_UNITS.get(provider, "gco2e")
    out: list[EmissionRow] = []
    for r in rows:
        gco2e = to_gco2e(r.get("amount", 0.0), r.get("unit") or default_unit)
        if gco2e <= 0:
            continue  # empty / zero emissions -> no row
        raw_id = r.get("resource_id")
        grain = r.get("grain") or ("resource" if raw_id else "service")
        resource_id = _retarget(raw_id, provider, account_id) if grain == "resource" else None
        out.append(
            EmissionRow(
                usage_date=_parse_date(r.get("usage_date")),
                provider=provider,
                account_id=account_id or r.get("account_id"),
                resource_id=resource_id,
                service_name=r.get("service_name"),
                location=r.get("location"),
                grain=grain,
                gco2e=round(gco2e, 4),
                source=r.get("source") or default_source,
                method=r.get("method") or "provider_estimate",
            )
        )
    return out


def attribute(rows: list[EmissionRow], resources: list[ResourceRecord]) -> list[EmissionRow]:
    """Attribute resource-grain rows to inventory, enriching type/location when available.

    A resource-grain row that matches an inventory resource is bound to it (its type and
    location filled in); a service/region-grain row — or a resource-grain row with no
    inventory match — is **kept as-is**, never split into fabricated per-resource rows."""
    by_id = {r.resource_id: r for r in resources}
    out: list[EmissionRow] = []
    for row in rows:
        resource = by_id.get(row.resource_id) if row.grain == "resource" else None
        if resource is None:
            out.append(row)
            continue
        out.append(
            row.model_copy(
                update={
                    "resource_type": row.resource_type or resource.type,
                    "location": row.location or resource.location,
                }
            )
        )
    return out


def per_resource_gco2e(rows: list[EmissionRow]) -> dict[str, float]:
    """Sum resource-grain emissions by resource id (service/region grain excluded)."""
    totals: dict[str, float] = {}
    for r in rows:
        if r.grain == "resource" and r.resource_id:
            totals[r.resource_id] = totals.get(r.resource_id, 0.0) + r.gco2e
    return totals


def wasted_emissions(
    recommendations: list[Recommendation], rows: list[EmissionRow]
) -> list[WastedEmission]:
    """Wasted emissions for each waste recommendation whose resource has attributed emissions.

    Only resource-grain emissions can be tied to a resource, so a rec whose resource has
    only service-grain (or no) emissions produces no line — never a fabricated figure."""
    by_resource = per_resource_gco2e(rows)
    out: list[WastedEmission] = []
    for rec in recommendations:
        gco2e = by_resource.get(rec.resource_id)
        if gco2e is None:
            continue
        out.append(
            WastedEmission(
                resource_id=rec.resource_id,
                category=rec.category,
                wasted_gco2e=round(gco2e, 4),
                wasted_monthly_cost=rec.est_monthly_savings,
                currency=rec.currency,
            )
        )
    return out


def summarize(rows: list[EmissionRow]) -> dict[str, Any]:
    """Roll emissions into a total + per-provider/service breakdown, sources & the caveat."""
    total = 0.0
    by_provider: dict[str, float] = {}
    by_service: dict[str, float] = {}
    sources: set[str] = set()
    for r in rows:
        total += r.gco2e
        by_provider[r.provider] = by_provider.get(r.provider, 0.0) + r.gco2e
        key = r.service_name or "(unattributed)"
        by_service[key] = by_service.get(key, 0.0) + r.gco2e
        if r.source:
            sources.add(r.source)
    return {
        "total_gco2e": round(total, 4),
        "total_kgco2e": round(total / 1000.0, 4),
        "by_provider": [
            {"provider": p, "gco2e": round(v, 4)}
            for p, v in sorted(by_provider.items(), key=lambda kv: -kv[1])
        ],
        "by_service": [
            {"service_name": s, "gco2e": round(v, 4)}
            for s, v in sorted(by_service.items(), key=lambda kv: -kv[1])
        ],
        "sources": sorted(sources),
        "methodology": METHODOLOGY_CAVEAT,
    }
