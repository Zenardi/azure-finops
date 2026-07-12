"""Execute (or dry-run) a remediation action against Azure via the write SP.

Supported: VM deallocate, VM resize, delete unattached disk, delete idle public
IP. ``dry_run=True`` returns a preview without importing the Azure SDK, so it is
fully testable offline. Live execution uses the write-scoped credential.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings
from ..resilience import with_retry

logger = logging.getLogger("azure_finops.remediation.executor")

SUPPORTED = {"deallocate", "resize", "delete_disk", "delete_public_ip"}


def _parse(resource_id: str) -> dict[str, str | None]:
    parts = resource_id.split("/")
    fields: dict[str, str | None] = {}
    i = 1
    while i + 1 < len(parts):
        fields[parts[i].lower()] = parts[i + 1]
        i += 2
    fields["name"] = parts[-1] if parts else None
    return fields


def preview(action_type: str, resource_id: str, params: dict[str, Any]) -> dict[str, Any]:
    target = params.get("recommended_sku")
    extra = f" → {target}" if action_type == "resize" and target else ""
    return {
        "executed": False,
        "dry_run": True,
        "action": action_type,
        "resource_id": resource_id,
        "message": f"[dry-run] would {action_type} {resource_id}{extra}",
    }


def execute(
    action_type: str,
    resource_id: str,
    params: dict[str, Any],
    settings: Settings,
    credential: Any = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    if dry_run:
        return preview(action_type, resource_id, params)
    if action_type not in SUPPORTED:
        return {
            "executed": False,
            "dry_run": False,
            "action": action_type,
            "resource_id": resource_id,
            "message": f"action '{action_type}' is not auto-executable; handle manually",
        }
    return _execute_live(action_type, resource_id, params, settings, credential)


@with_retry()
def _execute_live(
    action_type: str,
    resource_id: str,
    params: dict[str, Any],
    settings: Settings,
    credential: Any,
) -> dict[str, Any]:
    from ..auth import write_credential

    cred = credential or write_credential()
    ids = _parse(resource_id)
    sub = ids.get("subscriptions") or settings.azure_subscription_id
    rg = ids.get("resourcegroups")
    name = ids.get("name")

    if action_type in ("deallocate", "resize", "delete_disk"):
        from azure.mgmt.compute import ComputeManagementClient

        compute = ComputeManagementClient(cred, sub)
        if action_type == "deallocate":
            compute.virtual_machines.begin_deallocate(rg, name).result()
        elif action_type == "resize":
            sku = params.get("recommended_sku")
            compute.virtual_machines.begin_update(
                rg, name, {"hardware_profile": {"vm_size": sku}}
            ).result()
        else:  # delete_disk
            compute.disks.begin_delete(rg, name).result()
    elif action_type == "delete_public_ip":
        from azure.mgmt.network import NetworkManagementClient

        network = NetworkManagementClient(cred, sub)
        network.public_ip_addresses.begin_delete(rg, name).result()

    return {
        "executed": True,
        "dry_run": False,
        "action": action_type,
        "resource_id": resource_id,
        "message": f"{action_type} completed",
    }
