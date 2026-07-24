# 10 · Notifications

Notifications deliver policy-violation alerts to your team's tools. The model is
**channels** (where to send) × **templates** (what to say), wired to **bindings**
(when to send). Dispatch is best-effort — a failed send never fails the run.

## Transports

| Transport | Target | Instance config (`.env`) |
|-----------|--------|--------------------------|
| `webhook` | any HTTP POST URL | — (target carries the URL) |
| `slack` | Slack incoming-webhook | `SLACK_WEBHOOK_URL` (default when channel has no target) |
| `email` | recipient address | `SMTP_HOST` `SMTP_PORT` `SMTP_FROM` `SMTP_USERNAME` `SMTP_PASSWORD` `SMTP_USE_TLS` |
| `teams` | Teams incoming-webhook | `TEAMS_WEBHOOK_URL` |
| `jira` | Jira project (creates issues) | `JIRA_BASE_URL` `JIRA_EMAIL` `JIRA_API_TOKEN` `JIRA_PROJECT` `JIRA_ISSUE_TYPE` |
| `servicenow` | ServiceNow (creates incidents) | `SERVICENOW_INSTANCE_URL` `SERVICENOW_USER` `SERVICENOW_PASSWORD` |

A channel may override the instance-level target (e.g. its own Slack webhook); if
it doesn't, the transport falls back to the `.env` default. Empty instance config
disables that transport's live delivery.

## Channels

Create/manage on the **Notifications** page or via API:

```bash
GET    /api/notification-channels
POST   /api/notification-channels
       { "name":"slack-security", "transport":"slack",
         "target":"https://hooks.slack.com/services/…", "enabled":true,
         "config":{ "extra":{ "channel":"#security-alerts" } } }
PUT    /api/notification-channels/{id}
DELETE /api/notification-channels/{id}
```

Duplicate names return 409; an unknown transport returns 400.

## Templates

Templates render with the violation context (sandboxed variable interpolation):

```bash
GET    /api/notification-templates
POST   /api/notification-templates
       { "name":"policy-violation",
         "subject":"[{{ policy_name }}] {{ count }} resources flagged",
         "body":"Policy {{ policy_name }} matched {{ count }} resources:\n{% for r in resources %}- {{ r.id }}\n{% endfor %}" }
DELETE /api/notification-templates/{id}
```

Common template variables: `policy_name`, `resource_type`, `count`,
`resource_ids`, `resource_id`, `resources` (list of `{id}` objects for looping).

## Wiring notifications to a binding

Attach one or more (channel, template) pairs to a binding; on a binding run with
matches, each pair renders and dispatches:

```bash
GET    /api/bindings/{binding_id}/notifications
POST   /api/bindings/{binding_id}/notifications   { "channel_id": 1, "template_id": 1 }
DELETE /api/bindings/{binding_id}/notifications/{notification_id}
```

## Operator workflow

1. Create a **channel** per destination (Slack, email, Jira, …).
2. Create **templates** for the alert types you care about.
3. **Wire** channel + template to the relevant bindings.
4. On each binding run, violations dispatch automatically; delivery result
   (dispatched / status / error) is recorded — a failure is logged, not fatal.

## ChatOps interactive approvals (M14.15)

CloudWarden already *sends* to Slack/Teams; ChatOps closes the loop. A pending
**remediation approval** (see [09 · Remediation](09-remediation.md)) is rendered as
an **actionable** message — an Approve and a Reject button — so an operator decides
without leaving chat. The decision flows through the *same* approval workflow the UI
uses (`remediation:approve` is still enforced); ChatOps is **not** a bypass, and it
never mutates anything on its own.

### How a decision is secured

Each button carries a **signed action id** — `HMAC-SHA256(action_id · decision ·
nonce)` under `CHATOPS_SIGNING_SECRET` — so approve/reject can't be forged or swapped.
When a button is clicked, the transport POSTs the interaction back and the inbound
endpoint:

1. **verifies the transport signature** — Slack's `v0` HMAC over `v0:{ts}:{body}` with
   `SLACK_SIGNING_SECRET` (+ a `CHATOPS_MAX_SKEW_SECONDS` timestamp window that rejects
   stale/replayed requests); Teams' `Authorization: HMAC {base64}` over the raw body
   with `TEAMS_SIGNING_SECRET`. A bad signature or stale timestamp → **401**;
2. **verifies our own action-id signature** (tamper → **401**);
3. **resolves the chat user → RBAC principal** via `CHATOPS_PRINCIPAL_MAP`
   (`{"U0123":"alice@corp.com"}`). An unmapped user → **403**;
4. **enforces `remediation:approve`** for that principal, then applies the decision
   through the existing workflow — unknown action → **404**, already-decided (a
   **replay**) → **409**;
5. **audits** the decision (actor + channel + target) and **updates the source
   message** with the outcome.

```bash
POST /api/chatops/slack     # inbound Slack block-actions interaction
POST /api/chatops/teams     # inbound Teams Action.Submit interaction
```

Configure the app signing secrets in [`.env`](../.env.example) — they are **never
logged**. Point the Slack app's interactivity request URL / Teams outgoing webhook at
the endpoints above. Verified end-to-end in mock mode (`FINOPS_MOCK=1`) with injected
transports — no live Slack/Teams is contacted. The decision *source* (`slack` / `teams`
/ UI) surfaces in the **Remediation** audit trail (`decided_via`).
