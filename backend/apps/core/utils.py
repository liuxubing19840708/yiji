"""通用工具：金额精度、菲票二维码内容。"""
from decimal import Decimal, ROUND_HALF_UP


def q3(value) -> Decimal:
    """金额统一保留 3 位小数（DECIMAL(10,3) 口径）。"""
    if value is None:
        return Decimal('0')
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)


def q2(value) -> Decimal:
    """金额保留 2 位小数（工资类字段口径）。"""
    if value is None:
        return Decimal('0')
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def build_ticket_qr(company_no: str, bed_no: str, style_no: str, bundle_no: str,
                    color: str, size: str, qty) -> str:
    """菲票二维码内容：companyNo|bedNo|styleNo|bundleNo|color|size|qty"""
    return '|'.join([
        company_no or '',
        bed_no or '',
        style_no or '',
        bundle_no or '',
        color or '',
        size or '',
        str(qty if qty is not None else ''),
    ])
