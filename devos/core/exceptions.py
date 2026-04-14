class AppException(Exception):
    def __init__(self, message: str, code: int, detail: str = "") -> None:
        self.message = message
        self.code = code
        self.detail = detail
        super().__init__(message)


class Http400Error(AppException):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, 400, detail)


class Http401Error(AppException):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, 401, detail)


class Http403Error(AppException):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, 403, detail)


class Http404Error(AppException):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, 404, detail)


class Http500Error(AppException):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, 500, detail)
