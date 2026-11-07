# Sync API (field laptop ↔ cloud)

Implemented by the Phoenix cloud (`/health`, `/sync/*`), called by the field sync worker. Request and response bodies are JSON and are defined by the
schemas in `contracts/sync/` and `contracts/event.schema.json`; each message has a valid and an invalid file in `contracts/examples/`
(`sync-*.valid.json`, `sync-*.invalid.json`). Timestamps are UTC with a trailing `Z`, seconds precision.

## Device auth
Every `/sync/*` request carries `Authorization: Bearer <token>`. The cloud keeps one token per device in `cloud/config/devices.exs` (device id → token) and derives the device from the token.
`GET /health` needs no auth. Production would use mTLS or signed requests; a bearer token over HTTPS is enough for 24 hours.
- Missing, malformed or unknown token → `401` `{"error": "unauthorized"}`.
- The `device_id` in a body, or the `device_id` of any event in it, differs from the token's device → `403` `{"error": "device_mismatch"}` (see `sync/error_response.schema.json`).

## `GET /health`
Ping target for the sync worker. No auth, no body. `200` → `{"status": "ok", "time": "2026-11-07T14:02:11Z"}` (`sync/health_response`).

## `POST /sync/heartbeat`
Sent after a successful ping, before draining the outbox. Request (`sync/heartbeat_request`):
`{"device_id", "sent_at", "outbox_backlog": int, "rules_version", "model_version"}`.
`200` → `{"ok": true, "server_time": "…Z"}` (`sync/heartbeat_response`). The cloud computes active / stale / offline from the time it last received a heartbeat (stale > 5 min, offline > 30 min).

## `POST /sync/events`
A batch of events, 1–200, oldest first. Request (`sync/events_request`): `{"device_id", "events": [<event envelope>, …]}`.
Response `200` (`sync/events_response`): `{"acked": [event_id, …], "rejected": [{"event_id", "error"}, …]}`.
- **Idempotent:** the cloud dedupes on `event_id`. An event it already has is **acked**, not rejected, so retries and double sends are harmless.
- `acked` events may be deleted from the outbox. `rejected` events failed validation (`error` is a short machine string such as `invalid_payload`); they must not be retried unchanged.
- An event that is neither acked nor rejected (for example because the request failed) stays in the outbox.
- A body that is not valid JSON, or has 0 or more than 200 events → `400` `{"error": "bad_request"}`.
- Cloud-side per-event validation is the `event.schema.json` envelope plus the payload schema for its `type`; the stored order is arrival order.

## `GET /sync/down?since=<n>`
Down-sync for the device (cut line 2). `since` is the cloud sequence number last seen (integer, default `0`). Response `200` (`sync/down_response`):
`{"next_since": int, "events": [<event envelope>, …], "approved": {"rules_version": string|null, "model_version": string|null}}`.
- `events`: `assigned` and `rescued` events from **other** devices with a cloud sequence number greater than `since`, oldest first (so two crews don't go to the same roof). The device's own events are not echoed.
- `approved`: the newest rules and model versions that passed the promotion gate (null if none yet). The device pulls a bundle only when its version differs.
- Persist `next_since` and send it on the next call.

## Errors
All error bodies are `{"error": "<snake_case_code>", "detail": "<optional text>"}` (`sync/error_response`). Codes used: `unauthorized` (401), `device_mismatch` (403), `bad_request` (400), `server_error` (5xx). The field worker backs off exponentially with jitter on connection errors and 5xx.

## Not in Wave 0
`GET /models/{version}` (bundle download, C10), `POST /corrections`, and the LiveView/dashboard routes are defined by their own tickets.
