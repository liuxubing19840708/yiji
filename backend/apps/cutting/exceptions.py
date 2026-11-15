"""裁片交接领域异常。"""

from apps.manufacturing import exceptions as manufacturing


class CuttingError(manufacturing.ManufacturingError):
    pass


class ValidationError(manufacturing.ValidationError, CuttingError):
    pass


class NotFound(manufacturing.NotFound, CuttingError):
    pass


class QuantityError(manufacturing.QuantityError, CuttingError):
    pass


class IdempotencyError(manufacturing.IdempotencyError, CuttingError):
    pass
