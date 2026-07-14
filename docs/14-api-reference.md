# 14 · API Reference

FastAPI app served by the `backend` service. **Base URL:** `http://localhost:8000`.
Interactive docs: **`/docs`** (Swagger), `/redoc`, `/openapi.json`.

> This is the operator-oriented catalogue (grouped by area). The live `/docs` is
> the authoritative source for exact request/response schemas.

## Auth model

- **Identity:** the `X-Principal` header (or the verified OIDC subject when
  `OIDC_ENABLED=true`).
- **RBAC:** when `RBAC_ENABLED=true`, **mutating** endpoints require the permission
  listed in the *Perm* column below; reads are open. When RBAC is off, everything
  is open. See [11 · Security](11-security-rbac-sso.md).
- **Event webhook:** `/api/events/azure` uses the Event Grid shared key
  (`x-events-key` / `?key=`), not RBAC.

## Health & docs
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + data-source resilience snapshot. |
| GET | `/docs` · `/redoc` · `/openapi.json` | API documentation. |

## Costs
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/costs/summary` | Total + by-type + by-region rollup. |
| GET | `/api/costs/by-type` | Cost by resource type. |
| GET | `/api/costs/by-region` | Cost by region. |
| GET | `/api/costs/by-resource?limit=` | Top-N resources by cost. |

## Recommendations
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| GET | `/api/recommendations` | List latest recommendations. | — |
| POST | `/api/recommendations/{rec_id}/decision` | Approve/reject (`{decision, actor}`). | `recommendation:decide` |
| POST | `/api/recommendations/{rec_id}/remediate?dry_run=&actor=` | Remediate an approved rec. | `remediation:approve` |

## Runs & summary
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| GET | `/api/runs` · `/api/runs/latest` | Pipeline run history / latest. | — |
| POST | `/api/runs?mock=&subscription_id=` | Trigger a run (all subs, or one). | `run:trigger` |
| GET | `/api/summary` | Latest AI executive summary. | — |

## Policies
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| GET | `/api/policies?enabled=` | List (team-scoped under RBAC). | — |
| GET | `/api/policies/{id}` | Fetch one. | — |
| POST | `/api/policies` | Create (validate-on-write; 422 invalid, 409 dup). | `policy:write` |
| PUT | `/api/policies/{id}` | Update (re-validates). | `policy:write` |
| DELETE | `/api/policies/{id}` | Delete. | `policy:write` |
| POST | `/api/policies/{id}/enabled?enabled=` | Enable/disable. | `policy:write` |
| GET | `/api/policies/{id}/versions` | Version history. | — |
| GET | `/api/policies/{id}/versions/diff?from_version=&to_version=` | Field-level diff. | — |
| POST | `/api/policies/validate` | Validate a spec (never persists). | — |
| POST | `/api/policies/{id}/dryrun?subscription_id=` | Push-mode dry-run (matches only). | `policy:run` |
| POST | `/api/policies/sync` | GitOps sync (never 500s). | `policy:write` |
| GET | `/api/custodian/schema?resource_type=` | List resource types / one type's filters+actions. | — |

## Collections
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| GET | `/api/collections` · `/api/collections/{id}` | List / fetch. | — |
| POST | `/api/collections` | Create (409 dup). | `collection:write` |
| DELETE | `/api/collections/{id}` | Delete (keeps policies). | `collection:write` |
| POST | `/api/collections/{cid}/policies/{policy_id}` | Add policy. | `collection:write` |
| DELETE | `/api/collections/{cid}/policies/{policy_id}` | Remove policy. | `collection:write` |

## Policy packs
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| GET | `/api/packs` · `/api/packs/installed` | Available / installed. | — |
| POST | `/api/packs/{name}/install` | Install → materialize into a collection. | `pack:install` |
| POST | `/api/packs/{name}/enabled` | Enable/disable installed pack. | `pack:install` |

## Account groups
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| GET | `/api/account-groups` · `/api/account-groups/{id}` | List / fetch. | — |
| POST | `/api/account-groups` | Create (409 dup). | `accountgroup:write` |
| DELETE | `/api/account-groups/{id}` | Delete (keeps subscriptions). | `accountgroup:write` |
| POST | `/api/account-groups/{gid}/subscriptions/{subscription_id}` | Add subscription. | `accountgroup:write` |
| DELETE | `/api/account-groups/{gid}/subscriptions/{subscription_id}` | Remove subscription. | `accountgroup:write` |

## Bindings
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| GET | `/api/bindings` · `/api/bindings/{id}` | List / fetch. | — |
| POST | `/api/bindings` | Create (400 bad schedule, 404 missing refs). | `binding:write` |
| PUT | `/api/bindings/{id}` | Update (partial). | `binding:write` |
| DELETE | `/api/bindings/{id}` | Delete. | `binding:write` |
| POST | `/api/bindings/{id}/run` | Execute now (disabled → skipped). | `binding:run` |
| GET | `/api/bindings/{id}/notifications` | List wired notifications. | — |
| POST | `/api/bindings/{id}/notifications` | Wire channel+template. | `notification:write` |
| DELETE | `/api/bindings/{id}/notifications/{notification_id}` | Unwire. | `notification:write` |

## Policy executions
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/policy-executions?policy_id=&subscription_id=&status=&limit=` | Pull-mode history. |
| GET | `/api/policy-executions/{execution_id}` | One execution. |
| GET | `/api/policy-executions/{execution_id}/matches` | Matched resources. |

## Governance
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/governance/posture?provider=` | Posture: totals + by policy/subscription/collection/control/provider. |
| GET | `/api/governance/execution-health?provider=` | Engine health by policy/binding/provider. |
| GET | `/api/governance/policy-health` | Per-policy compliance aggregates. |
| GET | `/api/governance/policies/{id}/matches` | Resources currently flagged (drill-down). |
| GET | `/api/governance/export?format=csv\|json` | Stream governance evidence. |

## AssetDB
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/assets/query` | Filtered query (allow-listed columns/operators; provider filter). |
| GET | `/api/assets/{resource_id}/relationships` | Relationship edges (path allows slashes). |
| GET | `/api/assets/{resource_id}/history` | Change timeline. |

> Asset **detail** in the UI is composed from `POST /api/assets/query` (by id) plus
> the relationships and history endpoints.

## Subscriptions (Azure + generic)
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| GET | `/api/subscriptions` | List all (azure/aws/gcp). | — |
| POST | `/api/subscriptions` | Create/update (upsert). | `subscription:write` |
| DELETE | `/api/subscriptions/{id}` | Delete. | `subscription:write` |
| POST | `/api/subscriptions/{id}/default` | Set default. | `subscription:write` |
| POST | `/api/subscriptions/{id}/test` | Test connectivity. | `subscription:write` |

## AWS / GCP onboarding
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| POST | `/api/aws/accounts` | Onboard AWS (STS validate). | `subscription:write` |
| POST | `/api/aws/accounts/{account_id}/ingest` | Ingest AWS assets. | `subscription:write` |
| POST | `/api/aws/policies/dryrun` | Dry-run an AWS c7n policy. | — |
| POST | `/api/gcp/projects` | Onboard GCP (Resource Manager validate). | `subscription:write` |
| POST | `/api/gcp/projects/{project_id}/ingest` | Ingest GCP assets. | `subscription:write` |
| POST | `/api/gcp/policies/dryrun` | Dry-run a GCP c7n policy. | — |

## Events
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/events/azure` | Event Grid webhook (shared-key auth; 202 if event mode off). |
| GET | `/api/events?limit=` | Recent deliveries. |
| GET | `/api/events/recent?limit=&offset=` | Deliveries + triggered executions. |

## Remediation & approvals
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| POST | `/api/policy-matches/{match_id}/actions` | Queue a matched action as pending. | `remediation:approve` |
| POST | `/api/remediation/{action_id}/approve?actor=` | Approve → guarded execution. | `remediation:approve` |
| POST | `/api/remediation/{action_id}/reject?actor=` | Reject (never executes). | `remediation:approve` |
| GET | `/api/remediation?limit=&source=` | Unified remediation audit. | — |

## Notifications
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| GET | `/api/notification-channels` · `/{id}` | List / fetch channels. | — |
| POST/PUT/DELETE | `/api/notification-channels[/{id}]` | Create/update/delete channel. | `notification:write` |
| GET | `/api/notification-templates` · `/{id}` | List / fetch templates. | — |
| POST/PUT/DELETE | `/api/notification-templates[/{id}]` | Create/update/delete template. | `notification:write` |

## RBAC & teams
| Method | Path | Purpose | Perm |
|--------|------|---------|------|
| GET | `/api/authz/me` | Current principal + resolved permissions. | — |
| GET | `/api/authz/roles` | Roles + permission grants. | — |
| GET | `/api/authz/role-bindings?principal=` | List bindings. | — |
| POST | `/api/authz/role-bindings` | Bind principal→role. | `rbac:admin` |
| DELETE | `/api/authz/role-bindings?principal=&role=` | Unbind. | `rbac:admin` |
| GET | `/api/teams` · `/{id}` · `/{id}/members` | List / fetch teams & members. | — |
| POST | `/api/teams` | Create team. | `team:write` |
| POST | `/api/teams/{id}/members` | Add member. | `team:write` |
| DELETE | `/api/teams/{id}/members/{principal}` | Remove member. | `team:write` |

## Audit & auth
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/audit?actor=&action=&target_type=&target_id=&limit=&offset=` | Append-only mutation log. |
| GET | `/api/auth/login` | OIDC authorization URL (404 if OIDC off). |
| GET | `/api/auth/callback?code=&state=` | Exchange code, set session cookie. |
| POST | `/api/auth/logout` | Clear session cookie. |

## Status codes

`200` ok · `201` created · `202` accepted (event mode off) · `400` bad input ·
`403` forbidden (cross-team / bad event key) · `404` not found · `409` conflict
(duplicate name / already-decided) · `422` invalid policy spec.
