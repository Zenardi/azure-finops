"""Cost -> inventory enrichment test."""

from __future__ import annotations

import datetime as dt

from azure_finops.models import CostRow, ResourceRecord
from azure_finops.orchestrator import _enrich_cost


def test_enrich_fills_type_and_location() -> None:
    resources = [
        ResourceRecord(
            resource_id="/x/vm1",
            name="vm1",
            type="microsoft.compute/virtualmachines",
            location="eastus",
            resource_group="rg",
            subscription_id="s",
        )
    ]
    cost = [CostRow(usage_date=dt.date.today(), resource_id="/x/vm1", cost=1.0)]
    _enrich_cost(cost, resources)
    assert cost[0].resource_type == "microsoft.compute/virtualmachines"
    assert cost[0].location == "eastus"
    assert cost[0].resource_group == "rg"
