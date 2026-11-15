# -*- coding: utf-8 -*-
"""单据单号统一生成器（2026-10-29 收编）。

收编前五处各写各的：订单 SO（orders/views._gen_order_no）、应收 AR
（finance/services.gen_receivable_no）、出货 SH（shipment/services.gen_shipment_no）、
报价 Q-xxx（quotation/services.next_quote_no）、仓库 CG/RK/LL/ZS
（warehouse/views._next_doc_no）。格式与既有语义逐域保留：

- 结构 = 前缀 + 日期(YYYYMMDD) + 分隔符(可空) + 当日序号（digits 位补零）；
- 扫描 all_objects（含软删行）防软删段复用混乱（订单/应收/出货/报价惯例）；
- first_free=False（默认）：当日本公司最大序号 + 1，跳号不回填；
  first_free=True：从 1 起存在性探测取第一个空号，跳号回填（仓库单据惯例）；
- 并发撞唯一约束由调用方事务内重试（候选号 seq_offset 递增）；
  需要取号串行化的域（报价）在调用方自行加 select_for_update 后再调本函数。
"""
from django.utils import timezone


def next_no(company, model, field, prefix, *, digits=3, separator='',
            first_free=False, date=None, seq_offset=0) -> str:
    """生成下一个单号（纯读计算，不落库不加锁）。"""
    day = (date or timezone.localdate()).strftime('%Y%m%d')
    base = f'{prefix}{day}'
    if first_free:
        n = 1
        while model.all_objects.filter(
                company=company, **{field: f'{base}{separator}{n:0{digits}d}'}).exists():
            n += 1
        return f'{base}{separator}{n:0{digits}d}'
    max_seq = 0
    for no in model.all_objects.filter(company=company,
                                       **{f'{field}__startswith': base}) \
            .values_list(field, flat=True):
        suffix = str(no)[len(base):].lstrip(separator)
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f'{base}{separator}{max_seq + 1 + seq_offset:0{digits}d}'
