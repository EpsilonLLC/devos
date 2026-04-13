"""Tests for the Execution Layer — worktree, spawner, monitor, reaction, CLI.

Test IDs:
  a) test_worktree_create_runs_git_worktree_add
  b) test_worktree_remove_runs_git_worktree_remove_force
  c) test_worktree_create_raises_on_git_failure
  d) test_spawner_assembles_context_before_launch
  e) test_spawner_copies_context_into_worktree
  f) test_spawner_writes_state_json_on_spawn
  g) test_spawner_raises_spawn_error_on_budget_exceeded
  h) test_monitor_returns_complete_when_process_exits_zero
  i) test_monitor_returns_stalled_when_timeout_exceeded
  j) test_monitor_returns_failed_when_process_exits_nonzero
  k) test_reaction_on_complete_distills_and_removes_worktree
  l) test_reaction_on_stall_logs_event_preserves_worktree
  m) test_reaction_on_failure_logs_event_preserves_worktree
  n) test_cli_run_fails_if_no_task_graph
  o) test_cli_run_fails_if_spec_hash_mismatch
  p) test_cli_run_selects_first_wave0_task_by_default

No live agent calls — subprocess, git, and Anthropic are mocked throughout.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from devos.agents.base import SessionState
from devos.agents.claude_code import ClaudeCodeAdapter
from devos.context.assembler import ContextBudgetExceededError, ContextPackage
from devos.context.distiller import DistilledSummary
from devos.decomposition.dependency_graph import Task
from devos.execution.monitor import SessionMonitor
from devos.execution.reaction import ReactionEngine
from devos.execution.spawner import AgentSpawner, SpawnError, write_state_json
from devos.execution.worktree import WorktreeError, WorktreeManager


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures / factories
# ─────────────────────────────────────────────────────────────────────────────


def _make_task(task_id: str = "T-001") -> Task:
    return Task(
        id=task_id,
        name="core module",
        component="core",
        spec_files=["spec/04_components.md"],
        relevant_files=[],
        depends_on=[],
        write_targets=["devos/core/"],
        write_tables=[],
        feature_ids=[],
        acceptance_ids=[],
    )


def _make_session(
    tmp_path: Path,
    task_id: str = "T-001",
    status: str = "running",
    output_path: Path | None = None,
) -> SessionState:
    wt = tmp_path / "worktrees" / task_id
    wt.mkdir(parents=True, exist_ok=True)
    return SessionState(
        session_id=f"{task_id}-abc12345",
        task_id=task_id,
        agent_name="ClaudeCodeAdapter",
        started_at=datetime.now(timezone.utc),
        worktree_path=wt,
        context_path=wt / ".devos" / "context",
        status=status,  # type: ignore[arg-type]
        output_path=output_path,
    )


def _make_distilled_summary(task_id: str = "T-001") -> DistilledSummary:
    md = f"# Task summary: {task_id} — core module\n\n## Decisions made\n- Used defaults\n"
    return DistilledSummary(
        task_id=task_id,
        task_name="core module",
        decisions=["Used defaults"],
        files_modified=[],
        interfaces_produced=[],
        deviations=None,
        tests_written=[],
        outstanding=None,
        rendered_markdown=md,
        token_estimate=len(md) // 4,
    )


# ─────────────────────────────────────────────────────────────────────────────
# a) WorktreeManager.create calls git worktree add
# ─────────────────────────────────────────────────────────────────────────────


def test_worktree_create_runs_git_worktree_add(tmp_path: Path) -> None:
    with patch("devos.execution.worktree.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock()
        wm = WorktreeManager(tmp_path)
        result = wm.create("T-001")

    expected_wt = tmp_path / ".devos" / "worktrees" / "T-001"
    mock_run.assert_called_once_with(
        ["git", "worktree", "add", str(expected_wt), "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    )
    assert result == expected_wt


# ─────────────────────────────────────────────────────────────────────────────
# b) WorktreeManager.remove calls git worktree remove --force
# ─────────────────────────────────────────────────────────────────────────────


def test_worktree_remove_runs_git_worktree_remove_force(tmp_path: Path) -> None:
    with patch("devos.execution.worktree.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock()
        wm = WorktreeManager(tmp_path)
        wm.remove("T-001")

    expected_wt = tmp_path / ".devos" / "worktrees" / "T-001"
    mock_run.assert_called_once_with(
        ["git", "worktree", "remove", "--force", str(expected_wt)],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# c) WorktreeManager.create raises WorktreeError on git failure
# ─────────────────────────────────────────────────────────────────────────────


def test_worktree_create_raises_on_git_failure(tmp_path: Path) -> None:
    err = subprocess.CalledProcessError(
        returncode=128,
        cmd=["git", "worktree", "add"],
        stderr="fatal: already exists",
    )
    with patch("devos.execution.worktree.subprocess.run", side_effect=err):
        wm = WorktreeManager(tmp_path)
        with pytest.raises(WorktreeError, match="T-001"):
            wm.create("T-001")


# ─────────────────────────────────────────────────────────────────────────────
# d) AgentSpawner.spawn calls assembler before launching process
# ─────────────────────────────────────────────────────────────────────────────


def test_spawner_assembles_context_before_launch(tmp_path: Path) -> None:
    call_order: list[str] = []

    # Build a minimal ContextPackage with a real session_dir on disk
    session_dir = tmp_path / "sessions" / "T-001" / "context"
    session_dir.mkdir(parents=True)
    (session_dir / "task.md").write_text("# Task", encoding="utf-8")

    pkg = ContextPackage(
        task_id="T-001",
        session_dir=session_dir,
        blocks=[],
        total_tokens=0,
        manifest={},
    )

    mock_assembler = MagicMock()

    def _assemble(*_a, **_kw):
        call_order.append("assemble")
        return pkg

    mock_assembler.assemble.side_effect = _assemble

    mock_wm = MagicMock()
    wt_path = tmp_path / "worktrees" / "T-001"
    wt_path.mkdir(parents=True)

    def _create(*_a, **_kw):
        call_order.append("create_worktree")
        return wt_path

    mock_wm.create.side_effect = _create

    mock_adapter = MagicMock()
    mock_adapter.build_launch_command.return_value = ["echo", "done"]

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()

    with patch("devos.execution.spawner.subprocess.Popen") as mock_popen, \
         patch("devos.execution.spawner.shutil.copytree"):
        mock_popen.return_value = mock_proc
        spawner = AgentSpawner(tmp_path, devos_dir, mock_assembler, mock_wm, mock_adapter)
        spawner.spawn(_make_task())

    assert call_order[0] == "assemble", "assemble must be called before worktree creation"
    assert call_order[1] == "create_worktree"


# ─────────────────────────────────────────────────────────────────────────────
# e) AgentSpawner.spawn copies context into worktree
# ─────────────────────────────────────────────────────────────────────────────


def test_spawner_copies_context_into_worktree(tmp_path: Path) -> None:
    session_dir = tmp_path / "sessions" / "T-001" / "context"
    session_dir.mkdir(parents=True)
    (session_dir / "task.md").write_text("# Task", encoding="utf-8")

    pkg = ContextPackage(
        task_id="T-001",
        session_dir=session_dir,
        blocks=[],
        total_tokens=0,
        manifest={},
    )

    mock_assembler = MagicMock()
    mock_assembler.assemble.return_value = pkg

    mock_wm = MagicMock()
    wt_path = tmp_path / "worktrees" / "T-001"
    wt_path.mkdir(parents=True)
    mock_wm.create.return_value = wt_path

    mock_adapter = MagicMock()
    mock_adapter.build_launch_command.return_value = ["echo", "done"]

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()

    captured_copytree_args: list[tuple] = []

    def _capture_copytree(src, dst, **kw):
        captured_copytree_args.append((src, dst))

    with patch("devos.execution.spawner.subprocess.Popen") as mock_popen, \
         patch("devos.execution.spawner.shutil.copytree", side_effect=_capture_copytree):
        mock_popen.return_value = mock_proc
        spawner = AgentSpawner(tmp_path, devos_dir, mock_assembler, mock_wm, mock_adapter)
        spawner.spawn(_make_task())

    assert len(captured_copytree_args) == 1
    src, dst = captured_copytree_args[0]
    assert src == session_dir
    assert dst == wt_path / ".devos" / "context"


# ─────────────────────────────────────────────────────────────────────────────
# f) AgentSpawner.spawn writes state.json immediately on spawn
# ─────────────────────────────────────────────────────────────────────────────


def test_spawner_writes_state_json_on_spawn(tmp_path: Path) -> None:
    session_dir = tmp_path / "sessions" / "T-001" / "context"
    session_dir.mkdir(parents=True)
    (session_dir / "task.md").write_text("# Task", encoding="utf-8")

    pkg = ContextPackage(
        task_id="T-001",
        session_dir=session_dir,
        blocks=[],
        total_tokens=0,
        manifest={},
    )

    mock_assembler = MagicMock()
    mock_assembler.assemble.return_value = pkg

    mock_wm = MagicMock()
    wt_path = tmp_path / "worktrees" / "T-001"
    wt_path.mkdir(parents=True)
    mock_wm.create.return_value = wt_path

    mock_adapter = MagicMock()
    mock_adapter.build_launch_command.return_value = ["echo", "done"]

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()

    with patch("devos.execution.spawner.subprocess.Popen") as mock_popen, \
         patch("devos.execution.spawner.shutil.copytree"):
        mock_popen.return_value = mock_proc
        spawner = AgentSpawner(tmp_path, devos_dir, mock_assembler, mock_wm, mock_adapter)
        session = spawner.spawn(_make_task())

    state_path = devos_dir / "sessions" / "T-001" / "state.json"
    assert state_path.exists(), "state.json must be written on spawn"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["task_id"] == "T-001"
    assert state["status"] == "running"
    assert state["session_id"] == session.session_id


# ─────────────────────────────────────────────────────────────────────────────
# g) AgentSpawner.spawn raises SpawnError when context budget is exceeded
# ─────────────────────────────────────────────────────────────────────────────


def test_spawner_raises_spawn_error_on_budget_exceeded(tmp_path: Path) -> None:
    mock_assembler = MagicMock()
    mock_assembler.assemble.side_effect = ContextBudgetExceededError("too large")

    mock_wm = MagicMock()
    mock_adapter = MagicMock()
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()

    spawner = AgentSpawner(tmp_path, devos_dir, mock_assembler, mock_wm, mock_adapter)
    with pytest.raises(SpawnError, match="Context budget exceeded"):
        spawner.spawn(_make_task())


# ─────────────────────────────────────────────────────────────────────────────
# h) SessionMonitor returns "complete" when process exits with code 0
# ─────────────────────────────────────────────────────────────────────────────


def test_monitor_returns_complete_when_process_exits_zero(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()

    mock_adapter = MagicMock()
    mock_adapter.is_complete.return_value = True
    mock_adapter.is_stalled.return_value = False

    session = _make_session(tmp_path)
    monitor = SessionMonitor(devos_dir, mock_adapter, poll_interval_seconds=0.0)

    result = monitor.watch(session)

    assert result.status == "complete"
    state_path = devos_dir / "sessions" / "T-001" / "state.json"
    assert state_path.exists()
    assert json.loads(state_path.read_text())["status"] == "complete"


# ─────────────────────────────────────────────────────────────────────────────
# i) SessionMonitor returns "stalled" when adapter reports stall
# ─────────────────────────────────────────────────────────────────────────────


def test_monitor_returns_stalled_when_timeout_exceeded(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()

    mock_adapter = MagicMock()
    mock_adapter.is_complete.return_value = False
    mock_adapter.is_stalled.return_value = True

    session = _make_session(tmp_path)
    monitor = SessionMonitor(devos_dir, mock_adapter, poll_interval_seconds=0.0)

    result = monitor.watch(session)

    assert result.status == "stalled"
    state_path = devos_dir / "sessions" / "T-001" / "state.json"
    assert json.loads(state_path.read_text())["status"] == "stalled"


# ─────────────────────────────────────────────────────────────────────────────
# j) SessionMonitor returns "failed" when process exits non-zero
# ─────────────────────────────────────────────────────────────────────────────


def test_monitor_returns_failed_when_process_exits_nonzero(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()

    mock_adapter = MagicMock()
    mock_adapter.is_complete.return_value = False
    mock_adapter.is_stalled.return_value = False
    mock_adapter.is_failed.return_value = True
    # Also expose _session_registry so _get_exit_code works
    mock_entry = MagicMock()
    mock_entry.process.poll.return_value = 1
    mock_adapter._session_registry = {"T-001-abc12345": mock_entry}

    session = _make_session(tmp_path)
    monitor = SessionMonitor(devos_dir, mock_adapter, poll_interval_seconds=0.0)

    result = monitor.watch(session)

    assert result.status == "failed"
    state_path = devos_dir / "sessions" / "T-001" / "state.json"
    state = json.loads(state_path.read_text())
    assert state["status"] == "failed"
    assert state.get("exit_code") == 1


# ─────────────────────────────────────────────────────────────────────────────
# k) ReactionEngine.on_complete distills output and removes worktree
# ─────────────────────────────────────────────────────────────────────────────


def test_reaction_on_complete_distills_and_removes_worktree(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    (devos_dir / "sessions" / "T-001").mkdir(parents=True)
    output_txt = devos_dir / "sessions" / "T-001" / "output.txt"
    output_txt.write_text("Agent did stuff.", encoding="utf-8")

    session = _make_session(tmp_path, output_path=output_txt)
    # pre-write a state.json for write_state_json to update
    write_state_json(session, devos_dir)

    summary = _make_distilled_summary()

    mock_distiller = MagicMock()
    mock_distiller.distill.return_value = summary
    mock_distiller.write.return_value = devos_dir / "summaries" / "T-001.md"

    mock_wm = MagicMock()
    mock_monitor = MagicMock()
    mock_monitor.get_output.return_value = "Agent did stuff."

    reaction = ReactionEngine(devos_dir, mock_distiller, mock_wm, MagicMock(), mock_monitor)
    result = reaction.on_complete(session, _make_task())

    mock_distiller.distill.assert_called_once_with("T-001", "core module", "Agent did stuff.")
    mock_wm.remove.assert_called_once_with("T-001")
    assert result is summary


# ─────────────────────────────────────────────────────────────────────────────
# l) ReactionEngine.on_stall logs event.jsonl and preserves worktree
# ─────────────────────────────────────────────────────────────────────────────


def test_reaction_on_stall_logs_event_preserves_worktree(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    (devos_dir / "sessions" / "T-001").mkdir(parents=True)
    write_state_json(_make_session(tmp_path), devos_dir)

    mock_distiller = MagicMock()
    mock_wm = MagicMock()
    mock_monitor = MagicMock()
    mock_monitor.tail_output.return_value = "last output line"

    session = _make_session(tmp_path, status="stalled")
    reaction = ReactionEngine(devos_dir, mock_distiller, mock_wm, MagicMock(), mock_monitor)
    reaction.on_stall(session, _make_task())

    # Worktree must NOT be removed
    mock_wm.remove.assert_not_called()

    # events.jsonl must be written
    events_path = devos_dir / "sessions" / "T-001" / "events.jsonl"
    assert events_path.exists()
    lines = [json.loads(ln) for ln in events_path.read_text().splitlines() if ln.strip()]
    assert any(e["event"] == "stall" for e in lines)


# ─────────────────────────────────────────────────────────────────────────────
# m) ReactionEngine.on_failure logs event.jsonl and preserves worktree
# ─────────────────────────────────────────────────────────────────────────────


def test_reaction_on_failure_logs_event_preserves_worktree(tmp_path: Path) -> None:
    devos_dir = tmp_path / ".devos"
    devos_dir.mkdir()
    session_dir = devos_dir / "sessions" / "T-001"
    session_dir.mkdir(parents=True)

    session = _make_session(tmp_path, status="failed")
    write_state_json(session, devos_dir, exit_code=1)

    mock_distiller = MagicMock()
    mock_wm = MagicMock()
    mock_monitor = MagicMock()
    mock_monitor.tail_stderr.return_value = "Error: something went wrong"

    reaction = ReactionEngine(devos_dir, mock_distiller, mock_wm, MagicMock(), mock_monitor)
    reaction.on_failure(session, _make_task())

    # Worktree must NOT be removed
    mock_wm.remove.assert_not_called()

    # events.jsonl must be written
    events_path = devos_dir / "sessions" / "T-001" / "events.jsonl"
    assert events_path.exists()
    lines = [json.loads(ln) for ln in events_path.read_text().splitlines() if ln.strip()]
    assert any(e["event"] == "failure" for e in lines)


# ─────────────────────────────────────────────────────────────────────────────
# n) CLI `devos run` fails if task_graph.json is absent
# ─────────────────────────────────────────────────────────────────────────────


def test_cli_run_fails_if_no_task_graph(tmp_path: Path) -> None:
    from devos.cli import cli

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["run"])

    assert result.exit_code != 0
    assert "devos build" in result.output.lower() or "task graph" in result.output.lower()


# ─────────────────────────────────────────────────────────────────────────────
# o) CLI `devos run` fails if spec hash does not match task graph
# ─────────────────────────────────────────────────────────────────────────────


def test_cli_run_fails_if_spec_hash_mismatch(tmp_path: Path) -> None:
    from devos.cli import cli

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # Create minimal spec dir so _compute_spec_hash does not error
        spec_dir = Path("spec")
        spec_dir.mkdir()
        for name in [
            "00_product.md",
            "01_functional.md",
            "02_data_model.md",
            "03_api_contract.md",
            "04_components.md",
            "05_acceptance.md",
        ]:
            (spec_dir / name).write_text(f"# {name}", encoding="utf-8")

        # Write task_graph.json with a deliberately wrong spec_hash
        devos_dir = Path(".devos")
        devos_dir.mkdir()
        task_graph = {
            "spec_hash": "0000000000000000000000000000000000000000000000000000000000000000",
            "waves": [{"wave": 0, "tasks": []}],
        }
        (devos_dir / "task_graph.json").write_text(
            json.dumps(task_graph), encoding="utf-8"
        )

        result = runner.invoke(cli, ["run"])

    assert result.exit_code != 0
    assert "spec" in result.output.lower() or "stale" in result.output.lower()


# ─────────────────────────────────────────────────────────────────────────────
# p) CLI `devos run` selects first Wave 0 task lexicographically by default
# ─────────────────────────────────────────────────────────────────────────────


def test_cli_run_selects_first_wave0_task_by_default(tmp_path: Path) -> None:
    """Verify that without --task, the CLI picks the lexicographically first
    task in Wave 0 and passes it to AgentSpawner.spawn."""
    from devos.cli import cli

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # Build real spec files so the hash matches
        spec_dir = Path("spec")
        spec_dir.mkdir()
        spec_content = {
            "00_product.md": "# Product\n",
            "01_functional.md": "# Functional\n",
            "02_data_model.md": "# Data\n",
            "03_api_contract.md": "# API\n",
            "04_components.md": "# Components\n",
            "05_acceptance.md": "# Acceptance\n",
        }
        for name, content in spec_content.items():
            (spec_dir / name).write_text(content, encoding="utf-8")

        # Compute the real spec hash via the CLI helper
        import hashlib

        hasher = hashlib.sha256()
        for name in sorted(spec_content):
            hasher.update(spec_content[name].encode("utf-8"))

        # Use _compute_spec_hash directly for correctness
        from devos.cli import _compute_spec_hash

        real_hash = _compute_spec_hash(spec_dir)

        wave0_tasks = [
            {
                "id": "T-002",
                "name": "task two",
                "component": "core",
                "spec_files": [],
                "relevant_files": [],
                "depends_on": [],
                "write_targets": [],
                "write_tables": [],
                "feature_ids": [],
                "acceptance_ids": [],
            },
            {
                "id": "T-001",
                "name": "task one",
                "component": "core",
                "spec_files": [],
                "relevant_files": [],
                "depends_on": [],
                "write_targets": [],
                "write_tables": [],
                "feature_ids": [],
                "acceptance_ids": [],
            },
        ]

        devos_dir = Path(".devos")
        devos_dir.mkdir()
        (devos_dir / "task_graph.json").write_text(
            json.dumps(
                {
                    "spec_hash": real_hash,
                    "waves": [{"wave": 0, "tasks": wave0_tasks}],
                }
            ),
            encoding="utf-8",
        )
        # Write a constraints.md so ConstraintInjector doesn't fail
        constraints_content = (
            "# Constraints\n"
            "## Hard rules\n"
            "- All DB queries include tenant_id\n"
            "- No raw SQL outside repositories\n"
            "- All endpoints return error envelope\n"
        )
        (devos_dir / "constraints.md").write_text(constraints_content, encoding="utf-8")

        spawned_tasks: list[str] = []

        def _fake_spawn(self, task, prior_summaries=None):
            spawned_tasks.append(task.id)
            raise SpawnError("test sentinel")

        with patch("devos.execution.spawner.AgentSpawner.spawn", _fake_spawn):
            result = runner.invoke(cli, ["run"])

    # T-001 is lexicographically first (before T-002)
    assert spawned_tasks == ["T-001"], (
        f"Expected T-001 to be spawned first, got {spawned_tasks!r}"
    )
