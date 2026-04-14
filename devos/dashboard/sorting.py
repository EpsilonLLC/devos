"""Task sorting and grouping logic for the personal dashboard.

Sort order (F-006 spec):
  Primary key  — status group: to-do (0) → in-progress (1) → done (2)
  Secondary key — priority:    high  (0) → medium     (1) → low  (2)
                                tasks with no/unknown priority sort last (99)

The sort is stable so tasks that tie on both keys preserve their original
order (typically insertion / created_at order from the repository query).
"""

from __future__ import annotations

from typing import Any

# Status group rank — lower number renders first
_STATUS_ORDER: dict[str, int] = {
    "to-do": 0,
    "in-progress": 1,
    "done": 2,
}

# Priority rank — lower number renders first within a status group
_PRIORITY_ORDER: dict[str, int] = {
    "high": 0,
    "medium": 1,
    "low": 2,
}

# Sentinel used when a field value is absent or unrecognised; sorts last
_UNKNOWN_RANK: int = 99


def sort_and_group_tasks(tasks: list[Any]) -> list[Any]:
    """Return *tasks* sorted by status group then priority.

    Accepts any sequence of objects that expose ``.status`` and
    ``.priority`` string attributes (SQLAlchemy ORM rows or Pydantic
    models both work).

    Tasks whose status or priority is not in the expected enum values are
    placed after all well-known values so that unknown data never raises.
    """

    def _sort_key(task: Any) -> tuple[int, int]:
        status_rank = _STATUS_ORDER.get(task.status, _UNKNOWN_RANK)
        priority_rank = _PRIORITY_ORDER.get(task.priority, _UNKNOWN_RANK)
        return (status_rank, priority_rank)

    return sorted(tasks, key=_sort_key)
