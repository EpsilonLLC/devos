"""Claude Code adapter — non-interactive subprocess invocation.

Claude Code reads a task.md file from the context directory and writes its
output to stdout.  Context delivery is filesystem-based; no interactive
session is needed for the single-agent phase.

Design:
  - build_launch_command: returns the claude --print command.
  - deliver_context: stores the process handle and output path in the
    class-level registry (the _process key in the context dict is the
    Popen object placed there by AgentSpawner after launch).
  - is_complete / is_stalled / is_failed: query _session_registry.
  - get_output: reads the captured stdout file.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from devos.agents.base import AgentAdapter, OutputError
from devos.context import MAX_EXCHANGES_PER_SESSION

# 25 min — stall detection wall-clock threshold
_STALL_TIMEOUT_SECONDS: int = MAX_EXCHANGES_PER_SESSION * 60


@dataclass
class _SessionEntry:
    """Per-session data stored in the class-level registry."""

    process: subprocess.Popen
    output_path: Path | None
    started_at: datetime


class ClaudeCodeAdapter(AgentAdapter):
    """Adapter for non-interactive Claude Code invocation.

    Claude Code is launched with ``--print --dangerously-skip-permissions``
    so it reads a task file and writes output to stdout without prompting.

    The class-level ``_session_registry`` maps session_id → _SessionEntry.
    It is populated by ``deliver_context``, which receives the Popen handle
    from AgentSpawner via the ``_process`` key in the context dict.
    """

    # Class-level: maps session_id → _SessionEntry
    _session_registry: dict[str, _SessionEntry] = {}

    # ── AgentAdapter interface ─────────────────────────────────────────────

    def build_launch_command(self, context_path: Path) -> list[str]:
        """Return the command to run Claude Code non-interactively on task.md."""
        return [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            str(context_path / "task.md"),
        ]

    def deliver_context(self, session_id: str, context: dict) -> None:
        """Register the process handle for monitoring.

        Context is already on the filesystem (assembler wrote it).  This
        method's role is to store the Popen handle so that is_complete,
        is_stalled, and get_output can query the process.

        The spawner places the Popen object under the ``_process`` key.
        If ``_process`` is absent the call is a genuine no-op (satisfies
        the interface for callers that don't need process tracking).
        """
        process = context.get("_process")
        if process is None:
            return
        self._session_registry[session_id] = _SessionEntry(
            process=process,
            output_path=context.get("_output_path"),
            started_at=context.get("_started_at", datetime.now()),
        )

    def is_complete(self, session_id: str) -> bool:
        """Return True if the process exited with code 0."""
        entry = self._session_registry.get(session_id)
        if entry is None:
            return False
        return entry.process.poll() == 0

    def is_stalled(self, session_id: str) -> bool:
        """Return True if the process is alive but has produced no new output
        for longer than the stall timeout.

        Stall threshold: MAX_EXCHANGES_PER_SESSION * 60 seconds (25 min).
        Last-activity reference: output file mtime if the file exists,
        otherwise the session start time.
        """
        entry = self._session_registry.get(session_id)
        if entry is None:
            return False

        # A process that has already exited is not "stalled" — it either
        # completed or failed.  Only flag stall for a live process.
        if entry.process.poll() is not None:
            return False

        output_path = entry.output_path
        if output_path and output_path.exists():
            last_activity = output_path.stat().st_mtime
        else:
            last_activity = entry.started_at.timestamp()

        return (time.time() - last_activity) > _STALL_TIMEOUT_SECONDS

    def get_output(self, session_id: str) -> str:
        """Return captured stdout from the session output file."""
        entry = self._session_registry.get(session_id)
        if entry is None:
            raise OutputError(f"No session registered for session_id={session_id!r}")
        if entry.output_path and entry.output_path.exists():
            return entry.output_path.read_text(encoding="utf-8", errors="replace")
        return ""

    # ── Concrete extension (not part of the frozen abstract interface) ─────

    def is_failed(self, session_id: str) -> bool:
        """Return True if the process exited with a non-zero exit code.

        This method is NOT part of the AgentAdapter abstract interface.
        SessionMonitor calls it via ``hasattr`` duck-typing so that the
        abstract interface remains frozen while concrete adapters can still
        surface failure state.
        """
        entry = self._session_registry.get(session_id)
        if entry is None:
            return False
        rc = entry.process.poll()
        return rc is not None and rc != 0
