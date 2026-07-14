# CloudWarden — Operational Manual

The complete guide to running and operating **CloudWarden**, a **multi-cloud
Governance-as-Code & FinOps platform** — a self-hosted blend of Cloud
Custodian-style governance and a FinOps cost-optimization engine, covering Azure,
AWS, and GCP behind one pane.

> **New here?** Read [01 · Overview](01-overview.md), then follow
> [02 · Getting Started](02-getting-started.md) to stand it up in mock mode (no
> cloud account needed), and [15 · Demo Data](15-demo-data.md) to populate it.

![The Overview page — 30-day cost, potential savings, last-run status, and the AI executive summary](images/overview.png)

## Contents

| # | Guide | What's inside |
|---|-------|---------------|
| 01 | [Overview & Architecture](01-overview.md) | What it is, the four services, the backend map, glossary. |
| 02 | [Getting Started](02-getting-started.md) | Prerequisites → `make up` → seed → the URLs. |
| 03 | [Configuration Reference](03-configuration.md) | Every `.env` key, by section; mock vs live. |
| 04 | [Operating the Stack](04-operating-the-stack.md) | Make/Compose/CLI, scheduler, backups, upgrades, dev. |
| 05 | [Web UI Guide](05-web-ui-guide.md) | Every page, what it shows, and what you can do. |
| 06 | [Multi-Cloud Onboarding](06-multi-cloud-onboarding.md) | Onboard Azure / AWS / GCP accounts. |
| 07 | [Governance-as-Code](07-governance-as-code.md) | Policies, packs, collections, bindings, GitOps, the 3 modes. |
| 08 | [FinOps: Cost & Recommendations](08-finops-cost-recommendations.md) | Cost pipeline, rightsizing rules, AI summary. |
| 09 | [Remediation](09-remediation.md) | Guardrails, approvals, dry-run → production rollout. |
| 10 | [Notifications](10-notifications.md) | Slack / email / Teams / Jira / ServiceNow channels. |
| 11 | [Security: RBAC, SSO/OIDC, Audit](11-security-rbac-sso.md) | Roles, teams, OIDC login, the audit log. |
| 12 | [Real-Time Enforcement](12-real-time-enforcement.md) | Event-mode policy triggering via Event Grid. |
| 13 | [Dashboards (Grafana)](13-dashboards-grafana.md) | The five dashboards + the provider filter. |
| 14 | [API Reference](14-api-reference.md) | Full endpoint catalogue with permissions. |
| 15 | [Demo Data & Seeding](15-demo-data.md) | Populate all three clouds offline. |
| 16 | [Troubleshooting & FAQ](16-troubleshooting.md) | Common issues and fixes. |

## Quick reference

| | |
|-|-|
| **Web UI** | http://localhost:3001 |
| **API + Swagger** | http://localhost:8000/docs |
| **API health** | http://localhost:8000/health |
| **Grafana** | http://localhost:3000 (anon viewer; admin `admin`/`admin`) |
| **Start / stop** | `make up` / `make down` |
| **Init schema** | `make initdb` |
| **Seed (quick)** | `make seed` |
| **Reset all data** | `docker compose down -v` |

## Common tasks → where to look

| I want to… | Guide |
|------------|-------|
| Run it locally right now | [02](02-getting-started.md) |
| Fill it with demo data | [15](15-demo-data.md) |
| Understand a specific screen | [05](05-web-ui-guide.md) |
| Onboard a real cloud account | [06](06-multi-cloud-onboarding.md) |
| Write and run a policy | [07](07-governance-as-code.md) |
| Cut cloud spend | [08](08-finops-cost-recommendations.md) |
| Actually take corrective action | [09](09-remediation.md) |
| Get alerted on violations | [10](10-notifications.md) |
| Lock it down for a team | [11](11-security-rbac-sso.md) |
| Enforce in real time | [12](12-real-time-enforcement.md) |
| Call the API directly | [14](14-api-reference.md) |
| Fix something that broke | [16](16-troubleshooting.md) |

## Conventions

- Commands assume the repo root as the working directory and the stack running via
  `make up`.
- `FINOPS_MOCK=1` (the default) means everything runs offline on bundled fixtures.
- All configuration is environment variables — the committed template is
  `.env.example`; your local copy is `.env` (gitignored).

---

Related root docs: [`README.md`](../README.md) (project intro & design),
[`DESIGN.md`](../DESIGN.md) (architecture rationale),
[`CHANGELOG.md`](../CHANGELOG.md) (milestone history).
