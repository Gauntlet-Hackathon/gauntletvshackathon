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
