"""devos.execution — Single-agent worktree execution layer.

Public API:
    WorktreeManager   Creates / removes isolated git worktrees
    AgentSpawner      Assembles context, launches agent subprocess
    SessionMonitor    Polls session to completion / stall / failure
    ReactionEngine    Reacts to terminal session states
    WorktreeError     Git worktree operation failed
    SpawnError        Agent session could not be started
    MonitorError      Monitor encountered an unrecoverable error
    ReactionError     Reaction handler failed
"""

from devos.execution.monitor import MonitorError, SessionMonitor  # noqa: F401
from devos.execution.reaction import ReactionEngine, ReactionError  # noqa: F401
from devos.execution.spawner import AgentSpawner, SpawnError  # noqa: F401
from devos.execution.worktree import WorktreeError, WorktreeManager  # noqa: F401

__all__ = [
    "AgentSpawner",
    "MonitorError",
    "ReactionEngine",
    "ReactionError",
    "SessionMonitor",
    "SpawnError",
    "WorktreeError",
    "WorktreeManager",
]
