# 04 · Decision rubric (draft)

This document is **both** the Mercury engine's spec **and** the guide humans use to label the golden set. Labels must be made by a *person* applying this rubric to the *raw text*, never by running the engine, or the eval becomes circular.

> Draft for the team to edit. Tiers and thresholds are starting points, not validated emergency-management doctrine. Say so in the pitch.

## Step 1 · Is it a request?

- Not a request (news, an offer of help, a general question, clearly old post) → `not_a_request`.
- If `is_request` confidence < 0.6 → `abstain: low_confidence_request`.

## Step 2 · Can we act on the location?

- No usable location (type `none`, or confidence < 0.6) → `abstain: no_usable_location`, follow-up: "What's the street address, or the nearest cross streets or landmark?"
- Landmark-only locations are allowed if they match the local landmark/street list; otherwise abstain with the same question.

## Step 3 · Priority tier

| Tier | Rule examples (any one triggers) |
| --- | --- |
| **P1** (immediate threat to life) | rising water + (on roof OR trapped); injury needing evacuation; medical device without power; infant/elderly/disabled + rising water; fire |
| **P2** (urgent) | trapped/stranded without immediate threat; vulnerable person present without an active hazard; no food/water/meds for > 24h |
| **P3** (needs help, not urgent) | evacuation request from a dry location; welfare check; supply request |

Hazard or vulnerability with confidence between 0.5 and 0.75 that **would raise** the tier → treat as the higher tier **and** add a follow-up question to confirm. Never lower a tier because of uncertainty.

## Step 4 · Duplicates

Same location (normalized) + overlapping people/contact within a time window → `duplicate`, link to the earliest request, and **keep the highest tier** of the group.

## Step 5 · Contradictions and hoaxes

- Counts or locations that conflict across duplicates → `abstain: conflicting_counts` with the specific question.
- Markers of a hoax or prank (configurable list plus low `is_request` confidence) → `abstain: possible_hoax`. **Never auto-discard**: route to a human.

## Ordering within a tier

P1 first, then by: number of vulnerable people, number of people, time since received (older first).

## Rule IDs

Every rule gets an ID (`R-P1-01`, `R-ABS-LOC`, ...) that appears on the decision and in the UI.
