"""Notes preview truncation for dashboard task cards.

The full notes field can hold up to 10,000 characters (NOTES_MAX_CHARS in
core/constants.py).  The dashboard card view shows only the first
NOTES_PREVIEW_MAX_LENGTH characters so that task lists stay scannable.
Full content is available via GET /api/v1/tasks/{task_id}.
"""

from __future__ import annotations

NOTES_PREVIEW_MAX_LENGTH: int = 200


def truncate_notes_preview(notes: str) -> str:
    """Return at most the first NOTES_PREVIEW_MAX_LENGTH characters of *notes*.

    Returns an empty string when *notes* is empty or None-like so that callers
    never receive None in the preview field.
    """
    if not notes:
        return ""
    return notes[:NOTES_PREVIEW_MAX_LENGTH]
