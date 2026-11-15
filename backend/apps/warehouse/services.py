"""P1 领料定额化/库存价链业务函数（docs/服装ERP全模块设计方案.md §3.2/§3.3）。

- 单位黑洞防线（A3）：IssueItem.qty 单位=Lot.unit，qty_req 单位=Material 主单位；
  两者不一致时以 Material 双单位 + 克重×幅宽换算折算后再比较，
  换算参数（weight_gsm/width_cm）缺失则 over 判定跳过 → 进「待补参数」清单。
- 超领零拦截：qty 折算主单位后 > qty_req 仅落 over 标记，正常保存（清单 C2）。
- 定额计算：qty_req = unit_usage × (1+loss_rate/100) × 床次件数，
  床次件数基数 Production.plan_qty 回落 total_qty（设计文档写死口径）。
"""
from decimal import Decimal, ROUND_HALF_UP

_KG_UNITS = {'kg', 'KG', '公斤', '千克'}
_M_UNITS = {'米', 'm', 'M'}
# 针织公斤、梭织米两条线之外的一律不折算（牛仔 oz/毛呢克重等例外 P4 再扩）


def _d(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def convert_to_main_unit(qty, from_unit: str, material):
    """把 qty（单位=from_unit，通常是 Lot.unit）折算成 Material 主单位。

    返回 Decimal；无法折算（单位对未知 / 克重×幅宽缺参）返回 None（待补参数）。
    """
    if material is None:
        return None
    fu = (from_unit or '').strip()
    tu = (material.unit or '').strip()
    if not tu or fu == tu:            # 同单位（或主单位未录）按原值
        return _d(qty)
    gsm = material.weight_gsm         # 克重 g/m²
    width = material.width_cm         # 幅宽 cm
    if fu in _KG_UNITS and tu in _M_UNITS:
        # 每米克重 = gsm × (width_cm/100)；米 = 公斤×1000 / 每米克重
        # （gsm/width 缺 0 或缺值已在上方卫语句拦下，此处必为正，无除零）
        if not gsm or not width:
            return None
        per_m_kg = _d(gsm) * _d(width) / Decimal('100000')
        return (_d(qty) / per_m_kg).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if fu in _M_UNITS and tu in _KG_UNITS:
        if not gsm or not width:
            return None
        per_m_kg = _d(gsm) * _d(width) / Decimal('100000')
        return (_d(qty) * per_m_kg).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return None                       # 未知单位对 → 待补参数


def convert_price_to_main_unit(price, from_unit: str, material):
    """把单价（用户原报单位 from_unit：元/公斤 或 元/米）规范化为按 Material 主单位。

    D19 拍板（2026-10-24）：unit_price/price 存储一律主单位规范化值；成本侧
    （Lot.unit_cost/OrderCost）语义零改动。kg↔m 用 per_m_kg = 克重×幅宽/100000
    （公斤/米）折算：元/公斤 × 公斤/米 = 元/米。缺克重/幅宽参数或单位对未知
    返回 None（接口层 400「该物料缺克重/幅宽，无法换算单价」）。
    """
    if material is None:
        return None
    fu = (from_unit or '').strip()
    tu = (material.unit or '').strip()
    if not fu or not tu or fu == tu:
        return _d(price)                 # 空=按主单位 / 同单位：零换算
    if fu in _KG_UNITS and tu in _KG_UNITS:     # 同族（公斤↔kg）：零换算（不依赖换算参数）
        return _d(price)
    if fu in _M_UNITS and tu in _M_UNITS:       # 同族（米↔m）：零换算
        return _d(price)
    gsm = material.weight_gsm
    width = material.width_cm
    if not gsm or not width:
        return None
    per_m_kg = _d(gsm) * _d(width) / Decimal('100000')
    if fu in _KG_UNITS and tu in _M_UNITS:      # 元/公斤 → 元/米
        return (_d(price) * per_m_kg).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
    if fu in _M_UNITS and tu in _KG_UNITS:      # 元/米 → 元/公斤
        return (_d(price) / per_m_kg).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)
    return None                        # 单位对未知 → 待补参数


def item_over_state(item) -> tuple:
    """领料行超额判定：(state, 主单位数量|None)。

    state ∈ normal / over / no_req（无物料或无定额）/ pending（换算缺参待补）。
    """
    mat = item.material
    if mat is None or item.qty_req is None:
        return 'no_req', None
    q = convert_to_main_unit(item.qty, item.lot.unit, mat)
    if q is None:
        return 'pending', None
    return ('over', q) if q > item.qty_req else ('normal', q)


def bed_base_qty(production) -> int:
    """床次件数基数：Production.plan_qty 回落 total_qty（可空/0 时回落）。"""
    return int(production.plan_qty or production.total_qty or 0)


def quota_qty_for(bom_item, production) -> Decimal:
    """定额数量（单位=BomItem.unit=Material 主单位）= 单耗×(1+损耗率)×床次件数。

    单耗与损耗率分列（B5），损耗后用量是计算属性不落库。
    """
    usage = _d(bom_item.unit_usage)
    loss = _d(bom_item.loss_rate or 0)
    qty = usage * (Decimal('1') + loss / Decimal('100')) * _d(bed_base_qty(production))
    return qty.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def current_req_map(productions) -> dict:
    """按当前 active BOM 重算的应发定额：{(production_id, material_id): Decimal}。

    与发料快照 Σ IssueItem.qty_req 并列展示（改版差额一目了然）。
    床次无款式/无 active BOM 时不产出键。
    """
    from apps.tech.models import Bom

    result = {}
    for p in productions:
        bom = (Bom.objects.filter(company=p.company, style=p.style, status='active')
               .prefetch_related('items').first())
        if bom is None:
            continue
        base = bed_base_qty(p)
        for bi in bom.items.all():
            loss = _d(bi.loss_rate or 0)
            qty = (_d(bi.unit_usage) * (Decimal('1') + loss / Decimal('100'))
                   * _d(base)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            key = (p.id, bi.material_id)
            result[key] = result.get(key, Decimal('0')) + qty
    return result
