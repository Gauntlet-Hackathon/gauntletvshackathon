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
See AGENT_RULES.md. Summary: work only on issues in your lane, one issue = one branch = one PR, never push to main,
never edit .github/, .claude/, CLAUDE.md or AGENTS.md from an agent PR, append one line to progress/<lane>.md per change
(one file per lane, so parallel PRs never conflict; never a shared progress.md).
