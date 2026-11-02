# ADR 0002: Agent harness findings (F5)

Status: accepted (2026-11-02)

## Tested
- **Parallel agents:** two Haiku `general-purpose` agents, each with `isolation: worktree`, were launched in one message. Both finished in about 10 s and each committed one file on its own branch. Parallel launch from a single message works.
- **Branch reporting:** each agent reported branch (`worktree-agent-<id>`), worktree path and commit hash when asked. The completion notice also carries the worktree path and branch. Keep the "state your branch" line in agent prompts.
- **Merge:** both branches merged into `main` with `--no-ff` with no conflicts (disjoint paths). Worktrees and branches were removed afterwards.
- **Agent cost:** about 67k tokens each for a trivial task, mostly fixed overhead (tool and skill definitions). One task per agent is therefore not free; batch small tasks.

## Re-check in fresh session (2026-11-02, start of L1)
- **Custom agents: confirmed.** `pipeline`, `ml`, `api`, `web`, `infra`, `docs-demo`, `labels` are all in the agent list. Note `labels` has no WebFetch/WebSearch tools (Read, Write, Edit, Glob, Grep, Bash only).
- **Auto-compact 25%: not applied.** `get_usage` still reports 97% of a 1M window although `.claude/settings.json` sets `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=25`. Do not rely on it; compact manually and keep tasks small.
- **Usage:** weekly 61% (resets in about 2d 22h), 5-hour 16%. The user has said allowance is not a concern, so the 85% stop rule and the pacing advice below are relaxed for this build.

## Not confirmed (as of F5)
- **Custom agents:** the seven `.claude/agents/*.md` files were not in the agent list of the running session, so F5 used `general-purpose` with `model: haiku`. Re-check after a new session starts.
- **Auto-compact at 25%:** this session reports auto-compact at 97% of a 1,000,000-token window. The `settings.json` env setting was added mid-session, so it is untested, not disproven. Re-check in a fresh session with `get_usage`. Note the window is 1M here, so 25% would be about 250k.

## Usage (Pro)
At the end of F5: 5-hour window 15% used, weekly (all models) 61% used, resets in about 3 days. The weekly figure is the constraint. Planning rules:
- Keep max 2 concurrent agents; prefer one at a time until the weekly figure is known to have headroom.
- Check `get_usage` before spawning and stop at about 85% weekly.
- Move bulk work (docs, scaffolding) to Haiku; reserve Opus for contracts and label analysis.
- Fable/extra usage is off and stays unused.
