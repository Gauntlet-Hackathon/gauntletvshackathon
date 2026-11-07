# Review of sets C and D

Paawan reviewed every drafted case for sets C (hard, 101) and D (abstain, 68) on 2026-11-07, on the eval review page.
C: 85 approved, 16 changed, 0 dropped. D: 52 approved, 16 changed, 0 dropped.
The approved and changed cases are `c_hard.jsonl` and `d_abstain.jsonl`; the drafts stay in `evals/drafts/`.
Only `gold_decision` was reviewed. `gold_extracted` is still the drafted extraction.

Labels are the reviewer's judgement of the raw text. Where it differs from `docs/plan/04-decision-rubric.md` (which the frozen
engine implements), the eval scores those cases against the reviewer, so they show up as engine misses.

| Case | Drafted | Reviewed | Why |
| --- | --- | --- | --- |
| C-006 | prioritized P2 | prioritized P3 | P3, changed from P2. Five people have been out of food and water since Thursday, but they are at home with no rising water, injury or other danger. They need supplies soon, not a rescue. |
| C-008 | prioritized P1 | prioritized P2 | P2, changed from P1. Two elderly people need a boat, but water is only at the porch and nothing says it is rising. Vulnerable people plus standing water is urgent, not life-threatening. |
| C-022 | prioritized P1 | prioritized P1 | No reason recorded. |
| C-023 | prioritized P2 | prioritized P3 | P3, changed from P2. The family wants a ride out and says the street is dry. The child has asthma but no attack is described, so nothing makes this urgent. |
| C-027 | prioritized P2 | prioritized P3 | P3, changed from P2. Four people have had no drinking water for 2 days, but nothing says water levels are rising or anyone is in danger. Bring water; no rescue needed. |
| C-029 | kept | kept | P2, kept. Mom has been without blood-pressure medicine for 6 days. The line is about 2 days: under that would be P3, and 6 days is well past it, so it stays urgent. |
| C-030 | prioritized P1 | prioritized P2 | P2, changed from P1. Knee-deep water with a baby, but the message says the water has stopped rising. Urgent because of the baby; not an immediate threat to life while the water is steady. |
| C-032 | prioritized P1 | prioritized P2 | P2, changed from P1. About 9 people in three flooded first-floor units, 2 of them children, but nothing says the water is rising. Urgent because of the numbers and children, not life-threatening yet. |
| C-044 | kept | kept | P2, kept. Three people are on the second floor with water on the first floor. That suggests the water came up, but the message does not say it is still rising, so urgent rather than P1. |
| C-046 | prioritized P2 | prioritized P3 | P3, changed from P2. Two men stranded at a store since morning with no hazards. They just need a ride out. |
| C-055 | prioritized P2 | prioritized P3 | P3, changed from P2. Four people need food and water, with no danger described. A supply request. |
| C-056 | duplicate P2 | duplicate P3 | Duplicate of the earlier 31 Pecan St post, P3 (changed from P2). Still only a supply need; three days without food is serious but no medical emergency is described. |
| C-078 | prioritized P1 | prioritized P2 | P2, changed from P1. Water is a foot from the door but not inside, and nothing says it is rising fast. They should leave soon, so urgent, but not an immediate threat. |
| C-081 | prioritized P2 | prioritized P3 | P3, changed from P2. No injury and no water in the house; just no power and a child with a cold. Not a medical emergency. |
| C-088 | prioritized P2 | prioritized P1 | P1, changed from P2. The message says the water is rising and they need a boat. Rising water with people who cannot leave is a threat to life. |
| C-093 | prioritized P2 | prioritized P1 | P1, changed from P2. A driver stuck in a flooded underpass with water at the door is trapped, and underpass water rises fast. Treated as trapped plus rising water. |
| C-096 | prioritized P2 | prioritized P1 | P1, changed from P2. A type 1 diabetic has been out of insulin for 3 days. That can become life-threatening within days, so it is a medical emergency even though the house is not flooded. |
| C-097 | prioritized P2 | prioritized P3 | P3, changed from P2. Six people with a baby, without clean water for 36 hours, asking for a pickup. No medical emergency or rising water is described. |
| D-025 | abstain (no_usable_location) | not a request | Not a request, changed from abstain. "The old mill behind the feed store" gives no address and no clear ask, but a follow-up should still try to pin down the location in case it can be found. |
| D-027 | abstain (no_usable_location) | not a request | No reason recorded. |
| D-028 | abstain (no_usable_location) | abstain (no_usable_location) | No reason recorded. |
| D-029 | abstain (no_usable_location) | not a request | No reason recorded. |
| D-030 | abstain (no_usable_location) | prioritized P1 | P1, changed from abstain. Water is rising and the phone is dying. The street is unclear (Bayou Rd or Bayou Ln), but if either is real this is life-threatening, so rank it as P1 and confirm the location. |
| D-031 | abstain (no_usable_location) | not a request | No reason recorded. |
| D-032 | abstain (no_usable_location) | not a request | No reason recorded. |
| D-038 | abstain (conflicting_counts) | not a request | No reason recorded. |
| D-039 | abstain (conflicting_counts) | not a request | No reason recorded. |
| D-040 | abstain (conflicting_counts) | not a request | No reason recorded. |
| D-063 | abstain (low_confidence_hazard) | prioritized P2 | P2, changed from abstain. A possible gas smell or smoke at a home with 2 adults. Unconfirmed, but gas or fire can turn into an emergency fast, so treat it as urgent and confirm the hazard. |
| D-064 | abstain (low_confidence_hazard) | prioritized P1 | P1, changed from abstain. Possibly rising water with an elderly mother in the home. Rising water plus an elderly person is P1; confirm the water level. |
| D-065 | abstain (low_confidence_hazard) | prioritized P3 | P3, changed from abstain. A secondhand report that someone may be hurt. Possibly a medical emergency, so it is ranked, low, with a follow-up to confirm. |
| D-066 | abstain (low_confidence_hazard) | not a request | Not a request, changed from abstain. A pacemaker does not depend on mains power, and no other emergency or ask is described. |
| D-067 | abstain (low_confidence_hazard) | not a request | Not a request, changed from abstain. No clear ask, but a crack in the ceiling and maybe water deserve a follow-up to confirm whether there is a hazard. |
| D-068 | abstain (low_confidence_hazard) | not a request | Not a request, changed from abstain. Someone is shouting but it is unclear why. The follow-up should confirm whether there is a hazard. |
