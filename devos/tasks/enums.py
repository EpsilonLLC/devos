import enum


class TaskStatus(str, enum.Enum):
    todo = "to-do"
    in_progress = "in-progress"
    done = "done"


class Priority(str, enum.Enum):
    high = "high"
    medium = "medium"
    low = "low"
