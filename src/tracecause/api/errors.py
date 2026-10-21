"""Safe boundary errors; never serialize exception details or request bodies."""

from uuid import UUID

from starlette.exceptions import HTTPException


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False):
        super().__init__(status_code=status, detail=code)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def envelope(error: ApiError, request_id: UUID) -> dict[str, object]:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "request_id": str(request_id),
            "retryable": error.retryable,
        }
    }
