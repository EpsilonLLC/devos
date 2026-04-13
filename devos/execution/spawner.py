"""Agent spawner — assembles context, creates worktree, and launches the agent.

Spawn sequence (single task, single agent):
  1. assembler.assemble(task, prior_summaries)  → ContextPackage
  2. worktree_manager.create(task.id)           → worktree_path
  3. shutil.copytree(session_dir, worktree/.devos/context/)
  4. session_id = f"{task.id}-{uuid4().hex[:8]}"
  5. adapter.build_launch_command(context_path)  → cmd
  6. subprocess.Popen(cmd, cwd=worktree_path, stdout→output.txt, stderr→stderr.txt)
  7. adapter.deliver_context(session_id, {_process, _output_path, _started_at})
  8. Write state.json; return SessionState(status="running")

All subprocess calls use cwd=worktree_path — never repo_root.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from devos.agents.base import AgentAdapter, SessionState
from devos.context.assembler import ContextAssembler, ContextBudgetExceededError
from devos.decomposition.dependency_graph import Task
from devos.execution.worktree import WorktreeManager


class SpawnError(Exception):
    """Raised when an agent session cannot be started.  Wraps the root cause."""


def write_state_json(session: SessionState, devos_dir: Path, **extra: object) -> None:
    """Serialize SessionState to .devos/sessions/{task_id}/state.json.

    Called on every status transition so state is never stale.  The ``**extra``
    keyword arguments are merged into the JSON (used by the monitor to record
    the process exit code on failure).

    Args:
        session:   Current session state.
        devos_dir: Root of the .devos/ runtime directory.
        **extra:   Optional additional fields written directly to the JSON dict.
    """
    state_dir = devos_dir / "sessions" / session.task_id
    state_dir.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "session_id": session.session_id,
        "task_id": session.task_id,
        "agent_name": session.agent_name,
        "started_at": session.started_at.isoformat(),
        "worktree_path": str(session.worktree_path),
        "context_path": str(session.context_path),
        "status": session.status,
        "output_path": str(session.output_path) if session.output_path else None,
    }
    data.update(extra)
    (state_dir / "state.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


class AgentSpawner:
    """Orchestrates context assembly, worktree creation, and agent launch.

    Args:
        repo_root:        Root of the project repository.
        devos_dir:        Path to .devos/ runtime directory.
        assembler:        Initialised ContextAssembler.
        worktree_manager: Initialised WorktreeManager.
        adapter:          Concrete AgentAdapter to use for this sprint.
    """

    def __init__(
        self,
        repo_root: Path,
        devos_dir: Path,
        assembler: ContextAssembler,
        worktree_manager: WorktreeManager,
        adapter: AgentAdapter,
    ) -> None:
        self._repo_root = repo_root
        self._devos_dir = devos_dir
        self._assembler = assembler
        self._worktree_manager = worktree_manager
        self._adapter = adapter

    def spawn(
        self,
        task: Task,
        prior_summaries: list[Path] | None = None,
    ) -> SessionState:
        """Run one task end-to-end through the spawn pipeline.

        Args:
            task:             Task dataclass from task_graph.json.
            prior_summaries:  Paths to distilled .md summaries for dependency
                              tasks.  Passed directly to the assembler.

        Returns:
            SessionState with status="running".

        Raises:
            SpawnError: If context assembly, worktree creation, or process
                        launch fail for any reason.
        """
        prior_summaries = list(prior_summaries) if prior_summaries else []

        # ── Step 1: Assemble context (fail fast on budget exceeded) ──────────
        try:
            context_package = self._assembler.assemble(task, prior_summaries)
        except ContextBudgetExceededError as exc:
            raise SpawnError(
                f"Context budget exceeded for {task.id}: {exc}"
            ) from exc
        except Exception as exc:
            raise SpawnError(
                f"Context assembly failed for {task.id}: {exc}"
            ) from exc

        # ── Step 2: Create isolated git worktree ─────────────────────────────
        try:
            worktree_path = self._worktree_manager.create(task.id)
        except Exception as exc:
            raise SpawnError(
                f"Worktree creation failed for {task.id}: {exc}"
            ) from exc

        # ── Step 3: Copy context package into worktree ───────────────────────
        worktree_context_dir = worktree_path / ".devos" / "context"
        try:
            shutil.copytree(
                context_package.session_dir,
                worktree_context_dir,
                dirs_exist_ok=True,
            )
        except Exception as exc:
            raise SpawnError(
                f"Failed to copy context into worktree for {task.id}: {exc}"
            ) from exc

        # ── Step 4: Generate unique session ID ───────────────────────────────
        session_id = f"{task.id}-{uuid4().hex[:8]}"

        # ── Step 5: Build launch command ─────────────────────────────────────
        context_path = worktree_context_dir
        cmd = self._adapter.build_launch_command(context_path)

        # ── Step 6: Launch subprocess with captured output ───────────────────
        sessions_dir = self._devos_dir / "sessions" / task.id
        sessions_dir.mkdir(parents=True, exist_ok=True)
        output_path = sessions_dir / "output.txt"
        stderr_path = sessions_dir / "stderr.txt"

        started_at = datetime.now(timezone.utc)

        try:
            # Open in text-append mode with line buffering (bufsize=1).
            # The subprocess writes its stdout directly to the file handle.
            stdout_file = open(output_path, "a", encoding="utf-8", buffering=1)  # noqa: WPS515
            stderr_file = open(stderr_path, "a", encoding="utf-8", buffering=1)  # noqa: WPS515
            process = subprocess.Popen(
                cmd,
                cwd=worktree_path,
                stdout=stdout_file,
                stderr=stderr_file,
            )
        except Exception as exc:
            raise SpawnError(
                f"Failed to launch agent process for {task.id}: {exc}"
            ) from exc

        # ── Step 7: Deliver context (registers process with the adapter) ─────
        self._adapter.deliver_context(
            session_id,
            {
                "_process": process,
                "_output_path": output_path,
                "_started_at": started_at,
            },
        )

        # ── Step 8: Construct SessionState and persist to state.json ─────────
        session = SessionState(
            session_id=session_id,
            task_id=task.id,
            agent_name=type(self._adapter).__name__,
            started_at=started_at,
            worktree_path=worktree_path,
            context_path=context_path,
            status="running",
            output_path=output_path,
        )
        write_state_json(session, self._devos_dir)
        return session
