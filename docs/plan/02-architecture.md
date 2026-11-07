# 02 · Architecture

See `diagram/architecture.png`. Three deployable parts plus one external dependency:

1. **Field system:** runs on a laptop, works offline.
2. **Hosted field demo:** the same field container at a public URL, for judges.
3. **Cloud system:** fleet dashboard, records, evals, model intake. Public URL.
4. **External:** your Hugging Face model repo, trained separately.

---

## 1. Field system (laptop)

Single container (or a single `docker compose`), no network required after install.

| Component | Tech | Job |
| --- | --- | --- |
| API gateway + UI | Python, FastAPI, serves HTMX | Intake form/paste/CSV upload, ranked board, follow-up queue |
| SLM | Fine-tuned small model from Hugging Face, pinned version | Extract typed fields + confidence. **Never decides.** |
| Decision engine | **Mercury**, compiled binary | Applies the rubric. Input: typed fields only, never raw text. Output: priority tier, dedupe group, abstain reason, rule ID |
| Local DB | SQLite (WAL mode) | Event log + local read view for the UI |
| Outbox | Table in the same SQLite DB | Durable queue of events to sync |
| Sync worker | Python background task | Ping cloud, push outbox, pull approved updates |

### Trust boundary (say this out loud in the demo)

`raw text → SLM → typed fields + confidence → Mercury → decision`

Mercury never sees raw text. The SLM never makes a decision. The rules are readable, versioned code.

### Mercury integration

- Interface: JSON in on stdin, JSON out on stdout, one request (or batch) per call. Called as a subprocess from Python. Keep it dumb.
- Use Mercury's determinism declarations (`det`) and exhaustive switches so the **compiler guarantees** every input gets exactly one outcome. That's a concrete trust claim no prompt can make.
- **De-risk:** write the same rules as a ~50-line Python fallback in hour one, behind the same JSON interface. If the Mercury build or FFI fights you, the demo still works and you swap Mercury in when ready.
- The binary embeds `rules_version` = git SHA of the rules + hash of the binary.

### Write path (one transaction)

On each request: insert the `received`, `extracted` and `decided` events **and** the matching outbox rows in a single SQLite transaction. If the laptop dies mid-way, either all of it exists or none of it does.

### Sync worker

1. Ping `GET /health` on the cloud gateway. Exponential backoff with jitter (e.g. 2s → 4s → … capped at 5 min).
2. On 200: send a heartbeat, then drain the outbox in batches (e.g. 200 events) to `POST /sync/events`.
3. Delete outbox rows **only after** the cloud acknowledges them.
4. Pull down-sync: other sites' `assigned` / `rescued` events (avoid two crews going to the same roof) and any newly approved model or rules version.
5. Keep the previous model and rules version locally for rollback.

### Idempotency

Every event has a ULID created on the laptop. The cloud upserts and dedupes on it, so retries and double sends are harmless.

---

## 2. Hosted field demo

- **The same container image** as the laptop, deployed to a public URL (Fly.io / Render / Railway).
- Registered in the cloud's device registry as `demo-field-01`.
- **"Simulate offline" toggle** in its UI: pauses the sync worker (blocks outbound sync). Judges can log requests, watch the outbox count climb and the cloud fleet panel turn the device stale, then flip it back and watch everything drain.
- Using the same image, not a lookalike, is what makes the offline claim credible.

---

## 3. Cloud system

| Component | Tech | Job |
| --- | --- | --- |
| Cloud API gateway | Python, FastAPI | Auth per device (device registry), accepts sync batches and heartbeats, serves down-sync |
| Device registry | Small table / config | Registered laptops and their keys |
| Kafka | Managed (Confluent Cloud or Redpanda Serverless; check free tiers) | **The only source of truth** |
| Replay verifier | Python consumer + Mercury binaries | Re-runs every decision with the **same** `rules_version` that made it; mismatch = bug or tampering |
| Versioned Mercury binaries | Container registry / artifact store | Immutable, tagged by git SHA + hash, ideally signed. Never overwrite one |
| Model puller | Python job | Pulls a pinned Hugging Face repo + revision, verifies file hashes, registers a **candidate** |
| Eval runner | Python batch job | Runs a candidate on all eval sets vs baselines; writes `eval-results` |
| Model registry | Artifact store + table | Approved model versions; laptops pull from here, never from Hugging Face |
| Projection consumer | Python consumer | Builds the read view from Kafka |
| Read view | Postgres | Disposable, rebuildable from Kafka at any time |
| Cloud UI | FastAPI + HTMX, live via SSE (htmx SSE extension) | Fleet, field activity, records, corrections, evals |

### Kafka topics

| Topic | Key | Retention | Contents |
| --- | --- | --- | --- |
| `rescue-events` | request ULID | forever (`retention.ms=-1`) | received / extracted / decided / assigned / rescued / corrected |
| `rescue-state` | request ULID | compacted | latest state per request (optional convenience) |
| `device-heartbeats` | device ID | e.g. 30 days | device ID, timestamp, outbox backlog, rules_version, model_version |
| `eval-results` | model_version | forever | per-set, per-metric results for each model and baseline |
| `corrections` | request ULID | forever | coordinator fixes to fields / flags |

**Gotcha:** Kafka's idempotent producer only dedupes within one producer session. A laptop retrying after a dropped connection can produce the same event twice, so **consumers dedupe on the event ULID**.

### Conflicts

Records are **append-only events**. Status only moves forward: `received → extracted → decided → assigned → rescued` (plus `closed_duplicate`, `escalated`). Two laptops can't overwrite each other. Cross-site duplicate merging happens in the cloud projection.

### Versioning

- **Rules:** every decision event carries `rules_version`. The replay verifier must use that exact version, not the latest, because a laptop offline for 3 days on v12 must be checked with v12. Keep every binary.
- **Bonus:** replay the whole log with the *newest* rules and show a diff of what would change. Rules updates become reviewable.
- **Model:** every extraction event carries `model_version` (HF repo + commit SHA).

### Cloud UI panels

1. **Fleet:** each registered laptop: active / stale (no heartbeat > X min) / offline (> Y min), last connection time, outbox backlog at last heartbeat, rules and model version. Computed **in the cloud from last heartbeat time**, because a laptop can't report that it went dark. X and Y in config (start: 5 and 30 minutes).
2. **Field activity since last sync:** per laptop, requests logged while offline, decisions, abstains, escalations, with timestamps from the laptop.
3. **Records:** searchable list of requests with full event history, the rule that fired, versions, and replay status.
4. **Corrections:** coordinator fixes a field or overrides a flag → `corrections` topic → human review queue → exported as a dataset for your separate training. **Never** flows into eval sets.
5. **Evals:** see `05-evals.md`.

Live updates: the projection consumer publishes changes; the UI subscribes over SSE and swaps HTMX fragments. No polling.

---

## 4. Model intake (training happens outside this system)

1. You train separately and push to a **public Hugging Face repo**.
2. The model puller pulls a **pinned revision** (commit SHA, never `main`) and verifies file hashes.
3. It becomes a **candidate** → eval runner → **promotion gate** (`05-evals.md`).
4. If it passes, it enters the model registry, and laptops pull it on their next down-sync.
5. Laptops never contact Hugging Face directly: one trust path, and every laptop runs a version that passed the evals.
6. Reviewed corrections are exported as a dataset you can use for your next training run.

**Needed from you:** the HF repo name, the model type (token-classification encoder vs small generative model with constrained JSON), and its exact output format.

### Model recommendation (for your separate training)

A **token-classification encoder** (DeBERTa-v3 / ModernBERT class, ~100–400M parameters) plus small classification heads for urgency and vulnerability is the best fit: fast on CPU, structured output by construction, and real probabilities you can calibrate (temperature scaling on a dev set). A small generative model with grammar-constrained JSON also works but is slower and harder to calibrate.
