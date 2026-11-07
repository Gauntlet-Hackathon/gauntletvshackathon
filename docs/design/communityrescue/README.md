# CommunityRescue design specs

Design specs for the volunteer app and the Emergency Control Center (ECC) intake page. Nothing here changes app code: these are the target screens, the words volunteers see, and the gaps the backend still needs to close.

| File | What it is |
|---|---|
| `volunteer-app.html` | Clickable prototype of the volunteer app, used in the field: Queue mode for the person sending boats, Respond mode for the boat crew. |
| `intake-simulator.html` | Intake page for the ECC laptop: creates rescue requests by form or CSV, checks each one with the field app's intake rules, then hands them to a field laptop. |
| `use-cases.md` | 15 use cases plus the grader hint system: input, contract fields, screen behavior, acceptance checks and gaps. |
| `assets/` | App icon: SVG master plus 1024, 512, 180, 64 and 32 px PNGs. |

## Where each one runs

| | Intake simulator | Volunteer app |
|---|---|---|
| Who | ECC staff | Volunteers in the field |
| Device | ECC laptop | Field laptop or phone |
| Connection | Usually online | Often offline |
| Signed in as | The ECC | The volunteer, with their ECC under the name |

**Getting requests from the ECC to the field.** Today the only paths are:

- **Same network:** enter the field laptop's address in the simulator, copy the commands, and run them in a terminal on the ECC laptop. They post each request to the field laptop's `/requests`.
- **No shared network:** save the CSV and get the file to the volunteer (USB stick, message, shared drive), who uploads it on the board.

Cloud down-sync (`GET /sync/down`) carries only `assigned` and `rescued` events, so the cloud can't deliver new requests to field laptops yet. That's gap 10 below.

## Opening them

Open either HTML file in a browser. Both work offline; without internet the fonts fall back to the system font.

- **Volunteer app:** fills a phone screen, and shows as a centered app column on a laptop. Toggle **Queue** and **Respond** under the name. Copy buttons are drawn only.
- **Intake simulator:** pick a scenario or load a CSV, then hand the requests to a field laptop as described above.

## Design system

Light mode only, sized for a phone and readable by a tired volunteer at night.

| Token | Value | Use |
|---|---|---|
| Font | Atkinson Hyperlegible | Everything; built for legibility |
| Rescue blue | `#0B57D0` | Primary buttons, selected states, icon |
| Ring orange | `#F59E0B` | Icon ring, offline dot |
| Ink | `#111418` | Text, incident badge outline |
| Muted | `#4A5260` | Secondary text |
| Line | `#D0D5DD` | Card borders |
| Ground | `#F6F7F9` | Page background |
| Urgent red | `#B00020` | "Needs help now" pill, live push to talk |
| Address green | `#E7F3E8` | Address block on job cards |
| Check-first amber | `#FFF4E5` on `#5C3300` | "Check first" pill |

Components, all in `volunteer-app.html`:

- **Header:** icon and name on the left; the signed-in volunteer on the right with role and ECC on two lines; Queue / Respond toggle; connection line ("No Internet: Local Store, Sync when online").
- **Job card:** priority pill plus incident badge (`#0147`), headline, address in a green block, details, contact, "Why is this one first?", then the Next / Waiting / Out now tabs.
- **Buttons:** primary 60 px, secondary 56 px, choice chips 48 px. Every tap target is at least 44 px.
- **ECC bar** (bottom, both modes): Push to talk, Message ECC, Send location. Message and location confirm "sends when online".
- **OSM chips** (Respond mode): Operational Status Messages: Sent to you, On the way, There, Done. Tapping one sets the status and writes the line to read to dispatch.

## Words volunteers see

| Contract value | On screen |
|---|---|
| `prioritized`, P1 | Needs help now |
| `prioritized`, P2 | Needs help soon |
| `prioritized`, P3 | Can wait |
| `abstain` | Check first, with the engine's exact follow-up question |
| `provisional_tier` | maybe Needs help now (dashed pill) |
| `not_a_request` | Set aside |
| `duplicate` | Folded into the first card as "+N other posts" |

Rule IDs, model versions and confidence scores stay off the volunteer's screen. "Why is this one first?" explains the rule in one plain sentence.

## What the backend still needs

The screens assume these. Each should become its own issue in the owning lane.

1. **Short incident numbers** with a per-laptop prefix (`A-0147`), so laptops working offline never collide. Internal IDs are 26-character ULIDs nobody can read over a radio.
2. **More rescue outcomes:** "got some out" and "nobody there" have no event type, and the drop-off destination isn't recorded.
3. **Boat roster:** name, seats, status and last check-in, entered by the dispatcher.
4. **Responder status events** (the OSM chips) and syncing them to the dispatcher's Out now list.
5. **Spanish versions** of the seven fixed follow-up questions, reviewed by a person.
6. **Adding a reply to an existing request**, for example a missing address.
7. **Grader mode:** hints and sample loading, with samples tagged and kept out of metrics and eval sets.
8. **Serving the intake simulator from the field app,** so it can post to `/requests` directly from the same address.
9. **ECC links** for push to talk, messages and location sharing.
10. **ECC to field delivery:** a way for requests entered at the ECC to reach field laptops through the cloud, for example a cloud intake endpoint plus `received` events in down-sync.

## Placeholders

- `[ECC NAME]` and `[SHELTER NAME]` need real names.
- People (Jordan K., Maria), addresses and 555 phone numbers are sample data.
