class FinishedGoodsError(Exception):
    """包装与成品入库业务异常。"""


class ValidationError(FinishedGoodsError):
    pass


class NotFound(FinishedGoodsError):
    """统一隐藏跨公司对象是否存在。"""


class IdempotencyError(ValidationError):
    pass


class FrozenError(ValidationError):
    pass
