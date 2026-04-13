"""devos.agents — Agent adapter plugin slots.

Public API:
    AgentAdapter      Abstract base (frozen interface — 5 methods)
    SessionState      Runtime state dataclass for one session
    ClaudeCodeAdapter Claude Code non-interactive adapter
    AgentError        Base exception
    LaunchError       Process could not be started
    StallError        Session stalled without progress
    OutputError       Output could not be retrieved
"""

from devos.agents.base import (  # noqa: F401
    AgentAdapter,
    AgentError,
    LaunchError,
    OutputError,
    SessionState,
    StallError,
)
from devos.agents.claude_code import ClaudeCodeAdapter  # noqa: F401

__all__ = [
    "AgentAdapter",
    "AgentError",
    "ClaudeCodeAdapter",
    "LaunchError",
    "OutputError",
    "SessionState",
    "StallError",
]
