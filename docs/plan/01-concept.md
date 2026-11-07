# 01 · Concept

## The user

**A volunteer rescue coordinator at a field command post during a flood or hurricane.** They run a handful of boats or high-water vehicles and receive a flood of requests through texts, social posts, call-center notes, and paper forms. They have a laptop, maybe a generator, and no reliable internet.

## The problem

- Requests are messy, duplicated, and in several languages. The same family can appear 10 times in 10 wordings.
- Volume spikes exactly when the network is worst.
- Sorting is done by hand, under stress, and is inconsistent between shifts.
- The worst failure is a **missed urgent request**: someone on a roof with rising water who sits at position 300 in a list.

## What the specialist does (one job)

Turn each incoming request into a ranked, deduplicated, actionable entry, and say clearly when it can't:

1. **Read** (SLM): extract location text, number of people, vulnerable people (children, elderly, disabled, medical), hazards (rising water, injury, fire, no power for medical devices), time references, and contact. Each field comes with a confidence score.
2. **Decide** (Mercury engine): apply the priority rubric (`04-decision-rubric.md`), merge duplicates, assign a priority tier, or **abstain** with a reason and the exact follow-up question to send.
3. **Record and sync**: write everything locally, then sync to the cloud when a connection exists.

## Why this beats a general AI (Opus 5.5), structurally

Not by being smarter on one answer. By being the right shape:

| Property | Opus 5.5 | This specialist |
| --- | --- | --- |
| Works with no internet | No. Scores zero | Yes, entirely on the laptop |
| Same input, same output | No. Verdicts can flip on reruns | Yes. Mercury is deterministic; the SLM runs with fixed weights and no sampling |
| Tunable error rate | No. One answer, self-reported confidence | Yes. Pick the threshold: e.g. "miss under 1% of urgent requests, accept N false alarms" |
| Calibrated "not sure" | Made-up confidence | Calibrated probabilities feed explicit abstain rules |
| Auditable decision | Prose explanation, may differ each time | Every decision cites the rule that fired and the model and rules versions |
| Cost / latency at surge volume | Per-call cost and latency | Milliseconds per request, zero marginal cost |

**Honest caveat to state in the pitch:** online, on raw extraction accuracy, Opus may match or beat the SLM. Show that row. The win is everything else.

## Why not plain code (regex)?

"My mom's stuck at the place behind the Kroger on Westheimer, she's 82 and on oxygen, water's at the steps" has no fixed format. Regex finds phone numbers and maybe street addresses. It can't tell that "on oxygen" plus "power out" means a medical emergency, or that two very different messages describe the same household. The model does the reading. Code does all the deciding.

## Mapping to the judging criteria

| Brief says | How we answer |
| --- | --- |
| Build something new | New project: offline triage specialist, SLM + Mercury rules + sync |
| Make it work | Hosted field instance and cloud dashboard, both live at public URLs |
| Prove the advantage | Evals panel: specialist vs regex vs Opus 5.5 (well-structured prompt) vs a generic small model |
| Earn the trust | Abstains with a reason and a follow-up question; deterministic replay; versioned rules and models; sealed holdout |
| Judging question: would you trust it? | "The model only reads. Readable rules decide. You can replay any decision. It tells you when it doesn't know." |

## The "damn, why didn't I think of that" line

> Every AI demo assumes you have internet. Disasters are exactly when you don't.
