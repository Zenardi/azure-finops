"""M14.14 — identity/IAM risk & exposure posture. Tests written FIRST (TDD).

CloudWarden governs resources but has no identity posture. This layer collects
principals + role assignments + credential/MFA/exposure signals per provider
(Entra/Azure RBAC, AWS IAM, GCP IAM), applies evidence-backed risk rules, and emits
a normalized 0-100 risk score — advisory only, never mutating identities.

Layers under test:
* **Rules + scoring** (pure, no DB): hand-built principals -> findings. Positive
  (over-privilege / unused / stale-key / no-MFA / public-exposure) AND negative
  (least-privilege, unknown-signal, disabled/fresh credential) cases. Every finding
  carries evidence + severity; the score is a pure, reproducible function of findings.
* **Collectors** (injected/mock clients): each provider's identity fixture normalizes
  to the same ``IdentityPrincipal`` shape; the live path is out of mock scope.
* **Orchestrator + repository + API** (``db`` fixture): collect identity per account,
  persist findings as an idempotent snapshot, read findings + a score reproducible
  from the persisted findings, RBAC-guarded.
"""

from __future__ import annotations

from cloudwarden.analysis import iam_risk
from cloudwarden.models import Credential, IdentityFinding, IdentityPrincipal, RoleAssignment

# --------------------------------------------------------------------------- #
# Builders — hand-built principals keep the rule tests isolated & repeatable.
# --------------------------------------------------------------------------- #


def _assignment(
    role: str = "ReadOnlyAccess",
    scope: str = "/scope/resource-a",
    scope_level: str = "resource",
    permissions: list[str] | None = None,
) -> RoleAssignment:
    return RoleAssignment(
        role=role, scope=scope, scope_level=scope_level, permissions=permissions or []
    )


def _principal(
    pid: str = "p1",
    *,
    principal_type: str = "user",
    assignments: list[RoleAssignment] | None = None,
    credentials: list[Credential] | None = None,
    mfa_enabled: bool | None = None,
    last_activity_days: int | None = None,
    public: bool = False,
    exposure_detail: str = "",
    provider: str = "aws",
    account_id: str = "123456789012",
    display_name: str = "",
) -> IdentityPrincipal:
    return IdentityPrincipal(
        principal_id=pid,
        display_name=display_name,
        provider=provider,
        account_id=account_id,
        principal_type=principal_type,
        assignments=assignments or [],
        credentials=credentials or [],
        mfa_enabled=mfa_enabled,
        last_activity_days=last_activity_days,
        public=public,
        exposure_detail=exposure_detail,
    )


def _categories(findings: list[IdentityFinding]) -> set[str]:
    return {f.category for f in findings}


# --------------------------------------------------------------------------- #
# The seven named TDD cases (issue #147)
# --------------------------------------------------------------------------- #
def test_wildcard_broad_scope_flagged_overprivileged() -> None:
    # Arrange — a wildcard ("*") grant at the organization root.
    p = _principal(
        assignments=[
            _assignment(
                role="AdministratorAccess",
                scope="org-root",
                scope_level="organization",
                permissions=["*"],
            )
        ],
        last_activity_days=1,
    )
    # Act
    findings = iam_risk.analyze_principal(p)
    # Assert — one over-privilege finding, evidence-backed, weight matches severity.
    over = [f for f in findings if f.category == "over_privilege"]
    assert len(over) == 1
    f = over[0]
    assert f.severity == "critical"  # wildcard at org scope is the worst case
    assert f.evidence["wildcard"] is True
    assert f.evidence["scope_level"] == "organization"
    assert f.weight == iam_risk.SEVERITY_WEIGHTS["critical"]
    assert f.rationale  # the "show your basis" principle


def test_unused_principal_flagged() -> None:
    # Arrange — standing access but no activity for longer than the threshold.
    p = _principal(assignments=[_assignment()], last_activity_days=120)
    # Act
    findings = iam_risk.analyze_principal(p)
    # Assert
    unused = [f for f in findings if f.category == "unused_principal"]
    assert len(unused) == 1
    assert unused[0].evidence["last_activity_days"] == 120
    assert unused[0].evidence["threshold_days"] == 90


def test_stale_key_flagged() -> None:
    # Arrange — an enabled access key well past the rotation threshold.
    p = _principal(
        mfa_enabled=True,
        assignments=[_assignment()],
        credentials=[Credential(kind="access_key", enabled=True, age_days=200)],
        last_activity_days=5,
    )
    # Act
    findings = iam_risk.analyze_principal(p)
    # Assert
    stale = [f for f in findings if f.category == "stale_credential"]
    assert len(stale) == 1
    assert stale[0].evidence["age_days"] == 200
    assert stale[0].evidence["kind"] == "access_key"
    assert stale[0].severity == "high"  # >= 2x threshold -> high


def test_missing_mfa_flagged() -> None:
    # Arrange — a human user with a password but MFA explicitly disabled.
    p = _principal(
        mfa_enabled=False,
        assignments=[_assignment()],
        credentials=[Credential(kind="password", enabled=True, age_days=10)],
        last_activity_days=1,
    )
    # Act
    findings = iam_risk.analyze_principal(p)
    # Assert
    mfa = [f for f in findings if f.category == "missing_mfa"]
    assert len(mfa) == 1
    assert mfa[0].severity == "high"
    assert mfa[0].evidence["mfa_enabled"] is False


def test_public_exposure_flagged() -> None:
    # Arrange — an anonymous / all-users principal (public assume/access).
    p = _principal(
        principal_type="role",
        public=True,
        exposure_detail="trust policy allows Principal:* (anonymous assume)",
        assignments=[_assignment(role="AmazonS3ReadOnlyAccess")],
    )
    # Act
    findings = iam_risk.analyze_principal(p)
    # Assert
    exp = [f for f in findings if f.category == "public_exposure"]
    assert len(exp) == 1
    assert exp[0].severity == "critical"
    assert "Principal:*" in exp[0].evidence["detail"]


def test_least_privilege_active_no_finding() -> None:
    # Arrange — scoped Reader, MFA on, fresh credential, recently active, not public.
    p = _principal(
        mfa_enabled=True,
        assignments=[
            _assignment(role="ReadOnlyAccess", scope_level="resource", permissions=["s3:GetObject"])
        ],
        credentials=[Credential(kind="password", enabled=True, age_days=5)],
        last_activity_days=2,
        public=False,
    )
    # Act / Assert — a well-behaved principal produces NO findings (no false positives).
    assert iam_risk.analyze_principal(p) == []


def test_risk_score_reproducible_from_findings() -> None:
    # Arrange — a mix of principals across risk archetypes + a clean one.
    principals = [
        _principal(
            pid="over",
            assignments=[_assignment(role="Owner", scope_level="organization", permissions=["*"])],
            last_activity_days=1,
        ),
        _principal(pid="unused", assignments=[_assignment()], last_activity_days=200),
        _principal(
            pid="clean",
            mfa_enabled=True,
            assignments=[_assignment()],
            credentials=[Credential(kind="password", enabled=True, age_days=3)],
            last_activity_days=1,
        ),
    ]
    # Act
    score = iam_risk.score_account(principals, account_id="123456789012", provider="aws")
    # Assert — score is normalized and reproducible from the returned findings alone.
    assert 0 < score.score <= 100
    assert score.finding_count == len(score.findings)
    assert iam_risk.compute_score(score.findings) == score.score
    # Deterministic across a second run.
    again = iam_risk.score_account(principals, account_id="123456789012", provider="aws")
    assert again.score == score.score


# --------------------------------------------------------------------------- #
# Negative / edge cases — no false positives, signal-gated on absence of data.
# --------------------------------------------------------------------------- #
def test_scoped_wildcard_not_overprivileged() -> None:
    # A wildcard at a NARROW (resource) scope is not broad over-privilege.
    p = _principal(
        assignments=[_assignment(permissions=["*"], scope_level="resource")], last_activity_days=1
    )
    assert not any(f.category == "over_privilege" for f in iam_risk.analyze_principal(p))


def test_admin_role_broad_scope_without_wildcard_flagged_high() -> None:
    # An admin role at account scope (no wildcard, not org) -> high, not critical.
    p = _principal(
        assignments=[
            _assignment(
                role="Owner", scope_level="account", permissions=["Microsoft.Authorization/*"]
            )
        ],
        last_activity_days=1,
    )
    over = [f for f in iam_risk.analyze_principal(p) if f.category == "over_privilege"]
    assert over and over[0].severity == "high"
    assert over[0].evidence["wildcard"] is False


def test_narrow_role_broad_scope_not_flagged() -> None:
    # A non-admin role at a broad scope is NOT over-privilege (least-privilege granted broadly).
    p = _principal(
        assignments=[_assignment(role="Reader", scope_level="account", permissions=["*/read"])],
        last_activity_days=1,
    )
    assert not any(f.category == "over_privilege" for f in iam_risk.analyze_principal(p))


def test_unknown_activity_not_flagged_unused() -> None:
    # last_activity_days is None -> unknown, never flagged on absence of data.
    p = _principal(assignments=[_assignment()], last_activity_days=None)
    assert not any(f.category == "unused_principal" for f in iam_risk.analyze_principal(p))


def test_unused_requires_standing_access() -> None:
    # A principal with no assignments has no standing access -> not an "unused" risk.
    p = _principal(assignments=[], last_activity_days=300)
    assert not any(f.category == "unused_principal" for f in iam_risk.analyze_principal(p))


def test_recent_activity_not_flagged_unused() -> None:
    p = _principal(assignments=[_assignment()], last_activity_days=10)
    assert not any(f.category == "unused_principal" for f in iam_risk.analyze_principal(p))


def test_disabled_stale_credential_not_flagged() -> None:
    p = _principal(
        mfa_enabled=True,
        credentials=[Credential(kind="access_key", enabled=False, age_days=300)],
    )
    assert not any(f.category == "stale_credential" for f in iam_risk.analyze_principal(p))


def test_fresh_credential_not_flagged() -> None:
    p = _principal(
        mfa_enabled=True,
        credentials=[Credential(kind="access_key", enabled=True, age_days=10)],
    )
    assert not any(f.category == "stale_credential" for f in iam_risk.analyze_principal(p))


def test_stale_credential_unknown_age_not_flagged() -> None:
    p = _principal(
        mfa_enabled=True,
        credentials=[Credential(kind="access_key", enabled=True, age_days=None)],
    )
    assert not any(f.category == "stale_credential" for f in iam_risk.analyze_principal(p))


def test_stale_credential_medium_below_2x_threshold() -> None:
    p = _principal(
        mfa_enabled=True,
        credentials=[Credential(kind="access_key", enabled=True, age_days=100)],
    )
    stale = [f for f in iam_risk.analyze_principal(p) if f.category == "stale_credential"]
    assert stale and stale[0].severity == "medium"


def test_mfa_not_applicable_for_service_principal() -> None:
    # Non-human principals have mfa_enabled None (N/A) -> never an MFA finding.
    p = _principal(
        principal_type="service_principal", mfa_enabled=None, assignments=[_assignment()]
    )
    assert not any(f.category == "missing_mfa" for f in iam_risk.analyze_principal(p))


def test_mfa_enabled_user_not_flagged() -> None:
    p = _principal(
        mfa_enabled=True,
        credentials=[Credential(kind="password", enabled=True, age_days=5)],
    )
    assert not any(f.category == "missing_mfa" for f in iam_risk.analyze_principal(p))


def test_multiple_stale_credentials_each_flagged() -> None:
    # Two stale keys -> two distinct findings (kind distinguishes them).
    p = _principal(
        mfa_enabled=True,
        credentials=[
            Credential(kind="access_key", enabled=True, age_days=200),
            Credential(kind="ssh_key", enabled=True, age_days=400),
        ],
    )
    stale = [f for f in iam_risk.analyze_principal(p) if f.category == "stale_credential"]
    assert len(stale) == 2
    assert {f.evidence["kind"] for f in stale} == {"access_key", "ssh_key"}


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def test_compute_score_caps_at_100() -> None:
    findings = [
        IdentityFinding(
            principal_id=f"p{i}",
            category="public_exposure",
            severity="critical",
            weight=iam_risk.SEVERITY_WEIGHTS["critical"],
        )
        for i in range(5)
    ]
    assert iam_risk.compute_score(findings) == 100


def test_compute_score_zero_without_findings() -> None:
    assert iam_risk.compute_score([]) == 0


def test_summarize_counts_by_severity() -> None:
    findings = [
        IdentityFinding(principal_id="a", category="missing_mfa", severity="high", weight=30),
        IdentityFinding(
            principal_id="b", category="unused_principal", severity="medium", weight=15
        ),
        IdentityFinding(
            principal_id="c", category="public_exposure", severity="critical", weight=50
        ),
    ]
    summary = iam_risk.summarize(findings, account_id="a", provider="aws")
    assert summary.by_severity == {"critical": 1, "high": 1, "medium": 1, "low": 0}
    assert summary.finding_count == 3
    assert summary.score == iam_risk.compute_score(findings)


def test_findings_ranked_by_blast_radius() -> None:
    # Org-scope over-privilege (critical, blast 8) outranks a resource-scope MFA gap.
    org = _principal(
        pid="org",
        assignments=[_assignment(role="Owner", scope_level="organization", permissions=["*"])],
        last_activity_days=1,
    )
    small = _principal(
        pid="small",
        mfa_enabled=False,
        assignments=[_assignment(role="Reader", scope_level="resource")],
        credentials=[Credential(kind="password", enabled=True, age_days=5)],
        last_activity_days=1,
    )
    ranked = iam_risk.analyze_principals([small, org])
    assert ranked[0].principal_id == "org"
    assert ranked[0].blast_radius >= ranked[-1].blast_radius


# --------------------------------------------------------------------------- #
# Collectors — normalize each provider's identity fixture (mock/injected client)
# --------------------------------------------------------------------------- #
_EXPECTED_CATEGORIES = {
    "over_privilege",
    "unused_principal",
    "stale_credential",
    "missing_mfa",
    "public_exposure",
}


def test_collect_identity_aws_normalizes() -> None:
    from cloudwarden.providers import aws_iam

    principals = aws_iam.collect_identity()
    assert principals and all(isinstance(p, IdentityPrincipal) for p in principals)
    assert all(p.provider == "aws" for p in principals)
    findings = iam_risk.analyze_principals(principals)
    assert _categories(findings) == _EXPECTED_CATEGORIES


def test_collect_identity_azure_normalizes() -> None:
    from cloudwarden.azure import identity

    principals = identity.collect_identity()
    assert principals and all(p.provider == "azure" for p in principals)
    findings = iam_risk.analyze_principals(principals)
    assert _categories(findings) == _EXPECTED_CATEGORIES


def test_collect_identity_gcp_normalizes() -> None:
    from cloudwarden.providers import gcp_iam

    principals = gcp_iam.collect_identity()
    assert principals and all(p.provider == "gcp" for p in principals)
    findings = iam_risk.analyze_principals(principals)
    assert _categories(findings) == _EXPECTED_CATEGORIES


def test_fixture_clean_principal_has_no_findings() -> None:
    from cloudwarden.providers import aws_iam

    principals = aws_iam.collect_identity()
    clean = [p for p in principals if p.display_name == "least-privilege"]
    assert clean and iam_risk.analyze_principal(clean[0]) == []


def test_collect_identity_accepts_injected_client() -> None:
    from cloudwarden.providers import aws_iam

    class _Fake:
        def list_principals(self) -> list[dict]:
            return [{"principal_id": "x", "principal_type": "user"}]

    principals = aws_iam.collect_identity(client=_Fake())
    assert len(principals) == 1 and principals[0].principal_id == "x"


def test_from_raw_retargets_account_placeholder() -> None:
    raw = {
        "principal_id": "arn:aws:iam::123456789012:user/x",
        "assignments": [
            {"role": "R", "scope": "arn:aws:iam::123456789012:root", "scope_level": "account"}
        ],
    }
    p = IdentityPrincipal.from_raw(raw, provider="aws", account_id="999900001111")
    assert "999900001111" in p.principal_id
    assert "999900001111" in p.assignments[0].scope
    assert p.account_id == "999900001111"


def test_from_raw_defaults_account_to_row_when_unset() -> None:
    raw = {"principal_id": "arn:aws:iam::123456789012:user/x", "account_id": "123456789012"}
    p = IdentityPrincipal.from_raw(raw, provider="aws", account_id=None)
    assert p.account_id == "123456789012"
    assert p.principal_id == raw["principal_id"]  # no retarget without a real account


# --------------------------------------------------------------------------- #
# Provider seam — collect_identity dispatches per registered cloud
# --------------------------------------------------------------------------- #
def test_provider_collect_identity_dispatch() -> None:
    from cloudwarden.providers import registry

    for name in ("aws", "azure", "gcp"):
        principals = registry.get(name).collect_identity()
        assert principals and all(p.provider == name for p in principals)


# --------------------------------------------------------------------------- #
# Orchestrator — collect identity per account; snapshot + persist
# --------------------------------------------------------------------------- #
def test_identity_snapshot_scores_are_reproducible() -> None:
    from cloudwarden.orchestrator import identity_snapshot

    snap = identity_snapshot(["aws"])
    assert snap["findings"] and snap["scores"]
    acct = snap["scores"][0]
    assert 0 < acct["score"] <= 100
    account_findings = [f for f in snap["findings"] if f["account_id"] == acct["account_id"]]
    assert acct["score"] == min(100, sum(f["weight"] for f in account_findings))


def test_run_identity_persists_findings(db) -> None:
    from cloudwarden.orchestrator import run_identity
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    counts = run_identity(["aws"])
    assert counts["identity_findings"] > 0
    with session_scope() as s:
        rows = repo.list_identity_findings(s, provider="aws")
    assert rows and all("evidence" in r and r["severity"] for r in rows)


def test_run_identity_is_idempotent_snapshot(db) -> None:
    from cloudwarden.orchestrator import run_identity
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    run_identity(["aws"])
    with session_scope() as s:
        first = len(repo.list_identity_findings(s, provider="aws"))
    run_identity(["aws"])
    with session_scope() as s:
        second = len(repo.list_identity_findings(s, provider="aws"))
    assert first == second and first > 0  # re-scan replaces, never duplicates


# --------------------------------------------------------------------------- #
# Repository — snapshot writer is idempotent; reads are filterable & ranked
# --------------------------------------------------------------------------- #
def test_replace_identity_findings_idempotent(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    findings = [
        IdentityFinding(
            principal_id="p1",
            account_id="acct-1",
            provider="aws",
            category="missing_mfa",
            severity="high",
            weight=30,
            title="MFA disabled for p1",
        )
    ]
    with session_scope() as s:
        n1 = repo.replace_identity_findings(
            s, provider="aws", account_id="acct-1", findings=findings
        )
        n2 = repo.replace_identity_findings(
            s, provider="aws", account_id="acct-1", findings=findings
        )
        rows = repo.list_identity_findings(s, provider="aws")
    assert n1 == 1 and n2 == 1 and len(rows) == 1


def test_list_identity_findings_filters(db) -> None:
    from cloudwarden.storage import repository as repo
    from cloudwarden.storage.db import session_scope

    findings = [
        IdentityFinding(
            principal_id="crit",
            account_id="acct-1",
            provider="aws",
            category="public_exposure",
            severity="critical",
            weight=50,
            blast_radius=8,
            title="public",
        ),
        IdentityFinding(
            principal_id="med",
            account_id="acct-1",
            provider="aws",
            category="unused_principal",
            severity="medium",
            weight=15,
            blast_radius=1,
            title="unused",
        ),
    ]
    with session_scope() as s:
        repo.replace_identity_findings(s, provider="aws", account_id="acct-1", findings=findings)
        crit = repo.list_identity_findings(s, provider="aws", severity="critical")
        by_cat = repo.list_identity_findings(s, category="unused_principal")
        by_account = repo.list_identity_findings(s, account_id="acct-1")
        none_account = repo.list_identity_findings(s, account_id="other")
        ranked = repo.list_identity_findings(s)
    assert len(crit) == 1 and crit[0]["principal_id"] == "crit"
    assert len(by_cat) == 1 and by_cat[0]["principal_id"] == "med"
    assert len(by_account) == 2 and none_account == []
    # Highest weight x blast radius ranks first.
    assert ranked[0]["principal_id"] == "crit"


def test_collect_identity_isolates_provider_failure() -> None:
    # A single failing cloud must never sink the fan-out (per-account isolation).
    from cloudwarden.azure.context import AccountContext
    from cloudwarden.orchestrator import collect_identity

    bogus = AccountContext(account_id="x", provider="bogus")
    assert collect_identity([bogus]) == []


# --------------------------------------------------------------------------- #
# API — GET /api/iam/findings, GET /api/iam/score, POST /api/iam/collect
# --------------------------------------------------------------------------- #
def _client():
    from fastapi.testclient import TestClient

    from cloudwarden.api.main import app

    return TestClient(app)


def test_api_iam_findings_empty_before_collect(db) -> None:
    resp = _client().get("/api/iam/findings")
    assert resp.status_code == 200
    assert resp.json()["findings"] == []


def test_api_iam_collect_then_findings(db) -> None:
    client = _client()
    collected = client.post("/api/iam/collect", params={"provider": "aws"})
    assert collected.status_code == 200
    assert collected.json()["identity_findings"] > 0

    listed = client.get("/api/iam/findings", params={"provider": "aws"})
    assert listed.status_code == 200
    findings = listed.json()["findings"]
    assert findings and all(f["severity"] for f in findings)

    critical = client.get("/api/iam/findings", params={"severity": "critical"})
    assert all(f["severity"] == "critical" for f in critical.json()["findings"])


def test_api_iam_score_reproducible_from_findings(db) -> None:
    client = _client()
    client.post("/api/iam/collect", params={"provider": "aws"})

    score = client.get("/api/iam/score").json()
    findings = client.get("/api/iam/findings").json()["findings"]
    assert score["overall"] == min(100, sum(f["weight"] for f in findings))
    assert score["accounts"] and all(0 <= a["score"] <= 100 for a in score["accounts"])


def test_api_iam_collect_invalid_provider(db) -> None:
    resp = _client().post("/api/iam/collect", params={"provider": "nope"})
    assert resp.status_code == 400
