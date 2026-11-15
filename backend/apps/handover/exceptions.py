"""通用交接领域异常，由 API 层映射为 400/404/409。"""


class HandoverError(Exception):
    pass


class ValidationError(HandoverError):
    pass


class NotFound(HandoverError):
    pass


class IdempotencyError(ValidationError):
    pass


class ImmutableEventError(ValidationError):
    pass
