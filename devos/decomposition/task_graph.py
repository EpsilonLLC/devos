"""Assign tasks to execution waves and serialize to .devos/task_graph.json.

Wave assignment algorithm:
  wave(task) = 0                            if task has no dependencies
  wave(task) = max(wave(dep) for dep) + 1   otherwise

The graph is deterministic: tasks within each wave are sorted by task ID,
waves are sorted numerically. Same spec always produces the same JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from devos.decomposition.dependency_graph import DependencyGraph, Task


@dataclass
class Wave:
    wave: int
    tasks: list[Task]


class TaskGraph:
    """Wave-ordered task graph built from a DependencyGraph."""

    def __init__(self, waves: list[Wave]) -> None:
        self.waves = waves

    @classmethod
    def build(cls, dependency_graph: DependencyGraph) -> "TaskGraph":
        """Assign tasks to waves based on dependency depth."""
        waves = _assign_waves(dependency_graph.tasks)
        return cls(waves=waves)

    def write(self, output_path: Path, spec_hash: str) -> None:
        """Serialize the task graph to JSON and write to output_path.

        Args:
            output_path: Destination path (typically .devos/task_graph.json).
            spec_hash:   SHA-256 of the 6 spec files — used at execution time
                         to detect a stale graph.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._serialize(spec_hash)
        output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _serialize(self, spec_hash: str) -> dict:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "spec_hash": spec_hash,
            "total_tasks": sum(len(w.tasks) for w in self.waves),
            "waves": [
                {
                    "wave": w.wave,
                    "tasks": [
                        _task_to_dict(t)
                        for t in sorted(w.tasks, key=lambda t: t.id)
                    ],
                }
                for w in sorted(self.waves, key=lambda w: w.wave)
            ],
        }


def _assign_waves(tasks: list[Task]) -> list[Wave]:
    """Compute wave number for each task via recursive depth resolution."""
    task_map: dict[str, Task] = {t.id: t for t in tasks}
    wave_cache: dict[str, int] = {}

    def get_wave(task_id: str) -> int:
        if task_id in wave_cache:
            return wave_cache[task_id]
        task = task_map[task_id]
        if not task.depends_on:
            wave_cache[task_id] = 0
        else:
            wave_cache[task_id] = max(get_wave(dep) for dep in task.depends_on) + 1
        return wave_cache[task_id]

    for task in tasks:
        get_wave(task.id)

    waves_dict: dict[int, list[Task]] = {}
    for task in tasks:
        w = wave_cache[task.id]
        waves_dict.setdefault(w, []).append(task)

    return [Wave(wave=w, tasks=ts) for w, ts in sorted(waves_dict.items())]


def _task_to_dict(task: Task) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "component": task.component,
        "spec_files": task.spec_files,
        "relevant_files": task.relevant_files,
        "depends_on": task.depends_on,
        "write_targets": task.write_targets,
        "write_tables": task.write_tables,
        "feature_ids": task.feature_ids,
        "acceptance_ids": task.acceptance_ids,
    }
