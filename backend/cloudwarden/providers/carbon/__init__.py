"""Per-provider carbon / emissions collectors (M14.16).

Each cloud's collector (``azure`` / ``aws`` / ``gcp``) replays that provider's recorded
sustainability data through one shared path: pull the emissions envelope from an
**injectable** client (tests pass a fake; mock mode replays the recorded fixture; the live
sustainability API is out of mock scope), freshen ``days_ago``-keyed rows to real dates so
they always land inside the query window, then normalize everything to gCO2e via
:mod:`cloudwarden.analysis.carbon`. The result is the provider-neutral ``EmissionRow``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ...analysis import carbon
from ...models import EmissionRow


def collect(provider: str, *, client: Any, account: Any = None) -> list[EmissionRow]:
    """Pull an emissions envelope from ``client`` and normalize it to gCO2e ``EmissionRow``s.

    The client returns ``{"unit", "source", "account_id", "rows": [...]}``; a row keyed by
    ``days_ago`` (rather than an absolute ``usage_date``) is freshened to a recent date so
    the recorded fixtures never fall out of the query window — mirroring the mock cost
    collector. The onboarded ``account`` id (when given) retargets the fixture placeholder."""
    envelope = client.emissions()
    today = dt.date.today()
    raw: list[dict[str, Any]] = []
    for r in envelope.get("rows", []):
        row = dict(r)
        if "usage_date" not in row and "days_ago" in row:
            row["usage_date"] = (today - dt.timedelta(days=int(row["days_ago"]))).isoformat()
        raw.append(row)
    account_id = account.account_id if account is not None else envelope.get("account_id")
    return carbon.normalize(
        raw,
        provider=provider,
        account_id=account_id,
        unit=envelope.get("unit"),
        source=envelope.get("source"),
    )
