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
2026-11-07 field #68 F10: down-sync (rescue_field/downsync.py): worker polls GET /sync/down?since=<cursor> after upload, stores other devices' valid assigned/rescued events without outbox rows, forward-only (skips repeats/backward), cursor + approved rules/model versions (previous kept for rollback) in a store meta table; malformed events skipped, down-sync errors back off
2026-11-07 field #42 F11: HFExtractor (rescue_field/slm_hf.py): pinned-SHA offline load, JSON output mapped+schema-validated, model_version hf:<repo>@<sha>; mocked unit tests + slow real-model test
2026-11-07 field #127 F12: ENGINE_CMD path-safe: default engine cmd built as argv list, tests quote with shlex.join, new test with a space in the path
2026-11-07 field #128 F13: demo seed (rescue_field/seed.py): templated messy requests via process_batch, deterministic per --seed; tests/test_seed.py
2026-11-07 field #129 F14: tests/test_sync_integration.py: contract-faithful in-process fake cloud (auth, schema validation, dedupe); offline queueing, full drain, 500 retry/lost-ack idempotency, 401 keeps rows, ordering
2026-11-07 field #152 F15: field/Dockerfile (python:3.12-slim, constraints, PYTHONPATH, uvicorn on $PORT, fallback engine) + root .dockerignore; verified image builds, GET / 200, POST /requests returns fallback-engine decision
2026-11-07 field #152 F15 re-verify: image rebuilt, GET / 200, POST /requests got fallback-engine decision; added tests/test_dockerfile.py static checks for the Dockerfile/.dockerignore contract
2026-11-07 field #173 F18: board intake panel (_intake.html: single, paste, CSV forms; fetch script with Processing/Added/alert states, board fragment refresh); tests/test_board_intake.py
