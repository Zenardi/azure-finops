"""Rich multi-cloud demo seed for the FinOps / governance platform.

Populates every dashboard with realistic, varied data so the platform can be
explored end-to-end offline (FINOPS_MOCK=1):

  * 3 onboarded cloud accounts  — Azure + AWS + GCP  (Subscriptions page)
  * AssetDB inventory across all 3 clouds            (Assets page + provider filter)
  * Azure cost / recommendations / AI summary        (Costs + Recommendations)
  * 9 governance policies spanning the 3 clouds       (Policies page)
  * Policy-execution history (succeeded + failed)     (Executions page,
        Compliance posture-by-provider, Grafana execution-health-by-provider)

Idempotent-ish: re-running reuses existing accounts/policies and appends a new
round of execution history. Safe to run against the docker-compose stack.

Run it (from the repo root, with the stack up):

    docker compose cp docs/examples/seed_demo.py backend:/tmp/seed_demo.py
    docker compose exec -e PYTHONPATH=/app backend python /tmp/seed_demo.py
"""

from __future__ import annotations

import os

os.environ.setdefault("FINOPS_MOCK", "1")

import uuid
from datetime import UTC, datetime, timedelta

from cloudwarden.config import get_settings
from cloudwarden.custodian import engine as custodian_engine
from cloudwarden.models import PolicyMatch
from cloudwarden.orchestrator import run_one_subscription
from cloudwarden.providers import registry
from cloudwarden.storage import repository as repo
from cloudwarden.storage import schema
from cloudwarden.storage.db import init_db, session_scope

settings = get_settings()
settings.finops_mock = True

AZURE_ID = settings.azure_subscription_id
AWS_ID = "123456789012"
GCP_ID = "finops-demo-prod"

# base clock for backdating execution history (script world has no Date.now guard,
# but we keep timestamps deterministic-ish relative to a single read of "now").
NOW = datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Policy catalogue — a realistic mix per cloud. ``force_zero`` fakes a fully
# compliant policy (no matches) so posture shows both green and red rows; the
# AWS/GCP "absent type" policies naturally match nothing in the fixtures.
# --------------------------------------------------------------------------- #
POLICIES = [
    # name, provider, resource_type, description, actions, force_zero
    (
        "azure-idle-virtual-machines",
        "azure",
        "azure.vm",
        "Stop VMs with <5% avg CPU over 14 days.",
        ["stop"],
        False,
    ),
    (
        "azure-untagged-storage-accounts",
        "azure",
        "azure.storage",
        "Flag storage accounts missing a CostCenter tag.",
        ["tag"],
        False,
    ),
    (
        "azure-orphaned-managed-disks",
        "azure",
        "azure.disk",
        "Delete unattached managed disks.",
        ["delete"],
        True,  # demo: fully compliant
    ),
    (
        "aws-public-s3-buckets",
        "aws",
        "aws.s3",
        "Remediate S3 buckets with public ACLs.",
        ["set-bucket-encryption"],
        False,
    ),
    (
        "aws-unencrypted-ebs-volumes",
        "aws",
        "aws.ebs",
        "Flag EBS volumes without encryption at rest.",
        ["encrypt-instance"],
        False,
    ),
    (
        "aws-idle-elastic-load-balancers",
        "aws",
        "aws.elb",  # absent from fixture -> 0 matches -> compliant
        "Delete ELBs with zero healthy targets.",
        ["delete"],
        False,
    ),
    (
        "gcp-public-storage-buckets",
        "gcp",
        "gcp.bucket",
        "Flag public Cloud Storage buckets.",
        ["set-iam-policy"],
        False,
    ),
    (
        "gcp-unused-persistent-disks",
        "gcp",
        "gcp.disk",
        "Delete unattached persistent disks.",
        ["delete"],
        False,
    ),
    (
        "gcp-unused-static-ips",
        "gcp",
        "gcp.address",  # absent from fixture -> 0 matches -> compliant
        "Release reserved-but-unused external IPs.",
        ["delete"],
        False,
    ),
]

ACCOUNT_OF = {"azure": AZURE_ID, "aws": AWS_ID, "gcp": GCP_ID}


def _spec(name: str, resource_type: str, actions: list[str]) -> dict:
    return {
        "policies": [
            {
                "name": name,
                "resource": resource_type,
                "description": f"demo policy {name}",
                "filters": [],
                "actions": list(actions),
            }
        ]
    }


def _matches(provider: str, spec: dict) -> list[PolicyMatch]:
    """Evaluate a policy via the real (mock-backed) provider engine → PolicyMatch rows."""
    if provider == "azure":
        ctx = registry.get("azure").account_context(
            account_id=AZURE_ID, credential=None, display_name="Azure Demo"
        )
        result = custodian_engine.run_policy(spec, subscription=ctx)
    elif provider == "aws":
        result = registry.get("aws").run_policy(spec, account_id=AWS_ID)
    else:
        result = registry.get("gcp").run_policy(spec, project_id=GCP_ID)
    out: list[PolicyMatch] = []
    for r in result.get("resources", []):
        rid = r.get("id") or r.get("resource_id") or ""
        out.append(PolicyMatch(resource_id=rid, resource_type=r.get("type")))
    return out


def _exec_id() -> str:
    return f"exec_{NOW.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"


def onboard_accounts() -> None:
    with session_scope() as s:
        repo.upsert_subscription(
            s, subscription_id=AZURE_ID, display_name="Azure — Production", provider="azure"
        )
        repo.upsert_subscription(
            s,
            subscription_id=AWS_ID,
            display_name="AWS — Prod (123456789012)",
            provider="aws",
        )
        repo.upsert_subscription(
            s,
            subscription_id=GCP_ID,
            display_name="GCP — Prod (finops-demo-prod)",
            provider="gcp",
        )
    print(f"  onboarded 3 accounts: azure={AZURE_ID[:8]}… aws={AWS_ID} gcp={GCP_ID}")


def ingest_provider_assets(provider: str, account_id: str) -> int:
    """Ingest AWS/GCP fixture assets into AssetDB (+ 'created' events), like the orchestrator."""
    prov = registry.get(provider)
    kw = {"account_id": account_id} if provider == "aws" else {"project_id": account_id}
    records = prov.collect_assets(**kw)
    with session_scope() as s:
        new_ids = repo.upsert_assets(s, records)
        by_id = {r.resource_id: r for r in records}
        for rid in new_ids:
            rec = by_id[rid]
            repo.append_asset_event(
                s,
                resource_id=rid,
                subscription_id=rec.subscription_id,
                event_type="created",
                data=rec.config,
            )
    return len(records)


def seed_executions(policy: dict, provider: str, matches: list[PolicyMatch], force_zero: bool):
    """Record 3 backdated executions (history) for one policy; middle one occasionally failed."""
    account_id = ACCOUNT_OF[provider]
    effective = [] if force_zero else matches
    actions = policy["spec"]["policies"][0]["actions"]
    # spread over the last ~12 days; latest is always succeeded so posture is stable.
    plan = [(11, "succeeded"), (5, "succeeded"), (0, "succeeded")]
    # inject a failure into the history of ~1 policy per provider for exec-health realism
    if policy["name"] in {"aws-idle-elastic-load-balancers", "gcp-unused-persistent-disks"}:
        plan[1] = (5, "failed")
    for days_ago, status in plan:
        ex_id = _exec_id()
        with session_scope() as s:
            repo.create_policy_execution(
                s, execution_id=ex_id, policy_id=policy["id"], subscription_id=account_id
            )
            if status == "succeeded":
                repo.insert_policy_matches(s, ex_id, effective)
                repo.finish_policy_execution(
                    s,
                    ex_id,
                    status="succeeded",
                    resources_matched=len(effective),
                    actions_taken=actions,
                )
            else:
                repo.finish_policy_execution(
                    s, ex_id, status="failed", error="mock: transient provider throttling (429)"
                )
            # backdate for a believable time series
            rec = s.get(schema.PolicyExecution, ex_id)
            started = NOW - timedelta(days=days_ago, minutes=days_ago * 7)
            rec.started_at = started
            rec.finished_at = started + timedelta(seconds=6 + days_ago)


def main() -> None:
    print("→ init_db")
    init_db()

    print("→ onboarding cloud accounts (azure/aws/gcp)")
    onboard_accounts()

    print("→ Azure cost pipeline (assets + cost + recommendations + AI summary)")
    res = run_one_subscription(AZURE_ID, mock=True)
    counts = (res or {}).get("counts", {})
    print(
        f"  azure run: assets={counts.get('assets')} cost_rows={counts.get('cost_rows')} "
        f"recs={counts.get('recommendations')} rollups={counts.get('rollups')}"
    )

    print("→ ingesting AWS + GCP assets into AssetDB")
    n_aws = ingest_provider_assets("aws", AWS_ID)
    n_gcp = ingest_provider_assets("gcp", GCP_ID)
    with session_scope() as s:
        edges = repo.build_relationships(s)
    print(f"  aws assets={n_aws} gcp assets={n_gcp} relationship edges={edges}")

    print("→ creating 9 governance policies + execution history")
    created = 0
    for name, provider, rtype, desc, actions, force_zero in POLICIES:
        with session_scope() as s:
            existing = repo.get_policy_by_name(s, name)
            if existing is None:
                pol = repo.create_policy(
                    s,
                    name=name,
                    resource_type=rtype,
                    spec=_spec(name, rtype, actions),
                    description=desc,
                    source="demo-seed",
                )
                created += 1
            else:
                pol = existing
        matches = _matches(provider, _spec(name, rtype, actions))
        seed_executions(pol, provider, matches, force_zero)
        tag = "compliant" if (force_zero or not matches) else f"{len(matches)} matched"
        print(f"  [{provider:5}] {name:34} → {tag}")
    print(f"  policies created this run: {created} (reused {len(POLICIES) - created})")

    # --- summary --------------------------------------------------------- #
    with session_scope() as s:
        posture = repo.governance_posture(s)
        health = repo.execution_health(s)
    print("\n=== POSTURE BY PROVIDER ===")
    for row in posture.get("by_provider", []):
        print(
            f"  {row['provider']:6} compliant={row['compliant']} "
            f"non_compliant={row['non_compliant']} violations={row['violations']} "
            f"evaluated={row['evaluated']}"
        )
    print("=== EXECUTION HEALTH BY PROVIDER ===")
    for row in health.get("by_provider", []):
        print(
            f"  {row['provider']:6} total={row['total_executions']} "
            f"succeeded={row['succeeded']} failed={row['failed']} "
            f"success_rate={row['success_rate']}"
        )
    print("\n✅ demo seed complete — explore the UI (:3001), API (:8000), Grafana (:3000)")


if __name__ == "__main__":
    main()
