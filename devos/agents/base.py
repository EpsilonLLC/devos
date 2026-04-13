"""Agent adapter abstract base class and shared data structures.

Every coding agent (Claude Code, Codex, Aider) plugs in by implementing
AgentAdapter.  The rest of the system — monitor, reaction engine, CLI —
never knows which concrete adapter is running.

Interface contract (FROZEN — see DEVOS_HANDOFF.md):
    build_launch_command(context_path: Path) -> list[str]
    deliver_context(session_id: str, context: dict) -> None
    is_complete(session_id: str) -> bool
    is_stalled(session_id: str) -> bool
    get_output(session_id: str) -> str
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


# ── Exceptions ─────────────────────────────────────────────────────────────────


class AgentError(Exception):
    """Base exception for all agent adapter errors."""


class LaunchError(AgentError):
    """Raised when an agent process cannot be started."""


class StallError(AgentError):
    """Raised when an agent session has stalled without making progress."""


class OutputError(AgentError):
    """Raised when an agent's output cannot be retrieved."""


# ── Session state ──────────────────────────────────────────────────────────────


@dataclass
class SessionState:
    """Runtime state for a single agent session.

    Written to .devos/sessions/{task_id}/state.json on every status
    transition — never only at the end.

    Attributes:
        session_id:    Unique identifier (e.g. 'T-001-abc12345').
        task_id:       Task identifier from task_graph.json.
        agent_name:    Name of the adapter driving this session.
        started_at:    Wall-clock timestamp when the session was spawned.
        worktree_path: Path to the isolated git worktree for this session.
        context_path:  Context package directory inside the worktree.
        status:        Current lifecycle status.
        output_path:   Captured stdout file (None until spawn completes).
    """

    session_id: str
    task_id: str
    agent_name: str
    started_at: datetime
    worktree_path: Path
    context_path: Path
    status: Literal["running", "complete", "stalled", "failed"]
    output_path: Path | None


# ── Abstract base ──────────────────────────────────────────────────────────────


class AgentAdapter(ABC):
    """
    Every coding agent plugs in by implementing this interface.
    The rest of the system never knows which agent is running.
    """

    @abstractmethod
    def build_launch_command(self, context_path: Path) -> list[str]:
        """Return the shell command to launch this agent with context."""

    @abstractmethod
    def deliver_context(self, session_id: str, context: dict) -> None:
        """Deliver the surgical context package to a running agent."""

    @abstractmethod
    def is_complete(self, session_id: str) -> bool:
        """Return True when the agent has finished its task."""

    @abstractmethod
    def is_stalled(self, session_id: str) -> bool:
        """Return True when degradation or stall is detected."""

    @abstractmethod
    def get_output(self, session_id: str) -> str:
        """Return the agent's completed output for distillation."""
