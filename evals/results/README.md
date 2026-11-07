# Stored eval results

Baselines are run ahead of time and stored here, never called live (docs/plan/05-evals.md).

## Opus with the rubric (`opus/claude-opus-5-5.jsonl`, `opus__C.json`, `opus__D.json`)

- Model `claude-opus-5-5`, run 2026-11-07 on the test splits of the reviewed sets C (61) and D (41), main at 05c16bc.
- Prompt (`rescue_evals.baselines.llm.build_prompt`): the extraction task, the full decision rubric
  (`docs/plan/04-decision-rubric.md`) and the `extracted` JSON Schema. Opus only extracts; the decision is
  made by the same fallback engine as every other system, so the columns are comparable.
- 102/102 outputs schema-valid. 281k input / 78k output tokens in total; median 6.5 s per request.
- Rerun (needs an Anthropic key; Set E is refused):
  `ANTHROPIC_API_KEY=... PYTHONPATH=shared:field:engine:evals python -m rescue_evals.baselines --system opus --split test --sets C,D`
- Re-score the stored outputs without the network:
  `STORED_OUTPUTS=evals/results/opus/claude-opus-5-5.jsonl PYTHONPATH=shared:field:engine:evals python -m rescue_evals.runner --system stored --sets C,D --results-dir /tmp/x`

| metric | keyword | v3 (pinned) | laya-s13 | Opus + rubric | gold extraction |
|---|---|---|---|---|---|
| C outcome accuracy | 0.26 | 0.44 | 0.61 | 0.89 (CI 0.78-0.94) | 0.92 |
| C tier accuracy | 0.04 | 0.26 | 0.35 | 0.85 | 0.91 |
| C P1 misses /1000 | 426 | 328 | 311 | 33 | 16 |
| C is_request accuracy | 0.46 | 0.75 | 0.85 | 0.98 | 1 |
| C ECE | 0.28 | 0.19 | 0.13 | 0.08 | 0.17 |
| D outcome accuracy | 0.24 | 0.41 | 0.49 | 0.83 (CI 0.69-0.91) | 0.88 |
| D abstain recall | 0.24 | | | 0.82 | |

Keyword and Opus are from this run; v3, laya-s13 and gold come from the 2026-11-07 model validation on the same sets.
Offline: Opus does not work offline; keyword and the trained model do.
