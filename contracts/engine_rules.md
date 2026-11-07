# Engine rules: the rubric made precise

`docs/plan/04-decision-rubric.md` is the source of truth. It is a draft and leaves some cases open; this file fixes **evaluation order, boundaries,
rule ids, follow-up questions and the few defaults the rubric does not state**, so every engine (Python fallback E1–E3, Mercury E7) and every hand-written fixture
gives the same answer. Where it adds something to 04, it says so ("added"). The engine reads `engine_input` (never raw text) and returns `engine_output`.
Thresholds come from `context.thresholds`; the values below are the defaults used by the fixtures.
Rules marked "(changed 2026-11-07, see DECISIONS)" follow `docs/DECISIONS.md` § "Step 3 review fixes (2026-11-07)"; the finding id (R1…R13, A2, A9) is given with each.

| threshold | default | used in |
|---|---|---|
| `request_confidence_min` | 0.6 | step 1 |
| `stale_confidence_min` | 0.6 | step 1 (added) |
| `hoax_marker_min` | 1 | step 4a (added) |
| `hoax_request_confidence_max` | 0.85 | step 4a (added) |
| `location_confidence_min` | 0.6 | step 3 |
| `hazard_firm_min` | 0.75 | step 4 (upper edge of 04's 0.5–0.75 band) |
| `hazard_uncertain_min` | 0.5 | step 4 (lower edge of 04's band) |
| `hazard_weak_min` | 0.25 | step 4 (added) |
| `need_hours_over` | 24 | step 4 |
| `duplicate_window_hours` | 12 | used by `duplicate_candidates`, not by the engine |

**Threshold order** (changed 2026-11-07, see DECISIONS; R8): the thresholds must satisfy `hazard_weak_min ≤ hazard_uncertain_min ≤ hazard_firm_min`.
An engine given thresholds that break it decides nothing and fails with the CLI error shape (exit 1, one stderr line).

**Input enums** (changed 2026-11-07, see DECISIONS; A9): `location.type`, `hazards[].kind`, `vulnerable[].kind`, `needs[].kind`, `request_kind` and
`duplicate_candidates[].priority_tier` must hold one of the contract's values (`payloads/extracted.schema.json`, `common.schema.json`); anything else is
rejected with exit 1 by both engines. A null or missing `request_kind` counts as `other`, a null candidate `priority_tier` as "no tier".

## Evaluation order (first match wins)
Step 4's tiers (below) are computed for every input first, because several earlier steps need them: `firm_tier`, `uncertain_tier` and `weak_tier`.
`weak_tier`, the most cautious reading, is the **provisional tier** that every `abstain` carries (changed 2026-11-07, see DECISIONS; R1).

1. **Request check** (04 step 1)
   - `is_request.confidence < request_confidence_min` → `abstain` / `low_confidence_request`, rule `R-ABS-REQ`.
   - else `is_request.value == false` → `not_a_request`, rule `R-NAR-01`.
   - else `time_ref.is_stale_post` and `time_ref.confidence ≥ stale_confidence_min` ("clearly old post"; added):
     - if `weak_tier` is P1 → `abstain` / `low_confidence_request`, rule `R-ABS-REQ` (changed 2026-11-07, see DECISIONS; R5: a stale-looking P1 goes to a human, never dropped);
     - else `not_a_request`, rule `R-NAR-02`.
2. **Hoax**: moved to step 4a, after the tier (changed 2026-11-07, see DECISIONS; R3).
3. **Location** (04 step 2): `location.type == none`, or `location.confidence < location_confidence_min`, or `location.text` is null or blank
   (empty or only ASCII whitespace: space, tab, newline, carriage return, form feed, vertical tab), or (`type == landmark` and `street_list_hits` is empty)
   → `abstain` / `no_usable_location`, rule `R-ABS-LOC`. Addresses and coordinates need no list hit.
   The null/blank text check is changed 2026-11-07, see DECISIONS (R11: nowhere to send a crew). DECISIONS names `address` and `coords`; it applies to every
   type, since `none` already fails and a `landmark` without text cannot have a list hit.
4. **Tier** (04 step 3, below), including the `low_confidence_hazard` abstain.
4a. **Hoax** (04 step 5, moved; added): `hoax_marker_hits ≥ hoax_marker_min` **and** `is_request.confidence < hoax_request_confidence_max` →
   (changed 2026-11-07, see DECISIONS; R3: the check now runs after the tier)
   - if step 4 gave `prioritized` with tier P1 → stays `prioritized` at P1 with its tier rule id, and `follow_up_question` = the `possible_hoax` text;
   - otherwise (P2, P3, or a step 4 `low_confidence_hazard` abstain) → `abstain` / `possible_hoax`, rule `R-ABS-HOAX`. Never discarded: a human reviews it.
5. **Duplicates and contradictions** (04 steps 4–5), only when steps 4–4a gave `prioritized` and `duplicate_candidates` is non-empty:
   - a candidate with `contact_match == true` whose `people_count` and the request's `people_count.value` are both non-null and **different** → `abstain` / `conflicting_counts`, rule `R-ABS-CNT`.
     Its `provisional_tier` is the highest of the request's `weak_tier` and every candidate's non-null `priority_tier` (changed 2026-11-07, see DECISIONS; R4).
   - else `outcome = duplicate`, `duplicate_of` = the first candidate (oldest), `priority_tier` = the highest of the request's own tier and every candidate's non-null `priority_tier` (P1 > P2 > P3), rule `R-DUP-01`.
     `follow_up_question`: the request's own question (confirm, `low_confidence_hazard` or `possible_hoax` text) is kept when the request's own tier is higher
     than every candidate's non-null tier, because then its own signals set the group tier, **or** when the request's `weak_tier` is higher than the
     group `priority_tier`, because then the question asks about a raise above the group tier (a weak P1 hint on a P2 duplicate keeps the
     `low_confidence_hazard` text); otherwise null (changed 2026-11-07, see DECISIONS; R9).
6. Otherwise the output of steps 4–4a.

Equality at a boundary counts as passing the minimum (`≥`): confidence 0.6 is enough for a request or a location. Confidence strictly below the minimum fails.

**`duplicate_of`** (changed 2026-11-07, see DECISIONS; R10, documentation only): candidates are **direct** matches (`contracts/logic.md`, no transitive closure),
so `duplicate_of` is the oldest *direct* candidate. It can be an intermediate member of a group rather than the group's earliest request (vector "window edge is
inclusive; groups are transitive, candidates are direct" in `contracts/vectors/duplicate_groups.json`). Consumers that need the group or its earliest request
must resolve groups with `duplicate_groups` (`contracts/logic.md`), not by following `duplicate_of` once.

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

**Extraction convention for acute medical emergencies** (changed 2026-11-07, see DECISIONS; R6): the hazard enum has no medical-emergency kind, so any
acute medical emergency (unconscious, not breathing, chest pain, severe bleeding, someone drowning) is extracted as hazard `injury`, which reaches `R-P1-02`.
Fixture C25 shows it. `R-P1-04` stays as the plan states it (child, elderly, disabled); `medical` and `pregnant` vulnerable entries reach `R-P2-02` only.

**Uncertainty (04: "treat as the higher tier and add a follow-up question; never lower"):**
- `firm_tier` = tier using only firm hazards/vulnerables. `uncertain_tier` = tier using firm + uncertain ones. `weak_tier` = tier using firm + uncertain + weak ones.
- If `weak_tier` is higher than `uncertain_tier` (a weak signal would raise the tier; changed 2026-11-07, see DECISIONS; R2):
  - if `uncertain_tier` is P3 → `abstain` / `low_confidence_hazard`, rule `R-ABS-HAZ` (added: routed to a human);
  - else (P2) → `prioritized` at `uncertain_tier` with its rule id, and `follow_up_question` = the `low_confidence_hazard` text. It is never lowered to an untiered abstain.
  - The `low_confidence_hazard` text is the vulnerable-person variant when the raise needs a weak vulnerable entry, that is when the tier using weak hazards
    but only firm + uncertain vulnerable entries is not as high as `weak_tier`; else the plain variant (changed 2026-11-07, see DECISIONS; R13).
- Else the request's tier is `uncertain_tier` and its rule id comes from that evaluation. If `uncertain_tier` is higher than `firm_tier`, set `follow_up_question` to the "confirm" text below.
- The tier is never lowered because a signal is uncertain.

## Outputs per outcome
| outcome | priority_tier | provisional_tier | duplicate_of | abstain_reason | follow_up_question |
|---|---|---|---|---|---|
| `prioritized` | P1/P2/P3 | null | null | null | null, or: the confirm text when step 4 raised the tier on an uncertain signal; the `low_confidence_hazard` text when a weak signal would raise a P2 (R2); the `possible_hoax` text for a P1 possible hoax (R3) |
| `duplicate` | highest of the group (kept) | null | request_id of the oldest candidate | null | null, or the request's own question when its own tier sets the group tier or its `weak_tier` is above the group tier (R9) |
| `abstain` | null | **required**: P1/P2/P3 (R1) | null | the reason | required (text below) |
| `not_a_request` | null | null | null | null | null |

`provisional_tier` (changed 2026-11-07, see DECISIONS; R1) is the step 4 tier read with firm, uncertain and weak signals together (`weak_tier`), whatever the abstain cause;
for `conflicting_counts` it is also raised to the highest candidate tier (R4). It ranks follow-ups: boards sort them by `provisional_tier`, then by the within-tier key.
It is never a decision: an abstain is still routed to a human.

`rules_version` in the output is the version of the engine that produced it. The source tree emits the placeholder `rules@0000000+bin:00000000`;
`python -m rescue_engine.build` stamps the real version into the engine it publishes to `engine/dist/<rules_version with ':' as '-'>/` (changed 2026-11-07, see DECISIONS; A2, WINPATH-1).
Fixtures use the placeholder; compare everything except `rules_version` against fixtures.

## Follow-up questions (exact strings)
| used for | text |
|---|---|
| `no_usable_location` (04) | `What's the street address, or the nearest cross streets or landmark?` |
| `low_confidence_request` | `Is this a request for help? If so, what do you need and where are you?` |
| `possible_hoax` | `Can you confirm this request is real and tell us exactly what help you need?` |
| `low_confidence_hazard` | `Can you confirm what is happening right now: is water rising, is anyone injured, trapped or in danger?` |
| `low_confidence_hazard`, vulnerable-person variant (R13) | `Can you confirm what is happening right now: is water rising, is anyone injured, trapped or in danger, and is a child, elderly or disabled person with you?` |
| `conflicting_counts` | `We have different counts for this location. How many people need help right now?` |
| confirm (tier raised on uncertain signal) | `Please confirm: is anyone in immediate danger (rising water, injury, fire, trapped), and is a child, elderly or disabled person with you?` |

## Rule ids
`R-NAR-01`, `R-NAR-02`, `R-ABS-REQ`, `R-ABS-HOAX`, `R-ABS-LOC`, `R-ABS-HAZ`, `R-ABS-CNT`, `R-DUP-01`, `R-P1-01…05`, `R-P2-01…04`, `R-P3-01…04`.
