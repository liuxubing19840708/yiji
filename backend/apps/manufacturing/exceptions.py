"""制造数量服务的领域异常；由未来 API 层统一映射为 400/404/409。"""


class ManufacturingError(Exception):
    """业务校验失败，不暴露跨公司对象是否存在。"""


class ValidationError(ManufacturingError):
    pass


class NotFound(ManufacturingError):
    pass


class TransitionError(ValidationError):
    pass


class QuantityError(ValidationError):
    pass


class IdempotencyError(ValidationError):
    pass


class ImmutableLedgerError(ValidationError):
    pass
