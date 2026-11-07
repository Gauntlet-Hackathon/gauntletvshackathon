# Offline rescue triage

Rescue requests arrive messy and often without a connection. On a field laptop a small model reads each request and
extracts typed fields with confidence; a deterministic, readable decision engine (never the model) ranks them and says
what to ask next when something is missing. Everything is stored locally first and syncs to a cloud dashboard when a
link appears. (VERSUS hackathon.)

## Architecture

```mermaid
flowchart TB
  subgraph FIELD["Field laptop (works offline)"]
    IN["Rescue requests<br/>posts, forms, CSV"] --> GW["Field app<br/>FastAPI + HTMX"]
    GW --> SLM["Extractor (SLM)<br/>typed fields + confidence<br/>(trained SLM planned, #104)"]
    SLM -->|"typed fields only, never raw text"| DE["Decision engine<br/>rules_version stamped"]
    DE --> LDB[("SQLite<br/>events + outbox")]
    LDB --> SW["Sync worker<br/>batched, idempotent by ULID"]
  end
  SW ==>|"HTTP when online"| CGW
  subgraph CLOUD["Cloud: one Phoenix app"]
    CGW["Sync API<br/>auth per device"] --> PG[("Postgres event store<br/>via Ecto")]
    PG --> RP["Replay verifier"]
    PG --> UI["LiveView dashboard<br/>fleet, records, activity,<br/>corrections, evals"]
  end
  EV["Eval runner<br/>regex vs LLM prompt vs generic SLM vs ours"] --> UI
  HF[("Model bundle download<br/>from Hugging Face (planned)")] -.-> SLM
  KAFKA[("Kafka event log (planned)")] -.-> PG
```

[Full diagram](docs/plan/diagram/architecture.png) (the full diagram shows the longer-term design, including Kafka;
today the Postgres event store is the source of truth, see `docs/DECISIONS.md`).

## Run it

```bash
cp .env.example .env        # then set SECRET_KEY_BASE (e.g. openssl rand -base64 48)
docker compose up --build   # services: db, cloud, field
```

- Field app: <http://localhost:8000> (volunteer queue); technical board: <http://localhost:8000/board>
- Cloud dashboard: <http://localhost:4000>
- The field app is registered as the dev device `laptop-dev-01` (token `dev-token-laptop-dev-01`).

End-to-end check (needs the stack from above; sends requests, stops the cloud, checks the outbox queues and drains
after restart): `scripts/smoke.sh` (`--no-docker` skips the cloud stop/start steps, for a deployed stack).

## Run the tests

Python 3.12 virtualenv, then install as in `CLAUDE.md`:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -c constraints.txt --group dev -r shared/requirements.txt -r field/requirements.txt -r engine/requirements.txt -r evals/requirements.txt
```

- field: `ruff check field && pytest field`
- engine and evals: `ruff check engine evals && pytest engine evals`
- shared and contracts: `ruff check shared && pytest shared`
- cloud (needs Postgres, `DATABASE_URL=postgres://postgres:postgres@localhost:5432`): `cd cloud && mix format --check-formatted && mix compile --warnings-as-errors && mix test`
- everything CI runs: `ruff check . && pytest`, plus the cloud command

## Demo walkthrough (about 3 minutes)

From `docs/plan/07-demo-script.md`:

1. **The line.** "Every AI demo assumes you have internet. Disasters are exactly when you don't." The user is a volunteer coordinator at a command post with no signal.
2. **Board intake.** Open the field board (<http://localhost:8000/board>). Go offline (with `DEMO_MODE=1` set on the field app, `POST /sync/offline-toggle` pauses sync; the sync badge shows "Offline (simulated)"), then paste a batch of messy requests (or upload a CSV) in the intake panel. The board ranks them: P1s on top, duplicates merged, and a follow-up queue for anything without a location, each with the exact question to text back.
3. **Trust boundary.** Click **Details** on one decision: the extracted fields with confidence, the rule that fired, rules and model versions. The model only reads; readable rules decide.
4. **Cloud fleet.** Open <http://localhost:4000/fleet>: the device shows as stale with the outbox backlog climbing. Back in the field app toggle offline again to go back online and watch the outbox drain and records appear live under `/records`.
5. **Replay.** On the cloud dashboard (<http://localhost:4000/>) the replay verifier shows decisions re-run with the same rules version: 0 mismatches.
6. **Evals.** Open <http://localhost:4000/evals>: one table comparing regex, an LLM prompt, a generic small model and ours. Headline metric: urgent requests missed per 1,000.
7. **Close.** "You can read every rule, replay every decision, see every eval, and it tells you when it doesn't know."

## Eval results

Results are produced by `python -m rescue_evals.report` into `evals/results/TABLE.md`; not run yet (that file does not
exist until the report is generated). The decision rubric is a draft that still has to be validated with emergency
managers.

## Working on this repo

Team repo for the Gauntlet vs hackathon. Agents work GitHub issues in lanes; see RUNBOOK.md (night shift) and AGENT_RULES.md (coordination rules).
