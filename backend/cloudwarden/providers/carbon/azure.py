"""Azure carbon collector — Emissions Impact Dashboard (M14.16).

Replays Azure's scope-2 emissions (tonnes CO2e) through the shared collector, normalized
to gCO2e. The client is injectable — tests pass a fake; mock mode replays
``fixtures/carbon/azure.json``. The live path (Azure Carbon Optimization API) is lazily
built and out of mock scope.
"""

from __future__ import annotations

from typing import Any

from ...azure._fixtures import load_carbon_fixture
from ...config import get_settings
from ...models import EmissionRow
from . import collect

PROVIDER = "azure"


class _FixtureClient:
    """Offline stand-in for the Emissions Impact Dashboard (replays the fixture)."""

    def emissions(self) -> dict:
        return load_carbon_fixture(PROVIDER)


def _live_client(account: Any) -> Any:  # pragma: no cover - requires live Azure
    return _LiveClient(account)


class _LiveClient:  # pragma: no cover - requires live Azure Carbon Optimization API
    """Live Azure emissions collection. Out of scope for mock mode (M14.16 verifies with
    FINOPS_MOCK=1 and no live cloud); wire the Carbon Optimization / Emissions Impact API
    here."""

    def __init__(self, account: Any) -> None:
        self._account = account

    def emissions(self) -> dict:
        raise NotImplementedError(
            "live Azure emissions collection requires the Carbon Optimization API; "
            "run with FINOPS_MOCK=1 for the recorded fixtures"
        )


def collect_carbon(
    *, account: Any = None, client: Any = None, settings: Any = None
) -> list[EmissionRow]:
    """Collect Azure emissions as normalized gCO2e :class:`EmissionRow` objects."""
    settings = settings or get_settings()
    if client is None:
        client = _FixtureClient() if settings.finops_mock else _live_client(account)
    return collect(PROVIDER, client=client, account=account)
