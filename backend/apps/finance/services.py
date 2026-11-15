"""应收域服务：建应收 / 核销 / 撤销 / 作废 / 移动加权库存估值（P2 §4.3 + D4）。

口径红线：
  - 超额收款 400 硬拦（金额级）。与领料超领零拦截的口径差异：多领料是
    生产弹性（可退可补、落 over 标记即可）；多收的钱是要退的资金错误，
    静默落账会制造假收入——必须拦截（docstring 与测试双落）。
  - received_amount 只在 receivable 行级 select_for_update 事务内推进；
    status 由核销/撤销重算，禁直改。
  - 作废走软删 + audit；有活动核销须先撤销（防作废行挂着已收资金）。
  - 移动加权估值（D4 双口径第二口径）：车间用批次价（Lot.unit_cost，与缸号
    绑定），财务对账用移动加权（本报表），双口径并存不互斥；
    revenue=理论收入与收款实绩的差异以应收核销复核（§3.3）。
"""
from collections import defaultdict
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.core.utils import q2, q3
from apps.finance.models import Receivable, ReceiptLine

_NO_DEC = Decimal('0')


# ============ 单号 ============

def gen_receivable_no(company, seq_offset: int = 0) -> str:
    """应收单号：AR + 日期(YYYYMMDD) + 当日 3 位顺延。

    扫描 all_objects（含软删行）防软删段复用混乱（同订单域 _gen_order_no）；
    并发撞 UNIQUE 由 create_receivable 事务内重试（候选号 +1）。
    生成逻辑收编 apps.core.numbering（2026-10-29），本函数保留域内签名。
    """
    from apps.core.numbering import next_no
    return next_no(company, Receivable, 'receivable_no', 'AR', seq_offset=seq_offset)


# ============ 建应收（手工端点与出货域 SA-P2-4 共用入口） ============

def create_receivable(company, *, customer, amount, order=None,
                      shipment_id=None, remark='', user=None) -> Receivable:
    """建应收账行（POST /finance/receivables 与出货域自动建单共用）。

    出货域对接签名（SA-P2-4）：create_receivable(company, customer=客户实例,
    amount=Decimal, order=订单实例或None, shipment_id=出货单id或None,
    remark='…', user=操作人)。amount 须 >0；customer/order 须属本公司
    （服务内二次校验，防调用方错传跨厂实例）；shipment_id 先落整列占位。
    单号自动生成；UNIQUE 并发撞号事务内重试 3 次（候选号递增）。
    """
    amount = q2(amount)
    if amount <= 0:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'amount': '应收金额必须大于 0'})
    if customer is not None and customer.company_id != company.id:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'customer': '客户不属于本公司'})
    if order is not None and order.company_id != company.id:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'order': '订单不属于本公司'})
    if shipment_id is not None:
        from apps.shipment.models import Shipment
        from rest_framework.exceptions import ValidationError
        shipment = Shipment.objects.filter(
            company=company, pk=shipment_id, is_deleted=False).first()
        if shipment is None:
            raise ValidationError({'shipment_id': '出货单不存在或不属于本公司'})
        if order is not None and shipment.order_id != order.pk:
            raise ValidationError({'order': '订单与出货单不匹配'})
        order = order or shipment.order
    if (order is not None and customer is not None and order.customer_id
            and order.customer_id != customer.pk):
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'customer': '客户与订单客户不匹配'})
    if (shipment_id is not None and customer is not None
            and shipment.customer_id and shipment.customer_id != customer.pk):
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'customer': '客户与出货单客户不匹配'})

    from apps.ops.models import AuditLog
    from apps.ops.services import audit

    attempts = 3
    while True:                     # 撞 UNIQUE 重试（候选号递增），耗尽即上抛不静默
        try:
            with transaction.atomic():
                rec = Receivable.objects.create(
                    company=company,
                    receivable_no=gen_receivable_no(
                        company, seq_offset=3 - attempts),
                    customer=customer, order=order, shipment_id=shipment_id,
                    amount=amount, remark=remark or '',
                    status=Receivable.Status.UNPAID)
                break
        except IntegrityError:      # 并发撞号：候选号 +1 重试
            attempts -= 1
            if attempts == 0:
                raise
    audit(company, AuditLog.Type.OTHER, operator=user,
          after={'receivable_id': rec.pk, 'receivable_no': rec.receivable_no,
                 'amount': str(rec.amount), 'customer': customer.name if customer else '',
                 'order_id': order.pk if order else None,
                 'shipment_id': shipment_id},
          message=(f'应收建立：【{rec.receivable_no}】'
                   f'{customer.name if customer else "（无客户）"} '
                   f'{q2(rec.amount)} 元'
                   + (f'（出货自动）' if shipment_id else '')))
    return rec


# ============ 核销 / 撤销 / 作废 ============

def _recalc_status(rec) -> None:
    """按 received_amount 重算状态：>=amount → settled；>0 → partial；=0 → unpaid。"""
    if rec.received_amount <= 0:
        rec.status = Receivable.Status.UNPAID
    elif rec.received_amount >= rec.amount:
        rec.status = Receivable.Status.SETTLED
    else:
        rec.status = Receivable.Status.PARTIAL


def add_receipt(company, receivable, *, amount, method=ReceiptLine.Method.CASH,
                received_at=None, remark='', handler='', user=None):
    """核销（收款）：行级锁事务内推进 received_amount 并重算 status。

    超额收款 400 硬拦——与领料超领零拦截的口径差异（写死不可改）：
      - 领料：多领是生产弹性，落 over 标记正常保存（warehouse.item_over_state）；
      - 收款：多收的钱是要退的，静默落账=假收入+资金差错，必须金额级拦截。
    已结清应收再收款同样落此拦截（received==amount，任何新核销必超额）。
    返回 (receivable, receipt_line)；audit 留痕。
    """
    amount = q2(amount)
    if amount <= 0:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'amount': '核销金额必须大于 0'})

    from apps.ops.models import AuditLog
    from apps.ops.services import audit

    with transaction.atomic():
        rec = (Receivable.all_objects.select_for_update()
               .get(pk=receivable.pk, company=company, is_deleted=False))
        old_received = q2(rec.received_amount)
        old_status = rec.get_status_display()
        new_received = q2(rec.received_amount + amount)
        if new_received > rec.amount:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({
                'amount': f'超额收款：应收 {q2(rec.amount)} 元，已收 '
                          f'{q2(rec.received_amount)} 元，本笔 {q2(amount)} 元'
                          f'超出 {q2(new_received - rec.amount)} 元（多收须拦截，'
                          f'如确需调整请改应收金额）'})
        line = ReceiptLine.objects.create(
            company=company, receivable=rec, amount=amount,
            method=method if method in ReceiptLine.Method.values else ReceiptLine.Method.OTHER,
            received_at=received_at or timezone.now(),
            handler=(handler or '').strip()[:32], remark=remark or '')
        rec.received_amount = new_received
        _recalc_status(rec)
        rec.save(update_fields=['received_amount', 'status', 'updated_at'])

    audit(company, AuditLog.Type.OTHER, operator=user,
          before={'received_amount': str(old_received), 'status': old_status},
          after={'receivable_no': rec.receivable_no, 'received_amount': str(new_received),
                 'status': rec.get_status_display(), 'receipt_id': line.pk,
                 'method': line.get_method_display(), 'handler': line.handler},
          message=(f'应收核销：【{rec.receivable_no}】收款 {q2(amount)} 元'
                   f'（{line.get_method_display()}），已收 {new_received}/'
                   f'{q2(rec.amount)}，{rec.get_status_display()}'))
    return rec, line


def batch_receipt_by_customer(company, customer, *, total, method=ReceiptLine.Method.CASH,
                              received_at=None, handler='', remark='', user=None):
    """按客户批量核销（2026-11-02 评审 Top10 #5 对齐）：一笔款付多单。

    FIFO 口径：客户未结清应收（unpaid/partial，未删）按 created_at 升序逐行
    分配 min(该行剩余, 未分配款)，直到款尽或应收藏清；溢收款 400 硬拦
    （与 add_receipt 同红线——多收须退，不给「预收款」出口，P3 再议）。
    复用 add_receipt（行级锁+audit 逐行留痕），本函数只做分配与事务编排。
    返回 {'allocated': [{'receivable_id','receivable_no','amount'}...],
          'total_allocated', 'leftover'(恒 0，防御字段)}。
    """
    total = q2(total)
    if total <= 0:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'amount': '收款金额必须大于 0'})
    ars = list(Receivable.objects.filter(
        company=company, customer=customer, is_deleted=False,
        status__in=[Receivable.Status.UNPAID, Receivable.Status.PARTIAL],
    ).order_by('created_at', 'id'))
    outstanding_sum = sum((q2(r.amount) - q2(r.received_amount)) for r in ars)
    if total > outstanding_sum:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({
            'amount': f'溢收款：该客户未结清应收合计 {q2(outstanding_sum)} 元，'
                      f'本笔 {total} 元超出 {q2(total - outstanding_sum)} 元'
                      '（多收须拦截，请拆单或先调应收金额）'})
    allocated = []
    remaining = total
    with transaction.atomic():
        for rec in ars:
            if remaining <= 0:
                break
            due = q2(rec.amount) - q2(rec.received_amount)
            pay = min(due, remaining)
            if pay <= 0:
                continue
            _, line = add_receipt(company, rec, amount=pay, method=method,
                                  received_at=received_at, handler=handler,
                                  remark=remark, user=user)
            allocated.append({'receivable_id': rec.pk,
                              'receivable_no': rec.receivable_no,
                              'amount': str(q2(pay))})
            remaining = q2(remaining - pay)
    return {'allocated': allocated, 'total_allocated': str(q2(total - remaining)),
            'leftover': str(remaining)}


def revoke_last_receipt(company, receivable, user=None):
    """撤销最近一条核销：软删该流水行并反向回退 received_amount、重算 status。

    只撤最近一行（按创建序，确定性口径）；无可撤核销 400。
    返回 (receivable, revoked_line)；audit 留痕（流水行软删保留，日志可追）。
    """
    from apps.ops.models import AuditLog
    from apps.ops.services import audit

    with transaction.atomic():
        rec = (Receivable.all_objects.select_for_update()
               .get(pk=receivable.pk, company=company, is_deleted=False))
        line = (rec.receipt_lines.filter(is_deleted=False)
                .select_for_update().order_by('-id').first())
        if line is None:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'detail': '无可撤销的核销记录'})
        rec.received_amount = q2(rec.received_amount - line.amount)
        _recalc_status(rec)
        rec.save(update_fields=['received_amount', 'status', 'updated_at'])
        line.is_deleted = True
        line.save(update_fields=['is_deleted', 'updated_at'])

    audit(company, AuditLog.Type.OTHER, operator=user,
          before={'receipt_id': line.pk, 'amount': str(q2(line.amount)),
                  'received_amount': str(q2(rec.received_amount + line.amount))},
          after={'receivable_no': rec.receivable_no,
                 'received_amount': str(rec.received_amount),
                 'status': rec.get_status_display()},
          message=(f'核销撤销：【{rec.receivable_no}】退回核销 {q2(line.amount)} 元，'
                   f'已收 {rec.received_amount}/{q2(rec.amount)}，'
                   f'{rec.get_status_display()}'))
    return rec, line


def void_receivable(company, receivable, user=None) -> Receivable:
    """作废应收：软删 + audit 留痕；有活动核销须先撤销（防资金悬空）。

    作废后从列表消失（is_deleted 过滤），流水与日志保留可追。
    """
    from apps.ops.models import AuditLog
    from apps.ops.services import audit

    with transaction.atomic():
        rec = (Receivable.all_objects.select_for_update()
               .get(pk=receivable.pk, company=company, is_deleted=False))
        live_cnt = rec.receipt_lines.filter(is_deleted=False).count()
        if live_cnt:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({
                'detail': f'该应收存在 {live_cnt} 条核销记录，'
                          f'须先全部撤销后才能作废'})
        rec.is_deleted = True
        rec.save(update_fields=['is_deleted', 'updated_at'])

    audit(company, AuditLog.Type.OTHER, operator=user,
          before={'status': rec.get_status_display(),
                  'amount': str(q2(rec.amount)),
                  'received_amount': str(rec.received_amount)},
          after={'receivable_no': rec.receivable_no, 'voided': True},
          message=f'应收作废：【{rec.receivable_no}】{q2(rec.amount)} 元'
                  f'（未核销行，作废即下架，日志可追）')
    return rec


# ============ 移动加权库存估值（D4 双口径第二口径） ============

def stock_valuation(company, material_id=None, date_to=None) -> dict:
    """按物料出移动加权平均单价 + 结存金额（GET /finance/stock-valuation）。

    口径（D4，双口径并存）：
      - 车间用批次价：Lot.unit_cost 与缸号绑定（P1 库存价链）；
      - 财务对账用移动加权：本报表，基于 Lot 流水逐笔递推——
        入库（StockInItem，单据 status=done）：qty+=n、amt+=n×unit_price；
        缺价行（unit_price NULL/0）不进额不进量（同材料成本缺价口径），计 missing；
        领料出库（已确认 MaterialIssue type=issue 行）：qty-=n、amt-=n×当前加权价；
        退料回库（type=return 行，qty 正数=退回量）：按当前加权价冲减净领用
        （issued_qty=领用−退料，加权价不受退料影响——出库回库同价，额随量平移）；
        流水按 (日期, id) 时序递推，qty 归零即残差清零（amt=0 防累积浮残）；
        负结存（先领后入/净退超领的数据现实）沿用最近加权价，原样示出不做截断。
      - 物料归集：入库行 StockInItem.material（P2 暂收/手工入库带出）优先，
        其次该批次已确认领料行 IssueItem.material；两者皆空 → material=None
        组（不伪造归集）。material_id 过滤只出该物料组。
    单位：批次行按原单位；物料组聚合为各批次直加（混合单位时以批次明细为准），
    与 convert_to_main_unit 主单位换算口径互不污染（估值不做跨单位折算）。
    """
    from apps.warehouse.models import IssueItem, Lot, MaterialIssue, StockIn, StockInItem

    # 1) 流水装载：lot_id -> [(sort_key, kind, qty, amt)]
    flows = defaultdict(list)
    missing_by_lot = defaultdict(int)

    in_qs = (StockInItem.objects
             .filter(stock_in__company=company,
                     stock_in__status=StockIn.Status.DONE,
                     stock_in__is_deleted=False, is_deleted=False)
             .select_related('stock_in'))
    if date_to:
        in_qs = in_qs.filter(stock_in__in_at__date__lte=date_to)
    lot_ids = set()
    for it in in_qs:
        if not it.lot_id:
            continue                      # 无批次行不进 Lot 流水估值
        lot_ids.add(it.lot_id)
        if it.unit_price is None or it.unit_price <= 0:
            missing_by_lot[it.lot_id] += 1    # 此处 lot_id 必非空（上方已 continue）
            continue                      # 缺价行：不进额不进量
        qty = q2(it.qty)
        flows[it.lot_id].append((
            (it.stock_in.in_at, it.id), 'in', qty, q2(qty * it.unit_price)))

    issue_qs = (IssueItem.objects
                .filter(issue__company=company,
                        issue__status=MaterialIssue.Status.CONFIRMED,
                        issue__is_deleted=False, is_deleted=False,
                        lot__isnull=False)
                .select_related('issue'))
    if date_to:
        issue_qs = issue_qs.filter(issue__created_at__date__lte=date_to)
    for it in issue_qs:
        lot_ids.add(it.lot_id)
        qty = q2(it.qty)
        kind = 'out' if it.issue.type == MaterialIssue.Type.ISSUE else 'ret'
        flows[it.lot_id].append(((it.issue.created_at, it.id), kind, qty, None))

    # 2) 物料归集：入库行 material 优先，其次已确认领料行 material
    mat_map = {}
    for lot_id, mid in in_qs.filter(material__isnull=False, lot__isnull=False) \
            .order_by('id').values_list('lot_id', 'material_id'):
        mat_map.setdefault(lot_id, mid)
    for lot_id, mid in (issue_qs.filter(material__isnull=False)
                        .order_by('id')
                        .values_list('lot_id', 'material_id')):
        mat_map.setdefault(lot_id, mid)

    # 3) 逐批次递推（时序不可并行——移动加权本质是递推，此处允许小循环）
    lot_rows = {}
    for lot_id in lot_ids:
        qty = amt = Decimal('0')
        wavg = Decimal('0')
        in_qty = in_amt = issued_qty = Decimal('0')
        for _key, kind, n, n_amt in sorted(flows.get(lot_id, []), key=lambda f: f[0]):
            if kind == 'in':
                qty += n
                amt += n_amt
                in_qty += n
                in_amt += n_amt
            elif kind == 'ret':            # 退料回库：量回加、额按当前加权价回冲
                qty += n
                amt += n * wavg
                issued_qty -= n            # 净领用 = 领用 − 退料
            else:                          # 领料出库：按当前加权价出账
                qty -= n
                amt -= n * wavg
                issued_qty += n
            if qty == 0:
                amt = Decimal('0')         # 归零清残差
            wavg = (amt / qty) if qty != 0 else Decimal('0')
        if in_qty == 0 and issued_qty == 0:
            continue                       # 无有效流水批次不进报表
        lot_rows[lot_id] = {
            'lot_id': lot_id, 'in_qty': q2(in_qty), 'in_amt': q2(in_amt),
            'issued_qty': q2(issued_qty), 'balance_qty': q2(qty),
            'wavg_price': q3(wavg), 'balance_amt': q2(amt),
            'missing_price_rows': missing_by_lot.get(lot_id, 0)}

    # 4) 物料分组聚合
    lots = {l.id: l for l in Lot.all_objects.filter(pk__in=lot_rows.keys())}
    mats = {}
    mat_ids = {m for m in mat_map.values() if m}
    if mat_ids:
        from apps.tech.models import Material
        mats = {m.id: m for m in Material.all_objects.filter(pk__in=mat_ids)}

    groups = {}
    for lot_id, row in lot_rows.items():
        lot = lots.get(lot_id)
        mid = mat_map.get(lot_id)
        if material_id is not None and mid != material_id:
            continue
        g = groups.setdefault(mid, {
            'material_id': mid,
            'material_code': (mats[mid].code if mid in mats else ''),
            'material_name': (mats[mid].name if mid in mats else ''),
            'unit': (lot.unit if lot else '') or '',
            'in_qty': Decimal('0'), 'in_amt': Decimal('0'),
            'issued_qty': Decimal('0'), 'balance_qty': Decimal('0'),
            'balance_amt': Decimal('0'), 'missing_price_rows': 0,
            'lots': []})
        g['in_qty'] += row['in_qty']
        g['in_amt'] += row['in_amt']
        g['issued_qty'] += row['issued_qty']
        g['balance_qty'] += row['balance_qty']
        g['balance_amt'] += row['balance_amt']
        g['missing_price_rows'] += row['missing_price_rows']
        detail = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in row.items()}
        detail.update({
            'lot_no': lot.lot_no if lot else '',
            'fabric_name': lot.fabric_name if lot else '',
            'color': (lot.color if lot else '') or '',
            'unit': (lot.unit if lot else '') or ''})
        g['lots'].append(detail)

    rows = []
    for mid in sorted(groups, key=lambda m: (m is not None, m or 0)):
        g = groups[mid]
        # 混合单位提示：组内批次单位不一致 → unit 置空（明细以批次行为准）
        units = {lt['unit'] for lt in g['lots']}
        if len(units) > 1:
            g['unit'] = ''
        g['wavg_price'] = q3(g['balance_amt'] / g['balance_qty']) \
            if g['balance_qty'] != 0 else q3(0)
        for k in ('in_qty', 'in_amt', 'issued_qty', 'balance_qty', 'balance_amt'):
            g[k] = str(q2(g[k]))
        g['wavg_price'] = str(g['wavg_price'])
        rows.append(g)

    return {
        'date_to': str(date_to) if date_to else None,
        'note': '车间用批次价（Lot.unit_cost）、财务对账用移动加权（本表），'
                '双口径并存（D4）；缺价入库行不进额不进量；'
                '领用/退库按当前加权价递推；material_id 只出该物料组。',
        'rows': rows,
    }
