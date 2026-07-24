"""GCP IAM identity collector (M14.14).

Enumerates members (users / service accounts / groups) with their IAM role bindings,
service-account key metadata (credentials), MFA status and public exposure (``allUsers``
/ ``allAuthenticatedUsers`` bindings), normalized to the provider-neutral
:class:`~cloudwarden.models.IdentityPrincipal` the IAM-risk rules score. The client is
injectable — tests pass a fake; mock mode replays ``fixtures/identity/gcp.json`` (no live
cloud). The live path (Cloud IAM + Policy Analyzer) is lazily built and out of mock scope.
"""

from __future__ import annotations

from typing import Any

from ..azure._fixtures import load_identity_fixture
from ..config import get_settings
from ..models import IdentityPrincipal

_PROVIDER = "gcp"


class _FixtureIdentityClient:
    """Offline stand-in for Cloud IAM (replays the recorded fixture)."""

    def list_principals(self) -> list[dict]:
        return load_identity_fixture(_PROVIDER).get("principals", [])


def _live_client(account: Any) -> Any:  # pragma: no cover - requires live GCP
    return _LiveIdentityClient(account)


class _LiveIdentityClient:  # pragma: no cover - requires live GCP IAM
    """Live GCP IAM identity enumeration. Out of scope for mock mode (M14.14 verifies
    with FINOPS_MOCK=1 and no live cloud); wire the Cloud IAM policy + service-account
    keys APIs (and Policy Analyzer for allUsers exposure) here."""

    def __init__(self, account: Any) -> None:
        self._account = account

    def list_principals(self) -> list[dict]:
        raise NotImplementedError(
            "live GCP IAM identity collection requires the Cloud IAM + Policy Analyzer "
            "APIs; run with FINOPS_MOCK=1 for the recorded fixtures"
        )


def collect_identity(
    *, account: Any = None, client: Any = None, settings: Any = None
) -> list[IdentityPrincipal]:
    """Collect GCP principals as normalized :class:`IdentityPrincipal` objects."""
    settings = settings or get_settings()
    account_id = account.account_id if account is not None else None
    if client is None:
        client = _FixtureIdentityClient() if settings.finops_mock else _live_client(account)
    return [
        IdentityPrincipal.from_raw(row, provider=_PROVIDER, account_id=account_id)
        for row in client.list_principals()
    ]
