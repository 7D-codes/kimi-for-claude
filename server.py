# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0"]
# ///
"""MCP server that delegates tasks to the Kimi CLI agent.

Exposes Kimi (https://moonshotai.github.io/kimi-cli/) as tools so a managing
agent (Claude Code) can hand off whole tasks and read back the result.

Design principle: save the manager's context. Results are truncated, file
changes are reported as a one-line summary (not a diff), and the manager is
expected to ask Kimi for summaries via kimi_continue instead of reading his
artifacts in full.

Kimi runs in non-interactive print mode with --yolo: he auto-approves his own
tool calls, so only delegate tasks you are prepared to let him execute.
"""

import asyncio
import itertools
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

KIMI_BIN = shutil.which("kimi") or os.path.expanduser("~/.local/bin/kimi")
MAX_RESULT_CHARS = 6000

mcp = FastMCP("kimi")


# ---------------------------------------------------------------- git snapshot

def _git_state(work_dir: str | None) -> dict[str, str] | None:
    """Map of path -> status for a git work_dir, or None if not a repo."""
    if not work_dir:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", work_dir, "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    state = {}
    for line in out.stdout.splitlines():
        if len(line) > 3:
            state[line[3:]] = line[:2]
    return state


def _git_changes_line(before: dict | None, work_dir: str | None) -> str:
    """One-line summary of files that changed during the task."""
    after = _git_state(work_dir)
    if before is None or after is None:
        return ""
    changed = sorted(
        set(after) - set(before)
        | {p for p in after if p in before and after[p] != before[p]}
        | set(before) - set(after)
    )
    if not changed:
        return "\n\n[git: no new file changes in work_dir]"
    shown = ", ".join(changed[:15])
    extra = f" (+{len(changed) - 15} more)" if len(changed) > 15 else ""
    return f"\n\n[git: {len(changed)} file(s) changed during task: {shown}{extra}]"


def _truncate(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return (
        text[:MAX_RESULT_CHARS]
        + f"\n\n[... truncated {len(text) - MAX_RESULT_CHARS} chars. "
        "Don't re-request the full text - ask Kimi to summarize or answer "
        "specific questions via kimi_continue.]"
    )


# ---------------------------------------------------------------- kimi runner

async def _run_kimi(
    prompt: str,
    work_dir: str | None,
    model: str | None,
    timeout_seconds: int,
    continue_session: bool,
    readonly: bool,
) -> str:
    cmd = [KIMI_BIN, "--print", "--final-message-only", "-p", prompt]
    cmd += ["--plan"] if readonly else ["--yolo"]
    if work_dir:
        work_dir = os.path.expanduser(work_dir)
        if not os.path.isdir(work_dir):
            return f"Error: work_dir does not exist: {work_dir}"
        cmd += ["-w", work_dir]
    if model:
        cmd += ["-m", model]
    if continue_session:
        cmd += ["-C"]

    git_before = None if readonly else _git_state(work_dir)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return (
            f"Error: Kimi did not finish within {timeout_seconds}s and was killed."
            f"{_git_changes_line(git_before, work_dir)}\n"
            "His session may be resumable: try kimi_continue with 'where did you "
            "get to?' or retry with a higher timeout_seconds."
        )
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise

    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    if proc.returncode != 0:
        return (
            f"Error: kimi exited with code {proc.returncode}.\n"
            f"stderr:\n{_truncate(err) or '(empty)'}\n"
            f"stdout:\n{_truncate(out) or '(empty)'}"
        )
    return _truncate(out or "(Kimi returned no output.)") + _git_changes_line(
        git_before, work_dir
    )


# ---------------------------------------------------------------- job registry

@dataclass
class Job:
    id: str
    desc: str
    work_dir: str | None
    started: float
    task: asyncio.Task = field(repr=False, default=None)

    @property
    def state(self) -> str:
        if not self.task.done():
            return "running"
        if self.task.cancelled():
            return "cancelled"
        return "done"


JOBS: dict[str, Job] = {}
_job_seq = itertools.count(1)


def _start_job(desc: str, work_dir: str | None, coro) -> str:
    job = Job(id=f"j{next(_job_seq)}", desc=desc[:80], work_dir=work_dir,
              started=time.time())
    job.task = asyncio.create_task(coro)
    JOBS[job.id] = job
    return (
        f"Started background job {job.id} ({job.desc!r}). "
        "Check kimi_status when you next need the result - you are not "
        "notified on completion. One job per work_dir: don't start another "
        "task in the same directory until this one finishes."
    )


# ---------------------------------------------------------------- tools

@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True,
                 "openWorldHint": True}
)
async def kimi_delegate(
    task: Annotated[str, Field(description=(
        "Full task description for Kimi. Be specific and self-contained: he "
        "starts with no context beyond this prompt and the files in work_dir. "
        "For large outputs, tell him to write to a file and report a summary."
    ))],
    work_dir: Annotated[str | None, Field(description=(
        "Directory Kimi works in. Pass the project root for file tasks; omit "
        "for pure Q&A. If it is a git repo, the result includes a one-line "
        "summary of files he changed."
    ))] = None,
    model: Annotated[str | None, Field(
        description="Override Kimi's default model. Usually omit.")] = None,
    timeout_seconds: Annotated[int, Field(
        ge=30, le=3600, description="Kill Kimi after this many seconds.")] = 600,
    readonly: Annotated[bool, Field(description=(
        "Run in plan mode: Kimi investigates and reports but does not write "
        "files or execute changes. Use for research/review tasks."
    ))] = False,
    background: Annotated[bool, Field(description=(
        "Return a job id immediately instead of waiting; collect with "
        "kimi_status. Use for long tasks you want to run in parallel."
    ))] = False,
) -> str:
    """Delegate a task to the Kimi CLI agent and return his final answer.

    Starts a fresh Kimi session that runs autonomously in work_dir. Use
    kimi_continue for follow-ups. Token economy: prefer asking Kimi to
    summarize or answer specific questions over reading his artifacts in full.
    """
    coro = _run_kimi(task, work_dir, model, timeout_seconds, False, readonly)
    if background:
        return _start_job(task, work_dir, coro)
    return await coro


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True,
                 "openWorldHint": True}
)
async def kimi_continue(
    prompt: Annotated[str, Field(description=(
        "Follow-up message for Kimi. He sees the prior session's history, so "
        "reference his earlier work directly. Ideal for: 'summarize what you "
        "did', 'what's in the file you wrote?', requesting fixes."
    ))],
    work_dir: Annotated[str | None, Field(description=(
        "Must match the work_dir of the session being continued; sessions are "
        "tracked per directory."
    ))] = None,
    timeout_seconds: Annotated[int, Field(
        ge=30, le=3600, description="Kill Kimi after this many seconds.")] = 600,
    readonly: Annotated[bool, Field(description=(
        "Plan mode: Kimi answers/investigates without making changes."
    ))] = False,
    background: Annotated[bool, Field(description=(
        "Return a job id immediately; collect with kimi_status."
    ))] = False,
) -> str:
    """Continue Kimi's most recent session in work_dir with a follow-up.

    Iterate without re-explaining context: request fixes, ask him to
    summarize his own work or artifacts. Token economy: prefer asking Kimi
    to summarize over reading his artifacts in full.
    """
    coro = _run_kimi(prompt, work_dir, None, timeout_seconds, True, readonly)
    if background:
        return _start_job(prompt, work_dir, coro)
    return await coro


@mcp.tool(annotations={"readOnlyHint": True})
async def kimi_status(
    job_id: Annotated[str | None, Field(description=(
        "Job to inspect; returns its result if finished. Omit to list all "
        "jobs from this session."
    ))] = None,
) -> str:
    """Check background Kimi jobs: list them all, or fetch one job's result."""
    if not JOBS:
        return "No background jobs in this session."
    if job_id is None:
        lines = [
            f"{j.id}: {j.state:9s} {int(time.time() - j.started)}s "
            f"work_dir={j.work_dir or '-'} task={j.desc!r}"
            for j in JOBS.values()
        ]
        return "\n".join(lines)
    job = JOBS.get(job_id)
    if job is None:
        return f"Error: unknown job {job_id!r}. Known: {', '.join(JOBS)}."
    if job.state == "running":
        return (
            f"{job.id} still running ({int(time.time() - job.started)}s). "
            "Do other work and check back; polling adds no speed."
        )
    if job.state == "cancelled":
        return f"{job.id} was cancelled."
    exc = job.task.exception()
    if exc:
        return f"{job.id} failed: {exc!r}"
    return job.task.result()


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def kimi_cancel(
    job_id: Annotated[str, Field(description="Background job to kill.")],
) -> str:
    """Cancel a running background Kimi job (kills the kimi process)."""
    job = JOBS.get(job_id)
    if job is None:
        return f"Error: unknown job {job_id!r}. Known: {', '.join(JOBS) or 'none'}."
    if job.task.done():
        return f"{job.id} already finished ({job.state}); nothing to cancel."
    job.task.cancel()
    try:
        await job.task
    except (asyncio.CancelledError, Exception):
        pass
    return f"Cancelled {job.id}. Kimi's partial work in work_dir may remain."


if __name__ == "__main__":
    mcp.run()
