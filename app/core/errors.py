"""Domain errors mapped to HTTP responses by the API layer."""


class DomainError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class InsufficientStockError(ConflictError):
    code = "insufficient_stock"


class AuthenticationError(DomainError):
    status_code = 401
    code = "not_authenticated"


class AccountLockedError(DomainError):
    status_code = 423
    code = "account_locked"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "forbidden"
