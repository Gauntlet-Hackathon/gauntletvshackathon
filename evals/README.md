# Evals: how the judges' table is scored

`python -m rescue_evals.runner --system <name> --sets B,C,D` scores one system per set and writes
`evals/results/<system>__<set>.json`; `python -m rescue_evals.report` pools sets A-D into
`summary.json` (the cloud panel and promotion gate read it) and `TABLE.md`. Set format:
`evals/sets/README.md`, plus the two optional fields below. Plan: `docs/plan/05-evals.md`.

## Optional set fields (review PR-3, H-01)

| field | type | notes |
|---|---|---|
| `contact` | string or null | the sender's phone or handle, as the field app receives it |
| `received_at` | `YYYY-MM-DDTHH:MM:SSZ` or null | arrival time; give it for every case of a set or for none |

Cases are decided in arrival order (`received_at`, then id; without `received_at`, file order one
second apart). Each case gets the context the field pipeline builds: street-list and hoax-marker
hits, and duplicate candidates among the set's earlier cases (`rescue_shared.logic.duplicate_candidates`,
with the tier the engine gave each earlier case). A duplicate needs the same location and either the
same contact or the same people count (3 or more), within 12 h. Only the scored split is in the
index, so a gold duplicate whose partner is in another split cannot be found (review L-02, labels).

## Denominators and pooling (H-03)

Each result doc carries `metric_n` (every metric's denominator) and `metric_counts` (tp/fp/fn behind
each F1). A metric whose denominator is 0 is left out of `metrics`, never written as 0.0 or 1.0: a
set without gold extraction has no extraction rows, a set without gold abstains has no abstain
recall, and so on. The report pools a rate as the sum of numerators over the sum of `metric_n` and
an F1 from the summed counts. A failed extraction is a miss on every extraction row: its gold labels
are false negatives in the F1 rows.

| metric | denominator (`metric_n`) |
|---|---|
| `extraction.*` exact / accuracy rows | cases with a gold extraction |
| `extraction.vulnerable_f1`, `hazard_f1`, `decision.duplicate_pairwise_f1` | tp + fp + fn (labels or pairs) |
| `decision.outcome_accuracy`, `p1_misses_per_1000_requests` | all cases |
| `decision.p1_misses_per_1000`, `p1_hard_misses_per_1000` | gold P1 (urgent) cases |
| `decision.tier_accuracy` | cases with a gold board tier |
| `decision.false_alarm_rate` | cases that are not gold P1 |
| `decision.abstain_precision` / `abstain_recall` | predicted / gold abstains |
| `decision.follow_up_field_rate` | cases that should abstain and did |
| `decision.top20_accuracy` | rows in the gold top 20 (fewer on a small set) |
| `calibration.ece` | cases with a gold and a predicted extraction |

## Definitions

- **Headline, `decision.p1_misses_per_1000`**: gold-P1 requests not shown at P1, per 1,000 *urgent*
  requests, i.e. `1000 x (1 - P1 recall)`, as the plan defines it ("Urgent requests missed per 1,000
  (P1 recall)", review O-03). Before PR-3 this key was per 1,000 requests of any kind; that number is
  kept as `decision.p1_misses_per_1000_requests`. The plan's "at a fixed false-alarm rate" is not
  implemented: the engine has no escalation threshold to sweep (`metrics.sweep` is unused).
- **Board tier**: the tier a request shows at on the field board, its own when `prioritized`, its
  group's when `duplicate` (review L-11). A gold-P1 duplicate merged into a P1 group is not a miss.
- **`decision.p1_hard_misses_per_1000`**: the headline minus the misses the engine still sent to the
  top of the follow-up queue (an abstain with `provisional_tier: P1`, DECISIONS R1). Which of the two
  is the headline is a DECISIONS call; both are written.
- **`decision.top20_accuracy`** (plan metric 2, H-04): the first 20 tier rows of the board, P1 then P2
  then P3, each tier sorted by `logic.within_tier_sort_key` (more vulnerable people, then more people,
  then older first), duplicates collapsed under the earliest request of their group, like
  `rescue_field.board`. The gold board uses gold tiers, gold duplicate groups and gold extractions; the
  predicted board uses the system's decisions and extractions. The score is the share of gold top-20
  households that are in the predicted top 20 (a missed merge that takes two slots counts once).
- **Location match** (L-01): words compared in any script (NFKD, accents stripped, casefolded, street
  abbreviations expanded with `logic.TOKEN_MAP`). Exact means the same words in any order, as the
  product's same-place key; partial means one side's words are contained in the other's.
- **Running conditions**: `running.median_ms_per_request` is the plan's row; the mean row is kept.
  Pooled over sets as an n-weighted mean of the per-set medians.
- Systems that look up an answer per case (stored outputs, the gold oracle) implement
  `extract_case(case)`, so two cases with identical text are scored separately (F-04).

Stored baselines are re-scored, without calling the model, with
`python -m rescue_evals.baselines --system opus --split test --sets B,C,D --rescore evals/results/opus/claude-opus-5-5.jsonl`.
Run it after any scoring change so every column uses the same scoring.
