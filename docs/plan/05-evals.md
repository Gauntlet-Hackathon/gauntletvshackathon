# 05 · Evals (the judging criteria)

The judges score on evals, so the evals are the product. Every metric should mean something to a rescue coordinator.

## Test sets

| Set | Size (target) | Source | Purpose |
| --- | --- | --- | --- |
| **A. Public** | 500+ | Labeled disaster posts from public research datasets (HumAID / CrisisNLP: **verify license and availability first**) | Real-world language; request vs not-request; urgency categories |
| **B. Golden** | ~300 | Synthetic requests written by a **different model family** than the SLM's training data generator, then **checked by hand**. Labels: all fields, tier, duplicate groups | Field accuracy and decision accuracy with known answers |
| **C. Hard** | ~100 | Hand-written: typos, Spanish/Vietnamese mix (if Houston), multiple households in one post, landmark-only locations, reposts, last year's post resurfacing, sarcasm | Robustness |
| **D. Abstain** | ~60 | No location, conflicting counts, possible hoaxes, low-signal posts | Correct answer is "flag + right follow-up question" |
| **E. Sealed holdout** | ~100 | Written early, by someone not building the model, **never looked at** while building | Preview of the judges' unseen challenge |

Split A–C into train/dev/test for your separate training. **Only test splits** are used here. Corrections never enter eval sets.

## Metrics (in the coordinator's priority order)

1. **Urgent requests missed per 1,000** (P1 recall) at a fixed false-alarm rate. **The headline number.**
2. **Top-20 accuracy:** of the first 20 requests the board shows, how many truly belong there?
3. **Field accuracy:** location (exact/partial match), people count (exact), vulnerable and hazard (F1).
4. **Duplicate merging:** pairwise F1 (each miss = a wasted boat trip).
5. **Abstain quality:** precision and recall on set D, plus whether the follow-up asks for the *right* missing field.
6. **Calibration:** expected calibration error + reliability chart (when it says 80%, is it right ~80% of the time?).
7. **Consistency:** run every case 10 times; % of cases with any change in output. (Specialist should be 0%.)
8. **Running conditions:** works offline (yes/no), median latency per request, time per 1,000 requests, cost per 1,000.

Score at **two points** in the pipeline: SLM extraction alone, and final decision (SLM + Mercury). That shows where errors come from.

## Baselines (same inputs, same scoring)

1. **Regex / keyword rules:** the "why not plain code?" answer.
2. **Opus 5.5 with a well-structured prompt** that includes the full rubric from `04-decision-rubric.md` and the same output JSON schema. That's the fair frontier comparison. **Run ahead of time and store results**; don't call it live on stage.
3. **A generic small model offline** (similar size, not fine-tuned, same prompt). The fair offline comparison, and judges will ask.

## How to present it

- One table, rows = metrics, columns = regex / Opus 5.5 / generic SLM / ours.
- **Be honest:** if Opus wins a row (likely raw extraction accuracy online), show it. A table where you lose one row is more believable than a clean sweep.
- The offline column: Opus = 0, regex = works but weak, ours = works.
- Report the sealed holdout separately, labeled as such.

## Promotion gate (for every new model pulled from Hugging Face)

A candidate is promoted to the model registry only if, on test splits of A–D:

- P1 misses per 1,000 ≤ current approved model, **and**
- abstain recall on D ≥ current, **and**
- no other metric regresses by more than a set tolerance (e.g. 1 point), **and**
- calibration error ≤ a set ceiling.

Results for every candidate (pass or fail) go to `eval-results`, keyed by model version.

## Evals panel (cloud UI)

- Current approved model and its scores vs baselines.
- History: every candidate version, pass/fail, and why.
- Per-laptop: which model version it's running, linked to that version's scores.
- Drill-down: click a metric → see the failing cases.
