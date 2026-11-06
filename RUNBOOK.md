# Night-shift runbook

Agents run on GitHub's runners after every merge to main (and every 20 minutes as a backstop), one per lane, so laptops can sleep. Each run: pick the next ready issue in the lane, run Claude Code headless on it, open a PR, and auto-merge it once `test` (CI) and `verdict` (independent Claude review) pass.

```
cron */20 ─► night-shift.yml ─► night-shift-lane.yml, once per lane (one run at a time per lane)
   job pick     (AGENT_PAT)       .github/night-shift/agent_pick.py: kill switch → lane busy? → main red? → next unblocked issue
                                  writes task.md from the issue + member comments only, records the attempt
   job agent    (Claude token)    claude -p on its own runner, no GitHub write access → result.json + git bundle
   job publish  (AGENT_PAT)       clean runner: rejects changes to protected paths, pushes, opens PR, auto-merge
PR ─► ci.yml (test) + agent-review.yml (verdict, prompt and CLAUDE.md taken from main) ─► auto-merge into main
      failed / conflicted / stale PR ─► picker closes it, issue returns to the queue (max 3 attempts → blocked)
      run that never started (expired token, usage limit, setup failure) ─► refunded, capped at 6 per issue
```

**Who holds which secret.** Each lane run gets exactly two secrets (its Claude token and `AGENT_PAT`), and the agent's machine gets only the Claude token. **Protected paths** that agent PRs may never change: `.github/` (workflows, picker, prompts, these tests), any `.claude/` folder, any `CLAUDE.md` or `AGENTS.md`, `.mcp.json`. They control the picker, the review gate and every agent's instructions, so only humans edit them.

## Setup (about 45 minutes; do it on a throwaway repo first)

1. **Copy the kit** into the repo root: `.github/` (workflows plus `night-shift/` with the picker, prompts, setup script and tests), `AGENT_RULES.md`, `RUNBOOK.md`. Paste `AGENT_RULES.md` into `CLAUDE.md`.
2. **Edit the lanes** in `.github/workflows/night-shift.yml` (`matrix.include`). Lane names must match the `lane:<name>` labels.
3. **Run setup:** `.github/night-shift/setup_repo.sh <owner/repo> <lane> <lane> ...` creates labels, enables auto-merge, sets `AGENTS_ENABLED=false` and `FIX_LANE`, creates the night-log issue (pin it), and protects `main` for everyone, admins included (so humans merge through PRs too).
4. **Add secrets** (Settings → Secrets and variables → Actions):
   - `AGENT_PAT`: fine-grained token limited to this repo. Contents, Issues and Pull requests: read and write. Actions: read (without it the picker can't see CI and every lane waits). **Do not grant Workflows**: that is what stops an agent's PR from editing CI or the review gate. A token is needed instead of the built-in one because pushes and PRs made with the built-in token don't start CI.
   - `CLAUDE_TOKEN_<PERSON>`: each person runs `claude setup-token` and sets their own secret (`gh secret set CLAUDE_TOKEN_<PERSON> -R <owner/repo>`, paste). Each lane's `token_secret` points at its owner's secret, so a person's lanes all spend their plan.
   - `CLAUDE_TOKEN_REVIEW`: from someone else, for the review gate.
5. **Replace the CI test step** in `ci.yml` with the real stack commands once Wave 0 picks the stack.
6. **Turn it on:** set the variable `AGENTS_ENABLED=true`.

## From Idea Factory to running lanes
1. The Idea Factory's github stage publishes issues labeled `epic:E01`, `wave:W01`, ..., with a "Blocked by: #n" links block. The picker reads those formats directly.
2. Map epics to lanes: `.github/night-shift/assign_lanes.sh <owner/repo> E01=api E02=web E03=data`. It lists any issue left without a lane.
3. Lane owners label what agents may do alone: `gh issue edit 12 13 14 -R <owner/repo> --add-label night-safe`.
4. Humans claim a ticket by assigning themselves; the picker skips assigned issues.

## Rehearsal (do before the event)
1. Create two small issues in one lane, labeled `lane:<x>`, `night-safe`, `wave:1`. Make the second say `Blocked by #<first>`.
2. Actions → night-shift → Run workflow with that lane.
3. Check: the run shows three jobs (pick, agent, publish), the attempt comment appears on issue 1, a PR `agent/<x>/issue-<n>` opens, `test` and `verdict` run, the PR merges, issue 1 closes.
4. Run again: it should now pick issue 2.
5. Break it on purpose: set `AGENTS_ENABLED=false` and confirm the next run does nothing.

## Before you sleep
- [ ] Main is green and the demo path works end to end.
- [ ] Each lane has 6–8 hours of `night-safe` issues with clear acceptance criteria and nothing waiting on a human decision.
- [ ] Each lane also has filler issues (tests, error handling, docs) so it doesn't sit idle when its main work is blocked.
- [ ] Lane tokens are fresh, and everyone has usage left on their plan.
- [ ] Someone has the kill switch on their phone: GitHub mobile → repo → Settings → Variables → `AGENTS_ENABLED`.

## Morning
1. Read the pinned night-log issue.
2. Triage `needs-human` and `blocked` issues; answer the questions in comments, then remove the label.
3. Skim merged agent PRs from the night and run the demo path.

## Troubleshooting
| Symptom | Likely cause |
|---|---|
| Workflow never starts | `AGENTS_ENABLED` not `true`; scheduled runs only run from the default branch; GitHub's cron can lag by several minutes |
| "Secret CLAUDE_TOKEN_X is empty" | Secret name doesn't match `token_secret` in the matrix |
| PR opens but no checks run | PR was created with the built-in token instead of `AGENT_PAT` |
| Lane stuck on "still in review" | A PR is waiting on checks. Failed, conflicted, or checkless after 45 min PRs are closed automatically |
| Every lane skips with "main is red" | Expected: `FIX_LANE` is fixing it. After 3 tries on the same failing commit it stops ("fix-main gave up"); fix main by hand |
| Every lane skips with "cannot read CI status" | `AGENT_PAT` is missing Actions: read |
| Night log shows "infra-error" | Lane token expired or plan usage ran out. The attempt wasn't counted; refresh the token or wait for the usage window |
| "rejected: agent changed a protected path" | Working as intended; make that change yourself |
| PR closed with `review-error` label | The review token expired or ran out of usage; the attempt was not counted. Refresh `CLAUDE_TOKEN_REVIEW` |
| Issue `blocked` after "runs that never started" | That lane's Claude token is expired or out of usage |
| Branch protection step fails | Private repos on free plans can't use it. Make the repo public for the hackathon |

## Model routing
- **First try:** the lane's `model` in `night-shift.yml` (default `sonnet`: fast, and spares everyone's usage).
- **Retry or fix-main:** `ESCALATE_MODEL` (default `opus`). A ticket that failed once gets the stronger model.
- **Per ticket:** add a label like `model:opus` or `model:haiku` to override (create the label first).
- **Review gate:** `REVIEW_MODEL` (default `opus`): the last check before main is worth the stronger model.
- The night log shows `[model]` on every line, so in the morning you can see which tickets needed escalation.

## Tunables (repo variables)
`READY_LABEL` (night-safe) · `FIX_LANE` · `CI_WORKFLOW` (ci.yml) · `MAX_TURNS` (80) · `CLAUDE_CODE_VERSION` (latest; pin after the rehearsal) · `CLAUDE_PERMISSION_FLAGS` (`--dangerously-skip-permissions`; the runner is disposable and holds no push credentials) · `ESCALATE_MODEL` (opus) · `REVIEW_MODEL` (opus)
