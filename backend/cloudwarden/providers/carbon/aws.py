"""AWS carbon collector — Customer Carbon Footprint Tool (M14.16).

Replays AWS's emissions (metric tons CO2e, at service/region grain) through the shared
collector, normalized to gCO2e. The client is injectable — tests pass a fake; mock mode
replays ``fixtures/carbon/aws.json``. The live path (CCFT via Cost Explorer / Billing &
Cost Management data exports) is lazily built and out of mock scope.
"""

from __future__ import annotations

from typing import Any

from ...azure._fixtures import load_carbon_fixture
from ...config import get_settings
from ...models import EmissionRow
from . import collect

PROVIDER = "aws"


class _FixtureClient:
    """Offline stand-in for the Customer Carbon Footprint Tool (replays the fixture)."""

    def emissions(self) -> dict:
        return load_carbon_fixture(PROVIDER)


def _live_client(account: Any) -> Any:  # pragma: no cover - requires live AWS
    return _LiveClient(account)


class _LiveClient:  # pragma: no cover - requires live AWS CCFT
    """Live AWS emissions collection. Out of scope for mock mode (M14.16 verifies with
    FINOPS_MOCK=1 and no live cloud); wire the Customer Carbon Footprint Tool export here."""

    def __init__(self, account: Any) -> None:
        self._account = account

    def emissions(self) -> dict:
        raise NotImplementedError(
            "live AWS emissions collection requires the Customer Carbon Footprint Tool "
            "export; run with FINOPS_MOCK=1 for the recorded fixtures"
        )


def collect_carbon(
    *, account: Any = None, client: Any = None, settings: Any = None
) -> list[EmissionRow]:
    """Collect AWS emissions as normalized gCO2e :class:`EmissionRow` objects."""
    settings = settings or get_settings()
    if client is None:
        client = _FixtureClient() if settings.finops_mock else _live_client(account)
    return collect(PROVIDER, client=client, account=account)
