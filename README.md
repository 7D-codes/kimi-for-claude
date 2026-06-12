# kimi-mcp

MCP server that lets Claude Code delegate tasks to the [Kimi CLI](https://moonshotai.github.io/kimi-cli/) agent. Claude is the manager; Kimi is the worker.

Design principle: save the manager's tokens. Results are truncated at 6k chars, file changes come back as a one-line git summary (not a diff), and Claude is expected to ask Kimi questions via `kimi_continue` instead of reading his artifacts in full.

## How it works

`server.py` is a single-file FastMCP stdio server (PEP 723 inline deps, run by `uv`). It shells out to:

```
kimi --print --final-message-only (--yolo | --plan) -p "<task>" [-w dir] [-m model] [-C]
```

## Tools

- **`kimi_delegate(task, work_dir?, model?, timeout_seconds=600, readonly=False, background=False)`** — fresh autonomous session. `readonly` = plan mode (investigate, don't write). `background` = returns a job id immediately.
- **`kimi_continue(prompt, work_dir?, ...)`** — follow-up on the most recent session in that work_dir (kimi `-C`).
- **`kimi_status(job_id?)`** — list background jobs, or collect one's result.
- **`kimi_cancel(job_id)`** — kill a running job.

If `work_dir` is a git repo, results end with `[git: N file(s) changed during task: ...]` computed from before/after `git status --porcelain` snapshots.

## Setup

Registered at user scope:

```
claude mcp add --scope user kimi -- uv run --script /home/toro/projects/kimi-mcp/server.py
```

After editing server.py, restart the server: `/mcp` → reconnect kimi (or start a new session). Remove with `claude mcp remove kimi -s user`.

## Notes / limits

- Background jobs live in the server process: they're lost if the session ends, and Claude is not notified on completion — it must poll `kimi_status`.
- One job per work_dir at a time (kimi `-C` sessions are tracked per directory).
- Non-readonly runs use `--yolo`; the safety gate is Claude Code's permission prompt on the MCP call itself.
- Kimi auth comes from `~/.kimi/`. Test manually: `kimi --quiet -p "hello"`.

## Ideas not built yet

Transcript/audit tool (kimi `export` / `--output-format stream-json`), session-id addressing (multiple parallel sessions per dir), worktree isolation, custom worker personas via `--agent-file`, MCP passthrough to give Kimi extra tools, usage/quota visibility.
