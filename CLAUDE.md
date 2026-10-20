# sourcewatch: rules for agents

Read `README.md` for how it works, `RUNBOOK.md` for procedures, `TASK.md` for what is open.
Global rules in `~/.claude/CLAUDE.md` still apply.

- Public repo: no personal data, account figures, third-party names or the owner's email in any file; the git gate blocks them.
- Real-time commits only, without exception: this repo's value is that it visibly ran over time. Never backdate, never set `GIT_AUTHOR_DATE`, and `/ship` must not ask for a window here.
- Releases follow hq CONVENTIONS (`~/developer/hq/CONVENTIONS.md`); the RUNBOOK names the steps.
- Docs follow the four-file skeleton: a fact lives in one file, and the commands table is generated.
- Traps: never commit observations or `data/` to `main`, they belong to the bot on the `data` branch; never store rows from a source, only observations; tests must not touch the network and dates are injected; observatory is pinned by tag, bump it with `uv lock` in its own commit.
