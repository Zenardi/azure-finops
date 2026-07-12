"""Azure Advisor cost recommendations (mock-backed).

Used as a ground-truth signal: when Advisor and our heuristics agree on a
resource, the recommendation is marked source='combined' and its confidence is
boosted. Resource ids are lower-cased to join with the inventory/rules.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings, get_settings
from ..resilience import REGISTRY, with_retry
from ._fixtures import load_fixture

logger = logging.getLogger("azure_finops.azure.advisor")


def _normalize(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for r in recs:
        if r.get("resource_id"):
            r["resource_id"] = str(r["resource_id"]).lower()
    return recs


def collect_advisor(client: Any = None) -> list[dict[str, Any]]:
    settings = get_settings()
    if settings.finops_mock:
        REGISTRY.set("advisor", ok=True)
        return _normalize(load_fixture("advisor"))
    return _collect_live(settings, client)


@with_retry()
def _collect_live(settings: Settings, client: Any) -> list[dict[str, Any]]:
    from azure.mgmt.advisor import AdvisorManagementClient

    from ..auth import read_credential

    advisor = client or AdvisorManagementClient(read_credential(), settings.azure_subscription_id)
    out: list[dict[str, Any]] = []
    for rec in advisor.recommendations.list(filter="Category eq 'Cost'"):
        props = getattr(rec, "extended_properties", None) or {}
        short = getattr(rec, "short_description", None)
        out.append(
            {
                "resource_id": (
                    getattr(rec, "resource_metadata", None).resource_id.lower()
                    if getattr(rec, "resource_metadata", None) and rec.resource_metadata.resource_id
                    else None
                ),
                "category": getattr(rec, "category", "Cost"),
                "impact": getattr(rec, "impact", None),
                "problem": getattr(short, "problem", None) if short else None,
                "solution": getattr(short, "solution", None) if short else None,
                "recommended_sku": props.get("targetSku"),
                "annual_savings": _to_float(props.get("annualSavingsAmount")),
                "extended_properties": dict(props),
            }
        )
    REGISTRY.set("advisor", ok=True)
    return out


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
