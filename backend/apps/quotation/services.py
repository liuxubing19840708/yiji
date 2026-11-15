"""报价域服务：算定口径 / 自动号 / 小单溢价规则 / 回填对比（P2 报价域 §4.8）。

算定口径（本文件为唯一口径源，serializer/view 不得散抄）：
  material_total = Σ QuoteMaterial(quote_unit_usage × price) × Quotation.qty
  labor_total    = labor_base × rate × (1 + small_lot_addon/100) + addon_amount
  total_price    = material_total + labor_total
  cost_assumed   = material_total + labor_base + addon_amount
                   （工价实际人工=base 不加倍率；附加费按过手成本处理，保守口径。
                    小单溢价与倍率溢价视为利润空间，不计入成本假设）
  profit_rate    = (total_price − cost_assumed) / total_price × 100；total=0 → null

回填对比软提醒语义（D3 拍板，backfill_compare 顶端注释同步）：
  本端点纯只读，不做任何拦截；完成/出货后的提醒属前端展示职责。
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.utils import q2, q3
from apps.quotation.models import QuoteLabor, QuoteMaterial, Quotation

# D2 拍板：倍率可配区间 1.8-3.0 默认 2.3（超界 warning 不拦截）
RATE_MIN = Decimal('1.8')
RATE_MAX = Decimal('3.0')
RATE_DEFAULT = Decimal('2.3')
# 小单溢价规则：qty < 300 默认 +30%，可改
SMALL_LOT_THRESHOLD = 300
SMALL_LOT_ADDON_PCT = Decimal('30')

_ONE = Decimal('1')
_HUNDRED = Decimal('100')


def snapshot_labor_base(style) -> Decimal:
    """款式工序工价快照合计（Σ 未删除 StyleProcess.price）。"""
    return q3(sum((sp.price for sp in style.processes.filter(is_deleted=False)),
                  start=Decimal('0')))


def auto_small_lot_addon(qty: int) -> Decimal:
    """小单溢价默认值：qty < 300 → 30%，否则 0（规则可在入参覆盖）。"""
    return SMALL_LOT_ADDON_PCT if qty < SMALL_LOT_THRESHOLD else Decimal('0')


def build_addon_rule(auto_applied: bool, threshold=SMALL_LOT_THRESHOLD,
                     addon_pct=int(SMALL_LOT_ADDON_PCT)) -> dict:
    return {'threshold': threshold, 'addon_pct': addon_pct,
            'auto_applied': bool(auto_applied)}


def next_quote_no(company, quote_date) -> str:
    """报价单自动号：Q+YYYYMMDD-3位流水（同公司同日顺延）。

    all_objects 扫描含软删段（软删后流水不复用，防审计口径混乱）；
    事务内首行加锁串行化并发取号。
    生成逻辑收编 apps.core.numbering（2026-10-29），本函数保留锁行语义。
    """
    from apps.core.numbering import next_no
    with transaction.atomic():
        (Quotation.all_objects.select_for_update()
         .filter(company=company, quote_no__startswith=f'Q{quote_date:%Y%m%d}')
         .first())
        return next_no(company, Quotation, 'quote_no', 'Q',
                       separator='-', date=quote_date)


def compute_totals(quotation, labor_base, rate, small_lot_addon, addon_amount,
                   rate_warning):
    """写算定字段：labor_total / material_total / total_price / profit_rate。

    返回 cost_assumed（回填对比的「假设成本」口径，见模块 docstring）。
    """
    material_total = q2(sum(
        (m.quote_unit_usage * m.price for m in quotation.materials.all()),
        start=Decimal('0')) * quotation.qty)
    labor_total = q2(q2(labor_base * rate * (_ONE + small_lot_addon / _HUNDRED))
                     + addon_amount)
    total_price = q2(material_total + labor_total)
    cost_assumed = q2(material_total + q2(labor_base) + addon_amount)
    profit_rate = (q2((total_price - cost_assumed) / total_price * _HUNDRED)
                   if total_price > 0 else None)
    labor = quotation.labor
    labor.labor_total = labor_total
    labor.rate_warning = rate_warning
    labor.save(update_fields=['labor_total', 'rate_warning', 'updated_at'])
    quotation.material_total = material_total
    quotation.total_price = total_price
    quotation.profit_rate = profit_rate
    quotation.save(update_fields=['material_total', 'total_price', 'profit_rate',
                                  'updated_at'])
    return cost_assumed


@transaction.atomic
def save_children(quotation, materials_data, labor_data):
    """创建/整体替换报价子表（用料行 + 人工行），并重算全部算定字段。

    materials_data: [{'material': Material, 'bom_item': BomItem|None,
                      'quote_unit_usage': Decimal, 'price': Decimal}]
    labor_data: {'labor_base': Decimal, 'rate': Decimal,
                 'small_lot_addon': Decimal, 'addon_rule': dict,
                 'addon_amount': Decimal, 'rate_warning': bool}
    更新语义：旧用料行物理删除后 bulk_create 全量重建（BOM 明细替换同款惯例，
    tech/views.py BomItem 全量替换；行无外部引用，无 PROTECT 风险）。
    """
    QuoteMaterial.objects.filter(quotation=quotation).delete()
    QuoteMaterial.objects.bulk_create(
        QuoteMaterial(quotation=quotation, **row) for row in materials_data)
    # 人工行 OneToOne：更新语义直接覆盖（不软删重建，保行 id 稳定）
    labor, _created = QuoteLabor.objects.get_or_create(quotation=quotation)
    for k, v in labor_data.items():
        setattr(labor, k, v)
    labor.save()
    return compute_totals(quotation, labor.labor_base, labor.rate,
                          labor.small_lot_addon, labor.addon_amount,
                          labor.rate_warning)


def _diff(assumed: Decimal, actual: Decimal) -> dict:
    """逐项差额 + 差异率（assumed=0 时差异率为 null，防除零）。"""
    diff = q2(actual - assumed)
    rate = (q2(diff / assumed * _HUNDRED) if assumed != 0 else None)
    return {'assumed': str(assumed), 'actual': str(actual),
            'diff': str(diff), 'diff_rate': None if rate is None else str(rate)}


def backfill_compare(quotation) -> dict:
    """GET /{id}/backfill-compare：报价假设 vs OrderCost 实际并排（D3 回填对比）。

    软提醒语义：本端点纯只读，不做任何拦截，不修改任何数据；完成/出货当周的
    提醒入口属前端展示职责（won 单列表角标/详情条幅等），后端只供数据。
      - 未关联订单：actual/diff 为 null，message 提示 won 后关联订单；
      - 关联订单但无 OrderCost 行：message 提示「先重算成本」；
      - 有行但 is_stale：message 追加软提醒（建议重算后复核，不拦截）。
    实际侧口径即 apps/cost.OrderCost（材料/人工/总成本/理论收入），逐项：
      diff = 实际 − 假设；diff_rate = diff/假设×100（假设 0 时为 null）。
    """
    assumed_labor = quotation.labor
    material_total = q2(sum(
        (m.quote_unit_usage * m.price for m in quotation.materials.all()),
        start=Decimal('0')) * quotation.qty)
    cost_assumed = q2(material_total + q2(assumed_labor.labor_base)
                      + assumed_labor.addon_amount)

    order = quotation.order
    data = {
        'quotation': {'id': quotation.id, 'quote_no': quotation.quote_no,
                      'status': quotation.status, 'qty': quotation.qty,
                      'quote_date': str(quotation.quote_date),
                      'total_price': str(quotation.total_price),
                      'profit_rate': (None if quotation.profit_rate is None
                                      else str(quotation.profit_rate))},
        'order': None,
        'assumed': {
            'material': str(material_total),
            'labor_base': str(assumed_labor.labor_base),
            'rate': str(assumed_labor.rate),
            'small_lot_addon': str(assumed_labor.small_lot_addon),
            'addon_amount': str(assumed_labor.addon_amount),
            'labor_total': str(assumed_labor.labor_total),
            'cost_assumed': str(cost_assumed),
            'total_price': str(quotation.total_price),
        },
        'actual': None,
        'diff': None,
        'message': '',
    }

    if order is None:
        data['message'] = ('报价未关联订单：won 后关联订单即可回填对比'
                           '（软提醒，不拦截任何操作）')
        return data

    data['order'] = {'id': order.id, 'order_no': order.order_no,
                     'status': order.status}
    from apps.cost.models import OrderCost
    oc = OrderCost.objects.filter(company=quotation.company, order=order).first()
    if oc is None:
        data['message'] = '订单尚未重算成本，请先重算成本后查看回填对比' \
                          '（POST /cost/orders/{order_id}/recompute，软提醒不拦截）'
        return data

    actual = {
        'material_cost': str(oc.material_cost),
        'labor_cost': str(oc.labor_cost),
        'outsourcing_cost': str(oc.outsourcing_cost),
        'overhead_cost': str(oc.overhead_cost),
        'total_cost': str(oc.total_cost),
        'revenue': str(oc.revenue),
        'status': oc.status,
        'computed_at': (oc.computed_at.strftime('%Y-%m-%d %H:%M:%S')
                        if oc.computed_at else None),
        'is_stale': oc.is_stale,
    }
    data['actual'] = actual
    data['diff'] = {
        'material': _diff(material_total, oc.material_cost),
        'labor': _diff(q2(assumed_labor.labor_base), oc.labor_cost),
        'total': _diff(cost_assumed, oc.total_cost),
        'revenue': _diff(quotation.total_price, oc.revenue),
    }
    if oc.is_stale:
        data['message'] = '订单成本数据已过期（上游单据变动），建议重算后复核' \
                          '（软提醒，不拦截任何操作）'
    return data
