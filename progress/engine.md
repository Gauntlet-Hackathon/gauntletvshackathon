# Progress: engine lane

One line per merged change: date, lane, issue, what changed.

2026-11-07 engine #29 E1: fallback engine steps 1-2 (request + location checks) with stdin/stdout CLI and fixture tests
2026-11-07 engine #30 EV1: evals/metrics.py (P1 misses/false alarms + sweep, top-k, field accuracy, dup pairwise F1, abstain, ECE/bins, consistency) with hand-computed toy tests
2026-11-07 engine #36 E2: step 3 priority tiers (R-P1-01..R-P3-04) + uncertainty escalation (confirm question / R-ABS-HAZ); moved fallback to engine/rescue_engine/fallback per CLAUDE.md path mapping
2026-11-07 engine #37 EV2: evals/rescue_evals/loaders.py JSONL case schema + loader (set E needs --open-sealed, needs_review files refused) + sets/README.md
2026-11-07 engine #43 EV5: drafted 101 hard (set C) + 68 abstain (set D) candidate rows in evals/drafts/, all needs_review:true, hand-labelled against 04 (never run through engine/model); tests in evals/tests/test_drafts.py
2026-11-07 engine #48 E3: steps 4-5 (hoax abstain, duplicate link to earliest + group-highest tier, conflicting_counts) in fallback/dedupe.py + engine.py; tests in engine/tests/test_steps_4_5.py
2026-11-07 engine #49 EV4: evals/rescue_evals/runner.py two-point scoring (extraction + decision), results <system>__<set>.json with failing ids and latency; tests in evals/tests/test_runner.py
2026-11-07 engine #41 E5: engine/rescue_engine/build.py writes immutable engine/dist/<rules_version>/ (copied sources + manifest.json), refuses overwrite; tests in engine/tests/test_build.py
2026-11-07 engine #51 EV3: evals/rescue_evals/baselines/regex_baseline.py regex/keyword extraction + fallback engine, registered as runner system regex-baseline; tests in evals/tests/test_regex_baseline.py
2026-11-07 engine #57 EV6: evals/rescue_evals/baselines/stored.py replays stored JSONL outputs through the runner (system 'stored', env STORED_OUTPUTS), counts missing/malformed rows; tests in evals/tests/test_stored.py
2026-11-07 engine #63 EV7: evals/rescue_evals/report.py builds results table (regex/Opus/generic SLM/ours, sealed E separate) and writes summary.json + TABLE.md; tests in evals/tests/test_report.py
2026-11-07 engine #59 E4: engine/tests/test_properties.py grid (3.6k inputs) exhaustiveness, 10x determinism, CLI batch parity, prompt-injection invariance
2026-11-07 engine #60 EV8: evals/rescue_evals/calibration.py (reliability PNG + ECE per field, numpy/zlib PNG) and consistency.py (N reruns, % cases changed); tests in evals/tests/test_calibration.py
2026-11-07 engine #54 E6: engine/rescue_engine/replay_diff.py re-decides the log's decided events with two engine versions, emits per-request tier/outcome diff as JSON + markdown; tests in engine/tests/test_replay_diff.py
2026-11-07 engine #61 EV9: evals/rescue_evals/systems/ours.py ('ours' system = imported HFExtractor + engine CLI via field engine_client, records model_version/rules_version); tests in evals/tests/test_ours.py (mocked extractor; real model slow)
2026-11-07 engine #133 E8: fallback CLI catches all errors (exit 1, one stderr line, no traceback); engine/tests/test_malformed_input.py table of malformed/edge inputs
2026-11-07 engine #134 EV10: evals/rescue_evals/stats.py (Wilson 95% CI, detection floor, paired improved/worsened/unchanged + sign test); runner records intervals, judge_id/version, refuse_sealed guard; tests in evals/tests/test_stats.py
2026-11-07 engine #135 EV11: evals/rescue_evals/error_analysis.py writes evals/reports/error_analysis.md (dev split only, failures grouped by differing field, counts + 3 examples); tests in evals/tests/test_error_analysis.py
2026-11-07 engine #24 W0-5: Mercury spike GO; engine/mercury/hello.m and mercury-spike CI job (pinned 22.01.8 .debs, mercury_json), decision in docs/DECISIONS.md
2026-11-07 engine #66 Mercury install moved to .github/actions/install-mercury (this repo's release mirror), added to the engine agent job
2026-11-07 engine #66 Mercury install added to the python CI job so pytest engine can use mmc
2026-11-07 engine #66 E7: Mercury port (engine/mercury rules.m det + exhaustive switches, json_io.m, mercury_engine.m CLI, Makefile with --halt-at-warn); build.py --kind mercury publishes bin/mercury_engine; engine/tests/test_parity.py (fixtures, E4 grid, 3k random inputs, error shape, missing-arm compile error)
2026-11-07 engine #156 EV12: python -m rescue_evals.baselines --system opus|slm --split dev|test [--mock] [--limit N]; writes evals/results/<system>/<model-id>.jsonl, scores via stored harness; tests in evals/tests/test_baselines_llm.py
