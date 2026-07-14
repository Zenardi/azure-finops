# 3 · Configuration Reference

All configuration is environment variables, read by
`backend/cloudwarden/config.py` (pydantic-settings). Field names map to
`UPPER_SNAKE` env keys, case-insensitively. The committed template is
**`.env.example`**; copy it to **`.env`** (gitignored) and edit.

> **Where values are read:** `backend` and `grafana` services both load `.env`
> via `env_file`. A handful of keys (RBAC/OIDC, Grafana DB wiring) are also
> surfaced as explicit `environment:` entries in `docker-compose.yml` with safe
> defaults.

## The one switch that matters most

| Env | Values | Effect |
|-----|--------|--------|
| **`FINOPS_MOCK`** | `1` (default) / `0` | `1` = use bundled fixtures, fully offline, no credentials. `0` = call real cloud APIs. |

Everything below is only needed when you move specific subsystems to live mode.

---

## Azure

| Env | Default | Purpose |
|-----|---------|---------|
| `AZURE_SUBSCRIPTION_ID` | `0000…` | Seeded as the **default** subscription on first start. |
| `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` | — | **Read-only** service principal for collection. Needs *Reader + Cost Management Reader + Monitoring Reader* (+ *Log Analytics Reader* for memory metrics). Empty → falls back to Managed Identity / `az` CLI. The Custodian policy engine (`c7n-azure`) reuses these same credentials. |
| `AZURE_REMEDIATION_TENANT_ID` / `_CLIENT_ID` / `_CLIENT_SECRET` | — | **Write-scoped** SP, only needed when `REMEDIATION_ENABLED=true`. Custom role limited to compute/network write on allowed resource groups. |
| `LOG_ANALYTICS_WORKSPACE_ID` | — | Enables memory-based downsize rules. Empty → CPU-only downsize at reduced confidence. |

Per-subscription credentials set on the **Subscriptions** page override these for
that subscription only. See [06 · Multi-Cloud Onboarding](06-multi-cloud-onboarding.md).

## AWS (M12.2)

| Env | Default | Purpose |
|-----|---------|---------|
| `AWS_ACCOUNT_ID` | `""` | Default AWS account id. |
| `AWS_DEFAULT_REGION` | `us-east-1` | Default region. |
| `AWS_ROLE_ARN` | — | Role to assume; optional. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | — | Static keys; optional. Live path falls back to the ambient role (IRSA / instance profile / env). |

Onboarding validates via STS `get_caller_identity`. Unset in mock dev.

## GCP (M12.3)

| Env | Default | Purpose |
|-----|---------|---------|
| `GCP_PROJECT_ID` | `""` | Default GCP project id. |
| `GCP_DEFAULT_REGION` | `us-central1` | Default region. |
| `GCP_SERVICE_ACCOUNT_JSON` | — | Path to (or inline body of) a service-account key. Empty → Application Default Credentials. |

Onboarding validates via Resource Manager `get_project`. **Live GCP policy
execution** needs the optional `c7n-gcp` extra (see `backend/requirements.txt`);
onboarding/ingestion/mock work without it.

---

## AI provider (executive summary)

| Env | Default | Purpose |
|-----|---------|---------|
| `AI_PROVIDER` | `anthropic` | `anthropic` or `openai` (any OpenAI-compatible endpoint). |
| `AI_MODEL` | `claude-opus-4-8` | Model id. |
| `ANTHROPIC_API_KEY` / `AI_API_KEY` | — | API key (`AI_API_KEY` wins if both set). |
| `AI_BASE_URL` | — | OpenAI-compatible endpoint for local models (Ollama/vLLM/LM Studio), e.g. `http://host.docker.internal:11434/v1`. |
| `AI_MAX_CANDIDATES` | `40` | Max recommendation candidates fed to the model. |
| `AI_MAX_TOKENS` | `8000` | Max output tokens. |

In mock mode the summary is generated from fixtures without calling a live model.

---

## Analysis windows & thresholds

| Env | Default | Purpose |
|-----|---------|---------|
| `METRIC_LOOKBACK_DAYS` | `14` | Metric analysis window. |
| `COST_LOOKBACK_DAYS` | `30` | Cost analysis window. |
| `MIN_DATA_COMPLETENESS` | `0.8` | Minimum metric completeness to trust a recommendation. |
| `SHUTDOWN_CPU_P95` / `SHUTDOWN_CPU_MAX` | `3.0` / `5.0` | Idle (stop) thresholds. |
| `DOWNSIZE_CPU_P95` / `DOWNSIZE_CPU_MAX` | `40.0` / `80.0` | Rightsizing (downsize) CPU thresholds. |
| `DOWNSIZE_MEM_P95` | `50.0` | Rightsizing memory threshold (needs Log Analytics). |

See [08 · FinOps: Cost & Recommendations](08-finops-cost-recommendations.md).

---

## Remediation guardrails

| Env | Default | Purpose |
|-----|---------|---------|
| `REMEDIATION_ENABLED` | `false` | `false` = dry-run only. Must be `true` for real writes. |
| `ALLOWED_RESOURCE_GROUPS` | `""` | Comma-separated allow-list; empty = nothing writable. |
| `EXCLUDE_TAG` | `finops:exclude` | Resources with this tag are never touched (`key:value`). |
| `ALLOWED_ACTIONS` | `""` | Comma-separated allow-list of Custodian action *types* (e.g. `tag,stop`). Empty = any action allowed. |

See [09 · Remediation](09-remediation.md).

---

## GitOps policy sync

| Env | Default | Purpose |
|-----|---------|---------|
| `GITOPS_REPO_URL` | `""` | Git repo to pull policy YAML from. **Empty disables sync.** |
| `GITOPS_BRANCH` | `main` | Branch to read. |
| `GITOPS_POLICY_PATH` | `policies` | Path within the repo holding policy files. |

Trigger with `POST /api/policies/sync`. See
[07 · Governance-as-Code](07-governance-as-code.md#gitops-sync).

---

## Real-time enforcement (Event Grid)

| Env | Default | Purpose |
|-----|---------|---------|
| `EVENT_MODE_ENABLED` | `true` | Master switch. `false` → `POST /api/events/azure` accepts (202) but stores/triggers nothing. |
| `AZURE_EVENTGRID_SHARED_KEY` | — | Shared key authenticating deliveries. Empty = accept all (mock/dev). When set, a delivery must present it via the `x-events-key` header or `?key=`, else 403. |

See [12 · Real-Time Enforcement](12-real-time-enforcement.md).

---

## Notification transports

| Env | Purpose |
|-----|---------|
| `SLACK_WEBHOOK_URL` | Default Slack incoming-webhook (used when a channel carries no target). |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_FROM` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_USE_TLS` | Email relay. Empty `SMTP_HOST` disables live email. |
| `TEAMS_WEBHOOK_URL` | Microsoft Teams incoming-webhook. |
| `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` / `JIRA_PROJECT` / `JIRA_ISSUE_TYPE` | Jira (one instance, many projects). Empty base URL disables live Jira. |
| `SERVICENOW_INSTANCE_URL` / `SERVICENOW_USER` / `SERVICENOW_PASSWORD` | ServiceNow Table API (create incident). |

See [10 · Notifications](10-notifications.md).

---

## Access control (RBAC) — M11.1

| Env | Default | Purpose |
|-----|---------|---------|
| `RBAC_ENABLED` | `false` | When `true`, mutating endpoints require the caller (`X-Principal`) to hold the endpoint's permission. |
| `RBAC_BOOTSTRAP_ADMIN` | `""` | Principal auto-bound to the `admin` role when roles are seeded — the identity that provisions all other bindings. |

## SSO / OIDC — M11.3

| Env | Default | Purpose |
|-----|---------|---------|
| `OIDC_ENABLED` | `false` | When `true`, requests carry identity as a verified OIDC bearer token or a first-party session. |
| `OIDC_ISSUER` | `""` | Issuer URL — validates token `iss`, derives JWKS/authorize/token endpoints. |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | `""` | OAuth2 client id (also expected `aud`) + secret. |
| `OIDC_REDIRECT_URI` | `""` | Where the IdP returns the auth code. |
| `OIDC_SCOPES` | `openid profile email` | Requested scopes. |
| `OIDC_PRINCIPAL_CLAIM` | `sub` | Which verified claim becomes the RBAC principal. |
| `OIDC_PUBLIC_KEY` | `""` | Optional static RS256 PEM (alternative to fetching JWKS — for air-gapped/pinned-key setups). |
| `SESSION_SECRET` | `""` | Signs our own session tokens (HS256). Empty falls back to client secret; set a dedicated value in production. |

See [11 · Security: RBAC, SSO/OIDC, Audit](11-security-rbac-sso.md).

---

## Runtime & scheduling

| Env | Default | Purpose |
|-----|---------|---------|
| `DATABASE_URL` | `postgresql+psycopg://finops:finops@localhost:5432/finops` | Postgres DSN. Compose overrides host to `db:5432` inside the network. |
| `APP_DATA_DIR` | `/data` | Backend writable dir (exports, artifacts). Mounted from the `appdata` volume. |
| `RUN_INTERVAL_SECONDS` | `86400` | Cost-pipeline cadence for the scheduler. |
| `POLICY_RUN_INTERVAL_SECONDS` | `86400` | Pull-mode policy cadence (independent of the cost pipeline). |
| `GOVERNANCE_REPORT_ENABLED` | `false` | When `true`, the scheduler writes a timestamped CSV export under `APP_DATA_DIR`. |
| `GOVERNANCE_REPORT_INTERVAL_SECONDS` | `86400` | Report cadence. |

---

## Grafana wiring (compose-level)

Set in `docker-compose.yml` (not usually in `.env`):

| Env | Default | Purpose |
|-----|---------|---------|
| `GF_SECURITY_ADMIN_PASSWORD` | `admin` | Grafana admin password. |
| `GF_AUTH_ANONYMOUS_ENABLED` | `true` | Anonymous read-only viewing. |
| `GF_AUTH_ANONYMOUS_ORG_ROLE` | `Viewer` | Anonymous role. |
| `FINOPS_DB_HOST` / `FINOPS_DB_NAME` / `FINOPS_DB_USER` / `FINOPS_DB_PASSWORD` | `db:5432` / `finops` / `finops` / `finops` | Postgres datasource wiring read by `datasources.yaml`. |

---

## Config quick-reference by goal

| I want to… | Set |
|------------|-----|
| Explore offline | `FINOPS_MOCK=1` (default) — nothing else |
| Pull real Azure cost/inventory | `FINOPS_MOCK=0` + `AZURE_SUBSCRIPTION_ID` + read SP + AI key |
| Onboard AWS/GCP | `FINOPS_MOCK=0` + AWS/GCP creds (or onboard via UI/API) |
| Enable remediation | `REMEDIATION_ENABLED=true` + write SP + `ALLOWED_RESOURCE_GROUPS` |
| Turn on auth | `RBAC_ENABLED=true` + `RBAC_BOOTSTRAP_ADMIN`; add `OIDC_*` for SSO |
| Sync policies from Git | `GITOPS_REPO_URL` + `GITOPS_BRANCH` + `GITOPS_POLICY_PATH` |
| Send alerts | one of the notification transport blocks above |
| Run on a schedule | switch `backend` command to `["scheduler"]`; tune `*_INTERVAL_SECONDS` |

Full annotated list lives in `.env.example`.
