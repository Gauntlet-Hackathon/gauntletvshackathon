# CLAUDE.md

Project rules for every agent and human.

## Product
Offline rescue triage (VERSUS hackathon). A small model reads messy rescue requests on a field laptop, a deterministic
decision engine ranks them, and everything syncs to a cloud dashboard when a connection appears.
Read before you build: `docs/plan/` (the product plan; `03-data-and-interfaces.md` and `04-decision-rubric.md` are the
source of truth) and `docs/IMPLEMENTATION_PLAN.md` (lanes, waves, ticket order). Interfaces live in `contracts/`;
decisions that were not in the plan live in `docs/DECISIONS.md`. Build against the contracts, never against another lane's code.

## Stack
- `field/`, `engine/`, `evals/`, `shared/`: Python 3.12 (never the system python). Use the repo virtualenv:
  `python3.12 -m venv .venv && source .venv/bin/activate`, then
  `pip install -c constraints.txt --group dev -r shared/requirements.txt -r field/requirements.txt -r engine/requirements.txt -r evals/requirements.txt`.
  Lane dependencies go in the lane's own `requirements.txt` and must be pinned in `constraints.txt` (integrator-owned).
- `cloud/`: ONE Elixir/Phoenix app (sync API + LiveView dashboard, Ecto + Postgres). Elixir and OTP versions are in
  `cloud/.tool-versions`. Read `cloud/CONVENTIONS.md` before writing cloud code. Tests need Postgres:
  `DATABASE_URL=postgres://postgres:postgres@localhost:5432` (CI and the agent runners set it).
- Tests marked `@pytest.mark.slow` (real model downloads) are skipped by default and in CI.

## Commands
Run your lane's command before opening a PR. All of them run from the repo root.
- `field` lane: `ruff check field && pytest field`
- `engine` lane (engine/ + evals/): `ruff check engine evals && pytest engine evals`
- `cloud` lane: `cd cloud && mix format --check-formatted && mix compile --warnings-as-errors && mix test`
- integrator (`shared/`, `contracts/`): `ruff check shared && pytest shared`
- night-shift picker: `python -m unittest discover -s .github/night-shift/tests`
- Everything CI runs: `ruff check . && pytest`, plus the `cloud` and picker commands above.

## Agent coordination
See AGENT_RULES.md. Summary: work only on issues in your lane, one issue = one branch = one PR, never push to main, write one line to a new file progress/<lane>/<YYYY-MM-DD>-issue-<n>.md per change
(one file per change, so parallel PRs never conflict; never append to progress/<lane>.md or a shared progress.md).
New field screens go in `field/rescue_field/screens/<name>.py` with a module-level `router`; they register themselves (no `api.py` edit).

## Ticket path mapping (read before every ticket)
The tickets were written before the packages were named. Wherever a ticket says the left side, use the right side:

| Ticket says | Use |
|---|---|
| `field/field_app/<file>` (incl. `templates/`) | `field/rescue_field/<file>` |
| `field_app.<module>` (imports, e.g. `field_app.slm_hf`) | `rescue_field.<module>` |
| `engine/fallback/<file>` | `engine/rescue_engine/fallback/<file>` |
| `engine/build.py`, `engine/replay_diff.py` | `engine/rescue_engine/build.py`, `engine/rescue_engine/replay_diff.py` |
| `python -m engine.fallback` / `python -m engine.build` | `python -m rescue_engine.fallback` / `python -m rescue_engine.build` |
| `evals/<file>.py`, `evals/baselines/…`, `evals/systems/…` | `evals/rescue_evals/<file>.py`, `evals/rescue_evals/baselines/…`, `evals/rescue_evals/systems/…` |
| `python -m evals.runner` (and other `evals.*`) | `python -m rescue_evals.runner` |

Unchanged: tests stay in `<lane>/tests/`; data stays at `engine/dist/`, `engine/mercury/`, `evals/sets/`, `evals/drafts/`, `evals/results/`; `cloud/` paths are as written.
Running a module outside pytest needs the lane folders on the path: `PYTHONPATH=shared:field:engine:evals python -m rescue_engine.fallback`.
The field engine client (F2) runs `ENGINE_CMD` (default `python -m rescue_engine.fallback`) with `PYTHONPATH` set to include `engine/` and `shared/`.
The field FastAPI app is `app` in `field/rescue_field/api.py` (the Dockerfile runs `uvicorn rescue_field.api:app`).

## Runtime configuration contract (Docker, compose and Fly use exactly these names)
Field app (read from environment, with these defaults for tests):
- `DEVICE_ID` (e.g. `laptop-dev-01`), `DEVICE_TOKEN` (bearer token for the cloud), `CLOUD_URL` (e.g. `http://cloud:4000`)
- `FIELD_DATA_DIR` (directory for the SQLite file; default `./data` locally, `/data` in Docker)
- `ECC_NAME` (default `ECC 1`), `VOLUNTEER_NAME` (default generated from `DEVICE_ID`, e.g. `Jordan K.`), `VOLUNTEER_ROLE` (default `Volunteer`),
  `SHELTER_NAMES` (comma-separated; default generated, e.g. `Shelter 1 (Lincoln High)`): demo values for the volunteer app shell
- `DEMO_MODE` (`1` enables POST /sync/offline-toggle), `ENGINE_CMD` (default `python -m rescue_engine.fallback`)
- The FastAPI app is `app` in `field/rescue_field/api.py`; it serves on `$PORT` (default 8000).
Cloud app: standard Phoenix release env (`DATABASE_URL`, `SECRET_KEY_BASE`, `PHX_HOST`, `PORT`). Device tokens must include a dev
device `laptop-dev-01` with token `dev-token-laptop-dev-01` in dev and test, and read extra `device_id:token` pairs from env
`DEVICE_TOKENS` (comma-separated) in prod, so deploys need no code change.
