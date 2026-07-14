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
