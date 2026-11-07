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
2026-11-07 field step3-R1: board sorts the follow-up queue by provisional_tier (P1 first, missing last; group takes its highest) then logic.within_tier_sort_key; follow-up card shows the provisional tier; tests in test_board.py
2026-11-07 field step3-A4: store persists rejected events (outbox rejected_at/reject_error, added to existing SQLite files on open), pending_outbox and backlog skip them, sync worker marks them and never resends, /sync/status shows "rejected"; tests in test_store.py, test_sync.py
2026-11-07 field step3-A5: one DEFAULT_DEVICE_ID = "laptop-dev-01" in pipeline.py, used by PipelineConfig and sync.worker_from_env; test in test_sync.py
2026-11-07 field step3-R1/A2: fake engine and test fixtures emit provisional_tier; heartbeat rules_version comes from the newest local decided event (EventStore.latest_rules_version), falling back to RULES_VERSION then the placeholder; tests in test_sync.py
2026-11-07 field #173 F18: board intake panel (_intake.html: single, paste, CSV forms; fetch script with Processing/Added/alert states, board fragment refresh); tests/test_board_intake.py
2026-11-07 field #174 F27: rescue_field/loadtest.py surge load test (real engine, optional sync drain, JSON timings); tests/test_loadtest.py
2026-11-07 field PR181: test_follow_up_group_takes_highest_provisional_tier gives the duplicate row a null provisional_tier (contract)
2026-11-07 field #154 F16: root docker-compose.yml (db+cloud+field), .env.example, RUNBOOK 'Run locally'; verified POST /requests syncs to the cloud db; cloud prod.exs force_ssl now excludes host 'cloud'
2026-11-07 field #155 F17: scripts/smoke.sh (health, 3 synced, cloud stop, 5 queued in outbox, restart, 5 arrive <=90s no dupes; --no-docker); verified PASS on every step against the compose stack
2026-11-07 field #175 F26: field/fly.toml (placeholder app rescue-field-demo, /data volume, /sync/status check), RUNBOOK 'Deploy hosted field demo to Fly', tests/test_fly_toml.py
2026-11-07 field #176 F28: root README (what it is, mermaid architecture, run it, tests, demo walkthrough, eval placeholder)
2026-11-07 field #182 F24a: KeywordExtractor (stub:keyword-extractor) with graded confidences; FixtureExtractor delegates non-fixture text to it
2026-11-07 field #183 F24b: tests/test_seed.py seeds 200 rows (seed 7) through the real fallback engine; counts from decided events: outcome prioritized 103 / abstain 83 / duplicate 14; decided tier (prioritized+duplicate only) P1 62 / P2 49 / P3 6; abstain_reason no_usable_location 61 / low_confidence_request 22. KeywordExtractor cannot reach a decided P3 (every recognised request is request_kind rescue -> >=P2), so seed.py adds rare dry evacuation reports using contract fixture texts C08/C24 (FixtureExtractor -> R-P3-01) and repeat reports reuse the first report phone so they dedupe
- 2026-11-07 field #184 F19: Simulate offline button (DEMO_MODE), badge role=status/aria-live, 'Sync status unavailable' on fetch error, 'N queued' text, no-worker badge reads store backlog
2026-11-07 field #185 F20: per-row status form (assigned+crew, escalated+reason, rescued+count) with aria-labels, board.py allowed_statuses from status.ORDER/RANK, delegated submit handler + #status-error alert, 4 tests
2026-11-07 field #186 F21: raised-tier rows show 'Needs confirmation' + question; build_board returns not_requests (newest first) rendered in a closed 'Not a request (N)' details; 2 tests
2026-11-07 field #187 F22: board 18px base/1rem meta/90rem width, AA colour pairs (P2 heading #8a4b00), :focus-visible outlines, skip link + header/main landmarks, tier sections aria-labelledby, tier label text on rows; 6 tests in test_board.py
2026-11-07 field #209 F29: seed.py --export-text/--export-csv (store untouched), committed field/demo/seed_requests.{txt,csv} + README for count 200 seed 7, 3 tests incl. bulk intake of both files
2026-11-07 field #210 F30: DEMO.md demo-day runbook (pre-flight, reset, challenges, fallbacks) + test_demo_challenges.py (6 cases via real fallback engine with input spy)
2026-11-07 field #218 F31: docker-compose field service sets DEMO_MODE=1; test_compose.py checks it as text
2026-11-07 field #180 F23: Dockerfile pins python:3.12-slim-trixie, ships only the frozen Mercury build from engine/dist, ENGINE_CMD/RULES_VERSION set from ENGINE_BUILD; 3 static tests
2026-11-07 field R15: thresholds read from contracts/fixtures/thresholds.json; app refuses to start when its config differs from the frozen build (rescue_field.freeze); Dockerfile ENGINE_BUILD → +bin:2a2fac7006832dd7
2026-11-07 field F33+F34+F35: keyword extractor fixes (A14 house-number/"last year" stale false positives, A15 SMS request words + firm hazard = request, A16 medical_device_no_power + acute medical as injury, A18 explicit request conf 0.9 + address skips counts/prepositions; seed low_confidence_request 22->0), board shows a duplicate's own question with hoax/confirm label (A17), offline button label from paused state (A19), DEMO.md reset/engine notes (A20)
2026-11-07 field #240 review follow-ups: year stale only when it describes the post, curly apostrophe, hoax marker caps explicit request below 0.85, extractor reads streets/markers like PipelineConfig, seed/loadtest run freeze.check, Q_HOAX contract test
2026-11-07 field #188 F25a: vendored trainer inference code into rescue_field/tagger (@c287541), field/requirements-model.txt, slm_hf.py rewritten (folder resolution, extractor_factory), torch-free tests
2026-11-07 field #232 F25b: lifespan picks HFExtractor when HF_REPO+HF_REVISION set (fallback to FixtureExtractor with one log line), model_version in board header/heartbeat; test_extractor_select.py
2026-11-07 field #233 F32: Dockerfile bakes pinned model (CPU torch, build-secret HF_TOKEN) only when HF_REPO+HF_REVISION build args are set; fly.toml/RUNBOOK docs; 4 static tests
2026-11-07 field #243 F36: fly.toml 2gb/2cpu VM + 60s grace, compose passes HF_REPO/HF_REVISION build args, RUNBOOK note, tests
2026-11-07 field #244 F37: select_extractor records extractor_info (ERROR log on fallback), /sync/status extractor object incl. calibrated, board alert banner, infer.py calibrated flag + ERROR when calibration.json missing; tests
2026-11-07 field #243 follow-up: field/fly.toml VM memory 2gb -> 4gb (measured ~630 MB with CPU torch and a tiny checkpoint before mmBERT-base's ~1.3 GB weights); test and RUNBOOK updated
2026-11-07 field #245 F38: HFExtractor builds model_version with rescue_shared.versions.model_version before resolving the checkpoint, so HF_REPO without an org fails in __init__ and select_extractor falls back; tests (incl. real-env select_extractor_with_info check)
2026-11-07 field #246 F39: location.confidence = min(type head, span confidence) via torch-free tagger/assemble.py (used by infer.py); DECISIONS.md bullet; tests
2026-11-07 field #254 F40: header lists local edits in vendored tagger/infer.py; torch-free AST guard test (test_tagger_vendored.py)
2026-11-07 field edge cases: sync worker backs off on any cycle error (a malformed 200 reply killed the thread), resume() cuts a running backoff short, tagger load drops calibration temperatures that are not finite or > 0 (T=0 made every text not_a_request); tests in test_sync.py and test_tagger_vendored.py
2026-11-07 field #104 interim model pin: fly.toml [build.args] HF_REPO/HF_REVISION = paawandesai/rescue-tagger@0588ed4 (laya-s13), DECISIONS note, pin test
2026-11-07 field #104 pin update: fly.toml HF_REVISION -> paawandesai/rescue-tagger@7279106 (v3), RUNBOOK/DECISIONS
2026-11-07 field review PR-1 intake hardening: NUL/lone surrogates cleaned from raw_text, contact and status text (sanitize.py); contact <= 200 chars; body cap 64 KB (bulk 8 MB) -> 413; rescued_count whole, <= 100000; tagger durations _number/clamp (local change); extractor error falls back to keyword extractor; store/engine JSON allow_nan=False; İ landmark slicing; sync worker quarantines unencodable events and bisects on 413 (H-02, O-01, F-02, F-03, O-06, L-05, M-05, L-04); tests in test_intake_hardening.py
2026-11-07 field fix CI on main: EventStore serializes its own sqlite connection with an internal RLock (a sync worker built without the app lock raced request threads; outbox_count got a None row in test_sync_status); regression test in test_store.py
2026-11-07 field #273 bulk intake validates each row's engine input (schema + non-finite floats) before the engine runs; EngineInputError -> 422 invalid_input with the 1-based row; tests in test_intake_hardening.py
