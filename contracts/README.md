# contracts/

The interfaces every lane builds against. Owned by the integrator; change them only through a ticket that says so.

| Path | What |
|---|---|
| `event.schema.json`, `payloads/*.schema.json` | Event envelope and the eight payloads (received, extracted, decided, assigned, rescued, corrected, escalated, closed_duplicate) |
| `engine_input.schema.json`, `engine_output.schema.json` | Decision-engine stdin/stdout JSON |
| `engine_manifest.schema.json` | `engine/dist/<rules_version with ':' as '-'>/manifest.json` |
| `sync/*.schema.json`, `sync_api.md` | Field ↔ cloud sync API |
| `engine_rules.md` | The rubric (`docs/plan/04`) made precise: order, boundaries, rule ids, follow-up texts |
| `logic.md` | Spec of `shared/rescue_shared/logic.py` (location normalization, duplicates, hoax markers, sort key) |
| `examples/<key>.valid.json`, `<key>.invalid.json` | One valid and one invalid file per schema. Python models, Python schema tests and the Elixir changeset tests all use these |
| `examples/extra/<key>.valid.json`, `<key>.invalid.json` | Extra examples for optional properties added later (e.g. `received.incident` / `sample`, #305); the main example keeps the old shape so it also proves backward compatibility |
| `vectors/*.json` | Input → expected-output cases that `rescue_shared.logic` and the Elixir cloud must both pass |
| `fixtures/` | The 25 hand-written rubric cases, street list and hoax markers |

Python side: `shared/rescue_shared/models.py` (Pydantic), `logic.py`, `ulid.py`, `versions.py`. Check: `pytest shared`.
Common types live in `common.schema.json` (ULID, timestamp, tiers, version formats, rule ids).
