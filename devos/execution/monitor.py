"""Session monitor — polls an agent session until it completes, stalls, or fails.

Poll loop (blocking):
  1. adapter.is_complete(session_id) → True  → status="complete", write state.json
  2. adapter.is_stalled(session_id)  → True  → status="stalled",  write state.json
  3. hasattr(adapter, 'is_failed') and adapter.is_failed(session_id)
                                     → True  → status="failed",   write state.json
  4. Print last line of output.txt to Rich console (same line — no scroll flood)
  5. Sleep poll_interval_seconds

State is written to disk on EVERY status transition.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from rich.console import Console

from devos.agents.base import AgentAdapter, SessionState
from devos.execution.spawner import write_state_json

console = Console(highlight=False)


class MonitorError(Exception):
    """Raised when the monitor encounters an unrecoverable internal error."""


class SessionMonitor:
    """Polls a running agent session and returns a terminal SessionState.

    Args:
        devos_dir:              Path to .devos/ runtime directory.
        adapter:                The AgentAdapter managing the session.
        poll_interval_seconds:  Seconds between polling cycles.
    """

    def __init__(
        self,
        devos_dir: Path,
        adapter: AgentAdapter,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._devos_dir = devos_dir
        self._adapter = adapter
        self._poll_interval = poll_interval_seconds

    def watch(self, session: SessionState) -> SessionState:
        """Block until the session reaches a terminal status.

        Polls ``adapter.is_complete``, ``adapter.is_stalled``, and (if the
        concrete adapter exposes it) ``adapter.is_failed`` in order.  Writes
        state.json on every status transition.

        Args:
            session: The SessionState returned by AgentSpawner.spawn().

        Returns:
            The same SessionState object with ``status`` updated to
            "complete", "stalled", or "failed".
        """
        _print_monitoring_header(session)

        while True:
            # ── Complete ───────────────────────────────────────────────────
            if self._adapter.is_complete(session.session_id):
                session.status = "complete"
                write_state_json(session, self._devos_dir)
                _clear_status_line()
                return session

            # ── Stalled ────────────────────────────────────────────────────
            if self._adapter.is_stalled(session.session_id):
                session.status = "stalled"
                write_state_json(session, self._devos_dir)
                _clear_status_line()
                return session

            # ── Failed (concrete adapter extension, not abstract interface) ─
            if hasattr(self._adapter, "is_failed") and self._adapter.is_failed(
                session.session_id
            ):
                session.status = "failed"
                # Retrieve exit code for state.json if possible
                exit_code = _get_exit_code(self._adapter, session.session_id)
                write_state_json(session, self._devos_dir, exit_code=exit_code)
                _clear_status_line()
                return session

            # ── Live tail: overwrite same terminal line ────────────────────
            if session.output_path and session.output_path.exists():
                last = _last_line(session.output_path)
                if last:
                    _write_status_line(last)

            time.sleep(self._poll_interval)

    def get_output(self, session: SessionState) -> str:
        """Read the full contents of the session output file.

        Args:
            session: A SessionState (need not be terminal).

        Returns:
            Full text of .devos/sessions/{task_id}/output.txt, or empty
            string if the file does not exist.
        """
        output_path = self._devos_dir / "sessions" / session.task_id / "output.txt"
        if output_path.exists():
            return output_path.read_text(encoding="utf-8", errors="replace")
        return ""

    def tail_output(self, session: SessionState, lines: int = 20) -> str:
        """Return the last N lines of the session output file.

        Args:
            session: A SessionState.
            lines:   Number of tail lines to return.

        Returns:
            Last ``lines`` lines joined by newline, or empty string.
        """
        output_path = self._devos_dir / "sessions" / session.task_id / "output.txt"
        if not output_path.exists():
            return ""
        all_lines = output_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
        return "\n".join(all_lines[-lines:])

    def tail_stderr(self, session: SessionState, lines: int = 20) -> str:
        """Return the last N lines of the session stderr file.

        Args:
            session: A SessionState.
            lines:   Number of tail lines to return.

        Returns:
            Last ``lines`` lines joined by newline, or empty string.
        """
        stderr_path = self._devos_dir / "sessions" / session.task_id / "stderr.txt"
        if not stderr_path.exists():
            return ""
        all_lines = stderr_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
        return "\n".join(all_lines[-lines:])


# ── Private helpers ─────────────────────────────────────────────────────────────


def _print_monitoring_header(session: SessionState) -> None:
    console.print(
        f"[cyan]Monitoring[/cyan] [bold]{session.task_id}[/bold] "
        f"[dim](session {session.session_id})[/dim]"
    )


def _write_status_line(text: str) -> None:
    """Overwrite the current terminal line with truncated status text."""
    truncated = text[:100].rstrip()
    sys.stdout.write(f"\r[dim]{truncated:<100}[/dim]")
    sys.stdout.flush()


def _clear_status_line() -> None:
    """Clear the live-tail status line before printing the final result."""
    sys.stdout.write(f"\r{' ' * 102}\r")
    sys.stdout.flush()


def _last_line(path: Path) -> str:
    """Return the last non-empty line of a file, or empty string."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return lines[-1] if lines else ""
    except OSError:
        return ""


def _get_exit_code(adapter: AgentAdapter, session_id: str) -> int | None:
    """Extract process exit code from the adapter registry if possible."""
    registry = getattr(adapter, "_session_registry", None)
    if registry is None:
        return None
    entry = registry.get(session_id)
    if entry is None:
        return None
    return entry.process.poll()
