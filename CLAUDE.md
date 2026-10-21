# sourcewatch: rules for agents

Instructions for coding agents working in this repo; a human reader wants `README.md`. Read
`README.md` for how it works, `RUNBOOK.md` for procedures, `TASK.md` for what is open. The
owner's global agent rules apply on top of these.

- Public repo: no personal data, account figures, third-party names or the owner's email in any file; a pre-commit scan blocks them.
- Real-time commits only, without exception: the history is the product. Never set `GIT_AUTHOR_DATE`.
- Release steps: `RUNBOOK.md` §Release.
- README, RUNBOOK, CLAUDE and TASK each hold one kind of fact; the README commands table is
  generated, never hand-edited.
- Traps: never commit observations or `data/` to `main`, they belong to the bot on the `data` branch; never store rows from a source, only observations; tests must not touch the network and dates are injected; observatory is pinned by tag, bump it with `uv lock` in its own commit.
