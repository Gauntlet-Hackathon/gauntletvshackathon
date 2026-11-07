# Progress: field lane

One line per merged change: date, lane, issue, what changed.

2026-11-07 field #31 F1: SQLite WAL event store with transactional outbox (field_app/store.py)
2026-11-07 field #32 F2: engine subprocess client (field_app/engine_client.py) with fake_engine tests
2026-11-07 field #33 F3: Extractor protocol + FixtureExtractor stub (field_app/slm.py)
2026-11-07 field #38 F4: intake pipeline process_request (rescue_field/pipeline.py): received/extracted/decided in one store transaction, context via rescue_shared.logic
2026-11-07 field #46 F5: intake API (rescue_field/api.py): POST /requests (JSON/form) and /requests/bulk (CSV upload, text/csv, paste); bulk decides in engine batch mode (one call per duplicate wave, in-batch candidates carry tiers), 422 writes nothing; store at $FIELD_DATA_DIR/field.db; sync-worker lifespan hook
2026-11-07 field #40 F8: sync worker (rescue_field/sync.py): /health ping, jittered backoff 2s..300s, heartbeat, batches of 200, delete-on-ack only; /sync/status + /sync/offline-toggle; started in app lifespan when CLOUD_URL set
2026-11-07 field #56 F6: ranked board GET / (rescue_field/board.py + templates): P1-P3 by within_tier_sort_key, abstain follow-up queue, duplicates collapsed under earliest at group's top tier, rule/rules/model versions and expandable fields; HX-Request returns fragment
2026-11-07 field #62 F7: status updates (rescue_field/status.py): POST /requests/{id}/status assigned/escalated/rescued, forward-only (409 on backward/repeat), appends event; board rows show status
2026-11-07 field fix-main: CI red on cloud SyncEventsTest (flaky): refute_receive on the global "events" PubSub topic caught a broadcast from async live_updates_test; scoped the refute to the batch's own event_ids
2026-11-07 field #65 F9: live sync badge (GET /sync/badge fragment + _sync_badge.html, polled by board); status/offline-toggle/lifespan wiring already from F8; tests/test_sync_status.py
2026-11-07 field #68 F10: down-sync (rescue_field/downsync.py): GET /sync/down polled after each sync cycle, cursor persisted, forward-only apply without outbox echo, approved rules/model versions recorded with previous kept; store meta table
