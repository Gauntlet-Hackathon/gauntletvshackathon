# Stored eval results

Baselines are run ahead of time and stored here, never called live (docs/plan/05-evals.md).

## Opus with the rubric (`opus/claude-opus-5-5.jsonl`, `opus__B.json`, `opus__C.json`, `opus__D.json`)

- Model `claude-opus-5-5`, run 2026-11-07 on the test splits of the reviewed sets C (61) and D (41), main at 05c16bc.
- Prompt (`rescue_evals.baselines.llm.build_prompt`): the extraction task, the full decision rubric
  (`docs/plan/04-decision-rubric.md`) and the `extracted` JSON Schema. Opus only extracts; the decision is
  made by the same fallback engine as every other system, so the columns are comparable.
- Set B (49 test cases, main at 130f9d8) was run separately with `--sets B` into a scratch dir and its rows appended to the same JSONL, so C/D rows were not overwritten; all 49 valid (135k in / 36k out tokens).
- C/D: 102/102 outputs schema-valid. 281k input / 78k output tokens in total; median 6.5 s per request.
- Rerun (needs an Anthropic key; Set E is refused):
  `ANTHROPIC_API_KEY=... PYTHONPATH=shared:field:engine:evals python -m rescue_evals.baselines --system opus --split test --sets B,C,D`
  (it rewrites the whole `opus/<model>.jsonl` with only the sets you pass)
- Re-score the stored outputs without the network:
  `STORED_OUTPUTS=evals/results/opus/claude-opus-5-5.jsonl PYTHONPATH=shared:field:engine:evals python -m rescue_evals.runner --system stored --sets B,C,D --results-dir /tmp/x`

### Re-scored with the PR-3 runner (2026-11-07)

`opus__{B,C,D}.json` were re-scored from the stored JSONL with `--rescore` (no model calls) after
the review PR-3 scoring fixes (definitions in `evals/README.md`). The tables below are from before
it: their "P1 misses /1000" is per 1,000 requests of any kind, which is now
`decision.p1_misses_per_1000_requests`; the headline key `decision.p1_misses_per_1000` is now per
1,000 urgent (gold P1) requests. The trained models (v3, laya-s13) are in "All columns on the current split" below.

| metric (new runner) | keyword B / C / D | Opus + rubric B / C / D | gold extraction B / C / D |
|---|---|---|---|
| urgent missed per 1,000 urgent (headline) | 762 / 931 / n/a | 0 / 69 / n/a | 0 / 34 / n/a |
| urgent missed per 1,000 requests | 327 / 443 / 0 | 0 / 33 / 0 | 0 / 16 / 0 |
| same, not counting P1-flagged follow-ups | 714 / 828 / n/a | 0 / 0 / n/a | 0 / 34 / n/a |
| Top-20 accuracy | 0.25 / 0.15 / 0.33 | 1.00 / 0.90 / 1.00 | 1.00 / 0.95 / 1.00 |
| duplicate pairwise F1 | 0.00 / 0.00 / n/a | 0.67 / 0.67 / n/a | 0.67 / 0.67 / n/a |
| outcome accuracy | 0.27 / 0.26 / 0.24 | 0.94 / 0.90 / 0.83 | 0.98 / 0.93 / 0.88 |

n/a: the set has no gold P1 (D) or no duplicate pair on either side, so the metric is left out.
Gold extraction stays below 1.0 on duplicates because two test-split groups need a `contact` the
sets do not carry yet (b-dup-4: counts None vs 5; C-dup-2: one person) and three C groups have a
member in another split.

### Set B (golden, n=49)

| metric | keyword | Opus + rubric | gold extraction |
|---|---|---|---|
| outcome accuracy | 0.27 | 0.92 (CI 0.81-0.97) | 0.96 |
| tier accuracy | 0.19 | 0.89 | 1 |
| P1 misses /1000 | 327 | 0 | 0 |
| is_request accuracy | 0.35 | 0.98 | 1 |
| location exact / partial | 0.88 / 0.92 | 0.88 / 0.98 | 1 / 1 |
| people count exact | 0.53 | 0.92 | 1 |
| hazard F1 / vulnerable F1 | 0.77 / 0.74 | 0.90 / 0.84 | 1 / 1 |
| ECE | 0.20 | 0.11 | 0.15 |

Gold tops out at 47/49 on outcome: B-078 and B-082 are duplicates the single-post runner cannot see.
The trained models have not been run on Set B yet.

### Sets C and D

| metric | keyword | v3 (pinned) | laya-s13 | Opus + rubric | gold extraction |
|---|---|---|---|---|---|
| C outcome accuracy | 0.26 | 0.44 | 0.61 | 0.89 (CI 0.78-0.94) | 0.92 |
| C tier accuracy | 0.04 | 0.26 | 0.35 | 0.85 | 0.91 |
| C P1 misses /1000 | 426 | 328 | 311 | 33 | 16 |
| C is_request accuracy | 0.46 | 0.75 | 0.85 | 0.98 | 1 |
| C ECE | 0.28 | 0.19 | 0.13 | 0.08 | 0.17 |
| D outcome accuracy | 0.24 | 0.41 | 0.49 | 0.83 (CI 0.69-0.91) | 0.88 |
| D is_request accuracy | 0.49 | 0.51 | 0.61 | 0.90 | 1 |
| D abstain recall | 0.24 | | | 0.82 | |
| D tier accuracy (n=3) | 0.33 | | | 0.33 | |
| D ECE | 0.28 | | | 0.18 | |

Opus is from the stored files here. Keyword is `python -m rescue_evals.runner --system regex-baseline --sets C,D` on the same commit (not committed: it is deterministic and reruns in seconds). v3, laya-s13 and gold come from the 2026-11-07 model validation on the same sets (not in the repo; the trained-model runs need an HF token).
Offline: Opus does not work offline; keyword and the trained model do.

### Set C split change (2026-11-07, proposed)

C-050, C-057 and C-060 moved from `test` to `dev` (`evals/sets/REVIEW.md`, "Split change"). `opus__C.json` was
re-scored from the stored JSONL with `--rescore` (no model calls); set C now has 58 test cases. Opus + rubric on C:
outcome accuracy 0.90 -> 0.93, tier accuracy 0.86 -> 0.85, urgent missed per 1,000 urgent 69 -> 74 (2 of 27 instead of
2 of 29), per 1,000 requests 33 -> 34, duplicate pairwise F1 0.67 (unchanged), Top-20 0.90 (unchanged). The C rows in
the tables above are from before the change; every column is re-run on the new split in the next section.

### All columns on the current split (2026-11-07, main at d6b1122)

Test splits of B (49), C (58, after the split change) and D (41); same runner and frozen Mercury engine for every
column. Keyword and gold extraction: `python -m rescue_evals.runner --system regex-baseline|gold-extraction --sets B,C,D`.
v3 and laya-s13: `HF_REPO=paawandesai/rescue-tagger HF_REVISION=<sha> HF_MODEL_DIR=<snapshot> ENGINE_CMD=engine/dist/<build>/bin/mercury_engine
python -m rescue_evals.runner --system ours --sets B,C,D` (needs an HF token to download the gated checkpoint; result
files not committed, both checkpoints report as system `ours`). Opus is the stored `opus__*.json`. Pooled = the
promotion-gate pooling (`rescue_evals.report.pool_metrics`) over B+C+D, n=148.

| metric | keyword | v3 (pinned) | laya-s13 | Opus + rubric | gold extraction |
|---|---|---|---|---|---|
| **pooled outcome accuracy** | 0.26 | 0.53 | 0.66 | 0.91 | 0.95 |
| pooled urgent missed per 1,000 urgent (headline) | 896 | 563 | 542 | 42 | 21 |
| pooled same, not counting P1-flagged follow-ups | 813 | 354 | 438 | 0 | 21 |
| pooled Top-20 accuracy | 0.21 | 0.53 | 0.70 | 0.95 | 0.98 |
| pooled is_request accuracy | 0.42 | 0.70 | 0.80 | 0.96 | 1 |
| pooled ECE | 0.26 | 0.19 | 0.14 | 0.12 | 0.23 |
| B outcome accuracy | 0.27 | 0.71 (CI 0.58-0.82) | 0.84 (CI 0.71-0.91) | 0.94 | 0.98 |
| B urgent missed per 1,000 urgent | 762 | 333 | 333 | 0 | 0 |
| B Top-20 / duplicate F1 | 0.25 / 0.00 | 0.70 / 0.67 | 0.80 / 0.67 | 1.00 / 0.67 | 1.00 / 0.67 |
| C outcome accuracy | 0.26 | 0.47 (CI 0.34-0.59) | 0.64 (CI 0.51-0.75) | 0.93 | 0.97 |
| C urgent missed per 1,000 urgent | 1000 | 741 | 704 | 74 | 37 |
| C same, not counting P1-flagged follow-ups | 889 | 444 | 556 | 0 | 37 |
| C Top-20 accuracy | 0.15 | 0.40 | 0.65 | 0.90 | 0.95 |
| C is_request accuracy | 0.43 | 0.76 | 0.86 | 0.98 | 1 |
| D outcome accuracy | 0.24 | 0.41 (CI 0.28-0.57) | 0.49 (CI 0.34-0.64) | 0.83 | 0.88 |
| D abstain recall | 0.24 | 0.42 | 0.50 | 0.82 | 0.87 |

Reading: laya-s13 beats the pinned v3 on accuracy, ranking (Top-20) and calibration; v3 misses fewer hard P1s (urgent
requests that do not even reach the follow-up queue as "P1?"). Both trained models are far above keyword and work
offline; Opus is close to the gold ceiling but needs the network.
