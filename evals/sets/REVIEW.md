# Review of sets C and D

Paawan reviewed every drafted case for sets C (hard, 101) and D (abstain, 68) on 2026-11-07 on the eval review page, then
asked for every call that differed from the frozen engine to follow the engine (`docs/plan/04-decision-rubric.md`).
Final labels: C 99 as drafted, 2 changed; D 62 as drafted, 6 changed. Nothing was dropped.
The drafts stay in `evals/drafts/`. Only `gold_decision` was reviewed; `gold_extracted` is the drafted extraction.

"Follows the engine" means the frozen fallback engine, given the case's gold extraction and the field's street and hoax
context, makes that decision under the named rule. Where it needs the other post to see a duplicate or a count conflict,
the rubric's label is used.

| Case | Drafted | Final | Why |
| --- | --- | --- | --- |
| C-078 | prioritized P1 | prioritized P2 | P2, changed from P1. Water is a foot from the door but not inside, and nothing says it is rising fast. They should leave soon, so urgent, but not an immediate threat. |
| C-093 | prioritized P2 | prioritized P1 | P1, changed from P2. A driver stuck in a flooded underpass with water at the door is trapped, and underpass water rises fast. Treated as trapped plus rising water. |
| D-063 | abstain (low_confidence_hazard) | prioritized P2 | P2, changed from abstain. A possible gas smell or smoke at a home with 2 adults. Unconfirmed, but gas or fire can turn into an emergency fast, so treat it as urgent and confirm the hazard. |
| D-064 | abstain (low_confidence_hazard) | prioritized P2 | Follows the engine (engine rule R-P2-02); Paawan agreed on 2026-11-07. Paawan's first call was prioritized P1. |
| D-065 | abstain (low_confidence_hazard) | prioritized P2 | Follows the engine (engine rule R-P2-04); Paawan agreed on 2026-11-07. Paawan's first call was prioritized P3. |
| D-066 | abstain (low_confidence_hazard) | prioritized P2 | Follows the engine (engine rule R-P2-04); Paawan agreed on 2026-11-07. Paawan's first call was not a request. |
| D-067 | abstain (low_confidence_hazard) | prioritized P2 | Follows the engine (engine rule R-P2-04); Paawan agreed on 2026-11-07. Paawan's first call was not a request. |
| D-068 | abstain (low_confidence_hazard) | prioritized P2 | Follows the engine (engine rule R-P2-04); Paawan agreed on 2026-11-07. Paawan's first call was not a request. |
