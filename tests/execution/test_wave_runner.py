"""Tests for WaveRunner — parallel wave execution and gate logic.

Test IDs:
  a) test_wave_runner_spawns_all_tasks_in_wave_in_parallel
  b) test_wave_runner_passes_only_relevant_prior_summaries_per_task
  c) test_wave_runner_result_categorizes_complete_stalled_failed
  d) test_run_all_gates_on_wave_failure_does_not_advance
  e) test_run_all_accumulates_summaries_across_waves
  f) test_run_all_passes_correct_wave_order
  g) test_wave_result_duration_is_wall_clock_not_sum_of_task_times
  h) test_wave_runner_handles_empty_wave_gracefully

All subprocess / I/O mocked — no live agent calls, no git operations.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devos.agents.base import SessionState
from devos.decomposition.dependency_graph import Task
from devos.execution.wave_runner import WaveResult, WaveRunner


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers / factories
# ─────────────────────────────────────────────────────────────────────────────


def _make_task_dict(
    task_id: str,
    depends_on: list[str] | None = None,
    component: str = "core",
) -> dict:
    return {
        "id": task_id,
        "name": f"{task_id} module",
        "component": component,
        "spec_files": [],
        "relevant_files": [],
        "depends_on": depends_on or [],
        "write_targets": [f"devos/{task_id.lower()}/"],
        "write_tables": [],
        "feature_ids": [],
        "acceptance_ids": [],
    }


def _make_session(
    tmp_path: Path,
    task_id: str = "T-001",
    status: str = "complete",
) -> SessionState:
    wt = tmp_path / "worktrees" / task_id
    wt.mkdir(parents=True, exist_ok=True)
    return SessionState(
        session_id=f"{task_id}-test1234",
        task_id=task_id,
        agent_name="ClaudeCodeAdapter",
        started_at=datetime.now(timezone.utc),
        worktree_path=wt,
        context_path=wt / ".devos" / "context",
        status=status,  # type: ignore[arg-type]
        output_path=None,
    )


def _make_wave_runner(tmp_path: Path) -> tuple[WaveRunner, MagicMock, MagicMock, MagicMock]:
    """Return (runner, mock_spawner, mock_monitor, mock_reaction)."""
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir(parents=True, exist_ok=True)

    mock_spawner = MagicMock()
    mock_monitor = MagicMock()
    mock_reaction = MagicMock()

    runner = WaveRunner(
        repo_root=tmp_path,
        devos_dir=devos_dir,
        spawner=mock_spawner,
        monitor=mock_monitor,
        reaction=mock_reaction,
    )
    return runner, mock_spawner, mock_monitor, mock_reaction


# ─────────────────────────────────────────────────────────────────────────────
# a) All tasks in a wave are submitted to the thread pool simultaneously
# ─────────────────────────────────────────────────────────────────────────────


def test_wave_runner_spawns_all_tasks_in_wave_in_parallel(tmp_path: Path) -> None:
    """Both tasks must be running concurrently, not one after the other.

    We use a threading.Barrier(2) inside the mock spawner.  If the runner
    were serial, the first thread would block waiting for a second that never
    comes, causing a timeout.
    """
    runner, mock_spawner, mock_monitor, mock_reaction = _make_wave_runner(tmp_path)

    barrier = threading.Barrier(2, timeout=5)
    spawned_task_ids: list[str] = []

    def _spawn(task: Task, summaries):
        spawned_task_ids.append(task.id)
        barrier.wait()  # both threads must arrive — times out if serial
        return _make_session(tmp_path, task.id)

    mock_spawner.spawn.side_effect = _spawn
    mock_monitor.watch.side_effect = lambda s: s  # return session unchanged
    mock_reaction.on_complete.return_value = MagicMock()

    wave = {
        "wave": 0,
        "tasks": [_make_task_dict("T-001"), _make_task_dict("T-002")],
    }
    result = runner.run_wave(wave, {})

    assert set(spawned_task_ids) == {"T-001", "T-002"}
    assert set(result.completed) == {"T-001", "T-002"}


# ─────────────────────────────────────────────────────────────────────────────
# b) Each task receives only its own dependencies' summaries
# ─────────────────────────────────────────────────────────────────────────────


def test_wave_runner_passes_only_relevant_prior_summaries_per_task(
    tmp_path: Path,
) -> None:
    """T-003 depends on T-001; T-004 depends on T-001.  T-002's summary
    must NOT be passed to either."""
    runner, mock_spawner, mock_monitor, mock_reaction = _make_wave_runner(tmp_path)

    summary_t001 = tmp_path / "summaries" / "T-001.md"
    summary_t002 = tmp_path / "summaries" / "T-002.md"

    received: dict[str, list[Path]] = {}

    def _spawn(task: Task, summaries):
        received[task.id] = list(summaries)
        return _make_session(tmp_path, task.id)

    mock_spawner.spawn.side_effect = _spawn
    mock_monitor.watch.side_effect = lambda s: s
    mock_reaction.on_complete.return_value = MagicMock()

    wave = {
        "wave": 1,
        "tasks": [
            _make_task_dict("T-003", depends_on=["T-001"]),
            _make_task_dict("T-004", depends_on=["T-001"]),
        ],
    }
    prior_summaries = {"T-001": summary_t001, "T-002": summary_t002}
    runner.run_wave(wave, prior_summaries)

    assert received["T-003"] == [summary_t001]
    assert received["T-004"] == [summary_t001]
    assert summary_t002 not in received["T-003"]
    assert summary_t002 not in received["T-004"]


# ─────────────────────────────────────────────────────────────────────────────
# c) WaveResult correctly categorises complete / stalled / failed tasks
# ─────────────────────────────────────────────────────────────────────────────


def test_wave_runner_result_categorizes_complete_stalled_failed(
    tmp_path: Path,
) -> None:
    runner, mock_spawner, mock_monitor, mock_reaction = _make_wave_runner(tmp_path)

    statuses = {"T-001": "complete", "T-002": "stalled", "T-003": "failed"}

    def _spawn(task: Task, summaries):
        s = _make_session(tmp_path, task.id, status="running")
        return s

    def _watch(session: SessionState) -> SessionState:
        session.status = statuses[session.task_id]  # type: ignore[assignment]
        return session

    mock_spawner.spawn.side_effect = _spawn
    mock_monitor.watch.side_effect = _watch
    mock_reaction.on_complete.return_value = MagicMock()

    wave = {
        "wave": 0,
        "tasks": [
            _make_task_dict("T-001"),
            _make_task_dict("T-002"),
            _make_task_dict("T-003"),
        ],
    }
    result = runner.run_wave(wave, {})

    assert result.completed == ["T-001"]
    assert result.stalled == ["T-002"]
    assert result.failed == ["T-003"]
    assert "T-001" in result.summaries
    assert "T-002" not in result.summaries
    assert "T-003" not in result.summaries


# ─────────────────────────────────────────────────────────────────────────────
# d) run_all gates on wave failure — next wave is NOT started
# ─────────────────────────────────────────────────────────────────────────────


def test_run_all_gates_on_wave_failure_does_not_advance(tmp_path: Path) -> None:
    runner, mock_spawner, mock_monitor, mock_reaction = _make_wave_runner(tmp_path)

    def _spawn(task: Task, summaries):
        return _make_session(tmp_path, task.id, status="running")

    def _watch(session: SessionState) -> SessionState:
        # Wave 0 task fails; wave 1 task should never be called
        session.status = "failed"  # type: ignore[assignment]
        return session

    mock_spawner.spawn.side_effect = _spawn
    mock_monitor.watch.side_effect = _watch

    task_graph = {
        "waves": [
            {"wave": 0, "tasks": [_make_task_dict("T-001")]},
            {"wave": 1, "tasks": [_make_task_dict("T-003", depends_on=["T-001"])]},
        ]
    }

    results = runner.run_all(task_graph)

    # Only wave 0 should have executed
    assert len(results) == 1
    assert results[0].wave_number == 0
    assert results[0].failed == ["T-001"]

    # Wave 1's task (T-003) must never have been spawned
    spawned_ids = [call.args[0].id for call in mock_spawner.spawn.call_args_list]
    assert "T-003" not in spawned_ids


# ─────────────────────────────────────────────────────────────────────────────
# e) run_all accumulates summaries across waves
# ─────────────────────────────────────────────────────────────────────────────


def test_run_all_accumulates_summaries_across_waves(tmp_path: Path) -> None:
    """Wave 1 tasks must receive the summaries produced by wave 0."""
    runner, mock_spawner, mock_monitor, mock_reaction = _make_wave_runner(tmp_path)
    devos_dir = tmp_path / ".devos"
    (devos_dir / "summaries").mkdir(parents=True, exist_ok=True)

    # Create dummy summary files so the path references are real
    (devos_dir / "summaries" / "T-001.md").write_text("# T-001 summary", encoding="utf-8")
    (devos_dir / "summaries" / "T-002.md").write_text("# T-002 summary", encoding="utf-8")

    received_by_task: dict[str, list[Path]] = {}

    def _spawn(task: Task, summaries):
        received_by_task[task.id] = list(summaries)
        return _make_session(tmp_path, task.id, status="running")

    mock_spawner.spawn.side_effect = _spawn
    mock_monitor.watch.side_effect = lambda s: (setattr(s, "status", "complete") or s)
    mock_reaction.on_complete.return_value = MagicMock()

    task_graph = {
        "waves": [
            {
                "wave": 0,
                "tasks": [_make_task_dict("T-001"), _make_task_dict("T-002")],
            },
            {
                "wave": 1,
                "tasks": [_make_task_dict("T-003", depends_on=["T-001"])],
            },
        ]
    }
    runner.run_all(task_graph)

    # T-003 should receive T-001's summary (it's in its depends_on)
    t003_received = received_by_task.get("T-003", [])
    expected_summary = devos_dir / "summaries" / "T-001.md"
    assert expected_summary in t003_received, (
        f"T-003 should have received T-001 summary, got: {t003_received}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# f) run_all executes waves in ascending order (0, 1, 2, 3)
# ─────────────────────────────────────────────────────────────────────────────


def test_run_all_passes_correct_wave_order(tmp_path: Path) -> None:
    runner, mock_spawner, mock_monitor, mock_reaction = _make_wave_runner(tmp_path)

    waves_executed: list[int] = []

    def _spawn(task: Task, summaries):
        # Infer wave from task ID: T-001/T-002 → wave 0, T-003/T-004 → wave 1 …
        return _make_session(tmp_path, task.id, status="running")

    mock_spawner.spawn.side_effect = _spawn
    mock_monitor.watch.side_effect = lambda s: (setattr(s, "status", "complete") or s)
    mock_reaction.on_complete.return_value = MagicMock()

    # Provide task_graph with waves intentionally in reverse order to verify sort
    task_graph = {
        "waves": [
            {"wave": 2, "tasks": [_make_task_dict("T-005")]},
            {"wave": 0, "tasks": [_make_task_dict("T-001")]},
            {"wave": 1, "tasks": [_make_task_dict("T-003", depends_on=["T-001"])]},
        ]
    }

    results = runner.run_all(task_graph)

    executed_wave_numbers = [r.wave_number for r in results]
    assert executed_wave_numbers == [0, 1, 2], (
        f"Expected waves [0, 1, 2], got {executed_wave_numbers}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# g) duration_seconds is wall-clock, not sum of individual task times
# ─────────────────────────────────────────────────────────────────────────────


def test_wave_result_duration_is_wall_clock_not_sum_of_task_times(
    tmp_path: Path,
) -> None:
    """Two tasks each sleep 0.08 s in parallel.  Wall-clock ≈ 0.08 s, not 0.16 s."""
    runner, mock_spawner, mock_monitor, mock_reaction = _make_wave_runner(tmp_path)
    TASK_SLEEP = 0.08  # seconds

    def _spawn(task: Task, summaries):
        time.sleep(TASK_SLEEP)
        return _make_session(tmp_path, task.id, status="running")

    mock_spawner.spawn.side_effect = _spawn
    mock_monitor.watch.side_effect = lambda s: (setattr(s, "status", "complete") or s)
    mock_reaction.on_complete.return_value = MagicMock()

    wave = {
        "wave": 0,
        "tasks": [_make_task_dict("T-001"), _make_task_dict("T-002")],
    }
    result = runner.run_wave(wave, {})

    # Wall-clock must be well under 2× TASK_SLEEP (parallel), with 30 % headroom.
    assert result.duration_seconds < TASK_SLEEP * 2 * 0.70, (
        f"duration {result.duration_seconds:.3f}s suggests serial execution "
        f"(expected < {TASK_SLEEP * 2 * 0.70:.3f}s)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# h) Empty wave returns WaveResult with all empty lists
# ─────────────────────────────────────────────────────────────────────────────


def test_wave_runner_handles_empty_wave_gracefully(tmp_path: Path) -> None:
    runner, mock_spawner, mock_monitor, mock_reaction = _make_wave_runner(tmp_path)

    wave = {"wave": 0, "tasks": []}
    result = runner.run_wave(wave, {})

    assert result.wave_number == 0
    assert result.completed == []
    assert result.stalled == []
    assert result.failed == []
    assert result.summaries == {}
    assert result.duration_seconds == 0.0

    mock_spawner.spawn.assert_not_called()
    mock_monitor.watch.assert_not_called()
