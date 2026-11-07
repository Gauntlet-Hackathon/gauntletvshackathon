# Progress: cloud lane

One line per merged change: date, lane, issue, what changed.

2026-11-07 cloud #27 C1: Events context (append_batch idempotent, read_since), Event schema + payload validators, create_events migration, tests against contracts/examples
2026-11-07 cloud #28 C2: /health public, DeviceAuth plug (config/devices.exs tokens), POST /sync/heartbeat upsert + PubSub "fleet" broadcast, heartbeats migration, tests
2026-11-07 cloud #34 C3: POST /sync/events (per-event validation, device_mismatch 403, idempotent acks, PubSub "events" broadcast of newly stored events), Events.append_batch also returns inserted
2026-11-07 cloud #35 C4: requests read model (Projection.apply_events/rebuild from events only, forward-only status where corrected never moves it, corrections applied to extracted, tier/rule/versions, cross-site duplicate_group), Normalize port passing contracts/vectors, sync/events wired to the projection, tests
2026-11-07 cloud #39 C5: /fleet LiveView (active<=5min, stale, offline>30min, configurable thresholds + injectable clock), PubSub "fleet" updates + re-age timer, LiveViewTest
2026-11-07 cloud #47 C6: /records LiveView (search by text/tier/status, detail with rule_id, rules/model versions, full event history) and /activity LiveView (per device: requests logged, decisions, abstains, escalations with laptop timestamps, windowed to the device's latest sync: new events.synced_at column stamped by append_batch, consecutive batches <= 300 s apart count as one sync), RescueCloud.Records context, tests
