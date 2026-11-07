# 03 · Data and interfaces

## Event envelope (every event, field and cloud)

```json
{
  "event_id": "01JC8Z...ULID",
  "request_id": "01JC8Y...ULID",
  "type": "received | extracted | decided | assigned | rescued | corrected | escalated | closed_duplicate",
  "device_id": "field-03",
  "occurred_at": "2026-11-07T14:02:11Z",
  "rules_version": "rules@a1b2c3d+bin:9f8e...",
  "model_version": "hf:org/rescue-tagger@4d5e6f7",
  "payload": { }
}
```

## `received` payload

```json
{ "channel": "sms | social | form | call_note | csv", "raw_text": "...", "lang_hint": "en", "contact": "+1..." }
```

Raw text stays in the field DB and cloud records. It is **never** passed to Mercury.

## SLM output contract (`extracted` payload)

```json
{
  "location": { "text": "behind the Kroger on Westheimer", "type": "landmark | address | coords | none", "confidence": 0.81 },
  "people_count": { "value": 3, "confidence": 0.93 },
  "vulnerable": [ { "kind": "child | elderly | disabled | medical | pregnant", "confidence": 0.88 } ],
  "hazards": [ { "kind": "rising_water | on_roof | injury | medical_device_no_power | fire | trapped", "confidence": 0.90 } ],
  "time_ref": { "text": "since last night", "is_stale_post": false, "confidence": 0.70 },
  "is_request": { "value": true, "confidence": 0.97 },
  "spans": [ { "start": 0, "end": 42, "label": "LOCATION" } ]
}
```

`is_request` catches non-requests (news, offers to help, old posts resurfacing).

## Mercury engine interface

**Input** (stdin, JSON): the `extracted` payload plus context (known duplicates candidates, local street list hits, thresholds from config).

**Output** (stdout, JSON):

```json
{
  "outcome": "prioritized | abstain | not_a_request | duplicate",
  "priority_tier": "P1 | P2 | P3 | null",
  "rule_id": "R-P1-03",
  "duplicate_of": null,
  "abstain_reason": "no_usable_location | conflicting_counts | low_confidence_hazard | possible_hoax | null",
  "follow_up_question": "Can you share the street address or the nearest cross streets?",
  "rules_version": "rules@a1b2c3d+bin:9f8e..."
}
```

Mercury must be declared `det` for this mapping: exactly one outcome per input.

## Field API (FastAPI)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | HTMX board: ranked requests, follow-up queue, sync status |
| POST | `/requests` | Submit one request (form or paste) |
| POST | `/requests/bulk` | CSV or multi-line paste |
| POST | `/requests/{id}/status` | assigned / rescued / escalated |
| GET | `/sync/status` | Outbox backlog, last successful sync, online/offline |
| POST | `/sync/offline-toggle` | Hosted demo only: pause/resume the sync worker |

## Cloud API (FastAPI)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Ping target for sync workers |
| POST | `/sync/heartbeat` | Device ID, outbox backlog, versions (device auth) |
| POST | `/sync/events` | Batch of events; returns acked event IDs |
| GET | `/sync/down?since=...` | Other sites' assigned/rescued, approved model/rules versions |
| GET | `/models/{version}` | Download an approved model bundle |
| GET | `/` | Cloud UI (fleet, activity, records, corrections, evals) |
| GET | `/sse` | Live updates for HTMX |
| POST | `/corrections` | Coordinator correction |

## Device auth

Each laptop gets a device ID and secret at registration (stored in the device registry). Requests are signed or carry a bearer token over HTTPS. Keep it simple for 24h; mention mTLS or signed requests as the production path.
