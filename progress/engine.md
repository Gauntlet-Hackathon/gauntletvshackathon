# Progress: engine lane

One line per merged change: date, lane, issue, what changed.

2026-11-07 engine #29 E1: fallback engine steps 1-2 (request + location checks) with stdin/stdout CLI and fixture tests
2026-11-07 engine #30 EV1: evals/metrics.py (P1 misses/false alarms + sweep, top-k, field accuracy, dup pairwise F1, abstain, ECE/bins, consistency) with hand-computed toy tests
2026-11-07 engine #36 E2: step 3 priority tiers (R-P1-01..R-P3-04) + uncertainty escalation (confirm question / R-ABS-HAZ); moved fallback to engine/rescue_engine/fallback per CLAUDE.md path mapping
2026-11-07 engine #37 EV2: evals/rescue_evals/loaders.py JSONL case schema + loader (set E needs --open-sealed, needs_review files refused) + sets/README.md
2026-11-07 engine #43 EV5: drafted 101 hard (set C) + 68 abstain (set D) candidate rows in evals/drafts/, all needs_review:true, hand-labelled against 04 (never run through engine/model); tests in evals/tests/test_drafts.py
