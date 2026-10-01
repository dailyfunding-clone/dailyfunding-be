from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError


class ApiError(APIException):
    """Base error rendered as {code, message, details}."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "INTERNAL"
    default_detail = "internal error"

    def __init__(self, message=None, details=None):
        self.details = details or {}
        super().__init__(detail=message or self.default_detail, code=self.default_code)


class ValidationFailed(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "VALIDATION_ERROR"
    default_detail = "validation failed"


class IdempotencyKeyRequired(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "IDEMPOTENCY_KEY_REQUIRED"
    default_detail = "Idempotency-Key header is required"


class IdempotencyKeyMismatch(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "IDEMPOTENCY_KEY_MISMATCH"
    default_detail = "idempotency key reused with a different payload"


class Unauthorized(ApiError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_code = "UNAUTHORIZED"
    default_detail = "authentication required or failed"


class ReauthRequired(ApiError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_code = "REAUTH_REQUIRED"
    default_detail = "reauth token required or expired"


class SuitabilityRequired(ApiError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "SUITABILITY_REQUIRED"
    default_detail = "suitability test required or expired"


class GradeLimitExceeded(ApiError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "GRADE_LIMIT_EXCEEDED"
    default_detail = "grade investment limit exceeded"


class BorrowerLimitExceeded(ApiError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "BORROWER_LIMIT_EXCEEDED"
    default_detail = "same-borrower limit exceeded"


class NotFound(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "NOT_FOUND"
    default_detail = "not found"


class RecruitmentClosed(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "RECRUITMENT_CLOSED"
    default_detail = "product is not recruiting"


class InsufficientDeposit(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "INSUFFICIENT_DEPOSIT"
    default_detail = "insufficient deposit"


class InsufficientRemaining(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "INSUFFICIENT_REMAINING"
    default_detail = "insufficient remaining recruitment amount"


class StateConflict(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "STATE_CONFLICT"
    default_detail = "state transition not allowed"


class PinLocked(ApiError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "PIN_LOCKED"
    default_detail = "pin locked after 5 failures"


class EmailTaken(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "EMAIL_TAKEN"
    default_detail = "email already registered"


class DuplicateCi(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "DUPLICATE_CI"
    default_detail = "an account with this identity already exists"


def _error_payload(code, message, details):
    return {"code": code, "message": message, "details": details or {}}


def exception_handler(exc, context):
    if isinstance(exc, ApiError):
        from rest_framework.response import Response

        return Response(
            _error_payload(exc.default_code, str(exc.detail), exc.details),
            status=exc.status_code,
        )

    if isinstance(exc, ValidationError):
        from rest_framework.response import Response

        return Response(
            _error_payload("VALIDATION_ERROR", "validation failed", exc.detail),
            status=status.HTTP_400_BAD_REQUEST,
        )

    from rest_framework.views import exception_handler as drf_exception_handler

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    code = "INTERNAL"
    if response.status_code == 401:
        code = "UNAUTHORIZED"
    elif response.status_code == 403:
        code = "FORBIDDEN"
    elif response.status_code == 404:
        code = "NOT_FOUND"
    detail = response.data
    message = detail.get("detail", "error") if isinstance(detail, dict) else "error"
    details = {k: v for k, v in detail.items() if k != "detail"} if isinstance(detail, dict) else {}
    response.data = _error_payload(code, message, details)
    return response
