# Review of sets C and D

Paawan reviewed every drafted case for sets C (hard, 101) and D (abstain, 68) on 2026-11-07 on the eval review page, then
asked for every call that differed from the frozen engine to follow the engine (`docs/plan/04-decision-rubric.md`).
Final labels: C 99 as drafted, 2 changed; D 62 as drafted, 6 changed. Nothing was dropped.
The drafts stay in `evals/drafts/`. Only `gold_decision` was reviewed; `gold_extracted` is the drafted extraction.

"Follows the engine" means the frozen fallback engine, given the case's gold extraction and the field's street and hoax
context, makes that decision under the named rule. Where it needs the other post to see a duplicate or a count conflict,
the rubric's label is used.

Drafted labels Paawan accepted were kept even where the engine differs (about 30 rows: duplicates and count conflicts it
can't see from one post, and the prank rows D-043 to D-052, which it abstains on as `low_confidence_request` rather than
`possible_hoax`). Those rows measure real engine gaps.

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

## Set B (golden): `b_golden.jsonl`

82 Houston flood cases written by Claude for this project (a different model family from the trainer's OpenAI synthetic
data; no tweets). Each has a full hand-built `gold_extracted` with LOCATION, COUNT and TIME spans, and a `gold_decision`
from the frozen engine on that extraction. The second post in each duplicate group is `duplicate` with the group's highest
tier. Splits: 49 test, 17 train, 16 dev (duplicate groups share a split). Paawan reviewed them on 2026-11-07.

| Case | Drafted | Final | Why |
| --- | --- | --- | --- |
| B-023 | prioritized P2 | prioritized P1 | Paawan: immediate medical emergency (her water broke) and the water is rising. The extraction now carries hazard `injury` (the rubric's acute-medical convention), so the engine reaches P1 under R-P1-02. |
| B-063 | abstain (low_confidence_request) | not a request | Paawan: an explicit test post. The extraction now has `is_request` false at 0.9, so the engine says not a request (R-NAR-01). |
| B-066 | prioritized P1 | prioritized P1 (unchanged) | Paawan: possible smoke is P1, and confirm the hazard. The engine already does both (R-P1-05 plus its confirm question). `follow_up_field` stays null because it is scored only on abstain rows (DECISIONS A7). |

Paawan's calls the frozen engine does not make (kept at the engine's P2, as with Sets C and D):

| Case | Engine | Paawan | Paawan's reason |
| --- | --- | --- | --- |
| B-042 | P2 (R-P2-02, vulnerable) | P3 | No sign of going without water for over 24 hours and no medical emergency. |
| B-044 | P2 (R-P2-01, trapped) | P3 | The building is dry, so no immediate emergency. |
| B-060 | P2 (R-P2-04, rising water) | P1, ask for people count | Rising water is P1, and the count is unclear. The engine sees count conflicts only across two posts. |

B-061 and B-062 had no review entry and were kept as drafted. B-078 and B-082 are duplicates the runner cannot detect from
one post, so they score as misses even with gold extraction.

## Split change: duplicate groups kept in one split (proposed for Paawan, 2026-11-07)

Three set C duplicate groups had a member in `test` and another in `train` or `dev`. If the trained model saw the
train/dev rows, it has effectively seen the matching test message (leakage). Also, the runner scores the `test` split
alone, so a test row whose group root is in another split can never be found as a duplicate: its gold `duplicate`
always scores as a miss for every system.

The test rows moved to `dev` (the train/dev rows stay where they are, because a model may already have trained on
them). Labels are unchanged.

| Group | Stays | Moved test -> dev |
| --- | --- | --- |
| C-dup-1 | C-049 (train) | C-050 |
| C-dup-5 | C-058 (dev) | C-057 |
| C-dup-6 | C-059 (train) | C-060 |

Set C splits are now 58 test, 23 dev, 20 train. No duplicate group in B, C or D has a member in `test` and another
outside it. Three C groups (C-dup-1, C-dup-3, C-dup-6) still span `train` and `dev`; neither split is scored, so that
does not affect results.
