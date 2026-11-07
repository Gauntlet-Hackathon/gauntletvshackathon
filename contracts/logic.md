# Shared logic spec

`shared/rescue_shared/logic.py` is the Python implementation; the Elixir cloud re-implements the first two functions.
Both must pass `contracts/vectors/*.json` (inputs → expected outputs written by hand from this spec). If the spec and a vector disagree, fix
the one that is wrong in a PR to `contracts/`; never change a vector just to make code pass.

## `normalize_location(text) -> string`
Equal output means "same place". `""` means no usable key. Steps, in order:
1. `null`, empty or whitespace-only input → `""`.
2. **Coordinates:** if the whole text is two decimal numbers (`-?\d{1,3}\.\d+`, both with a decimal point) separated by a comma, semicolon or
   whitespace → `geo:<lat>,<lon>`, each rounded to 4 decimals (`%.4f`). Example: `29.760412,-95.369811` → `geo:29.7604,-95.3698`.
   (Vectors avoid exact .5 ties at the 5th decimal.)
3. Unicode NFKD, delete combining marks (accents), case-fold (lowercase).
4. Delete apostrophes (`'` and `’`); replace `&` with ` and `.
5. Replace every run of characters outside `a-z0-9` with one space; split on spaces. (Non-Latin letters are dropped.)
6. Replace whole tokens using the table: `st→street ave/av→avenue blvd→boulevard rd→road dr→drive ln→lane hwy→highway fwy→freeway pkwy→parkway
   ct→court cir→circle pl→place trl→trail ter→terrace n→north s→south e→east w→west ne→northeast nw→northwest se→southeast sw→southwest`.
   (`st` is always `street`, so "St. Joseph" becomes `street joseph`; both sides of a comparison get the same treatment.)
7. Drop stop words: `the a an at near by behind in on off next to across from of around outside inside front beside between just past over under close`.
8. Sort the remaining tokens (ASCII order, digits before letters) and join with single spaces.

## `normalize_contact(contact) -> string`
Keep digits only. If the result has 11 digits and starts with `1`, drop that `1`. `""` means no contact.

## `street_list_hits(location_text, streets) -> [string]`
For each list entry (in list order): tokenize the entry like steps 3–7 above, remove street-type words
(`street avenue boulevard road drive lane highway freeway parkway court circle place trail terrace`); the entry **hits** when what is left is
non-empty and every remaining token appears in the location's tokens. Returns the original entry strings. Known limitation: an entry that is only a
street name (`Memorial Drive`) also hits a landmark that shares the name (`Memorial Park`). The field pipeline passes the result as
`context.street_list_hits`.

## `hoax_marker_hits(text, markers) -> int`
Case-fold the text and each marker and collapse whitespace. Count the **distinct, non-blank markers** that occur in the text as whole words or phrases (not
inside a longer word). The list is configuration; `contracts/fixtures/hoax_markers.txt` is the default. The field pipeline passes the count as `context.hoax_marker_hits`;
the engine never sees text.

## `duplicate_candidates(requests, window_hours) -> {request_id: [candidate]}`
Input items: `request_id`, `received_at` (UTC `…Z`), `location_text`, `people_count` (int or null), `contact` (string or null), `priority_tier` (P1/P2/P3 or null).
B is a **candidate** of A when all hold:
- B is earlier than A, ordering by `(received_at, request_id)`;
- `A.received_at − B.received_at ≤ window_hours × 3600` seconds (inclusive);
- `normalize_location` of both is equal and not `""`;
- **contact_match** (normalized contacts equal and not `""`) **or** both `people_count` are non-null and equal.

Each candidate is `{request_id, received_at, people_count, contact_match, priority_tier}` of B (the `engine_input.context.duplicate_candidates` shape), sorted oldest first.
Every input request is a key in the result, with `[]` when it has no candidate. Candidates are **direct** matches only (no transitive closure).

## `duplicate_groups(requests, window_hours) -> [[request_id]]`
Connected components of the candidate relation (the cloud projection uses these to merge duplicates across sites). Groups with 2+ members only; members sorted oldest first
by `(received_at, request_id)`; groups sorted by their oldest member.

## `within_tier_sort_key(extracted, received_at, request_id) -> tuple`
Sort ascending within one tier (04 "Ordering within a tier"): `(−vulnerable_count, −people, received_at, request_id)`, where `vulnerable_count` is the number
of `vulnerable` entries with confidence ≥ 0.5 and `people` is `people_count.value` (null counts as 0). Rank tiers P1, P2, P3 first, then use this key.
