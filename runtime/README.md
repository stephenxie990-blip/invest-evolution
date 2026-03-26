# Runtime Artifacts

`runtime/` is the default home for generated outputs, logs, memory files, and state snapshots created while running `invest-evolution`.

Typical contents include:

- `outputs/` for training results and leaderboards
- `logs/` for meeting and runtime event logs
- `state/` for config snapshots, runtime plans, and ephemeral state
- `memory/` and `sessions/` for local agent workflows

These artifacts are intentionally not tracked in version control.
