"""Guarded remediation of an approved recommendation.

Flow: recommendation must be `approved` → build a dry-run/execute plan
(REMEDIATION_ENABLED forces dry-run when false) → guardrails (exclude tag +
allow-list) → execute (skipped in mock mode) → record a `remediation_actions`
audit row and update the recommendation status. Every attempt is persisted.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..storage import schema
from . import executor, guardrails

logger = logging.getLogger("azure_finops.remediation")


class NotFound(Exception):
    pass


class NotApproved(Exception):
    pass


def _result(action: schema.RemediationAction) -> dict[str, Any]:
    message = (action.result or {}).get("message") if action.result else None
    return {
        "action_id": action.id,
        "recommendation_id": action.recommendation_id,
        "action_type": action.action_type,
        "dry_run": action.dry_run,
        "status": action.status,
        "message": message,
        "error": action.error,
    }


def remediate(
    session: Session, rec_id: int, actor: str | None = None, dry_run: bool = True
) -> dict[str, Any]:
    settings = get_settings()
    rec = session.get(schema.Recommendation, rec_id)
    if rec is None:
        raise NotFound(f"recommendation {rec_id} not found")
    if rec.status not in ("approved", "failed"):
        raise NotApproved(f"recommendation status is '{rec.status}'; must be 'approved'")

    # When remediation is disabled globally, force dry-run — never touch Azure.
    effective_dry_run = True if not settings.remediation_enabled else dry_run
    action_type = rec.action or rec.category
    resource = session.get(schema.Resource, rec.resource_id)
    tags = resource.tags if resource else {}

    action = schema.RemediationAction(
        recommendation_id=rec.id,
        action_type=action_type,
        params={"resource_id": rec.resource_id, "recommended_sku": rec.recommended_sku},
        dry_run=effective_dry_run,
        actor=actor,
        status="pending",
    )
    session.add(action)
    session.flush()

    guard = guardrails.check(rec.resource_id, tags, settings)
    guard_note = (
        "" if guard.allowed else " (guardrails would block: " + "; ".join(guard.reasons) + ")"
    )
    # Guardrails hard-block only real execution; a dry-run still previews.
    if not guard.allowed and not effective_dry_run:
        action.status = "blocked"
        action.error = "; ".join(guard.reasons)
        logger.info("remediation blocked for %s: %s", rec.resource_id, action.error)
        return _result(action)

    if settings.finops_mock:
        phase = "dry-run" if effective_dry_run else "mock-exec"
        action.result = {
            "mock": True,
            "message": f"[{phase}] {action_type} {rec.resource_id}{guard_note}",
        }
        action.status = "dry_run" if effective_dry_run else "executed"
        action.executed_at = datetime.now(UTC)
        if not effective_dry_run:
            rec.status = "executed"
            rec.decided_by = actor
        return _result(action)

    try:
        rec.status = "executing"
        session.flush()
        from ..auth import write_credential

        res = executor.execute(
            action_type,
            rec.resource_id,
            action.params,
            settings,
            credential=write_credential(),
            dry_run=effective_dry_run,
        )
        if guard_note and isinstance(res.get("message"), str):
            res["message"] += guard_note
        action.result = res
        action.executed_at = datetime.now(UTC)
        if res.get("executed"):
            action.status = "executed"
            rec.status = "executed"
        else:
            action.status = "dry_run" if effective_dry_run else "skipped"
            rec.status = "approved"  # not executed → remains actionable
    except Exception as exc:  # noqa: BLE001 - recorded on the audit row
        action.status = "failed"
        action.error = str(exc)
        rec.status = "failed"
        logger.exception("remediation failed for %s", rec.resource_id)
    return _result(action)
