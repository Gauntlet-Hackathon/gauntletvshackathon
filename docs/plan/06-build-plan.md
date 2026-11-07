# 06 · 24-hour build plan

Assumes 2–3 people. Training happens separately (see `02-architecture.md`, section 4). Times are hours from start.

## Workstreams

- **A. Field app:** FastAPI + HTMX, SQLite + outbox, SLM loading and inference, Mercury call.
- **B. Mercury engine:** rubric as rules, JSON interface, determinism declarations, Python fallback.
- **C. Cloud:** gateway, Kafka, projection, read view, cloud UI, SSE.
- **D. Evals + data:** test sets, labeling, eval runner, baselines, evals panel.
- **E. Model (separate track):** you train and push to Hugging Face.

## Schedule

| Hours | A · Field | B · Mercury | C · Cloud | D · Evals + data |
| --- | --- | --- | --- | --- |
| 0–1 | Repo, container skeleton, event schema | Write rubric v1 with team (`04`) | Provision Kafka + Postgres + hosting | **Check public dataset licenses**; write sealed holdout (person not building the model) |
| 1–3 | Intake form, SQLite events + outbox in one transaction | **Python fallback engine** behind JSON interface | Gateway: `/health`, `/sync/heartbeat`, `/sync/events`, device registry | Golden set generation + hand checking begins |
| 3–6 | Board UI (ranked list, follow-up queue), stub SLM output | Mercury rules: tiers, abstain, duplicates; `det` declarations | Projection consumer → Postgres; fleet panel with stale/offline logic | Hard + abstain sets; eval runner skeleton; regex baseline |
| 6–9 | Sync worker: ping, backoff, batches, delete-on-ack, down-sync | Mercury ↔ Python subprocess; parity tests vs fallback | Records + field-activity panels; SSE live updates | Run Opus 5.5 baseline (store results); generic-SLM baseline |
| 9–12 | **Integrate real SLM** from HF (pinned revision) | Embed `rules_version`; publish versioned binary | Replay verifier with version lookup; model puller | Run candidate model through eval runner; first results table |
| 12–15 | Hosted field demo deploy + "simulate offline" toggle | Fix rubric issues found in evals | Evals panel; model registry + promotion gate; down-sync of model | Calibration chart; consistency runs; two-point scoring |
| 15–18 | End-to-end: laptop offline → reconnect → cloud | Freeze rules for demo | Corrections flow → topic → review queue → export | Error analysis; second model iteration if you have one |
| 18–21 | Polish UI; seed demo data (200 messy requests) | Replay-with-newest-rules diff (bonus) | Polish dashboard; load test with a surge | **Open the sealed holdout once**; record results |
| 21–23 | Demo rehearsal ×3 | | | Final results table + slide |
| 23–24 | Buffer | | | Buffer |

## Cut lines (if behind, cut in this order)

1. Replay-with-newest-rules diff
2. Down-sync of other sites' events
3. Corrections → export flow (keep the UI button, log only)
4. Kafka → use Postgres as the event store, keep the same event schema (say Kafka is the production path)
5. Mercury → ship the Python fallback, show Mercury on a slide (last resort)

**Never cut:** the evals table, the offline demo, abstain behavior, the sealed holdout.

## Definition of done

- Both public URLs live (hosted field demo + cloud).
- Offline → reconnect demo works three times in a row.
- Evals table complete with all four columns and the sealed holdout reported.
- Every decision in the UI shows rule ID, rules version, model version.
