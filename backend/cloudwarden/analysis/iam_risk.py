"""Identity / IAM risk rules, scoring and evidence (M14.14).

Pure over injected :class:`~cloudwarden.models.IdentityPrincipal` objects — the
provider collectors normalize every cloud (Entra/Azure RBAC, AWS IAM, GCP IAM) to
that one shape, so these rules are cloud-agnostic and fully unit-testable offline.

Five rules, each emitting an **evidence-backed** finding with a severity:

* **over_privilege** — a *privileged* grant (wildcard ``"*"`` or an admin role) at a
  *broad* scope (organization / account). Wildcard-at-org is ``critical``, else ``high``.
* **unused_principal** — a principal with standing access whose last activity is older
  than the threshold. Signal-gated: unknown activity (``None``) is never flagged.
* **stale_credential** — an *enabled* credential older than the rotation threshold
  (``high`` past 2x the threshold, else ``medium``). Unknown age / disabled → not flagged.
* **missing_mfa** — a human ``user`` with MFA explicitly disabled (``mfa_enabled is False``);
  ``None`` (non-human / N/A) is never flagged.
* **public_exposure** — an anonymous / all-users principal (``public``): ``critical``.

Everything is **advisory** — a finding never carries a remediation action. The 0-100
account score is ``min(100, sum(weights))`` — a pure, reproducible function of findings.
"""

from __future__ import annotations

from ..models import IdentityFinding, IdentityPrincipal, IdentityRiskScore, RoleAssignment

# Severity -> score contribution. The account score sums these (capped at 100), so a
# single critical (50) plus a high (30) already reads as an elevated 80.
SEVERITY_WEIGHTS = {"low": 5, "medium": 15, "high": 30, "critical": 50}
_SEVERITY_ORDER = ("critical", "high", "medium", "low")

# Scope breadth -> blast-radius multiplier for ranking (broad grants rank first).
_SCOPE_BLAST = {"organization": 8, "account": 4, "resource_group": 2, "resource": 1}
# Scopes considered "broad" for the over-privilege rule.
_BROAD_SCOPES = {"organization", "account"}
# Roles that confer broad administrative power across clouds (compared case-insensitively).
_ADMIN_ROLES = {
    "owner",
    "contributor",
    "administratoraccess",
    "poweruseraccess",
    "user access administrator",
    "global administrator",
    "roles/owner",
    "roles/editor",
    "roles/iam.securityadmin",
}


def _has_wildcard(assignment: RoleAssignment) -> bool:
    return any(perm.strip() == "*" for perm in assignment.permissions)


def _is_admin_role(assignment: RoleAssignment) -> bool:
    return (assignment.role or "").strip().lower() in _ADMIN_ROLES


def _blast(scope_level: str) -> int:
    return _SCOPE_BLAST.get(scope_level, 1)


def _principal_blast(principal: IdentityPrincipal) -> int:
    """Broadest grant a principal holds — its blast radius for principal-level findings."""
    return max((_blast(a.scope_level) for a in principal.assignments), default=1)


def _finding(
    principal: IdentityPrincipal,
    *,
    category: str,
    severity: str,
    title: str,
    rationale: str,
    blast_radius: int,
    evidence: dict,
) -> IdentityFinding:
    return IdentityFinding(
        principal_id=principal.principal_id,
        principal_type=principal.principal_type,
        provider=principal.provider,
        account_id=principal.account_id,
        category=category,
        severity=severity,
        title=title,
        rationale=rationale,
        blast_radius=blast_radius,
        weight=SEVERITY_WEIGHTS[severity],
        evidence=evidence,
    )


def _rule_over_privilege(principal: IdentityPrincipal) -> list[IdentityFinding]:
    findings: list[IdentityFinding] = []
    for a in principal.assignments:
        wildcard = _has_wildcard(a)
        privileged = wildcard or _is_admin_role(a)
        if not (privileged and a.scope_level in _BROAD_SCOPES):
            continue  # least-privilege, or privilege confined to a narrow scope
        severity = "critical" if (wildcard and a.scope_level == "organization") else "high"
        findings.append(
            _finding(
                principal,
                category="over_privilege",
                severity=severity,
                title=f"{principal.principal_id} holds {a.role or 'a privileged role'} "
                f"at {a.scope_level} scope",
                rationale=(
                    f"Principal is granted {a.role or 'a role'} "
                    f"({'wildcard *' if wildcard else 'admin role'}) at {a.scope_level} "
                    f"scope {a.scope or '(broad)'}. Broad standing privilege is the "
                    f"highest-blast-radius identity risk — scope it down to least privilege."
                ),
                blast_radius=_blast(a.scope_level),
                evidence={
                    "role": a.role,
                    "scope": a.scope,
                    "scope_level": a.scope_level,
                    "wildcard": wildcard,
                    "permissions": a.permissions,
                },
            )
        )
    return findings


def _rule_unused_principal(
    principal: IdentityPrincipal, *, unused_days: int
) -> list[IdentityFinding]:
    days = principal.last_activity_days
    if not principal.assignments or days is None or days < unused_days:
        return []  # no standing access, or unknown/recent activity -> not flagged
    return [
        _finding(
            principal,
            category="unused_principal",
            severity="medium",
            title=f"{principal.principal_id} unused for {days} days but retains access",
            rationale=(
                f"Principal has standing role assignments but no recorded activity for "
                f"{days} days (>= {unused_days}). Dormant access is unnecessary attack "
                f"surface — disable or remove it if the principal is no longer needed."
            ),
            blast_radius=_principal_blast(principal),
            evidence={
                "last_activity_days": days,
                "threshold_days": unused_days,
                "assignment_count": len(principal.assignments),
            },
        )
    ]


def _rule_stale_credentials(
    principal: IdentityPrincipal, *, stale_days: int
) -> list[IdentityFinding]:
    findings: list[IdentityFinding] = []
    for c in principal.credentials:
        if not c.enabled or c.age_days is None or c.age_days < stale_days:
            continue  # disabled, unknown age, or freshly rotated -> not flagged
        severity = "high" if c.age_days >= 2 * stale_days else "medium"
        findings.append(
            _finding(
                principal,
                category="stale_credential",
                severity=severity,
                title=f"{principal.principal_id} has a stale {c.kind} ({c.age_days}d old)",
                rationale=(
                    f"An enabled {c.kind} is {c.age_days} days old (>= {stale_days}). "
                    f"Long-lived credentials widen the exposure window if leaked — rotate it."
                ),
                blast_radius=_principal_blast(principal),
                evidence={
                    "kind": c.kind,
                    "age_days": c.age_days,
                    "threshold_days": stale_days,
                    "last_used_days": c.last_used_days,
                },
            )
        )
    return findings


def _rule_missing_mfa(principal: IdentityPrincipal) -> list[IdentityFinding]:
    if principal.principal_type != "user" or principal.mfa_enabled is not False:
        return []  # non-human (N/A) or MFA enabled -> not flagged
    has_password = any(c.kind == "password" for c in principal.credentials)
    return [
        _finding(
            principal,
            category="missing_mfa",
            severity="high",
            title=f"{principal.principal_id} is a user with MFA disabled",
            rationale=(
                "Human user has MFA disabled; a phished or reused password grants "
                "unmitigated access. Require MFA for interactive sign-in."
            ),
            blast_radius=_principal_blast(principal),
            evidence={"mfa_enabled": False, "has_password": has_password},
        )
    ]


def _rule_public_exposure(principal: IdentityPrincipal) -> list[IdentityFinding]:
    if not principal.public:
        return []
    return [
        _finding(
            principal,
            category="public_exposure",
            severity="critical",
            title=f"{principal.principal_id} is publicly / anonymously exposed",
            rationale=(
                "Principal grants anonymous / all-users access "
                f"({principal.exposure_detail or 'public exposure'}). Anyone on the "
                "internet inherits its permissions — remove the public grant."
            ),
            blast_radius=max(_principal_blast(principal), 4),
            evidence={
                "detail": principal.exposure_detail,
                "roles": [a.role for a in principal.assignments],
            },
        )
    ]


def analyze_principal(principal: IdentityPrincipal, *, settings=None) -> list[IdentityFinding]:
    """Every risk finding for one principal (unranked)."""
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    unused_days = settings.iam_unused_days
    stale_days = settings.iam_stale_credential_days
    return [
        *_rule_over_privilege(principal),
        *_rule_unused_principal(principal, unused_days=unused_days),
        *_rule_stale_credentials(principal, stale_days=stale_days),
        *_rule_missing_mfa(principal),
        *_rule_public_exposure(principal),
    ]


def _rank_key(finding: IdentityFinding) -> tuple:
    # severity x blast radius, then severity, then a stable id — the investigation worklist.
    sev_rank = len(_SEVERITY_ORDER) - _SEVERITY_ORDER.index(finding.severity)
    return (-finding.weight * finding.blast_radius, -sev_rank, finding.principal_id)


def analyze_principals(
    principals: list[IdentityPrincipal], *, settings=None
) -> list[IdentityFinding]:
    """All findings across principals, ranked by severity x blast radius (worst first)."""
    findings: list[IdentityFinding] = []
    for principal in principals:
        findings.extend(analyze_principal(principal, settings=settings))
    return sorted(findings, key=_rank_key)


def compute_score(findings: list[IdentityFinding]) -> int:
    """Normalized 0-100 risk score — ``min(100, sum(weights))``. Pure & reproducible."""
    return min(100, sum(f.weight for f in findings))


def summarize(
    findings: list[IdentityFinding], *, account_id: str | None, provider: str
) -> IdentityRiskScore:
    """Roll a finding list into a reproducible :class:`IdentityRiskScore`."""
    by_severity = {sev: 0 for sev in _SEVERITY_ORDER}
    for f in findings:
        if f.severity in by_severity:
            by_severity[f.severity] += 1
    return IdentityRiskScore(
        account_id=account_id,
        provider=provider,
        score=compute_score(findings),
        finding_count=len(findings),
        by_severity=by_severity,
        findings=list(findings),
    )


def score_account(
    principals: list[IdentityPrincipal],
    *,
    account_id: str | None,
    provider: str,
    settings=None,
) -> IdentityRiskScore:
    """Analyze an account's principals and summarize into a reproducible score."""
    findings = analyze_principals(principals, settings=settings)
    return summarize(findings, account_id=account_id, provider=provider)
