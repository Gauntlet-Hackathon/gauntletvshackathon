# Engine rules: the rubric made precise

`docs/plan/04-decision-rubric.md` is the source of truth. It is a draft and leaves some cases open; this file fixes **evaluation order, boundaries,
rule ids, follow-up questions and the few defaults the rubric does not state**, so every engine (Python fallback E1–E3, Mercury E7) and every hand-written fixture
gives the same answer. Where it adds something to 04, it says so ("added"). The engine reads `engine_input` (never raw text) and returns `engine_output`.
Thresholds come from `context.thresholds`; the values below are the defaults used by the fixtures.

| threshold | default | used in |
|---|---|---|
| `request_confidence_min` | 0.6 | step 1 |
| `stale_confidence_min` | 0.6 | step 1 (added) |
| `hoax_marker_min` | 1 | step 2 (added) |
| `hoax_request_confidence_max` | 0.85 | step 2 (added) |
| `location_confidence_min` | 0.6 | step 3 |
| `hazard_firm_min` | 0.75 | step 4 (upper edge of 04's 0.5–0.75 band) |
| `hazard_uncertain_min` | 0.5 | step 4 (lower edge of 04's band) |
| `hazard_weak_min` | 0.25 | step 4 (added) |
| `need_hours_over` | 24 | step 4 |
| `duplicate_window_hours` | 12 | used by `duplicate_candidates`, not by the engine |

## Evaluation order (first match wins)
1. **Request check** (04 step 1)
   - `is_request.confidence < request_confidence_min` → `abstain` / `low_confidence_request`, rule `R-ABS-REQ`.
   - else `is_request.value == false` → `not_a_request`, rule `R-NAR-01`.
   - else `time_ref.is_stale_post` and `time_ref.confidence ≥ stale_confidence_min` → `not_a_request`, rule `R-NAR-02` ("clearly old post"; added).
2. **Hoax** (04 step 5, moved earlier; added): `hoax_marker_hits ≥ hoax_marker_min` **and** `is_request.confidence < hoax_request_confidence_max` → `abstain` / `possible_hoax`, rule `R-ABS-HOAX`. Never discarded: a human reviews it.
3. **Location** (04 step 2): `location.type == none`, or `location.confidence < location_confidence_min`, or (`type == landmark` and `street_list_hits` is empty) → `abstain` / `no_usable_location`, rule `R-ABS-LOC`.
   Addresses and coordinates need no list hit.
4. **Tier** (04 step 3, below).
5. **Duplicates and contradictions** (04 steps 4–5), only when `duplicate_candidates` is non-empty:
   - a candidate with `contact_match == true` whose `people_count` and the request's `people_count.value` are both non-null and **different** → `abstain` / `conflicting_counts`, rule `R-ABS-CNT`.
   - else `outcome = duplicate`, `duplicate_of` = the first candidate (oldest), `priority_tier` = the highest of the request's own tier and every candidate's non-null `priority_tier` (P1 > P2 > P3), rule `R-DUP-01`.
6. Otherwise `outcome = prioritized` with the tier and the rule id from step 4.

Equality at a boundary counts as passing the minimum (`≥`): confidence 0.6 is enough for a request or a location. Confidence strictly below the minimum fails.

## Step 4: tier
Hazards and vulnerable entries are scored by confidence band: **firm** `c ≥ hazard_firm_min` (0.75), **uncertain** `hazard_uncertain_min ≤ c < hazard_firm_min` (0.5–0.75),
**weak** `hazard_weak_min ≤ c < hazard_uncertain_min` (0.25–0.5), ignored below 0.25. Needs and `request_kind` have no confidence and always count.

Rules (the tier of the **first matching rule in this table order** wins; P1 rules are tried before P2, P2 before P3):

| rule_id | tier | condition |
|---|---|---|
| `R-P1-01` | P1 | `rising_water` and (`on_roof` or `trapped`) |
| `R-P1-02` | P1 | `injury` |
| `R-P1-03` | P1 | `medical_device_no_power` |
| `R-P1-04` | P1 | `rising_water` and a vulnerable of kind `child`, `elderly` or `disabled` (04: infant/elderly/disabled) |
| `R-P1-05` | P1 | `fire` |
| `R-P2-01` | P2 | `trapped` or `on_roof` (without the P1 combinations above) |
| `R-P2-02` | P2 | any vulnerable entry (any kind) |
| `R-P2-03` | P2 | a need (food, water or meds) with `hours_without > need_hours_over` |
| `R-P2-04` | P2 | any other hazard (`rising_water`) present, or `request_kind == rescue` (added: a rescue request is stranded people) |
| `R-P3-01` | P3 | `request_kind == evacuation` |
| `R-P3-02` | P3 | `request_kind == welfare_check` |
| `R-P3-03` | P3 | `request_kind == supply` |
| `R-P3-04` | P3 | anything else (`request_kind == other`; added default) |

A need with `hours_without` null is unknown and never triggers `R-P2-03`.

**Uncertainty (04: "treat as the higher tier and add a follow-up question; never lower"):**
- `firm_tier` = tier using only firm hazards/vulnerables. `uncertain_tier` = tier using firm + uncertain ones. `weak_tier` = tier using firm + uncertain + weak ones.
- If `weak_tier` is higher than `uncertain_tier` → `abstain` / `low_confidence_hazard`, rule `R-ABS-HAZ` (added: a weak signal that would raise the tier is routed to a human).
- Else the request's tier is `uncertain_tier` and its rule id comes from that evaluation. If `uncertain_tier` is higher than `firm_tier`, set `follow_up_question` to the "confirm" text below.
  The tier is never lowered because a signal is uncertain.

## Outputs per outcome
| outcome | priority_tier | duplicate_of | abstain_reason | follow_up_question |
|---|---|---|---|---|
| `prioritized` | P1/P2/P3 | null | null | null, or the confirm text when step 4 raised the tier on an uncertain signal |
| `duplicate` | highest of the group (kept) | request_id of the oldest candidate | null | null |
| `abstain` | null | null | the reason | required (text below) |
| `not_a_request` | null | null | null | null |

`rules_version` in the output is the version of the engine that produced it. Fixtures use the placeholder `rules@0000000+bin:00000000`; compare everything except `rules_version` against fixtures.

## Follow-up questions (exact strings)
| used for | text |
|---|---|
| `no_usable_location` (04) | `What's the street address, or the nearest cross streets or landmark?` |
| `low_confidence_request` | `Is this a request for help? If so, what do you need and where are you?` |
| `possible_hoax` | `Can you confirm this request is real and tell us exactly what help you need?` |
| `low_confidence_hazard` | `Can you confirm what is happening right now: is water rising, is anyone injured, trapped or in danger?` |
| `conflicting_counts` | `We have different counts for this location. How many people need help right now?` |
| confirm (tier raised on uncertain signal) | `Please confirm: is anyone in immediate danger (rising water, injury, fire, trapped), and is a child, elderly or disabled person with you?` |

## Rule ids
`R-NAR-01`, `R-NAR-02`, `R-ABS-REQ`, `R-ABS-HOAX`, `R-ABS-LOC`, `R-ABS-HAZ`, `R-ABS-CNT`, `R-DUP-01`, `R-P1-01…05`, `R-P2-01…04`, `R-P3-01…04`.
