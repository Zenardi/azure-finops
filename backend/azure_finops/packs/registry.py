"""Policy-pack registry — discover bundled packs and install them (M10.1).

A *policy pack* is a curated, versioned bundle of Cloud Custodian policies shipped
as YAML under ``packs/defs/``. Each file declares ``name`` / ``version`` /
``description`` and a ``policies`` list (the same shape as a single ``policies:``
entry). The registry:

* :func:`list_packs` / :func:`get_pack` — discover the bundled YAML (offline, no DB);
* :func:`install_pack` — validate every policy through the engine, then materialize
  the (upserted) policies plus a collection named after the pack, recording the
  installed version in ``installed_packs``.

Install is **atomic on validation**: every policy is validated up front, so a pack
with any invalid policy reports the error and writes nothing. Re-installing the
same version is idempotent (upsert-by-name + get-or-create collection + a single
``installed_packs`` row). The engine ``runner`` seam is injectable so tests stay
fully offline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from ..custodian.engine import CustodianRunner, validate_policy
from ..storage import repository as repo
from ..storage.db import session_scope

logger = logging.getLogger("azure_finops.packs.registry")

# Bundled pack definitions live alongside this module in ``defs/``.
PACKS_DIR = Path(__file__).resolve().parent / "defs"

_PACK_EXTS = {".yml", ".yaml"}


def _pack_files(packs_dir: Path) -> list[Path]:
    if not packs_dir.is_dir():
        return []
    return sorted(p for p in packs_dir.iterdir() if p.is_file() and p.suffix.lower() in _PACK_EXTS)


def _load_packs(packs_dir: Path | None) -> dict[str, dict[str, Any]]:
    """Parse every pack YAML in ``packs_dir`` into a ``{name: pack}`` mapping.

    Files that don't parse to a mapping with a ``name`` are ignored (a stray,
    non-pack YAML in the directory is not an error).
    """
    directory = packs_dir if packs_dir is not None else PACKS_DIR
    packs: dict[str, dict[str, Any]] = {}
    for path in _pack_files(directory):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("name"):
            continue
        packs[data["name"]] = data
    return packs


def _pack_summary(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": pack["name"],
        "version": str(pack.get("version") or ""),
        "title": pack.get("title") or pack["name"],
        "description": pack.get("description") or "",
        "policy_count": len(pack.get("policies") or []),
    }


def list_packs(packs_dir: Path | None = None) -> list[dict[str, Any]]:
    """List discoverable packs (name/version/title/description/policy_count), sorted."""
    packs = _load_packs(packs_dir)
    return [_pack_summary(packs[name]) for name in sorted(packs)]


def get_pack(name: str, packs_dir: Path | None = None) -> dict[str, Any] | None:
    """Return the full parsed pack (with its ``policies``), or ``None`` if unknown."""
    return _load_packs(packs_dir).get(name)


def install_pack(
    name: str,
    runner: CustodianRunner | None = None,
    packs_dir: Path | None = None,
) -> dict[str, Any]:
    """Install a pack: validate all policies, then materialize policies + a collection.

    Never raises — returns a report ``{ok, pack, version, collection_id, added,
    updated, unchanged, policies, errors, error}``. On an unknown pack or any
    invalid policy, ``ok`` is ``False`` and **nothing** is persisted.
    """
    report: dict[str, Any] = {
        "ok": False,
        "pack": name,
        "version": None,
        "collection_id": None,
        "added": 0,
        "updated": 0,
        "unchanged": 0,
        "policies": [],
        "errors": [],
        "error": None,
    }

    pack = get_pack(name, packs_dir=packs_dir)
    if pack is None:
        report["error"] = f"unknown pack: {name}"
        return report

    version = str(pack.get("version") or "")
    policies = pack.get("policies") or []
    report["version"] = version

    # Validate every policy up front so an invalid pack installs nothing (atomic).
    errors: list[dict[str, Any]] = []
    for policy in policies:
        spec = {"policies": [policy]}
        validation = validate_policy(spec, runner=runner)
        if not validation.get("valid"):
            errors.append({"policy": policy.get("name"), "errors": validation.get("errors") or []})
    if errors:
        report["errors"] = errors
        report["error"] = f"pack '{name}' has invalid policies"
        return report

    with session_scope() as session:
        collection = repo.get_or_create_collection(
            session, name=name, description=pack.get("description")
        )
        collection_id = collection["id"]
        for policy in policies:
            spec = {"policies": [policy]}
            outcome = repo.upsert_policy_by_name(
                session,
                name=policy["name"],
                resource_type=policy.get("resource", ""),
                spec=spec,
                description=policy.get("description"),
                source="pack",
            )
            report[outcome] += 1
            stored = repo.get_policy_by_name(session, policy["name"])
            repo.add_policy_to_collection(session, collection_id, stored["id"])
        repo.upsert_installed_pack(session, name=name, version=version, collection_id=collection_id)
        report["collection_id"] = collection_id
        report["policies"] = [p["name"] for p in policies]

    report["ok"] = True
    return report
