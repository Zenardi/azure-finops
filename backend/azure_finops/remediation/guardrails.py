"""Remediation guardrails.

A resource is only actionable if it is NOT carrying the exclude tag and its
resource group is on the allow-list. An empty allow-list denies everything
(safe default); ``*`` allows any. These are pure functions, unit-tested without
Azure or a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings


@dataclass
class GuardResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def resource_group_of(resource_id: str) -> str | None:
    parts = (resource_id or "").lower().split("/")
    if "resourcegroups" in parts:
        idx = parts.index("resourcegroups")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def check(resource_id: str, tags: dict | None, settings: Settings) -> GuardResult:
    reasons: list[str] = []
    tags = tags or {}

    kv = settings.exclude_tag_kv
    if kv:
        key, value = kv
        for tk, tv in tags.items():
            if tk.lower() == key.lower() and str(tv).lower() == value.lower():
                reasons.append(f"excluded by tag {key}={value}")
                break

    allow = [a.lower() for a in settings.allowed_rg_list]
    rg = resource_group_of(resource_id)
    if "*" in allow:
        pass
    elif not allow:
        reasons.append("no resource groups are allow-listed (set ALLOWED_RESOURCE_GROUPS)")
    elif rg not in allow:
        reasons.append(f"resource group '{rg}' is not in the allow-list")

    return GuardResult(allowed=not reasons, reasons=reasons)
