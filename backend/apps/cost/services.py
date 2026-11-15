"""订单成本服务：stale 埋点 / 重算 / 批量重算 / 工资对账（P1 cost 域 §3.3）。

口径红线（三路核对定稿 2026-10-24，不可改动）：
  - Record.price 是登记时点快照，禁引 StyleProcess/Process 现价（改价污染历史成本）；
  - labor_cost 取全量 Record（含已结算）——成本是历史事实不随结算时点漂移；
    whole 类不读 Record.price：完成菲票 × member.piece_rate ÷ distinct 参与人数
    （与 wage.py:66-99 同源），按完成菲票所属床次归属订单；
  - commission 差额不进任何订单、fixed 为期间费用——工资对账单列示；
  - 材料成本只取已确认领料单，qty 折算 Material 主单位后 × lot.unit_cost，
    退料（type=return，qty 正数=退回量）冲减；缺价（NULL/0）/换算缺参行不进成本；
  - 重算：transaction.atomic + all_objects.select_for_update + get_or_create
    （防并发撞 UNIQUE），聚合一律 ORM values+annotate+Sum（order_by() 前置），
    仅分组级换算允许小循环（禁逐行 Python 计算）。
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum
from django.utils import timezone

from apps.catalog.models import Ticket
from apps.core.utils import q2, q3
from apps.cost.models import OrderCost

_DEC3 = DecimalField(max_digits=14, decimal_places=3)


# ============ stale 埋点（同步、事务内调用，幂等） ============

def mark_orders_stale(company, order_ids) -> int:
    """把订单的成本行翻 is_stale（幂等：仅 is_stale=False 的行才写，
    重复埋点第二次 update 影响 0 行=零写入）；未挂单床次（order_id=None）忽略。
    """
    ids = {oid for oid in (order_ids or []) if oid}
    if not ids:
        return 0
    return (OrderCost.objects
            .filter(company=company, order_id__in=ids, is_stale=False)
            .update(is_stale=True))


def mark_issue_confirmed_stale(company, issue) -> int:
    """埋点①：MaterialIssue 确认（apps/warehouse/views.py 确认事务内调用）。

    领料/退料行床次的挂单订单全部翻 stale（一单可跨多床多订单）。
    """
    from apps.warehouse.models import IssueItem
    order_ids = IssueItem.objects.filter(issue=issue) \
        .values_list('production__order_id', flat=True)
    return mark_orders_stale(company, order_ids)


def mark_ticket_stale(company, ticket) -> int:
    """埋点②：records 登记/撤销（apps/records/views.py 事务内调用）。

    菲票所属床次的挂单订单翻 stale；未挂单床次成本不入订单（no_order_cnt 口径）。
    """
    order_id = (Ticket.objects.filter(pk=ticket.pk)
                .values_list('production__order_id', flat=True).first())
    return mark_orders_stale(company, [order_id])


# ============ 重算 ============

def _material_part(company, order):
    """材料成本 + 缺价行数。

    取数口径（SA2 交接备注，已实测）：已确认领料单；IssueItem 无 company 列
    （公司过滤走 issue__company）；qty 单位=Lot.unit、lot.unit_cost 与之配对，
    Material 主单位不同时按克重×幅宽折算（缺参/未知单位对 → 不进成本计缺价）；
    退料 type=return 行 qty 正数=退回量，冲减材料成本。
    """
    from apps.warehouse.models import IssueItem, MaterialIssue
    from apps.warehouse.services import convert_to_main_unit

    base = IssueItem.objects.filter(
        issue__company=company,
        issue__status=MaterialIssue.Status.CONFIRMED,
        issue__is_deleted=False,
        production__order=order,
        production__is_deleted=False,
    ).order_by()

    # 缺价行（lot 无价 / 0 价）：不进成本，计 missing_price_cnt（报表附下钻）
    missing = base.filter(Q(lot__unit_cost__isnull=True) | Q(lot__unit_cost=0)).count()

    # 有价行：ORM 按 (单据类型, 批次, 物料) 分组聚合（禁逐行 Python 计算）
    groups = list(base.filter(lot__unit_cost__isnull=False, lot__unit_cost__gt=0)
                  .values('issue__type', 'lot_id', 'lot__unit', 'lot__unit_cost',
                          'material_id', 'material__unit')
                  .annotate(qty=Sum('qty')))
    mats = {}
    mat_ids = {g['material_id'] for g in groups if g['material_id']}
    if mat_ids:
        from apps.tech.models import Material
        mats = {m.id: m for m in Material.objects.filter(pk__in=mat_ids)}

    total = Decimal('0')
    for g in groups:
        qty = g['qty'] or Decimal('0')
        mat = mats.get(g['material_id'])
        mat_unit = (mat.unit or '') if mat else ''
        if mat is not None and (g['lot__unit'] or '') != mat_unit:
            q_main = convert_to_main_unit(qty, g['lot__unit'], mat)
            if q_main is None:
                # 换算缺参/未知单位对：该组全部行不进成本，计缺价（口径=未能计价）
                missing += base.filter(issue__type=g['issue__type'], lot_id=g['lot_id'],
                                       material_id=g['material_id']).count()
                continue
            qty = q_main
        cost = qty * g['lot__unit_cost']
        if g['issue__type'] == MaterialIssue.Type.RETURN:
            total -= cost      # 退料冲减
        else:
            total += cost
    return total, missing


def _labor_part(company, order):
    """人工成本 + 产出件数。

    process 类：Σ Record.qty × Record.price（全量含已结算）；
    whole 类：完成菲票（distinct 工序数 ≥ 床次配置工序数，口径同 stats.py）
              × piece_rate ÷ distinct 参与人数（票面均摊额合计，不读 Record.price）；
    无单床次成本不入订单（no_order_cnt 另计）。
    """
    from apps.accounts.models import UserCompany
    from apps.production.stats import production_pc_map
    from apps.records.models import Record

    rec_qs = Record.objects.filter(
        company=company,
        ticket__is_deleted=False,
        ticket__production__is_deleted=False,
        ticket__production__order=order,
    ).order_by()

    whole_members = list(UserCompany.objects.filter(
        company=company, is_deleted=False,
        salary_type=UserCompany.SalaryType.WHOLE))
    whole_user_ids = {m.user_id for m in whole_members}

    # process 部分：whole 类成员的登记不按 qty×price 计（其工资=票面均摊，防双算）
    process_q = rec_qs
    if whole_user_ids:
        process_q = process_q.exclude(user_id__in=whole_user_ids)
    process_sum = process_q.aggregate(
        s=Sum(F('qty') * F('price'), output_field=_DEC3))['s'] or Decimal('0')

    whole_sum = Decimal('0')
    output_qty = 0
    done_map = {}
    if whole_user_ids and rec_qs.exists():
        pc_map = production_pc_map(
            list(rec_qs.values_list('ticket__production_id', flat=True).distinct()))
        # 每张菲票：distinct 工序数 / 参与人数 / 扎量（ORM 分组聚合）
        t_rows = list(rec_qs.values('ticket_id', 'ticket__production_id', 'ticket__qty')
                      .annotate(pn=Count('process_id', distinct=True),
                                users=Count('user_id', distinct=True)))
        for r in t_rows:
            pc = pc_map.get(r['ticket__production_id']) or 0
            if pc and r['pn'] >= pc:
                done_map[r['ticket_id']] = (r['ticket__qty'] or 0, max(r['users'], 1))
                output_qty += r['ticket__qty'] or 0
        # whole 类参与对（票面均摊：rate ÷ 参与人数，与 wage.py 同源）
        rate_map = {m.user_id: q2(m.piece_rate or 0) for m in whole_members}
        pairs = (rec_qs.filter(user_id__in=whole_user_ids)
                 .values_list('ticket_id', 'user_id'))
        for ticket_id, user_id in pairs:
            info = done_map.get(ticket_id)
            if not info:
                continue
            rate = rate_map.get(user_id) or Decimal('0')
            if rate > 0:
                whole_sum += rate / Decimal(str(info[1]))
    return process_sum + whole_sum, output_qty


def _no_order_cnt(company):
    """未归属床次数（公司级口径）：有成本活动（已确认领料或登记）但未挂单的床次。

    其领料与登记成本静默漏掉=毛利虚高且无警示（§3.3），不入任何订单、计数提示。
    """
    from apps.records.models import Record
    from apps.warehouse.models import IssueItem, MaterialIssue

    pids = set(IssueItem.objects.filter(
        issue__company=company,
        issue__status=MaterialIssue.Status.CONFIRMED,
        issue__is_deleted=False,
        production__order__isnull=True,
        production__is_deleted=False,
    ).values_list('production_id', flat=True))
    pids |= set(Record.objects.filter(
        company=company,
        ticket__is_deleted=False,
        ticket__production__is_deleted=False,
        ticket__production__order__isnull=True,
    ).values_list('ticket__production_id', flat=True))
    return len(pids)


def _compute_fields(company, order):
    """单订单全部成本字段（不含 overhead——保留行上已有手录值）。"""
    material_cost, missing_price_cnt = _material_part(company, order)
    labor_cost, output_qty = _labor_part(company, order)
    revenue = q2(_revenue_of(order))
    return {
        'material_cost': q2(material_cost),
        'labor_cost': q2(labor_cost),
        'output_qty': output_qty,
        'revenue': revenue,
        'no_order_cnt': _no_order_cnt(company),
        'missing_price_cnt': missing_price_cnt,
    }


def _revenue_of(order) -> Decimal:
    """理论收入 = Σ OrderItem.qty × price（未定价行不计；与实际收款差异属预期）。"""
    from apps.orders.models import OrderItem
    return (OrderItem.objects.filter(order=order, price__isnull=False)
            .order_by()
            .aggregate(s=Sum(F('qty') * F('price'), output_field=_DEC3))['s']
            or Decimal('0'))


def recompute(company, order, user=None) -> OrderCost:
    """重算单订单成本（视图层保证 order 属本公司且未软删）。

    事务 + all_objects.select_for_update + get_or_create：并发重算串行化，
    防撞 UNIQUE(company, order, is_deleted=False)；无 items 订单算全 0 行照常落表。
    """
    from apps.orders.models import Order

    with transaction.atomic():
        Order.all_objects.select_for_update().get(pk=order.pk)
        oc, _created = (OrderCost.all_objects.select_for_update()
                        .get_or_create(company=company, order=order,
                                       defaults={'status': OrderCost.Status.DRAFT}))
        data = _compute_fields(company, order)
        for k, v in data.items():
            setattr(oc, k, v)
        total = (oc.material_cost + oc.labor_cost + oc.outsourcing_cost
                 + oc.overhead_cost)
        oc.total_cost = q2(total)
        oc.unit_cost = q3(total / oc.output_qty) if oc.output_qty else None
        oc.gross_profit = q2(oc.revenue - oc.total_cost)
        oc.gross_margin = (q2(oc.gross_profit / oc.revenue * 100)
                           if oc.revenue > 0 else None)
        oc.status = OrderCost.Status.COMPUTED
        oc.computed_at = timezone.now()
        oc.computed_by = user
        oc.is_stale = False
        oc.is_deleted = False
        oc.save()
    return oc


def recompute_batch(company, orders, user=None):
    """批量重算（月底一键）：逐单隔离异常，返回 (成功单 id 清单, 失败明细)。"""
    success, failed = [], []
    for order in orders:
        try:
            recompute(company, order, user=user)
            success.append(order.id)
        except Exception as e:  # noqa: BLE001 批量语义：单单失败不拖垮整批
            failed.append({'order_id': order.id, 'error': str(e)[:200]})
    return success, failed


# ============ 工资总额对账单（§3.3 必做界面） ============

def wage_reconciliation(company, date_from, date_to):
    """Σ订单 labor_cost + whole/commission/fixed 与工资发放总额的逐项差异列表。

    口径差异属预期，逐项列来源：
      - Σ订单人工：全量含已结算、订单全周期 vs 发放=结算单快照（期间重叠）；
      - 四类工资口径走 wage.py（settlement__isnull=True 未结算部分）；
      - commission 差额不进任何订单；fixed 为期间费用不入订单成本。
    """
    from apps.accounts.models import UserCompany
    from apps.salary.models import Settlement
    from apps.salary.wage import compute_wage

    paid_total = q2(Settlement.objects.filter(
        company=company, status=Settlement.Status.SETTLED,
        period_start__lte=date_to, period_end__gte=date_from,
    ).aggregate(s=Sum('amount'))['s'] or 0)

    order_labor = q2(OrderCost.objects.filter(
        company=company, status=OrderCost.Status.COMPUTED,
        order__is_deleted=False,
    ).aggregate(s=Sum('labor_cost'))['s'] or 0)

    sums = {st: Decimal('0') for st in UserCompany.SalaryType.values}
    group_cache = {}
    members = UserCompany.objects.filter(
        company=company, is_deleted=False,
        status__in=[UserCompany.Status.ACTIVE, UserCompany.Status.RESIGNED])
    for m in members:
        r = compute_wage(company, m, date_from, date_to, group_cache)
        sums[r['salary_type']] += r['amount']
    wage_total = q2(sum(sums.values()))

    items = [
        ('order_labor', 'Σ订单人工成本',
         'cost_order_cost.labor_cost（全量 Record 含已结算，订单全周期）', order_labor),
        ('process_wage', '工序计件应发（未结算）',
         'wage.py process：Σ Record.qty×price（settlement__isnull=True）',
         q2(sums[UserCompany.SalaryType.PROCESS])),
        ('whole_wage', '整件计件应发（未结算）',
         'wage.py whole：完成菲票×piece_rate÷参与人数（settlement__isnull=True）',
         q2(sums[UserCompany.SalaryType.WHOLE])),
        ('commission_wage', '计件提成应发（未结算）',
         'wage.py commission：组Σqty×price×rate%（差额不进任何订单）',
         q2(sums[UserCompany.SalaryType.COMMISSION])),
        ('fixed_wage', '固定工资应发（期间费用）',
         'wage.py fixed：期间费用，不入订单成本',
         q2(sums[UserCompany.SalaryType.FIXED])),
    ]
    rows = [{'key': key, 'label': label, 'source': source, 'amount': str(amount),
             'diff': str(q2(amount - paid_total))}
            for key, label, source, amount in items]
    notes = [
        'Σ订单人工成本与工资发放的差异属预期：成本取全量登记（含已结算、订单全周期），'
        '发放取结算单快照（按期间）；以本表逐项核对口径差。',
        '提成差额不进任何订单成本；固定工资为期间费用不入订单成本——均在列示行中体现。',
        '理论收入（Σ订单明细 qty×price）与实际收款（折扣/尾数/坏账）差异属预期，'
        'P2 应收核销后以收款实绩复核。',
    ]
    return {'date_from': str(date_from), 'date_to': str(date_to),
            'paid_total': str(paid_total), 'wage_total': str(wage_total),
            'rows': rows, 'notes': notes}
