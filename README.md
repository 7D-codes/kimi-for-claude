# kimi-for-claude

claude is a genius at managing, but using it for raw coding is a token drain. kimi has claude-level quality for a fraction of the price, but orchestrating both manually is a headache

so i built a bridge

claude acts as the supervisor, delegating the heavy coding to kimi in the background. you get claude's brain guiding the project, with kimi's budget-friendly execution

---

## What it does

Send Claude a task too large, too tedious, or better run in parallel, Claude hands it off to Kimi with full context, waits for the result (or doesn't), then picks up the conversation. Kimi gets his own workspace, his own tools (shell, file ops, web search, all skills), and runs autonomously.

---

## Tools

| Tool | What it does |
|------|-------------|
| `kimi_delegate` | Start a fresh Kimi session with a task. Returns Kimi's final answer. |
| `kimi_continue` | Follow up on Kimi's last session — ask him to summarize, fix, or extend without re-explaining context. |
| `kimi_status` | List background jobs or collect a finished one's result. |
| `kimi_cancel` | Kill a running job. |

Every tool supports:
- **`work_dir`** — Kimi's workspace. If it's a git repo, results include a one-line summary of files he changed.
- **`readonly`** — Plan mode: Kimi investigates and reports, no writes.
- **`background`** — Returns a job ID instantly so Claude can keep working in parallel.

Results are truncated at 6k chars by design, Claude asks Kimi to summarize rather than reading full output, keeping token usage lean.

---

## Requirements

- [Claude Code](https://claude.ai/code)
- [Kimi CLI](https://moonshotai.github.io/kimi-cli/) installed and authenticated (`kimi login`)
- Python 3.10+ and [uv](https://github.com/astral-sh/uv)

---

## Setup

**1. Clone the repo**
```bash
git clone https://github.com/7D-codes/kimi-for-claude
cd kimi-for-claude
```

**2. Register with Claude Code**
```bash
claude mcp add --scope user kimi -- uv run --script "$PWD/server.py"
```

**3. Verify it connected**
```bash
claude mcp list
# kimi: uv run --script ... - ✔ Connected
```

**4. Start a new Claude Code session** — the tools won't appear in your current session, only new ones.

That's it. `kimi_delegate`, `kimi_continue`, `kimi_status`, and `kimi_cancel` are now in Claude's toolbox.

> npm package coming soon.

---

## Usage examples

**Delegate a task**
> "Have Kimi write all the unit tests for this module while you review the architecture."

**Research in parallel**
> "Ask Kimi to find the best approach for rate-limiting in Redis — summarize when done."

**Iterate**
> Claude delegates → reviews Kimi's output → `kimi_continue` to request fixes → done.

**Background job**
> Claude fires off a long build task to Kimi with `background=true`, keeps working, checks `kimi_status` when convenient.

---

## How it works

`server.py` is a single-file [FastMCP](https://github.com/jlowin/fastmcp) stdio server with inline dependencies (PEP 723), run by `uv` — no install step, no virtualenv to manage. It shells out to:

```
kimi --print --final-message-only (--yolo | --plan) -p "<task>" [-w dir] [-C]
```

Kimi runs with `--yolo` (auto-approves his own tool calls). The safety gate is Claude Code's permission prompt on each MCP tool call — you see every task before it reaches Kimi.

---

## Remove

```bash
claude mcp remove kimi -s user
```
