# Shared fixtures: 24 rubric cases

Unit-test fixtures every lane uses (not an eval set; the eval sets live in `evals/`). Each line of `cases.jsonl` is one case:

| key | meaning |
|---|---|
| `case_id`, `description` | `C01`…`C24` and what the case exercises |
| `raw_text`, `request_id`, `received_at`, `contact` | the incoming message and its metadata (what the field pipeline sees) |
| `extracted` | the `extracted` payload a correct extractor produces from `raw_text` (field/engine/evals tests feed this to their code) |
| `context` | the engine context: `duplicate_candidates`, `street_list_hits`, `hoax_marker_hits`, `thresholds` (the defaults from `contracts/engine_rules.md`) |
| `expected` | the `engine_output` for `{extracted, context}` |

`{"extracted": …, "context": …}` is a valid `engine_input`. Span offsets are Unicode code points (Python string indices).

**The expected outputs were written by a person applying `docs/plan/04-decision-rubric.md` as made precise in `contracts/engine_rules.md`; no engine produced them.**
Do not regenerate them from code. If an engine disagrees with a case, decide by the rubric which one is wrong and fix it in a PR. `expected.rules_version` is the placeholder
`rules@0000000+bin:00000000`: compare every field except `rules_version`.

## Coverage
| What | Cases |
|---|---|
| P1 | C01 (R-P1-01), C02 (R-P1-02), C03 (R-P1-03, Spanish), C04 (raised from P2 by an uncertain signal, with a confirm question) |
| P2 | C05 (R-P2-01), C06 (R-P2-02), C07 (R-P2-03, location confidence exactly 0.6) |
| P3 | C08 (R-P3-01), C09 (R-P3-03, also the prompt-injection case) |
| not_a_request | C10 (offer of help), C11 (old post resurfacing) |
| abstain `low_confidence_request` | C12, C13 |
| abstain `no_usable_location` | C14 (no location), C15 (landmark not in the street list) |
| abstain `low_confidence_hazard` | C16, C17 |
| abstain `conflicting_counts` | C18 (vs C06), C19 (vs C03) |
| abstain `possible_hoax` | C20, C21 |
| duplicate groups (outcome `duplicate`) | group 1: C22 → C01 (group keeps P1); group 2: C23 → C05 (own tier P1 raises the group from P2); group 3: C24 → C08 (P3) |
| prompt injection | C09: an instruction hidden in the text; the extracted fields carry none of it, so the tier stays P3 |

The conflicting-counts pairs (C18/C06 and C03/C19) are also duplicate relations as far as `duplicate_candidates` is concerned (same place, same contact).

## Other files
- `streets.txt`: 50 Houston streets and landmarks for location matching (`rescue_shared.logic.street_list_hits`).
- `hoax_markers.txt`: the default hoax marker list (`rescue_shared.logic.hoax_marker_hits`). Real deployments make it configurable.

`shared/tests/test_fixtures.py` checks that every case validates against the schemas and that the case data is internally consistent (spans, street hits, hoax marker counts and
duplicate candidates match the shared logic). It does not check `expected`; only an engine can, and the engine lane's tests do.
