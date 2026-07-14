# 12 · Real-Time Enforcement (Event Mode)

Beyond scheduled (pull) runs, the platform can enforce policies **reactively** the
moment a resource changes, driven by **Azure Event Grid**. A change event upserts
the resource into AssetDB and triggers any event-mode policies that match it.

## Configuration

```
EVENT_MODE_ENABLED=true          # master switch (default true)
AZURE_EVENTGRID_SHARED_KEY=      # optional shared secret authenticating deliveries
```

- `EVENT_MODE_ENABLED=false` → deliveries are accepted (**202**) but nothing is
  stored or triggered. This lets you pause enforcement **without** tearing down the
  Event Grid subscription.
- `AZURE_EVENTGRID_SHARED_KEY` empty → all deliveries accepted (local/mock dev).
  When set, a delivery must present the key via the `x-events-key` header or a
  `?key=` query param, or it's rejected with **403** (constant-time compare).

## The webhook

```
POST /api/events/azure
```

- **Body:** a JSON array of Event Grid events (or a single event object).
- **Handshake:** Event Grid's one-time subscription-validation handshake is
  answered automatically.
- **Responses:** `200 {received, processed}` on success · `202` if event mode is
  off · `403` on key mismatch · `400` on bad JSON.

## What happens on an event

For each event in the payload:

1. **Normalize** — only resource write/action/delete success events are handled;
   others are silently skipped. Extracts resource id, subscription, resource type
   (from the ARM operation), actor, and status.
2. **AssetDB update** — upsert the asset (refresh `last_seen`; delete events mark
   `state=deleted`) and append an asset-change event to its history.
3. **Policy trigger** — select enabled **event-mode** policies whose resource type
   matches the event, run each (dry-run by default), and record a `PolicyExecution`
   (`mode=event`) with its matches.

Event-mode policies are matched by resource type; the engine maps `c7n` short names
(`azure.vm`, `azure.disk`, `azure.storage`, …) to their ARM types
(`microsoft.compute/virtualmachines`, …), case-insensitively.

## Observing events

```bash
GET /api/events?limit=50            # recent deliveries, newest-first
GET /api/events/recent?limit=20&offset=0   # deliveries + the executions they triggered
```

The **Events** page renders this feed (event type, resource, subscription, received
time, triggered-execution status).

## Wiring up Azure Event Grid (live)

1. Ensure the backend's `/api/events/azure` is reachable from Azure (public URL /
   ingress).
2. Set `AZURE_EVENTGRID_SHARED_KEY` and configure the Event Grid subscription to
   send it (header or query param).
3. Create an Event Grid subscription on the resource group / subscription scope,
   webhook endpoint = `https://<host>/api/events/azure`. The validation handshake
   is handled automatically.
4. Author policies for event mode and keep `EVENT_MODE_ENABLED=true`.

In mock mode you can exercise the whole path by POSTing a sample Event Grid payload
to `/api/events/azure` directly.
