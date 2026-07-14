# 16 · Troubleshooting & FAQ

## Stack won't start / services unhealthy

```bash
docker compose ps            # what's up and what's healthy
docker compose logs -f backend   # (or db / grafana / frontend)
```

- **Port already in use** (3000/3001/8000/5432): another process holds the port.
  Stop it, or remap the host port in `docker-compose.yml` (`"3002:3000"`).
- **Backend unhealthy at boot:** it waits for `db` to be healthy first. Give the
  DB a few seconds; check `docker compose logs db` for init errors.
- **First `make up` is slow:** it builds the backend and frontend images. Normal.

## The UI loads but pages are empty

- You started the stack but haven't loaded data. Run a seed — see
  [15 · Demo Data](15-demo-data.md).
- **Compliance / cross-cloud views empty after `make seed`:** expected. `make seed`
  is Azure-cost-only and creates no policies/accounts. Use the multi-cloud demo
  seed (Option B) instead.
- **Schema missing / DB errors:** run `make initdb`.

## `ModuleNotFoundError: No module named 'cloudwarden'` when running a script

Running a script by path (`python /tmp/foo.py`) puts the script's directory on
`sys.path`, not the container workdir `/app`. Pass `PYTHONPATH=/app`:

```bash
docker compose exec -e PYTHONPATH=/app backend python /tmp/seed_demo.py
```

## "skipping malformed activity record" warning during a run

Benign — one fixture Activity Log record is intentionally malformed; the pipeline
logs and skips it. Not an error.

## Grafana

- **Login:** anonymous viewing is on (read-only). To edit, log in as `admin` /
  `admin` (or `GF_SECURITY_ADMIN_PASSWORD`).
- **Panels empty:** no data seeded yet, or the `provider` template variable is
  scoped to a cloud with no rows — set it to **All**.
- **Azure Monitor datasource errors:** expected in mock mode (no `AZURE_*` creds).
  The Postgres-backed panels still work.

## API returns 401/403 after enabling RBAC

- `RBAC_ENABLED=true` requires a principal with the endpoint's permission. Send
  `X-Principal: <you>` and make sure you're bound to a role
  (`GET /api/authz/me` shows your resolved permissions).
- Locked out? Set `RBAC_BOOTSTRAP_ADMIN=<you>` and re-seed (`make initdb`) to
  auto-bind yourself to `admin`. See [11 · Security](11-security-rbac-sso.md).
- **403 on a policy you can see elsewhere:** RBAC scopes policies to teams; you're
  hitting another team's policy without admin.

## Live mode (`FINOPS_MOCK=0`) errors

- **Azure auth failures:** verify the read SP has *Reader + Cost Management Reader
  + Monitoring Reader* and that `AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET` are set.
- **AWS onboarding 400:** STS `get_caller_identity` rejected the credentials —
  check keys/role and region.
- **GCP onboarding 400:** Resource Manager `get_project` rejected — check the
  service-account JSON / ADC and that the SA can read the project.
- **GCP policy execution fails but onboarding worked:** live GCP policy eval needs
  the optional `c7n-gcp` extra; onboarding/ingestion don't.
- **No AI summary / AI errors:** set `ANTHROPIC_API_KEY` (or `AI_API_KEY` +
  `AI_BASE_URL` for a local model). Mock mode needs none.

## Remediation didn't actually change anything

By design. Remediation is dry-run unless **all** of: `REMEDIATION_ENABLED=true`,
the resource's group is in `ALLOWED_RESOURCE_GROUPS`, the resource lacks the
`EXCLUDE_TAG`, the action type is permitted by `ALLOWED_ACTIONS`, and the write SP
is configured. See [09 · Remediation](09-remediation.md).

## Events return 202 and nothing happens

`EVENT_MODE_ENABLED=false` accepts deliveries but stores/triggers nothing. Set it
`true`. A **403** means the shared-key check failed — send `x-events-key` (or
`?key=`) matching `AZURE_EVENTGRID_SHARED_KEY`.

## Reset everything

```bash
docker compose down -v       # removes pgdata, appdata, grafana-data
make up && make initdb       # fresh, then re-seed
```

## Useful one-liners

```bash
docker compose exec db psql -U finops -d finops -c '\dt'      # list tables
curl -s localhost:8000/health | jq                            # API health
curl -s localhost:8000/api/governance/posture | jq '.by_provider'
docker compose exec -T db pg_dump -U finops finops > backup.sql   # backup
```

## FAQ

**Do I need cloud credentials to try it?** No — `FINOPS_MOCK=1` (the default) runs
everything on fixtures.

**Why is the web UI on 3001, not 3000?** Grafana owns 3000; the frontend container
listens on 3000 internally but is published on host **3001**.

**Can it run on a schedule?** Yes — switch the `backend` command to `["scheduler"]`
and tune the `*_INTERVAL_SECONDS` keys. See
[04 · Operating the Stack](04-operating-the-stack.md#one-shot-vs-scheduled).

**Is cost analysis multi-cloud?** Not yet — AWS/GCP participate fully in AssetDB
and governance; the cost/rightsizing pipeline is Azure-centric today.
