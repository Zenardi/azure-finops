# 11 · Security: RBAC, SSO/OIDC, Audit

Three layers, all **off by default** so local/mock dev needs no identity provider:
**RBAC** (who can do what), **OIDC** (how identity is proven), and the **audit
log** (what was done). Turn them on for shared/production deployments.

## RBAC (M11.1)

```
RBAC_ENABLED=false                    # true → mutating endpoints require permission
RBAC_BOOTSTRAP_ADMIN=                 # principal auto-bound to admin on seed
```

When enabled, every **mutating** endpoint checks that the caller holds the
endpoint's permission. Reads stay open. The caller's identity is the **principal**
— taken from the `X-Principal` header (or the verified OIDC subject when OIDC is on).

### Default roles (seeded, idempotent)

| Role | Permissions |
|------|-------------|
| `admin` | `*` (everything, incl. `rbac:admin`, `team:write`) |
| `editor` | `policy:write` `policy:run` `collection:write` `pack:install` `accountgroup:write` `binding:write` `binding:run` `run:trigger` `subscription:write` `remediation:approve` `notification:write` `recommendation:decide` |
| `viewer` | (none — read-only) |

Each mutating route declares the permission it needs (e.g. `policy:write`,
`binding:run`, `remediation:approve`). Permissions are visible on `GET /api/authz/roles`.

### Bootstrapping

`rbac:admin` is itself required to create bindings — so the **first** admin must be
seeded out-of-band. Set `RBAC_BOOTSTRAP_ADMIN=alice@corp.com` and that principal is
auto-bound to `admin` when roles are seeded. Then:

```bash
# See your own resolved permissions
curl -H 'X-Principal: alice@corp.com' localhost:8000/api/authz/me

# Bind another principal to a role (needs rbac:admin)
curl -X POST -H 'X-Principal: alice@corp.com' -H 'Content-Type: application/json' \
     -d '{"principal":"bob@corp.com","role":"editor"}' \
     localhost:8000/api/authz/role-bindings

# List / remove bindings
GET    /api/authz/role-bindings?principal=bob@corp.com
DELETE /api/authz/role-bindings?principal=bob@corp.com&role=editor
```

### Teams / multi-tenancy (M11.2)

With RBAC on, **policies are scoped to owning teams** — a non-admin member sees and
edits only their team's policies (cross-team access is 403 unless admin).

```bash
GET    /api/teams
POST   /api/teams                              { name, description }        # admin
GET    /api/teams/{id}/members
POST   /api/teams/{id}/members                 { principal, role:"member" } # admin
DELETE /api/teams/{id}/members/{principal}                                  # admin
```

## SSO / OIDC (M11.3)

```
OIDC_ENABLED=false
OIDC_ISSUER=https://auth.corp.com     # derives JWKS/authorize/token endpoints
OIDC_CLIENT_ID=finops-app             # also the expected token `aud`
OIDC_CLIENT_SECRET=…
OIDC_REDIRECT_URI=https://finops.corp.com/api/auth/callback
OIDC_SCOPES=openid profile email
OIDC_PRINCIPAL_CLAIM=sub              # which verified claim becomes the principal
OIDC_PUBLIC_KEY=                      # optional static RS256 PEM (air-gapped/pinned)
SESSION_SECRET=                       # signs first-party session tokens (HS256)
```

When enabled, identity comes from a **verified OIDC token** (or a first-party
session cookie), and that verified subject becomes the RBAC principal — replacing
`X-Principal`.

### Login flow

```
GET  /api/auth/login      → { authorization_url, state }   # redirect the browser here
   ↓ user authenticates at the IdP, which redirects back to:
GET  /api/auth/callback?code=…&state=…
   → verifies the token (signature/expiry/iss/aud), extracts the principal claim,
     issues a first-party session JWT, sets the `_finops_session` cookie
POST /api/auth/logout     → clears the session cookie
```

On subsequent requests the backend accepts either the session cookie (cheap HS256
verify, checked first) or an `Authorization: Bearer <id_token>` (full RS256 verify
against `OIDC_PUBLIC_KEY` or the issuer's JWKS). The **Login** page drives this
flow; an `AuthGate` guards all other UI routes.

`OIDC_PRINCIPAL_CLAIM` picks which claim identifies the user: `sub` (stable,
recommended), `email`, or `preferred_username` (friendlier for role bindings).

## Audit log (M11.4)

An **append-only** trail of every governance mutation — reads are not audited. Each
entry: `{ timestamp, actor, action, target_type, target_id, before, after }`.

Audited actions include policy/collection/binding/pack/subscription/notification
create-update-delete, policy enable/disable & sync, role-binding and team changes,
and recommendation/remediation approvals.

```bash
GET /api/audit?actor=&action=&target_type=&target_id=&limit=100&offset=0
```

Newest-first; always readable. The **Audit** page renders it with actor/target
filters and text search.

## Turning it on — order of operations

1. Set `RBAC_ENABLED=true` and `RBAC_BOOTSTRAP_ADMIN=<you>`, restart, `make initdb`
   (seeds roles + binds you as admin).
2. As the bootstrap admin, create teams and bind principals to roles.
3. (Optional) Add `OIDC_*` to replace `X-Principal` with real SSO identity.
4. Review activity anytime via `GET /api/audit` / the Audit page.
