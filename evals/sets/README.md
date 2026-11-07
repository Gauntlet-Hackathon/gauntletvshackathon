# Eval sets A-E

One JSONL file per set (or part of a set); one case per line. Load with
`rescue_evals.loaders.load_cases(path)`; check a file with
`PYTHONPATH=shared:evals python -m rescue_evals.loaders FILE [--open-sealed]`.

| field | type | notes |
|---|---|---|
| `id` | string | unique within the file |
| `raw_text` | string | the message as received |
| `gold_extracted` | object or null | the `extracted` payload from `contracts/` (exact schema, human-labelled); null if the set labels only the decision |
| `gold_decision` | object | `outcome` (prioritized/abstain/not_a_request/duplicate), `priority_tier` (P1-P3, required for prioritized), `abstain_reason` (exactly when abstain), `follow_up_field` (optional: the missing field the follow-up should ask about) |
| `gold_duplicate_group` | string or null | cases sharing a value are the same household |
| `set` | `A`..`E` | |
| `split` | `train`/`dev`/`test` | only `test` is scored |
| `needs_review` | `true` (optional) | marks a draft; the loader refuses the whole file |

Rules the loader enforces:
- Every row is validated; the first bad row raises with `file:line`.
- Set E is the sealed holdout. It is refused unless `open_sealed=True` / `--open-sealed`, and opening it is logged as a warning. Files named `e`, `set_e`, `*sealed*` or `*holdout*` are refused before being read.
- Any row with `needs_review: true` refuses the file. Drafts live in `evals/drafts/`, never here.
