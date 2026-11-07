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

## Generic small model offline (`slm/qwen2.5-1.5b-instruct-q4_k_m.jsonl`, `slm__B.json`, `slm__C.json`, `slm__D.json`)

The plan's third baseline: "a generic small model offline (similar size, not fine-tuned, same prompt)".
Ticket #64 names no model, so this uses **Qwen2.5-1.5B-Instruct**, quantized Q4_K_M
(`Qwen/Qwen2.5-1.5B-Instruct-GGUF@91cad51170dc346986eccefdc2dd33a9da36ead9`, file
`qwen2.5-1.5b-instruct-q4_k_m.gguf`, sha256 `6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`).
Why this one: Apache-2.0, instruction-tuned but not fine-tuned on our task, multilingual (the sets mix in
Spanish and Vietnamese), and its 1.1 GB file is about the footprint of our tagger (~1.15 GB RSS), so it fits
the same field laptop. Qwen2.5-0.5B (closer in parameter count to our ~300M encoder) was tried first and
produced no schema-valid output on the same prompt, which would be a straw man.

- Run 2026-11-07 on the B, C and D test splits (151 cases), main at d80a9c1, fully offline on 4 CPU cores
  with `llama-cpp-python[server]==0.3.16` (`python -m llama_cpp.server --model <gguf> --n_ctx 8192
  --chat_format chatml --port 8081`). Median 20 s per request on this CPU (no GPU).
- Same system prompt as Opus (`build_prompt`: task + full rubric + `extracted` schema), temperature 0.
  `SLM_JSON_SCHEMA=1` adds schema-constrained decoding (`response_format` with the contract schema,
  `$ref`s inlined, span labels as an enum because grammars ignore `pattern`). Without it the model's JSON
  was close but failed validation on every case (lower-case span labels); with it 151/151 are valid.
  Output is still validated against the real contract.
- Rerun: start the server as above, then
  `SLM_JSON_SCHEMA=1 SLM_ENDPOINT=http://127.0.0.1:8081/v1/chat/completions SLM_MODEL_ID=qwen2.5-1.5b-instruct-q4_k_m PYTHONPATH=shared:field:engine:evals python -m rescue_evals.baselines --system slm --split test --sets B,C,D`

| metric (new runner) B / C / D | keyword | generic SLM | Opus + rubric |
|---|---|---|---|
| urgent missed per 1,000 urgent (headline) | 762 / 931 / n/a | 95 / 241 / n/a | 0 / 69 / n/a |
| urgent missed per 1,000 requests | 327 / 443 / 0 | 41 / 115 / 0 | 0 / 33 / 0 |
| Top-20 accuracy | 0.25 / 0.15 / 0.33 | 0.70 / 0.55 / 0.33 | 1.00 / 0.90 / 1.00 |
| outcome accuracy | 0.27 / 0.26 / 0.24 | 0.63 / 0.70 / 0.07 | 0.94 / 0.90 / 0.83 |
| tier accuracy | | 0.54 / 0.54 / 0.00 | 0.90 / 0.86 / 0.33 |
| is_request accuracy | | 0.76 / 0.77 / 0.93 | 0.98 / 0.98 / 0.90 |
| location exact | | 0.78 / 0.48 / 0.39 | 0.88 / 0.69 / 0.71 |
| people count exact | | 0.65 / 0.66 / 0.29 | 0.92 / 0.79 / 0.61 |
| hazard F1 / vulnerable F1 | | 0.41 / 0.48 / 0.42 ; 0.33 / 0.38 / 0.24 | 0.90 / 0.91 / 0.76 ; 0.84 / 0.93 / 1.00 |
| abstain recall (D) | | 0.03 | 0.82 |
| ECE | | 0.34 / 0.30 / 0.42 | 0.11 / 0.08 / 0.18 |
| works offline | yes | yes | no |

The generic model rarely abstains: it reports high confidence and fills in locations and counts that are not
there, so Set D (correct answer: ask a follow-up) collapses to 0.07, and it misses many hazards and vulnerable
people. Keyword numbers are from the PR-3 table above. The trained-model columns need a re-run with the new runner.

## Opus re-scored with the PR-3 runner (2026-11-07)

`opus__{B,C,D}.json` were re-scored from the stored JSONL with `--rescore` (no model calls) after
the review PR-3 scoring fixes (definitions in `evals/README.md`). The tables below are from before
it: their "P1 misses /1000" is per 1,000 requests of any kind, which is now
`decision.p1_misses_per_1000_requests`; the headline key `decision.p1_misses_per_1000` is now per
1,000 urgent (gold P1) requests. The v3 / laya-s13 columns need a re-run with the new runner.

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
