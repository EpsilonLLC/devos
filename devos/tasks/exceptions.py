from devos.core.exceptions import Http400Error, Http403Error, Http404Error


class TaskNotFoundError(Http404Error):
    def __init__(self, task_id: str = "") -> None:
        detail = f"task_id={task_id}" if task_id else ""
        super().__init__("Task not found", detail)


class TaskForbiddenError(Http403Error):
    def __init__(self, task_id: str = "") -> None:
        detail = f"task_id={task_id}" if task_id else ""
        super().__init__("Access to this task is forbidden", detail)


class TaskValidationError(Http400Error):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, detail)
