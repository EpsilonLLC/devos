"""Reaction engine — responds to terminal session states.

Three reactions:
  on_complete: distill output, write summary, remove worktree.
  on_stall:    log event, surface Rich warning, preserve worktree.
  on_failure:  log event, surface Rich error panel, preserve worktree.

Stall and failure ALWAYS preserve the worktree so the user can inspect.
Auto-retry is not implemented — surface to user, require manual intervention.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from rich.console import Console
from rich.panel import Panel

from devos.agents.base import SessionState
from devos.context.distiller import DistilledSummary, MemoryDistiller
from devos.execution.monitor import SessionMonitor
from devos.execution.spawner import AgentSpawner, write_state_json
from devos.execution.worktree import WorktreeManager

console = Console(highlight=False)


class ReactionError(Exception):
    """Raised when the reaction engine encounters an unrecoverable error."""


class ReactionEngine:
    """Reacts to terminal session states produced by SessionMonitor.

    Args:
        devos_dir:        Path to .devos/ runtime directory.
        distiller:        Initialised MemoryDistiller.
        worktree_manager: Initialised WorktreeManager.
        spawner:          Initialised AgentSpawner (unused in this sprint but
                          wired in for future retry support).
        monitor:          Initialised SessionMonitor used to read output.
    """

    def __init__(
        self,
        devos_dir: Path,
        distiller: MemoryDistiller,
        worktree_manager: WorktreeManager,
        spawner: AgentSpawner,
        monitor: SessionMonitor,
    ) -> None:
        self._devos_dir = devos_dir
        self._distiller = distiller
        self._worktree_manager = worktree_manager
        self._spawner = spawner
        self._monitor = monitor

    # ── Public reactions ───────────────────────────────────────────────────────

    def on_complete(
        self,
        session: SessionState,
        task,
        remove_worktree: bool = True,
    ) -> DistilledSummary:
        """Distill output, write summary, optionally remove worktree, update state.

        Args:
            session:          Terminal SessionState with status="complete".
            task:             Task dataclass for name / id lookup.
            remove_worktree:  When True (default, single-task mode), the worktree
                              is removed after distillation.  Set to False in wave
                              mode so the synthesis layer can read files before
                              the merger cleans up.

        Returns:
            DistilledSummary written to .devos/summaries/{task_id}.md.

        Raises:
            ReactionError: If distillation or worktree removal fails.
        """
        raw_output = self._monitor.get_output(session)

        try:
            summary = self._distiller.distill(task.id, task.name, raw_output)
            self._distiller.write(summary, self._devos_dir / "summaries")
        except Exception as exc:
            raise ReactionError(
                f"Distillation failed for {task.id}: {exc}"
            ) from exc

        _commit_worktree(session.worktree_path, task.id)

        if remove_worktree:
            try:
                self._worktree_manager.remove(task.id)
            except Exception as exc:
                raise ReactionError(
                    f"Worktree removal failed for {task.id}: {exc}"
                ) from exc

        session.status = "complete"
        write_state_json(
            session,
            self._devos_dir,
            summary_path=str(self._devos_dir / "summaries" / f"{task.id}.md"),
        )
        return summary

    def on_stall(self, session: SessionState, task) -> None:
        """Log stall event and surface a Rich warning panel.

        The worktree is PRESERVED for manual inspection.  The user must run
        ``devos retry {task_id}`` to re-attempt (command stub only — not yet
        implemented).

        Args:
            session: Terminal SessionState with status="stalled".
            task:    Task dataclass for display.
        """
        _append_event(
            self._devos_dir / "sessions" / task.id / "events.jsonl",
            event="stall",
            session_id=session.session_id,
        )

        last_lines = self._monitor.tail_output(session, lines=5)
        console.print(
            Panel(
                f"[bold yellow]Task stalled without progress.[/bold yellow]\n\n"
                f"[dim]Session:[/dim] {session.session_id}\n"
                f"[dim]Worktree preserved at:[/dim] {session.worktree_path}\n\n"
                f"[dim]Last output:[/dim]\n{last_lines or '(no output)'}",
                title=f"[bold yellow]STALL — {task.name}[/bold yellow]",
                border_style="yellow",
            )
        )
        console.print(
            "[yellow]To re-attempt this task run:[/yellow] "
            f"[bold]devos retry {task.id}[/bold]"
        )

        session.status = "stalled"
        write_state_json(session, self._devos_dir)

    def on_failure(self, session: SessionState, task) -> None:
        """Log failure event and surface a Rich error panel.

        The worktree is PRESERVED (never auto-cleaned on failure).

        Args:
            session: Terminal SessionState with status="failed".
            task:    Task dataclass for display.
        """
        # Read exit code from state.json (written by monitor)
        exit_code = _read_exit_code(
            self._devos_dir / "sessions" / task.id / "state.json"
        )

        _append_event(
            self._devos_dir / "sessions" / task.id / "events.jsonl",
            event="failure",
            session_id=session.session_id,
            exit_code=exit_code,
        )

        stderr_tail = self._monitor.tail_stderr(session, lines=20)
        exit_display = str(exit_code) if exit_code is not None else "non-zero"
        console.print(
            Panel(
                f"[bold red]Agent process exited with code {exit_display}.[/bold red]\n\n"
                f"[dim]Session:[/dim] {session.session_id}\n"
                f"[dim]Worktree preserved at:[/dim] {session.worktree_path}\n\n"
                f"[dim]Last stderr:[/dim]\n{stderr_tail or '(no stderr)'}",
                title=f"[bold red]FAILURE — {task.name}[/bold red]",
                border_style="red",
            )
        )

        session.status = "failed"
        write_state_json(session, self._devos_dir, exit_code=exit_code)


# ── Private helpers ─────────────────────────────────────────────────────────────


def _commit_worktree(worktree_path: str | Path, task_id: str) -> None:
    """Stage and commit all agent output inside the worktree.

    Runs ``git add -A`` then ``git commit`` so the agent's work is captured
    in git history before the worktree directory is deleted.  If there is
    nothing to commit (clean tree) the function returns silently.  Any git
    failure is logged as a warning rather than raised — a failed commit must
    not block the rest of on_complete.

    Args:
        worktree_path: Absolute path to the worktree root.
        task_id:       Used in the commit message (e.g. ``T-001``).
    """
    wt = Path(worktree_path)
    if not wt.exists():
        logger.warning("_commit_worktree: worktree path does not exist: %s", wt)
        return

    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=wt,
            capture_output=True,
            check=True,
            text=True,
        )
        result = subprocess.run(
            ["git", "commit", "-m", f"task({task_id}): agent output"],
            cwd=wt,
            capture_output=True,
            text=True,
        )
        if result.returncode not in (0, 1):
            # exit 1 from git commit means "nothing to commit" — not an error.
            logger.warning(
                "git commit in worktree %s exited %d: %s",
                wt,
                result.returncode,
                result.stderr.strip(),
            )
        else:
            logger.debug("Committed agent output in %s: %s", wt, result.stdout.strip())
    except subprocess.CalledProcessError as exc:
        logger.warning("git add -A failed in worktree %s: %s", wt, exc.stderr.strip())
    except OSError as exc:
        logger.warning("Could not run git in worktree %s: %s", wt, exc)


def _append_event(events_path: Path, **fields: object) -> None:
    """Append a JSON event record to an events.jsonl file.

    Creates the file and parent directories if absent.
    """
    events_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": datetime.now(timezone.utc).isoformat(), **fields}
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _read_exit_code(state_path: Path) -> int | None:
    """Read exit_code from state.json if present."""
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data.get("exit_code")
    except (json.JSONDecodeError, OSError):
        return None
