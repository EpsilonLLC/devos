"""Wave runner — spawns all tasks in a wave in parallel, gates on completion.

Wave execution sequence (per wave):
  1. Deserialise every task dict → Task dataclass.
  2. Submit one thread per task to ThreadPoolExecutor.
  3. Each thread:
       a. Filter prior_summaries to only the task's own depends_on keys.
       b. spawner.spawn(task, task_summaries)
       c. monitor.watch(session) — blocks until terminal status.
       d. reaction.on_complete(session, task, remove_worktree=False)
            OR reaction.on_stall / on_failure.
  4. Collect WaveResult (wall-clock duration, not sum of task times).
  5. run_all() gates: any stall/failure in wave N → wave N+1 never starts.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table as RichTable

from devos.decomposition.dependency_graph import Task
from devos.execution.monitor import SessionMonitor
from devos.execution.reaction import ReactionEngine
from devos.execution.spawner import AgentSpawner

console = Console(highlight=False)
logger = logging.getLogger(__name__)


# ── Exceptions ──────────────────────────────────────────────────────────────────


class WaveRunnerError(Exception):
    """Raised when the wave runner encounters an unrecoverable internal error."""


# ── Result dataclass ────────────────────────────────────────────────────────────


@dataclass
class WaveResult:
    """Outcome of executing a single wave of tasks.

    Attributes:
        wave_number:      Wave index (0, 1, 2, …).
        completed:        Task IDs that finished cleanly.
        stalled:          Task IDs where no progress was detected.
        failed:           Task IDs that exited non-zero or raised an exception.
        summaries:        task_id → absolute path to distilled summary
                          (populated only for completed tasks).
        duration_seconds: Wall-clock time for the entire wave — equals the
                          longest individual task, NOT the sum.
    """

    wave_number: int
    completed: list[str] = field(default_factory=list)
    stalled: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    summaries: dict[str, Path] = field(default_factory=dict)
    duration_seconds: float = 0.0


# ── Main class ──────────────────────────────────────────────────────────────────


class WaveRunner:
    """Runs all tasks within a wave in parallel and gates advancement.

    Args:
        repo_root:  Root of the git repository.
        devos_dir:  Path to .devos/ runtime directory.
        spawner:    Initialised AgentSpawner.
        monitor:    Initialised SessionMonitor.
        reaction:   Initialised ReactionEngine.
    """

    def __init__(
        self,
        repo_root: Path,
        devos_dir: Path,
        spawner: AgentSpawner,
        monitor: SessionMonitor,
        reaction: ReactionEngine,
    ) -> None:
        self._repo_root = repo_root
        self._devos_dir = devos_dir
        self._spawner = spawner
        self._monitor = monitor
        self._reaction = reaction

    # ── Public API ─────────────────────────────────────────────────────────────

    def run_wave(
        self,
        wave: dict,
        prior_summaries: dict[str, Path],
    ) -> WaveResult:
        """Spawn all tasks in the wave in parallel and collect results.

        Args:
            wave:            One element from task_graph.json["waves"]:
                             ``{"wave": N, "tasks": [...]}``.
            prior_summaries: Map of task_id → summary path for every completed
                             upstream task.  Each thread receives only the
                             subset matching its own ``depends_on`` list.

        Returns:
            WaveResult with completion/stall/failure categorisation and
            summary paths for the tasks that completed cleanly.
        """
        wave_number: int = wave["wave"]
        tasks: list[Task] = [Task(**t) for t in wave.get("tasks", [])]

        if not tasks:
            return WaveResult(wave_number=wave_number)

        start_time = time.monotonic()

        def _run_single(task: Task) -> tuple[str, str, Path | None]:
            """Thread body — returns (task_id, status, summary_path | None)."""
            # Only pass summaries the task actually depends on.
            task_summaries: list[Path] = [
                v for k, v in prior_summaries.items() if k in task.depends_on
            ]

            try:
                session = self._spawner.spawn(task, task_summaries)
            except Exception as exc:
                logger.error("Spawn failed for %s: %s", task.id, exc)
                return (task.id, "failed", None)

            session = self._monitor.watch(session)

            if session.status == "complete":
                try:
                    # remove_worktree=False: keep worktree for synthesis/merger.
                    self._reaction.on_complete(session, task, remove_worktree=False)
                    summary_path = self._devos_dir / "summaries" / f"{task.id}.md"
                    return (task.id, "complete", summary_path)
                except Exception as exc:
                    logger.error(
                        "on_complete reaction failed for %s: %s", task.id, exc
                    )
                    return (task.id, "failed", None)

            elif session.status == "stalled":
                self._reaction.on_stall(session, task)
                return (task.id, "stalled", None)

            else:  # "failed" or any unexpected terminal status
                self._reaction.on_failure(session, task)
                return (task.id, "failed", None)

        # One thread per task — waves are at most 4 tasks in the 6-task graph.
        thread_count = len(tasks)
        thread_results: list[tuple[str, str, Path | None]] = []

        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            futures = [executor.submit(_run_single, t) for t in tasks]
            for future in as_completed(futures):
                thread_results.append(future.result())

        duration_seconds = time.monotonic() - start_time

        completed = [r[0] for r in thread_results if r[1] == "complete"]
        stalled = [r[0] for r in thread_results if r[1] == "stalled"]
        failed = [r[0] for r in thread_results if r[1] == "failed"]
        summaries: dict[str, Path] = {
            r[0]: r[2]
            for r in thread_results
            if r[1] == "complete" and r[2] is not None
        }

        return WaveResult(
            wave_number=wave_number,
            completed=completed,
            stalled=stalled,
            failed=failed,
            summaries=summaries,
            duration_seconds=duration_seconds,
        )

    def run_all(self, task_graph: dict) -> list[WaveResult]:
        """Execute all waves in dependency order, accumulating summaries.

        Waves are sorted ascending by their ``wave`` key.  After each wave,
        the completed task summaries are merged into ``prior_summaries`` so
        the next wave receives the full upstream context.

        Gate rule: if *any* task in a wave stalls or fails, subsequent waves
        are NOT started.  An error panel is printed and the partial list of
        WaveResults is returned immediately.

        Args:
            task_graph: Parsed task_graph.json dict (must contain "waves").

        Returns:
            List of WaveResults in execution order.  Stops at the first
            blocking wave — subsequent waves are omitted from the list.
        """
        waves = sorted(task_graph.get("waves", []), key=lambda w: w["wave"])
        prior_summaries: dict[str, Path] = {}
        results: list[WaveResult] = []

        for wave in waves:
            wave_number = wave["wave"]
            task_count = len(wave.get("tasks", []))
            console.print(
                f"\n[bold cyan]── Wave {wave_number}[/bold cyan] "
                f"[dim]({task_count} task{'s' if task_count != 1 else ''})[/dim]"
            )

            wave_result = self.run_wave(wave, prior_summaries)
            results.append(wave_result)

            _print_wave_table(wave_result)

            # Gate: any stall or failure blocks all subsequent waves.
            if wave_result.stalled or wave_result.failed:
                n_stalled = len(wave_result.stalled)
                n_failed = len(wave_result.failed)
                console.print(
                    Panel(
                        f"[bold red]Wave {wave_number} incomplete — "
                        f"{n_stalled} task{'s' if n_stalled != 1 else ''} stalled, "
                        f"{n_failed} task{'s' if n_failed != 1 else ''} failed.[/bold red]\n"
                        "Fix and re-run, or use [bold]devos retry[/bold] "
                        "to retry stalled tasks.",
                        border_style="red",
                    )
                )
                return results

            # Accumulate completed summaries for the next wave.
            prior_summaries.update(wave_result.summaries)

        return results


# ── Private helpers ─────────────────────────────────────────────────────────────


def _print_wave_table(wave_result: WaveResult) -> None:
    """Print a one-row Rich table summarising a completed wave."""
    table = RichTable(
        show_header=True,
        header_style="bold",
        border_style="dim",
        show_lines=False,
    )
    table.add_column("Wave", justify="center", width=6)
    table.add_column("Tasks", justify="right", width=7)
    table.add_column("Completed", justify="right", style="green", width=11)
    table.add_column("Stalled", justify="right", style="yellow", width=9)
    table.add_column("Failed", justify="right", style="red", width=8)
    table.add_column("Duration", justify="right", width=10)

    total = (
        len(wave_result.completed)
        + len(wave_result.stalled)
        + len(wave_result.failed)
    )
    table.add_row(
        str(wave_result.wave_number),
        str(total),
        str(len(wave_result.completed)),
        str(len(wave_result.stalled)),
        str(len(wave_result.failed)),
        f"{wave_result.duration_seconds:.1f}s",
    )
    console.print(table)
