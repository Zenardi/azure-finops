"""ChatOps interactive approvals (M14.15) — Slack / Teams Approve-Reject in chat.

Written test-first (TDD). CloudWarden already *sends* to Slack/Teams; this closes the
loop: a pending-approval message carries **Approve / Reject** buttons, and an inbound,
**signature-verified** interaction resolves the chat user to an RBAC principal and
applies the decision through the *existing* :mod:`cloudwarden.remediation.approval`
workflow — no parallel approval path, no ``require_permission`` bypass.

Every seam is injected so the whole flow runs offline (``FINOPS_MOCK=1``): the signing
secrets come from settings, the clock (``now``) and the message ``updater`` are passed
in, and no live Slack/Teams endpoint is ever contacted.

Behaviours under test (Arrange–Act–Assert, one assertion of intent each):

* a pending approval renders an **actionable** message whose buttons carry signed ids;
* a valid, signed **approve** applies the decision (and a **reject** rejects it);
* a **bad transport signature** → 401; a **stale/replayed** interaction → rejected;
* an **unmapped / unauthorized** chat user → 403; an **unknown action id** → 404;
* every decision is **audited** (actor + channel) and the source message is **updated**.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cloudwarden.api.main import app
from cloudwarden.config import get_settings
from cloudwarden.notify import interactive
from cloudwarden.remediation import approval
from cloudwarden.storage import repository as repo
from cloudwarden.storage import schema
from cloudwarden.storage.db import session_scope

RID = "/subscriptions/s/resourceGroups/rg-app/providers/Microsoft.Compute/virtualMachines/vm-1"

CHATOPS_SECRET = "chatops-signing-secret"
SLACK_SECRET = "slack-signing-secret"
TEAMS_SECRET_B64 = base64.b64encode(b"teams-outgoing-secret").decode()


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class _SpyTransport:
    """Records every :meth:`send` — the outbound dispatch seam."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, *, target: str, subject: str, body: str, config: dict) -> dict:
        self.calls.append({"target": target, "subject": subject, "body": body, "config": config})
        return {"ok": True}


class _SpyUpdater:
    """Records every source-message update — the outbound update seam."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, target: str, text: str) -> None:
        self.calls.append((target, text))


def _configure(
    monkeypatch,
    *,
    principals: dict[str, str] | None = None,
    skew: int = 300,
    rbac: bool = False,
):
    """Point settings at the test signing secrets + chat-user → principal map."""
    monkeypatch.setenv("CHATOPS_SIGNING_SECRET", CHATOPS_SECRET)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SLACK_SECRET)
    monkeypatch.setenv("TEAMS_SIGNING_SECRET", TEAMS_SECRET_B64)
    monkeypatch.setenv("CHATOPS_MAX_SKEW_SECONDS", str(skew))
    monkeypatch.setenv("CHATOPS_PRINCIPAL_MAP", json.dumps(principals or {}))
    if rbac:
        monkeypatch.setenv("RBAC_ENABLED", "true")
    get_settings.cache_clear()
    return get_settings()


def _seed_pending_action(action: str = "stop") -> int:
    """Create a policy + execution + match and queue one **pending** action."""
    with session_scope() as s:
        pid = repo.create_policy(
            s,
            name="guard-vms",
            resource_type="azure.vm",
            spec={"policies": [{"name": "guard-vms", "resource": "azure.vm", "actions": [action]}]},
        )["id"]
        repo.create_policy_execution(s, execution_id="ex-1", policy_id=pid, subscription_id="sub-1")
        match = schema.PolicyMatch(execution_id="ex-1", resource_id=RID, resource_type="azure.vm")
        s.add(match)
        s.flush()
        return approval.queue_policy_action(s, match.id, action, dry_run=True)["action_id"]


def _slack_body(token: str, *, user: str = "U-alice", response_url: str | None = None) -> bytes:
    """A Slack ``block_actions`` interaction, url-encoded exactly as Slack posts it."""
    payload = {
        "type": "block_actions",
        "user": {"id": user, "username": "alice"},
        "response_url": response_url or "https://hooks.slack.test/actions/1/2/3",
        "actions": [{"action_id": "cw-decide", "value": token}],
    }
    return urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()


def _slack_headers(body: bytes, *, secret: str = SLACK_SECRET, ts: str | None = None) -> dict:
    ts = ts or str(int(time.time()))
    sig = interactive.sign_slack_request(secret, timestamp=ts, body=body)
    return {
        interactive.SLACK_TS_HEADER: ts,
        interactive.SLACK_SIG_HEADER: sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _teams_body(token: str, *, user: str = "29:bob") -> bytes:
    return json.dumps({"from": {"id": user}, "value": {"token": token}}).encode()


# --------------------------------------------------------------------------- #
# signed action tokens — round-trip + tamper detection
# --------------------------------------------------------------------------- #
def test_sign_and_parse_token_roundtrip() -> None:
    token = interactive.sign_token(CHATOPS_SECRET, action_id=42, decision="approve", nonce="n1")

    assert interactive.parse_token(CHATOPS_SECRET, token) == (42, "approve", "n1")


def test_parse_token_tampered_signature_raises() -> None:
    token = interactive.sign_token(CHATOPS_SECRET, action_id=42, decision="approve", nonce="n1")
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")

    with pytest.raises(interactive.BadSignature):
        interactive.parse_token(CHATOPS_SECRET, tampered)


def test_parse_token_swapped_decision_raises() -> None:
    """Flipping approve→reject invalidates the signature (the decision is signed)."""
    token = interactive.sign_token(CHATOPS_SECRET, action_id=42, decision="approve", nonce="n1")
    forged = token.replace("approve", "reject", 1)

    with pytest.raises(interactive.BadSignature):
        interactive.parse_token(CHATOPS_SECRET, forged)


def test_parse_token_malformed_raises() -> None:
    with pytest.raises(interactive.BadSignature):
        interactive.parse_token(CHATOPS_SECRET, "not-a-valid-token")


def test_sign_token_rejects_unknown_decision() -> None:
    with pytest.raises(ValueError, match="decision must be one of"):
        interactive.sign_token(CHATOPS_SECRET, action_id=1, decision="maybe", nonce="n1")


def _forge_token(secret: str, payload: str) -> str:
    """A validly-*signed* token over an arbitrary payload (to probe post-signature checks)."""
    return payload + "." + interactive._token_signature(secret, payload)


def test_parse_token_valid_signature_unknown_decision_raises() -> None:
    token = _forge_token(CHATOPS_SECRET, "5.frobnicate.n1")

    with pytest.raises(interactive.BadSignature, match="unknown decision"):
        interactive.parse_token(CHATOPS_SECRET, token)


def test_parse_token_valid_signature_non_integer_id_raises() -> None:
    token = _forge_token(CHATOPS_SECRET, "notint.approve.n1")

    with pytest.raises(interactive.BadSignature, match="not an integer"):
        interactive.parse_token(CHATOPS_SECRET, token)


# --------------------------------------------------------------------------- #
# transport signature verification — Slack + Teams
# --------------------------------------------------------------------------- #
def test_slack_signature_roundtrip_verifies() -> None:
    body = b"payload=%7B%7D"
    ts = str(int(time.time()))
    sig = interactive.sign_slack_request(SLACK_SECRET, timestamp=ts, body=body)

    # Does not raise.
    interactive.verify_slack_signature(
        SLACK_SECRET, timestamp=ts, body=body, signature=sig, now=int(ts), max_skew=300
    )


def test_slack_bad_signature_raises() -> None:
    body = b"payload=%7B%7D"
    ts = str(int(time.time()))

    with pytest.raises(interactive.BadSignature):
        interactive.verify_slack_signature(
            SLACK_SECRET,
            timestamp=ts,
            body=body,
            signature="v0=deadbeef",
            now=int(ts),
            max_skew=300,
        )


def test_slack_stale_timestamp_raises() -> None:
    body = b"payload=%7B%7D"
    ts = str(int(time.time()) - 10_000)  # far outside the skew window
    sig = interactive.sign_slack_request(SLACK_SECRET, timestamp=ts, body=body)

    with pytest.raises(interactive.StaleRequest):
        interactive.verify_slack_signature(
            SLACK_SECRET, timestamp=ts, body=body, signature=sig, now=int(time.time()), max_skew=300
        )


def test_slack_missing_or_empty_secret_rejects() -> None:
    body = b"payload=%7B%7D"
    ts = str(int(time.time()))

    with pytest.raises(interactive.BadSignature):
        interactive.verify_slack_signature(
            "", timestamp=ts, body=body, signature="v0=whatever", now=int(ts), max_skew=300
        )


def test_slack_non_integer_timestamp_raises() -> None:
    body = b"payload=%7B%7D"
    with pytest.raises(interactive.BadSignature):
        interactive.verify_slack_signature(
            SLACK_SECRET, timestamp="not-a-number", body=body, signature="v0=x", now=0, max_skew=300
        )


def test_teams_signature_roundtrip_verifies() -> None:
    body = _teams_body("tok")
    sig = interactive.sign_teams_request(TEAMS_SECRET_B64, body=body)

    interactive.verify_teams_signature(TEAMS_SECRET_B64, body=body, signature=sig)


def test_teams_bad_signature_raises() -> None:
    body = _teams_body("tok")

    with pytest.raises(interactive.BadSignature):
        interactive.verify_teams_signature(TEAMS_SECRET_B64, body=body, signature="HMAC nope")


def test_teams_missing_secret_rejects() -> None:
    body = _teams_body("tok")

    with pytest.raises(interactive.BadSignature):
        interactive.verify_teams_signature("", body=body, signature="HMAC anything")


def test_teams_signature_matches_reference_hmac() -> None:
    """The Teams signature is a base64 HMAC-SHA256 of the raw body under the b64 secret."""
    body = _teams_body("tok")
    expected = base64.b64encode(
        hmac.new(base64.b64decode(TEAMS_SECRET_B64), body, hashlib.sha256).digest()
    ).decode()

    assert interactive.sign_teams_request(TEAMS_SECRET_B64, body=body) == f"HMAC {expected}"


# --------------------------------------------------------------------------- #
# actionable message builders — carry signed approve/reject ids
# --------------------------------------------------------------------------- #
def test_build_slack_message_has_two_signed_actions() -> None:
    msg = interactive.build_slack_message(
        CHATOPS_SECRET, action_id=7, title="Approve stop vm-1", summary="rg-app"
    )
    elements = msg["blocks"][1]["elements"]
    decoded = {interactive.parse_token(CHATOPS_SECRET, e["value"])[1] for e in elements}

    assert decoded == {"approve", "reject"}
    assert all(interactive.parse_token(CHATOPS_SECRET, e["value"])[0] == 7 for e in elements)


def test_build_teams_message_has_two_signed_actions() -> None:
    msg = interactive.build_teams_message(
        CHATOPS_SECRET, action_id=7, title="Approve stop vm-1", summary="rg-app"
    )
    actions = msg["attachments"][0]["content"]["actions"]
    decoded = {interactive.parse_token(CHATOPS_SECRET, a["data"]["token"])[1] for a in actions}

    assert decoded == {"approve", "reject"}


# --------------------------------------------------------------------------- #
# interaction parsing — extract chat user + token
# --------------------------------------------------------------------------- #
def test_parse_slack_interaction_extracts_user_and_token() -> None:
    body = _slack_body("the-token", user="U-99", response_url="https://hooks.slack.test/x")

    user, token, response_target = interactive.parse_slack_interaction(body)

    assert user == "U-99"
    assert token == "the-token"
    assert response_target == "https://hooks.slack.test/x"


def test_parse_slack_interaction_malformed_raises() -> None:
    with pytest.raises(interactive.MalformedInteraction):
        interactive.parse_slack_interaction(b"payload=not-json")


def test_parse_teams_interaction_extracts_user_and_token() -> None:
    user, token, _target = interactive.parse_teams_interaction(_teams_body("tk", user="29:x"))

    assert user == "29:x"
    assert token == "tk"


def test_parse_teams_interaction_malformed_raises() -> None:
    with pytest.raises(interactive.MalformedInteraction):
        interactive.parse_teams_interaction(b"{ not json")


# --------------------------------------------------------------------------- #
# actor resolution — chat user id → RBAC principal
# --------------------------------------------------------------------------- #
def test_resolve_chat_principal_from_map(monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})

    assert interactive.resolve_chat_principal("U-alice", settings) == "alice@corp.com"


def test_resolve_unmapped_chat_user_returns_none(monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})

    assert interactive.resolve_chat_principal("U-stranger", settings) is None


def test_resolve_invalid_json_map_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("CHATOPS_PRINCIPAL_MAP", "{not json}")
    get_settings.cache_clear()

    assert interactive.resolve_chat_principal("U-alice", get_settings()) is None


# --------------------------------------------------------------------------- #
# outbound — a pending approval sends an actionable message (NAMED)
# --------------------------------------------------------------------------- #
def test_pending_approval_sends_actionable_message(db, monkeypatch) -> None:
    settings = _configure(monkeypatch)
    aid = _seed_pending_action()
    spy = _SpyTransport()

    with session_scope() as s:
        interactive.send_pending_approval(
            s,
            action_id=aid,
            channel="slack",
            target="https://hooks.slack.test/x",
            transport=spy,
            settings=settings,
        )

    assert len(spy.calls) == 1
    blocks = spy.calls[0]["config"]["extra"]["blocks"]
    elements = blocks[1]["elements"]
    decisions = {interactive.parse_token(CHATOPS_SECRET, e["value"])[1] for e in elements}
    assert decisions == {"approve", "reject"}


def test_pending_approval_message_renders_action_text(db, monkeypatch) -> None:
    """The dispatched message text is rendered from the approval template context."""
    from cloudwarden.notify import service

    settings = _configure(monkeypatch)
    aid = _seed_pending_action()
    spy = _SpyTransport()

    with session_scope() as s:
        interactive.send_pending_approval(
            s, action_id=aid, channel="slack", target="x", transport=spy, settings=settings
        )

    ctx = service.build_approval_context(action_id=aid, action_type="stop", resource_id=RID)
    assert ctx["action_id"] == aid
    assert str(aid) in spy.calls[0]["body"]


def test_send_pending_approval_unknown_action_raises(db, monkeypatch) -> None:
    settings = _configure(monkeypatch)
    with session_scope() as s, pytest.raises(approval.NotFound):
        interactive.send_pending_approval(
            s,
            action_id=10_000_000,
            channel="slack",
            target="x",
            transport=_SpyTransport(),
            settings=settings,
        )


def test_send_pending_approval_teams_builds_card(db, monkeypatch) -> None:
    settings = _configure(monkeypatch)
    aid = _seed_pending_action()
    spy = _SpyTransport()

    with session_scope() as s:
        interactive.send_pending_approval(
            s, action_id=aid, channel="teams", target="x", transport=spy, settings=settings
        )

    actions = spy.calls[0]["config"]["extra"]["attachments"][0]["content"]["actions"]
    decoded = {interactive.parse_token(CHATOPS_SECRET, a["data"]["token"])[1] for a in actions}
    assert decoded == {"approve", "reject"}


# --------------------------------------------------------------------------- #
# inbound apply — valid signed approve/reject through the existing workflow (NAMED)
# --------------------------------------------------------------------------- #
def test_valid_signed_approve_applies_decision(db, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _slack_body(token)
    updater = _SpyUpdater()

    with session_scope() as s:
        result = interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=updater,
        )

    assert result["status"] != "pending"
    with session_scope() as s:
        row = s.get(schema.RemediationAction, aid)
        assert row.status != "pending"
        assert row.actor == "alice@corp.com"
        assert row.decided_via == "slack"


def test_valid_signed_reject_applies_decision(db, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="reject", nonce="n1")
    body = _slack_body(token)

    with session_scope() as s:
        result = interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=_SpyUpdater(),
        )

    assert result["status"] == "rejected"
    with session_scope() as s:
        assert s.get(schema.RemediationAction, aid).status == "rejected"


def test_teams_valid_signed_approve_applies_decision(db, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"29:bob": "bob@corp.com"})
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _teams_body(token)
    headers = {"Authorization": interactive.sign_teams_request(TEAMS_SECRET_B64, body=body)}

    with session_scope() as s:
        interactive.handle_teams_interaction(
            raw_body=body, headers=headers, settings=settings, session=s, updater=_SpyUpdater()
        )

    with session_scope() as s:
        row = s.get(schema.RemediationAction, aid)
        assert row.actor == "bob@corp.com"
        assert row.decided_via == "teams"


# --------------------------------------------------------------------------- #
# inbound — negative / security cases (NAMED)
# --------------------------------------------------------------------------- #
def test_invalid_signature_rejected(db, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _slack_body(token)
    headers = {
        interactive.SLACK_TS_HEADER: str(int(time.time())),
        interactive.SLACK_SIG_HEADER: "v0=deadbeef",  # forged
    }

    with session_scope() as s, pytest.raises(HTTPException) as exc:
        interactive.handle_slack_interaction(
            raw_body=body, headers=headers, settings=settings, session=s, updater=_SpyUpdater()
        )
    assert exc.value.status_code == 401


def test_replayed_interaction_rejected(db, monkeypatch) -> None:
    """The same signed approve applied twice: the second is rejected (already decided)."""
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _slack_body(token)

    with session_scope() as s:
        interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=_SpyUpdater(),
        )

    with session_scope() as s, pytest.raises(HTTPException) as exc:
        interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=_SpyUpdater(),
        )
    assert exc.value.status_code == 409


def test_unauthorized_chat_user_forbidden(db, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _slack_body(token, user="U-stranger")  # not in the principal map

    with session_scope() as s, pytest.raises(HTTPException) as exc:
        interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=_SpyUpdater(),
        )
    assert exc.value.status_code == 403


def test_rbac_principal_without_permission_forbidden(db, monkeypatch) -> None:
    """A mapped principal still needs ``remediation:approve`` when RBAC is enabled."""
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"}, rbac=True)
    with session_scope() as s:
        from cloudwarden.authz import rbac as rbac_mod

        rbac_mod.seed_default_roles(s)
        repo.assign_role(s, principal="alice@corp.com", role_name="viewer")  # read-only
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _slack_body(token)

    with session_scope() as s, pytest.raises(HTTPException) as exc:
        interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=_SpyUpdater(),
        )
    assert exc.value.status_code == 403
    with session_scope() as s:
        assert s.get(schema.RemediationAction, aid).status == "pending"  # never decided


def test_unknown_action_id_not_found(db, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    token = interactive.sign_token(
        CHATOPS_SECRET, action_id=10_000_000, decision="approve", nonce="n1"
    )
    body = _slack_body(token)

    with session_scope() as s, pytest.raises(HTTPException) as exc:
        interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=_SpyUpdater(),
        )
    assert exc.value.status_code == 404


def test_tampered_action_token_rejected(db, monkeypatch) -> None:
    """A correctly transport-signed request whose *token* is tampered → 401 (our sig)."""
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    aid = _seed_pending_action()
    good = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    tampered = good[:-1] + ("0" if good[-1] != "0" else "1")
    body = _slack_body(tampered)

    with session_scope() as s, pytest.raises(HTTPException) as exc:
        interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=_SpyUpdater(),
        )
    assert exc.value.status_code == 401


def test_teams_bad_transport_signature_401(db, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"29:bob": "bob@corp.com"})
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _teams_body(token)

    with session_scope() as s, pytest.raises(HTTPException) as exc:
        interactive.handle_teams_interaction(
            raw_body=body,
            headers={"Authorization": "HMAC forged"},
            settings=settings,
            session=s,
            updater=_SpyUpdater(),
        )
    assert exc.value.status_code == 401


def test_teams_malformed_payload_400(db, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"29:bob": "bob@corp.com"})
    body = b"{ not json"
    headers = {"Authorization": interactive.sign_teams_request(TEAMS_SECRET_B64, body=body)}

    with session_scope() as s, pytest.raises(HTTPException) as exc:
        interactive.handle_teams_interaction(
            raw_body=body, headers=headers, settings=settings, session=s, updater=_SpyUpdater()
        )
    assert exc.value.status_code == 400


def test_malformed_slack_payload_400(db, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    body = b"payload=not-json"

    with session_scope() as s, pytest.raises(HTTPException) as exc:
        interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=_SpyUpdater(),
        )
    assert exc.value.status_code == 400


# --------------------------------------------------------------------------- #
# audit + message update (NAMED)
# --------------------------------------------------------------------------- #
def test_decision_audited_and_message_updated(db, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _slack_body(token, response_url="https://hooks.slack.test/resp")
    updater = _SpyUpdater()

    with session_scope() as s:
        interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=updater,
        )

    # source message updated with the outcome
    assert len(updater.calls) == 1
    assert updater.calls[0][0] == "https://hooks.slack.test/resp"
    assert "alice@corp.com" in updater.calls[0][1]

    # decision audited with actor + channel
    with session_scope() as s:
        rows = repo.list_audit_logs(s, target_type="remediation_action", target_id=str(aid))
    assert rows
    entry = rows[0]
    assert entry["actor"] == "alice@corp.com"
    assert entry["action"] == "chatops:approve"


# --------------------------------------------------------------------------- #
# HTTP endpoints — end-to-end through the FastAPI app (updater monkeypatched)
# --------------------------------------------------------------------------- #
def test_api_slack_valid_approve_200(db, client, monkeypatch) -> None:
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    updates: list = []
    monkeypatch.setattr(interactive, "_live_message_update", lambda t, x: updates.append((t, x)))
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _slack_body(token)

    resp = client.post("/api/chatops/slack", content=body, headers=_slack_headers(body))

    assert resp.status_code == 200
    assert resp.json()["status"] != "pending"
    assert updates  # message updated
    assert settings is not None


def test_api_slack_bad_signature_401(db, client, monkeypatch) -> None:
    _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _slack_body(token)
    headers = {
        interactive.SLACK_TS_HEADER: str(int(time.time())),
        interactive.SLACK_SIG_HEADER: "v0=deadbeef",
    }

    assert client.post("/api/chatops/slack", content=body, headers=headers).status_code == 401


def test_api_slack_unknown_action_404(db, client, monkeypatch) -> None:
    _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    monkeypatch.setattr(interactive, "_live_message_update", lambda t, x: None)
    token = interactive.sign_token(
        CHATOPS_SECRET, action_id=10_000_000, decision="approve", nonce="n1"
    )
    body = _slack_body(token)

    resp = client.post("/api/chatops/slack", content=body, headers=_slack_headers(body))
    assert resp.status_code == 404


def test_api_teams_valid_approve_200(db, client, monkeypatch) -> None:
    _configure(monkeypatch, principals={"29:bob": "bob@corp.com"})
    monkeypatch.setattr(interactive, "_live_message_update", lambda t, x: None)
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _teams_body(token)
    headers = {"Authorization": interactive.sign_teams_request(TEAMS_SECRET_B64, body=body)}

    resp = client.post("/api/chatops/teams", content=body, headers=headers)

    assert resp.status_code == 200
    with session_scope() as s:
        assert s.get(schema.RemediationAction, aid).decided_via == "teams"


def test_decided_via_surfaces_in_remediation_list(db, monkeypatch) -> None:
    """The remediation audit list carries ``decided_via`` for the UI decision source."""
    settings = _configure(monkeypatch, principals={"U-alice": "alice@corp.com"})
    aid = _seed_pending_action()
    token = interactive.sign_token(CHATOPS_SECRET, action_id=aid, decision="approve", nonce="n1")
    body = _slack_body(token)

    with session_scope() as s:
        interactive.handle_slack_interaction(
            raw_body=body,
            headers=_slack_headers(body),
            settings=settings,
            session=s,
            updater=_SpyUpdater(),
        )

    with session_scope() as s:
        rows = repo.list_remediation_actions(s)
    assert any(r["id"] == aid and r["decided_via"] == "slack" for r in rows)
