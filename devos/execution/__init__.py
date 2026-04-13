"""devos.execution — Single-agent and parallel wave execution layer.

Public API:
    WorktreeManager   Creates / removes isolated git worktrees
    AgentSpawner      Assembles context, launches agent subprocess
    SessionMonitor    Polls session to completion / stall / failure
    ReactionEngine    Reacts to terminal session states
    WaveRunner        Spawns all tasks in a wave in parallel, gates on result
    WaveResult        Result dataclass for a completed wave
    WorktreeError     Git worktree operation failed
    SpawnError        Agent session could not be started
    MonitorError      Monitor encountered an unrecoverable error
    ReactionError     Reaction handler failed
    WaveRunnerError   Wave runner encountered an unrecoverable error
"""

from devos.execution.monitor import MonitorError, SessionMonitor  # noqa: F401
from devos.execution.reaction import ReactionEngine, ReactionError  # noqa: F401
from devos.execution.spawner import AgentSpawner, SpawnError  # noqa: F401
from devos.execution.wave_runner import WaveResult, WaveRunner, WaveRunnerError  # noqa: F401
from devos.execution.worktree import WorktreeError, WorktreeManager  # noqa: F401

__all__ = [
    "AgentSpawner",
    "MonitorError",
    "ReactionEngine",
    "ReactionError",
    "SessionMonitor",
    "SpawnError",
    "WaveResult",
    "WaveRunner",
    "WaveRunnerError",
    "WorktreeError",
    "WorktreeManager",
]
