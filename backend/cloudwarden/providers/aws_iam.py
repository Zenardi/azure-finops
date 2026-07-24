"""AWS IAM identity collector (M14.14).

Enumerates IAM users / roles with their attached policies (role assignments), access
keys / passwords (credential metadata), MFA status and public / anonymous exposure
(``Principal:*`` trust), normalized to the provider-neutral
:class:`~cloudwarden.models.IdentityPrincipal` the IAM-risk rules score. The client is
injectable — tests pass a fake; mock mode replays ``fixtures/identity/aws.json`` (no
live cloud). The live path (IAM + Access Analyzer) is lazily built and out of mock scope.
"""

from __future__ import annotations

from typing import Any

from ..azure._fixtures import load_identity_fixture
from ..config import get_settings
from ..models import IdentityPrincipal

_PROVIDER = "aws"


class _FixtureIdentityClient:
    """Offline stand-in for IAM (replays the recorded fixture)."""

    def list_principals(self) -> list[dict]:
        return load_identity_fixture(_PROVIDER).get("principals", [])


def _live_client(account: Any) -> Any:  # pragma: no cover - requires live AWS
    return _LiveIdentityClient(account)


class _LiveIdentityClient:  # pragma: no cover - requires live AWS IAM
    """Live AWS IAM identity enumeration. Out of scope for mock mode (M14.14 verifies
    with FINOPS_MOCK=1 and no live cloud); wire boto3 IAM (list_users/roles,
    list_access_keys, get_login_profile, MFA devices) + Access Analyzer here."""

    def __init__(self, account: Any) -> None:
        self._account = account

    def list_principals(self) -> list[dict]:
        raise NotImplementedError(
            "live AWS IAM identity collection requires boto3 IAM + Access Analyzer; "
            "run with FINOPS_MOCK=1 for the recorded fixtures"
        )


def collect_identity(
    *, account: Any = None, client: Any = None, settings: Any = None
) -> list[IdentityPrincipal]:
    """Collect AWS principals as normalized :class:`IdentityPrincipal` objects."""
    settings = settings or get_settings()
    account_id = account.account_id if account is not None else None
    if client is None:
        client = _FixtureIdentityClient() if settings.finops_mock else _live_client(account)
    return [
        IdentityPrincipal.from_raw(row, provider=_PROVIDER, account_id=account_id)
        for row in client.list_principals()
    ]
