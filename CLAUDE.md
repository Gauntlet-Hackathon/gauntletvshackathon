# CLAUDE.md

Project rules for every agent and human. The product sections are filled in at kickoff from the Idea Factory output.

## Commands
- Tests: `python3 -m unittest discover -s .github/night-shift/tests` (replace with the real stack's commands in Wave 0)

## Agent coordination
See AGENT_RULES.md. Summary: work only on issues in your lane, one issue = one branch = one PR, never push to main,
never edit .github/, .claude/, CLAUDE.md or AGENTS.md from an agent PR, append one line to progress/<lane>.md per change (one file per lane, so parallel PRs never conflict).
