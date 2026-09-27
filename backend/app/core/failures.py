from enum import StrEnum


class FailureType(StrEnum):
    RECOVERABLE = "recoverable"
    USER_CONFIGURATION = "user_configuration"
    PERMISSION = "permission"
    EXTERNAL_SERVICE = "external_service"
    POLICY_DENIED = "policy_denied"
    VALIDATION_FAILED = "validation_failed"
    APPROVAL_INVALIDATED = "approval_invalidated"
    FATAL = "fatal"


def is_retryable(failure: FailureType, status_code: int | None = None) -> bool:
    return failure in {FailureType.RECOVERABLE, FailureType.EXTERNAL_SERVICE} and status_code not in {401, 403}
