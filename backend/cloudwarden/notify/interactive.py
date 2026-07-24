"""ChatOps interactive approvals (M14.15) — Slack / Teams Approve-Reject in chat.

CloudWarden already *sends* to Slack and Teams; this closes the loop. A pending-approval
message carries **Approve / Reject** buttons whose values are **signed action ids**
(action id + decision + nonce, HMAC-SHA256). When a user clicks one, the transport POSTs
an interaction back to :func:`handle_slack_interaction` / :func:`handle_teams_interaction`,
which:

1. verifies the **transport signature** (Slack ``v0`` HMAC over ``v0:ts:body`` with the
   app signing secret + a timestamp-skew replay guard; Teams ``HMAC`` base64 over the raw
   body) — a bad signature or a stale timestamp is a ``401``;
2. verifies **our own signature** on the embedded action id (tampering → ``401``);
3. resolves the **chat user id → RBAC principal** via ``chatops_principal_map`` (an
   unmapped user is a ``403`` — it cannot decide);
4. enforces ``remediation:approve`` through :func:`cloudwarden.authz.rbac.check_permission`
   (no bypass) and applies the decision through the **existing**
   :mod:`cloudwarden.remediation.approval` workflow (unknown → ``404``; already-decided,
   i.e. a **replay** → ``409``);
5. **audits** the decision (actor + channel + target) and **updates the source message**.

Every seam is injected — signing secrets from settings, the clock (``now``) and the
message ``updater`` passed in — so the whole flow runs offline with no live Slack/Teams
endpoint contacted (``FINOPS_MOCK=1``). Signing secrets are read from config and NEVER
logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import urllib.parse
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from fastapi import HTTPException

from ..authz import rbac
from ..remediation import approval
from ..storage import repository as repo
from ..storage.db import session_scope

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from ..config import Settings

logger = logging.getLogger("cloudwarden.notify.interactive")

# Slack signs each request ``v0=HMAC(secret, "v0:{ts}:{body}")`` and carries the timestamp
# in its own header; both are needed to verify + guard against replay.
SLACK_SIG_HEADER = "X-Slack-Signature"
SLACK_TS_HEADER = "X-Slack-Request-Timestamp"
SLACK_SIG_VERSION = "v0"
# Teams outgoing webhooks sign the raw body: ``Authorization: HMAC {base64(hmac)}``.
TEAMS_AUTH_HEADER = "Authorization"
TEAMS_AUTH_PREFIX = "HMAC "
# The permission every ChatOps decision must hold — the same gate the UI/API use.
DECISION_PERMISSION = "remediation:approve"
# Field separator inside a signed action id. None of the parts (int id, the words
# approve/reject, hex nonce, hex signature) can contain it, so ``split`` is unambiguous.
_TOKEN_SEP = "."
_DECISIONS = ("approve", "reject")

__all__ = [
    "BadSignature",
    "StaleRequest",
    "MalformedInteraction",
    "sign_token",
    "parse_token",
    "sign_slack_request",
    "verify_slack_signature",
    "sign_teams_request",
    "verify_teams_signature",
    "build_slack_message",
    "build_teams_message",
    "parse_slack_interaction",
    "parse_teams_interaction",
    "resolve_chat_principal",
    "send_pending_approval",
    "handle_slack_interaction",
    "handle_teams_interaction",
]


class BadSignature(Exception):
    """A transport signature, or our own action-id signature, failed verification."""


class StaleRequest(Exception):
    """An inbound interaction's timestamp is outside the allowed skew (replay guard)."""


class MalformedInteraction(Exception):
    """An inbound interaction payload could not be parsed into (user, token)."""


@runtime_checkable
class MessageUpdater(Protocol):
    """Updates the source chat message with the decision outcome — injected in tests."""

    def __call__(self, target: str, text: str) -> None: ...


# --------------------------------------------------------------------------- #
# Signed action ids — HMAC-SHA256 over "{action_id}.{decision}.{nonce}"
# --------------------------------------------------------------------------- #
def new_nonce() -> str:
    """A short, unguessable nonce so each rendered button value is unique."""
    return secrets.token_hex(8)


def _token_signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def sign_token(secret: str, *, action_id: int, decision: str, nonce: str) -> str:
    """Return a signed action id ``"{action_id}.{decision}.{nonce}.{sig}"``.

    ``decision`` is part of the signed payload, so approve/reject cannot be swapped
    without invalidating the signature.
    """
    if decision not in _DECISIONS:
        raise ValueError(f"decision must be one of {_DECISIONS}, got {decision!r}")
    payload = _TOKEN_SEP.join((str(action_id), decision, nonce))
    return payload + _TOKEN_SEP + _token_signature(secret, payload)


def parse_token(secret: str, token: str) -> tuple[int, str, str]:
    """Verify a signed action id and return ``(action_id, decision, nonce)``.

    Raises :class:`BadSignature` for a malformed, tampered, or wrong-decision token —
    the signature covers the id + decision + nonce, checked in constant time.
    """
    parts = token.split(_TOKEN_SEP)
    if len(parts) != 4:
        raise BadSignature("action id is malformed")
    raw_id, decision, nonce, signature = parts
    payload = _TOKEN_SEP.join((raw_id, decision, nonce))
    expected = _token_signature(secret, payload)
    if not hmac.compare_digest(expected, signature):
        raise BadSignature("action id signature mismatch")
    if decision not in _DECISIONS:
        raise BadSignature(f"unknown decision {decision!r}")
    try:
        action_id = int(raw_id)
    except ValueError as exc:
        raise BadSignature("action id is not an integer") from exc
    return action_id, decision, nonce


# --------------------------------------------------------------------------- #
# Transport signature verification — Slack (v0) + Teams (HMAC)
# --------------------------------------------------------------------------- #
def sign_slack_request(signing_secret: str, *, timestamp: str, body: bytes) -> str:
    """Compute Slack's ``v0=…`` request signature over ``v0:{timestamp}:{body}``."""
    basestring = f"{SLACK_SIG_VERSION}:{timestamp}:".encode() + body
    digest = hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return f"{SLACK_SIG_VERSION}={digest}"


def verify_slack_signature(
    signing_secret: str,
    *,
    timestamp: str,
    body: bytes,
    signature: str,
    now: int,
    max_skew: int,
) -> None:
    """Verify an inbound Slack interaction's signature + timestamp.

    Raises :class:`BadSignature` (missing secret / bad signature / non-numeric
    timestamp) or :class:`StaleRequest` (timestamp outside ``max_skew`` — replay guard).
    """
    if not signing_secret:
        raise BadSignature("slack signing secret is not configured")
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise BadSignature("slack request timestamp is missing or non-numeric") from exc
    if abs(now - ts) > max_skew:
        raise StaleRequest("slack request timestamp outside the allowed skew")
    expected = sign_slack_request(signing_secret, timestamp=timestamp, body=body)
    if not hmac.compare_digest(expected, signature or ""):
        raise BadSignature("slack signature mismatch")


def sign_teams_request(signing_secret_b64: str, *, body: bytes) -> str:
    """Compute a Teams outgoing-webhook ``HMAC {base64}`` header over the raw body."""
    key = base64.b64decode(signing_secret_b64)
    digest = hmac.new(key, body, hashlib.sha256).digest()
    return TEAMS_AUTH_PREFIX + base64.b64encode(digest).decode()


def verify_teams_signature(signing_secret_b64: str, *, body: bytes, signature: str) -> None:
    """Verify an inbound Teams interaction's ``Authorization: HMAC …`` header.

    Raises :class:`BadSignature` for a missing secret or a signature mismatch.
    """
    if not signing_secret_b64:
        raise BadSignature("teams signing secret is not configured")
    expected = sign_teams_request(signing_secret_b64, body=body)
    if not hmac.compare_digest(expected, signature or ""):
        raise BadSignature("teams signature mismatch")


# --------------------------------------------------------------------------- #
# Actionable message builders — Approve / Reject buttons carrying signed ids
# --------------------------------------------------------------------------- #
def _decision_tokens(
    secret: str, action_id: int, *, approve_nonce: str | None, reject_nonce: str | None
) -> tuple[str, str]:
    approve = sign_token(
        secret, action_id=action_id, decision="approve", nonce=approve_nonce or new_nonce()
    )
    reject = sign_token(
        secret, action_id=action_id, decision="reject", nonce=reject_nonce or new_nonce()
    )
    return approve, reject


def build_slack_message(
    secret: str,
    *,
    action_id: int,
    title: str,
    summary: str,
    approve_nonce: str | None = None,
    reject_nonce: str | None = None,
) -> dict[str, Any]:
    """A Slack Block Kit message: a context section + Approve/Reject action buttons."""
    approve, reject = _decision_tokens(
        secret, action_id, approve_nonce=approve_nonce, reject_nonce=reject_nonce
    )
    return {
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*\n{summary}"}},
            {
                "type": "actions",
                "block_id": f"cw-approval-{action_id}",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "cw-approve",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "value": approve,
                    },
                    {
                        "type": "button",
                        "action_id": "cw-reject",
                        "style": "danger",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "value": reject,
                    },
                ],
            },
        ]
    }


def build_teams_message(
    secret: str,
    *,
    action_id: int,
    title: str,
    summary: str,
    approve_nonce: str | None = None,
    reject_nonce: str | None = None,
) -> dict[str, Any]:
    """A Teams Adaptive Card with Approve/Reject ``Action.Submit`` carrying signed ids."""
    approve, reject = _decision_tokens(
        secret, action_id, approve_nonce=approve_nonce, reject_nonce=reject_nonce
    )
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": title, "weight": "Bolder", "wrap": True},
                        {"type": "TextBlock", "text": summary, "wrap": True},
                    ],
                    "actions": [
                        {"type": "Action.Submit", "title": "Approve", "data": {"token": approve}},
                        {"type": "Action.Submit", "title": "Reject", "data": {"token": reject}},
                    ],
                },
            }
        ],
    }


# --------------------------------------------------------------------------- #
# Inbound interaction parsing — extract (chat user id, signed token, response target)
# --------------------------------------------------------------------------- #
def parse_slack_interaction(raw_body: bytes) -> tuple[str, str, str | None]:
    """Parse a Slack ``block_actions`` interaction → ``(user_id, token, response_url)``.

    Slack POSTs ``payload=<url-encoded JSON>``; the clicked button's ``value`` is our
    signed action id. Raises :class:`MalformedInteraction` on any parse failure.
    """
    try:
        form = urllib.parse.parse_qs(raw_body.decode())
        payload = json.loads(form["payload"][0])
        user_id = str(payload["user"]["id"])
        token = str(payload["actions"][0]["value"])
    except (KeyError, IndexError, ValueError, UnicodeDecodeError) as exc:
        raise MalformedInteraction(f"unparseable slack interaction: {exc}") from exc
    response_url = payload.get("response_url")
    return user_id, token, response_url


def parse_teams_interaction(raw_body: bytes) -> tuple[str, str, str | None]:
    """Parse a Teams ``Action.Submit`` interaction → ``(user_id, token, response_url)``.

    The card action posts ``{"from": {"id": …}, "value": {"token": …}}``. Raises
    :class:`MalformedInteraction` on any parse failure.
    """
    try:
        payload = json.loads(raw_body.decode())
        user_id = str(payload["from"]["id"])
        token = str(payload["value"]["token"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise MalformedInteraction(f"unparseable teams interaction: {exc}") from exc
    response_url = (payload.get("value") or {}).get("response_url")
    return user_id, token, response_url


# --------------------------------------------------------------------------- #
# Actor resolution — chat user id → RBAC principal
# --------------------------------------------------------------------------- #
def _principal_map(settings: Settings) -> dict[str, str]:
    try:
        parsed = json.loads(settings.chatops_principal_map or "{}")
    except (ValueError, TypeError):
        logger.warning("CHATOPS_PRINCIPAL_MAP is not valid JSON; no chat users can decide")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_chat_principal(chat_user_id: str, settings: Settings) -> str | None:
    """Map a chat user id to its RBAC principal, or ``None`` when it has no mapping."""
    return _principal_map(settings).get(chat_user_id)


# --------------------------------------------------------------------------- #
# Outbound — render + dispatch an actionable pending-approval message
# --------------------------------------------------------------------------- #
def _remediation_action_model():
    # Local import keeps schema off the module import graph's hot path.
    from ..storage import schema

    return schema.RemediationAction


def send_pending_approval(
    session: Session,
    *,
    action_id: int,
    channel: str,
    target: str,
    transport: Any,
    settings: Settings,
    title: str | None = None,
    summary: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an actionable Approve/Reject message for a pending action and dispatch it.

    Reuses the injectable :class:`~cloudwarden.notify.service.Transport` seam — the
    actionable payload (Slack ``blocks`` / Teams ``attachments``) rides in
    ``config["extra"]``. Raises :class:`approval.NotFound` for an unknown action.
    """
    from . import service

    row = session.get(_remediation_action_model(), action_id)
    if row is None:
        raise approval.NotFound(f"remediation action {action_id} not found")
    resource_id = (row.params or {}).get("resource_id", "")
    ctx = service.build_approval_context(
        action_id=action_id, action_type=row.action_type, resource_id=resource_id
    )
    title = title or service.render(service.DEFAULT_APPROVAL_SUBJECT, ctx)
    summary = summary or service.render(service.DEFAULT_APPROVAL_BODY, ctx)
    secret = settings.resolved_chatops_secret
    if channel == "teams":
        message = build_teams_message(secret, action_id=action_id, title=title, summary=summary)
    else:
        message = build_slack_message(secret, action_id=action_id, title=title, summary=summary)
    payload_config = dict(config or {})
    extra = dict(payload_config.get("extra") or {})
    extra.update(message)
    payload_config["extra"] = extra
    result = transport.send(target=target, subject=title, body=summary, config=payload_config)
    return {"channel": channel, "action_id": action_id, "message": message, "result": result}


# --------------------------------------------------------------------------- #
# Inbound — verify, resolve, apply the decision, audit + update the message
# --------------------------------------------------------------------------- #
def _outcome_text(decision: str, principal: str, result: dict[str, Any]) -> str:
    if decision == "approve":
        return (
            f":white_check_mark: Approved by {principal} — "
            f"action #{result['action_id']} is now *{result['status']}*."
        )
    return f":no_entry_sign: Rejected by {principal} — action #{result['action_id']} will not run."


def _live_message_update(target: str, text: str) -> None:  # pragma: no cover - live network path
    """Update the source chat message by POSTing to its response target (Slack ``response_url``)."""
    import httpx

    with httpx.Client(timeout=10.0) as client:
        client.post(target, json={"text": text, "replace_original": True})


def apply_decision(
    session: Session,
    *,
    action_id: int,
    decision: str,
    principal: str,
    channel: str,
    settings: Settings,
    updater: MessageUpdater | None = None,
    response_target: str | None = None,
) -> dict[str, Any]:
    """Enforce ``remediation:approve`` then apply the decision through the M7.2 workflow.

    ``rbac.check_permission`` is a no-op when RBAC is disabled but a hard ``403`` gate
    when it is on — a ChatOps decision is never a permission bypass. Approval raises
    ``NotFound`` (→ ``404``) / ``AlreadyDecided`` (→ ``409``, i.e. a replay); both are
    translated to :class:`~fastapi.HTTPException`. On success the decision is audited
    (actor + channel + target) and the source message is updated.
    """
    rbac.check_permission(
        session, principal, DECISION_PERMISSION, rbac_enabled=settings.rbac_enabled
    )
    try:
        if decision == "approve":
            result = approval.approve_action(session, action_id, actor=principal, channel=channel)
        else:
            result = approval.reject_action(session, action_id, actor=principal, channel=channel)
    except approval.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except approval.AlreadyDecided as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    repo.insert_audit_log(
        session,
        actor=principal,
        action=f"chatops:{decision}",
        target_type="remediation_action",
        target_id=str(action_id),
        before={"status": "pending"},
        after={"status": result["status"], "channel": channel, "decided_via": channel},
    )

    if response_target:
        send_update = updater or _live_message_update
        send_update(response_target, _outcome_text(decision, principal, result))
    return result


def _dispatch(
    *,
    channel: str,
    chat_user: str,
    token: str,
    response_target: str | None,
    settings: Settings,
    session: Session | None,
    updater: MessageUpdater | None,
) -> dict[str, Any]:
    """Shared inbound tail: verify our token, resolve the actor, apply the decision."""
    try:
        action_id, decision, _nonce = parse_token(settings.resolved_chatops_secret, token)
    except BadSignature as exc:
        raise HTTPException(status_code=401, detail=f"invalid action id: {exc}") from exc

    principal = resolve_chat_principal(chat_user, settings)
    if not principal:
        raise HTTPException(status_code=403, detail=f"chat user {chat_user!r} is not authorized")

    def _run(s: Session) -> dict[str, Any]:
        return apply_decision(
            s,
            action_id=action_id,
            decision=decision,
            principal=principal,
            channel=channel,
            settings=settings,
            updater=updater,
            response_target=response_target,
        )

    if session is not None:
        return _run(session)
    with session_scope() as scoped:
        return _run(scoped)


def handle_slack_interaction(
    *,
    raw_body: bytes,
    headers: Any,
    settings: Settings,
    now: int | None = None,
    session: Session | None = None,
    updater: MessageUpdater | None = None,
) -> dict[str, Any]:
    """Verify a Slack interaction end-to-end and apply the decision (HTTP-status errors).

    Raises :class:`~fastapi.HTTPException`: ``401`` (bad/stale signature or tampered
    action id), ``400`` (unparseable payload), ``403`` (unauthorized chat user / missing
    permission), ``404`` (unknown action), ``409`` (already decided / replay).
    """
    import time

    now = now if now is not None else int(time.time())
    try:
        verify_slack_signature(
            settings.slack_signing_secret,
            timestamp=headers.get(SLACK_TS_HEADER, ""),
            body=raw_body,
            signature=headers.get(SLACK_SIG_HEADER, ""),
            now=now,
            max_skew=settings.chatops_max_skew_seconds,
        )
    except (BadSignature, StaleRequest) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        chat_user, token, response_target = parse_slack_interaction(raw_body)
    except MalformedInteraction as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _dispatch(
        channel="slack",
        chat_user=chat_user,
        token=token,
        response_target=response_target,
        settings=settings,
        session=session,
        updater=updater,
    )


def handle_teams_interaction(
    *,
    raw_body: bytes,
    headers: Any,
    settings: Settings,
    session: Session | None = None,
    updater: MessageUpdater | None = None,
) -> dict[str, Any]:
    """Verify a Teams interaction end-to-end and apply the decision (HTTP-status errors).

    Same status contract as :func:`handle_slack_interaction`. Teams carries no request
    timestamp, so replay is caught by the approval state machine (a re-decided action is
    ``409``).
    """
    try:
        verify_teams_signature(
            settings.teams_signing_secret,
            body=raw_body,
            signature=headers.get(TEAMS_AUTH_HEADER, ""),
        )
    except BadSignature as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        chat_user, token, response_target = parse_teams_interaction(raw_body)
    except MalformedInteraction as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _dispatch(
        channel="teams",
        chat_user=chat_user,
        token=token,
        response_target=response_target,
        settings=settings,
        session=session,
        updater=updater,
    )
