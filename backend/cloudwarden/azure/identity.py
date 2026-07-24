"""Entra ID / Azure RBAC identity collector (M14.14).

Enumerates principals (users / service principals / groups) with their role
assignments, credential metadata, MFA status and public-exposure signal, normalized
to the provider-neutral :class:`~cloudwarden.models.IdentityPrincipal` the IAM-risk
rules score. The client is injectable — tests pass a fake; mock mode replays the
recorded ``fixtures/identity/azure.json`` (no live directory). The live path (Microsoft
Graph + Authorization role assignments) is lazily built and out of mock scope.
"""

from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..models import IdentityPrincipal
from ._fixtures import load_identity_fixture

_PROVIDER = "azure"


class _FixtureIdentityClient:
    """Offline stand-in for the Entra/RBAC directory (replays the recorded fixture)."""

    def list_principals(self) -> list[dict]:
        return load_identity_fixture(_PROVIDER).get("principals", [])


def _live_client(account: Any) -> Any:  # pragma: no cover - requires live directory
    return _LiveIdentityClient(account)


class _LiveIdentityClient:  # pragma: no cover - requires live Entra / Azure RBAC
    """Live Entra ID + Azure RBAC identity enumeration. Out of scope for mock mode
    (M14.14 verifies with FINOPS_MOCK=1 and no live directory); wire Microsoft Graph
    (users/servicePrincipals/credentials/MFA) + Authorization role assignments here."""

    def __init__(self, account: Any) -> None:
        self._account = account

    def list_principals(self) -> list[dict]:
        raise NotImplementedError(
            "live Entra/Azure RBAC identity collection requires Microsoft Graph + "
            "Authorization APIs; run with FINOPS_MOCK=1 for the recorded fixtures"
        )


def collect_identity(
    *, account: Any = None, client: Any = None, settings: Any = None
) -> list[IdentityPrincipal]:
    """Collect Azure principals as normalized :class:`IdentityPrincipal` objects."""
    settings = settings or get_settings()
    account_id = account.account_id if account is not None else None
    if client is None:
        client = _FixtureIdentityClient() if settings.finops_mock else _live_client(account)
    return [
        IdentityPrincipal.from_raw(row, provider=_PROVIDER, account_id=account_id)
        for row in client.list_principals()
    ]
