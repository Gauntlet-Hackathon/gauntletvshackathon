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
2026-11-07 engine step3-A7: evals runner derives the follow-up field from abstain_reason (runner.FOLLOW_UP_FIELD / follow_up_field) instead of reading follow_up_field; tests in evals/tests/test_runner.py
2026-11-07 engine step3-A3: runner writes metrics["calibration.ece"] (pooled extracted-field ECE from calibration.case_samples/pooled_ece; failures = wrong at confidence >= 0.75) into each result and so summary.json results; report treats it as lower-is-better; tests in evals/tests/test_runner.py
2026-11-07 engine step-3 review: R1 provisional_tier on every abstain (schema, examples, fixtures); R2/R3/R4/R5/R8/R9/R11/R13 rule changes and R6/R10 notes in contracts/engine_rules.md, fixture C25; A2 build.py stamps the real rules_version into dist (python rules.py line, mercury version slot); A9 fallback rejects unknown enums; Python and Mercury in parity (engine/tests/test_review_fixes.py, test_parity.py)
2026-11-07 engine step3-R1: replay_diff also diffs provisional_tier (provisional_tier_changed per row, counted as changed, md column); evals test that baseline engine outputs carry provisional_tier; tests in engine/tests/test_replay_diff.py, evals/tests/test_regex_baseline.py
2026-11-07 engine #171 EV13: report.py columns accept system aliases (opus/slm), main block gains running.mean_ms_per_request, running.seconds_per_1000_requests, running.works_offline, consistency.pct_cases_changed; tests in evals/tests/test_report.py
2026-11-07 engine #172 EV14: baseline rows record input/output_tokens + latency_ms; result docs get baseline_latency and (with *_USD_PER_MTOK_* env) running.cost_per_1000_usd; report.py adds the cost row and fills Opus/SLM latency from baseline_latency; tests in test_baselines_llm.py and test_report.py
2026-11-07 engine PR181-B1: a duplicate also keeps its own follow-up question when its weak_tier is above the group tier (weak P1 on a P2 duplicate keeps Q_HAZARD); Python check_duplicates, rules.m, engine_rules.md step 5, DECISIONS R9; tests and parity inputs in test_review_fixes.py / test_parity.py
2026-11-07 engine R15 (H12 config freeze): build copies thresholds.json/hoax_markers.txt/streets.txt into config/, writes config_hash+artifact_hash, bin_hash covers both; --rebuild-from; frozen build republished as +bin:2a2fac7006832dd7; replay_diff reports config_drift
2026-11-07 engine runner CLI: `python -m rescue_evals.runner --system ours|stored|regex-baseline` now finds those systems (they registered into a second copy of the runner module); test in evals/tests/test_runner_cli.py
2026-11-07 engine DECISIONS: record model delivery (baked into field image from an approved, pinned revision; laptop pull from the cloud registry not built) against plan 02/06
2026-11-07 engine evals runner/consistency: engine context now gets street_list_hits + hoax_marker_hits from contracts/fixtures like the field pipeline (was always empty/0); DEMO.md notes how the challenge rows look with the trained model
2026-11-07 engine evals: sets C (101) and D (68) from Paawan's review of the drafts, calls aligned with the frozen engine -> evals/sets/c_hard.jsonl, d_abstain.jsonl; 8 changes from the drafts with reasons in evals/sets/REVIEW.md
