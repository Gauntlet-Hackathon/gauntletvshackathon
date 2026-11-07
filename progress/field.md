# Progress: field lane

One line per merged change: date, lane, issue, what changed.

2026-11-07 field #31 F1: SQLite WAL event store with transactional outbox (field_app/store.py)
2026-11-07 field #32 F2: engine subprocess client (field_app/engine_client.py) with fake_engine tests
2026-11-07 field #33 F3: Extractor protocol + FixtureExtractor stub (field_app/slm.py)
2026-11-07 field #38 F4: intake pipeline process_request (rescue_field/pipeline.py): received/extracted/decided in one store transaction, context via rescue_shared.logic
2026-11-07 field #46 F5: intake API (rescue_field/api.py): POST /requests (JSON/form) and /requests/bulk (CSV upload, text/csv, paste) with one batch engine call, in-batch duplicate candidates, 422 + nothing written on bad input
