"""出货域服务：单号生成 / 已出量·完工量口径 / 审核 / 作废 / 未交矩阵（P2 §4.2）。

口径红线（docstring 与测试双落）：
  - 未交量真口径：undelivered = OrderItem.qty − Σ已出量（已出 = 已审核出货单
    明细同色码合计，作废/草稿单不计）。P1 §3.3 的近似口径（登记完成件数）
    自出货域上线起只用于「完工结存」，不再用于未交量。
  - 完工结存（成品库存口径）：逐床次只取明确末道必过工序的有效 Record.qty，
    按菲票色码聚合。严禁把同一扎的多道工序报工累加成成品；多员工报工仍累计。
    末道认证规则与历史单工序过渡口径见 produced_qty_map。
  - 负库存 400 硬拦 vs 面料超领零拦截的口径差异：面料多领是生产弹性（可退可
    补、落 over 标记）；成品出货超完工量 = 仓库发不出货的物理错误，静默过账
    制造假出库与假收入，必须拦截。
  - 审核 = 事务内 select_for_update：校验未交量 → 负库存 → 缺价 → 扣账
    （已出量随 status 流转落库）→ 自动建应收（finance.create_receivable 共用
    入口，勿自造单）。
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.utils import q2
from apps.shipment.models import Shipment


# ============ 单号 ============

def gen_shipment_no(company, seq_offset: int = 0) -> str:
    """出货单号：SH + 日期(YYYYMMDD) + 当日 3 位顺延。

    扫描 all_objects（含软删行）防软删段复用混乱（同订单域/应收域惯例）；
    并发撞 UNIQUE 由审核外层/建单视图事务内重试。
    生成逻辑收编 apps.core.numbering（2026-10-29），本函数保留域内签名。
    """
    from apps.core.numbering import next_no
    return next_no(company, Shipment, 'shipment_no', 'SH', seq_offset=seq_offset)


# ============ 口径聚合（已出量 / 完工结存） ============

def shipped_qty_map(company, order, exclude_pk=None) -> dict:
    """该订单已出量 {(color, size): qty}。

    只计已审核（status != draft）且未作废（is_deleted=False）出货单的明细；
    作废单回到未交池（撤单即恢复未交量，与「不自动撤应收」的口径分工一致）。
    """
    from apps.shipment.models import ShipmentItem
    rows = (ShipmentItem.objects
            .filter(shipment__company=company,
                    shipment__order=order,
                    shipment__is_deleted=False)
            .exclude(shipment__status=Shipment.Status.DRAFT)
            .exclude(shipment__pk=exclude_pk)
            .order_by()  # order_by() 前置：防 Meta.ordering 混入 GROUP BY
            .values('color', 'size')
            .annotate(qty=Sum('qty')))
    return {(r['color'], r['size']): int(r['qty'] or 0) for r in rows}


def _finished_process_id(production):
    """返回床次可认证的成品工序；无法认证时抛出带原因的 ValueError。

    床次工序集沿用扫菲规则：ProductionProcess 非空则覆盖 StyleProcess，否则
    回退 StyleProcess。多工序必须全部有款式顺序，且 sort 最大者唯一并明确
    allow_skip=False。不会用报工时间、Record 主键或 ProductionProcess 主键猜末道。

    过渡兼容只有两种不存在多工序重复量纲的情形：
      1. 床次有效配置仅一道工序（即使旧数据 allow_skip=True）；
      2. 完全未配置工序，且有效菲票的有效报工实际只涉及一道工序。
    第二种是历史无配置单工序床次的临时出口；一旦报过多道工序即拒绝出货，
    必须补配末道必过工序。空床次不猜工序，完工量自然为 0。
    """
    from apps.catalog.models import StyleProcess
    from apps.production.models import ProductionProcess
    from apps.records.models import Record

    order_process_ids = list(ProductionProcess.objects.filter(
        production=production).values_list('process_id', flat=True))
    style_processes = list(StyleProcess.objects.filter(style=production.style))
    configured_ids = (order_process_ids if order_process_ids
                      else [sp.process_id for sp in style_processes])

    if len(configured_ids) == 1:
        return configured_ids[0]
    if not configured_ids:
        record_process_ids = list(Record.objects.filter(
            company_id=production.company_id,
            ticket__production=production,
            ticket__is_deleted=False,
        ).order_by().values_list('process_id', flat=True).distinct())
        if len(record_process_ids) == 1:
            return record_process_ids[0]
        if not record_process_ids:
            return None
        raise ValueError('未配置工序且已有多道工序报工')

    style_by_id = {sp.process_id: sp for sp in style_processes}
    if any(pid not in style_by_id for pid in configured_ids):
        raise ValueError('床次有效工序未全部关联款式工序，无法确认统一顺序')
    configured = [style_by_id[pid] for pid in configured_ids]
    last_sort = max(sp.sort for sp in configured)
    last = [sp for sp in configured if sp.sort == last_sort]
    if len(last) != 1:
        raise ValueError(f'末道 sort={last_sort} 存在并列工序')
    if last[0].allow_skip:
        raise ValueError(f'末道工序【{last[0].process.name}】未设置禁止跳序')
    return last[0].process_id


def _uses_finished_ledger(company, order):
    """新流程订单只认成品流水；完全无路线实例的历史订单才使用 Record 过渡口径。"""
    from apps.manufacturing.models import ProductionRoute
    return ProductionRoute.objects.filter(
        company=company, is_template=False).filter(
            Q(order=order) | Q(production__order=order)).exists()


def _finished_stock_rows(company, order):
    from apps.finished_goods.services import get_inventory
    return get_inventory(company, order_id=order.pk, include_zero=True)


def _finished_available_map(company, order):
    available = {}
    for row in _finished_stock_rows(company, order):
        key = (row['color'], row['size'])
        available[key] = available.get(key, 0) + row['available']
    return available


def produced_qty_map(company, order) -> dict:
    """该订单可出货量 {(color, size): qty}，只累计末道必过工序登记。

    同一床次逐扎、同扎跨员工的末道 Record 均求和，但其他工序一律不进入
    成品库存。无明确末道的多工序床次会抛出 ValidationError，列出床次及原因，
    避免静默少算或重复计算；修复方式是把有效工序集中 sort 最大且唯一的
    StyleProcess 设置为 allow_skip=False。历史单工序例外见
    _finished_process_id，不要求修改全局 settings 或伪造报工迁移。
    """
    from apps.production.models import Production
    from apps.records.models import Record
    from rest_framework.exceptions import ValidationError

    if _uses_finished_ledger(company, order):
        # available + transferred 是累计净入库；已出量仍由出货单单独扣减，避免双重扣减。
        stocked = {}
        for row in _finished_stock_rows(company, order):
            key = (row['color'], row['size'])
            stocked[key] = stocked.get(key, 0) + row['available'] + row['transferred']
        return stocked

    productions = list(Production.objects.filter(
        company=company, order=order).select_related('style'))
    process_by_production = {}
    problems = []
    for production in productions:
        try:
            process_id = _finished_process_id(production)
        except ValueError as exc:
            problems.append(f'床次【{production.bed_no}】{exc}')
            continue
        if process_id is not None:
            process_by_production[production.pk] = process_id
    if problems:
        raise ValidationError({'items': (
            '无法确定成品末道必过工序，未按最近报工猜测：'
            + '；'.join(problems)
            + '。请将床次有效工序集中排序最高且唯一的款式工序设为禁止跳序'
              '（allow_skip=false）后重试')})
    if not process_by_production:
        return {}

    selected = Q()
    for production_id, process_id in process_by_production.items():
        selected |= Q(ticket__production_id=production_id, process_id=process_id)
    rows = (Record.objects.filter(
                selected,
                company=company,
                ticket__is_deleted=False,
                ticket__production__is_deleted=False,
                ticket__production__order=order)
            .order_by()  # order_by() 前置：防 Meta.ordering 混入 GROUP BY
            .values('ticket__color', 'ticket__size')
            .annotate(qty=Sum('qty')))
    return {(r['ticket__color'], r['ticket__size']): int(r['qty'] or 0)
            for r in rows}


def undelivered_matrix(company, order) -> dict:
    """订单未交量矩阵：色码 × 尺码 qty − 已出量（真口径）。

    响应结构（GET /shipments/{id}/undelivered）：
      {'order_id', 'order_no', 'items': [{'color', 'size', 'qty',
        'shipped_qty', 'undelivered_qty'}],
       'total_qty', 'total_shipped', 'total_undelivered'}
    """
    shipped = shipped_qty_map(company, order)
    items, total_qty = [], 0
    total_shipped = 0
    for oi in order.items.all():
        s = shipped.get((oi.color, oi.size), 0)
        items.append({'order_item_id': oi.id, 'color': oi.color, 'size': oi.size,
                      'qty': oi.qty, 'shipped_qty': s,
                      'undelivered_qty': max(oi.qty - s, 0)})
        total_qty += oi.qty
        total_shipped += s
    return {
        'order_id': order.id, 'order_no': order.order_no,
        'items': items,
        'total_qty': total_qty, 'total_shipped': total_shipped,
        'total_undelivered': max(total_qty - total_shipped, 0),
        'note': '未交量真口径：OrderItem.qty − Σ已审核出货明细（作废单不计）；'
                '部分出货不自动拆单，剩余量即未交',
    }


# ============ 审核 ============

def _validate_shipment_boxes(company, order, shipment, within, *, finished_ledger):
    """装箱行与出货色码总量对账；新流程箱号还必须来自本订单已审核包装箱。"""
    from apps.shipment.models import PackingBox
    boxes = list(PackingBox.objects.filter(shipment=shipment))
    if not boxes:
        if finished_ledger:
            raise ValidationError({'boxes': '新流程出货必须登记实际装箱行'})
        return
    boxed = {}
    for box in boxes:
        key = (box.color, box.size)
        boxed[key] = boxed.get(key, 0) + box.qty
    if boxed != within:
        raise ValidationError({'boxes': f'装箱数量与出货明细不一致：装箱 {boxed}，出货 {within}'})
    if not finished_ledger:
        return
    from apps.finished_goods.models import PackingBox as FinishedBox, DocumentStatus
    box_nos = {box.box_no for box in boxes}
    valid_nos = set(FinishedBox.objects.filter(
        company=company, box_no__in=box_nos, task__status=DocumentStatus.APPROVED).filter(
            Q(task__route__order=order) | Q(task__route__production__order=order)
        ).values_list('box_no', flat=True))
    missing = box_nos - valid_nos
    if missing:
        raise ValidationError({'boxes': f'箱号不属于本订单已审核包装箱：{"、".join(sorted(missing))}'})

    from apps.finished_goods.models import PackingBoxLine
    packed = {}
    for line in PackingBoxLine.objects.filter(
            company=company, box__box_no__in=box_nos,
            box__task__status=DocumentStatus.APPROVED).filter(
                Q(box__task__route__order=order) | Q(box__task__route__production__order=order)):
        key = (line.box.box_no, line.color, line.size)
        packed[key] = packed.get(key, 0) + line.quantity
    previous = {}
    historical = PackingBox.objects.filter(
        shipment__company=company, shipment__order=order, shipment__is_deleted=False,
        box_no__in=box_nos).exclude(shipment__status=Shipment.Status.DRAFT)
    for box in historical:
        key = (box.box_no, box.color, box.size)
        previous[key] = previous.get(key, 0) + box.qty
    for box in boxes:
        key = (box.box_no, box.color, box.size)
        used = previous.get(key, 0) + box.qty
        if used > packed.get(key, 0):
            raise ValidationError({'boxes': (
                f'箱 {box.box_no} 的 {box.color}/{box.size} 累计出货 {used} 件，'
                f'超过已包装数量 {packed.get(key, 0)} 件')})


def _consume_finished_stock(company, shipment, within, user):
    """按路线、菲票确定性地扣减现货；任一票不足则整个出货审核回滚。"""
    from apps.finished_goods.services import release_finished_stock
    from apps.manufacturing.exceptions import ValidationError as ManufacturingValidationError
    from apps.manufacturing.models import RouteNode
    from apps.catalog.models import Ticket

    rows = sorted((row for row in _finished_stock_rows(company, shipment.order)
                   if row['available'] > 0), key=lambda row: (row['route_id'], row['ticket_id']))
    for key, quantity in within.items():
        remaining = quantity
        for row in rows:
            if remaining == 0:
                break
            if (row['color'], row['size']) != key or row['available'] <= 0:
                continue
            consumed = min(remaining, row['available'])
            try:
                release_finished_stock(
                    company, RouteNode.objects.get(pk=row['node_id']),
                    Ticket.objects.get(pk=row['ticket_id']), consumed, operator=user,
                    request_id=f'shipment:{shipment.pk}:ticket:{row["ticket_id"]}',
                    source=f'shipment:{shipment.pk}')
            except ManufacturingValidationError as exc:
                raise ValidationError({'items': f'成品库存扣减失败：{exc}'}) from None
            row['available'] -= consumed
            remaining -= consumed
        if remaining:
            raise ValidationError({'items': f'{key[0]}/{key[1]} 成品现货不足，尚缺 {remaining} 件'})


def audit_shipment(company, shipment, user):
    """审核出货单（事务内 select_for_update）：三道校验 → 扣账 → 自动建应收。

    校验顺序（400 硬拦，messages 可测）：
      1. 明细非空、色码须在订单内；
      2. 未交量：Σ已出 + 本次 ≤ OrderItem.qty（真口径）；
      3. 成品负库存：完工结存 − Σ已出 − 本次 < 0 → 400（口径差异见模块 docstring）；
      4. 缺价：命中行 OrderItem.price 为 null → 400 提示补价
         （衔接 P1「缺价显示—并提示补价」，补价入口=订单编辑）。
    通过后：
      - amount = Σ qty × OrderItem.price（q2）；
      - 自动建应收 create_receivable（共用入口；撞号由服务内重试）；
      - status 自动判定：该订单全部色码未交量清零 → confirmed，否则 partial；
      - total_qty/boxes/audited_by/audited_at 落库；ops audit 留痕。
    返回 (shipment, receivable)。
    """
    from rest_framework.exceptions import ValidationError

    from apps.ops.models import AuditLog
    from apps.ops.services import audit

    with transaction.atomic():
        # 只锁出货主表。customer 可空，select_related 会生成 LEFT OUTER JOIN；
        # PostgreSQL 禁止 FOR UPDATE 锁外连接的可空侧，SQLite 则不会暴露该错误。
        shp = Shipment.objects.select_for_update().get(pk=shipment.pk, company=company)
        if shp.status != Shipment.Status.DRAFT:
            raise ValidationError({'detail': '该出货单已审核，不能重复审核'})
        rows = list(shp.items.all())
        if not rows:
            raise ValidationError({'items': '出货明细为空，不能审核'})
        order = shp.order
        if order is None or order.is_deleted:
            raise ValidationError({'order': '出货单未挂有效订单'})
        # 同一订单的不同出货单也必须串行审核。只锁各出货单会让两个事务
        # 同时读到相同已出量，双双通过后形成超发和重复应收。
        from apps.orders.models import Order
        order = Order.objects.select_for_update().get(pk=order.pk, company=company)
        if order.is_deleted:
            raise ValidationError({'order': '出货单未挂有效订单'})

        oi_map = {(oi.color, oi.size): oi for oi in order.items.all()}
        shipped = shipped_qty_map(company, order)
        produced = produced_qty_map(company, order)
        finished_ledger = _uses_finished_ledger(company, order)
        available = _finished_available_map(company, order) if finished_ledger else {}

        # 同单内同色码聚合（创建侧已拦重复，此处兜底求和不静默丢行）
        within = {}
        for r in rows:
            within[(r.color, r.size)] = within.get((r.color, r.size), 0) + r.qty

        missing_keys, missing_price = [], []
        for key, this in within.items():
            oi = oi_map.get(key)
            if oi is None:
                missing_keys.append(f'{key[0]}/{key[1]}')
                continue
            already = shipped.get(key, 0)
            # ① 未交量（真口径）
            if already + this > oi.qty:
                raise ValidationError({'items': (
                    f'超出未交量：{key[0]}/{key[1]} 订单 {oi.qty} 件，'
                    f'已出 {already} 件，本单 {this} 件，超出 '
                    f'{already + this - oi.qty} 件')})
            # ② 新流程直接检查现货；历史路线才使用累计完工量减已出量的过渡口径。
            produced_now = produced.get(key, 0)
            insufficient = (available.get(key, 0) < this if finished_ledger
                            else produced_now - already - this < 0)
            if insufficient:
                if finished_ledger:
                    raise ValidationError({'items': (
                        f'成品库存不足：{key[0]}/{key[1]} 现货 {available.get(key, 0)} 件，'
                        f'本单 {this} 件，请先完成成品入库')})
                raise ValidationError({'items': (
                    f'成品库存不足：{key[0]}/{key[1]} 完工 {produced_now} 件，'
                    f'已出 {already} 件，本单 {this} 件（成品出货不可负库存，'
                    f'请先完成扫菲登记）')})
            # ③ 缺价（null=未定价；0 为合法赠价，金额为 0 时由 create_receivable 拦）
            if oi.price is None:
                missing_price.append(f'{key[0]}/{key[1]}')
        if missing_keys:
            raise ValidationError({'items':
                f'明细色码不在订单 {order.order_no} 中：{("、".join(missing_keys))}'})
        if missing_price:
            raise ValidationError({'items':
                f'以下色码未定价，请先在订单中补价：{("、".join(missing_price))}'})

        amount = q2(sum((Decimal(within[k]) * oi_map[k].price for k in within),
                        Decimal('0')))

        _validate_shipment_boxes(company, order, shp, within, finished_ledger=finished_ledger)
        if finished_ledger:
            _consume_finished_stock(company, shp, within, user)

        from apps.finance.services import create_receivable
        receivable = create_receivable(
            company, customer=shp.customer, amount=amount, order=order,
            shipment_id=shp.pk,
            remark=f'出货自动应收（{shp.shipment_no}）', user=user)

        # status 自动判定：该订单全部色码未交量清零 → confirmed，否则 partial
        new_shipped = dict(shipped)
        for k, q in within.items():
            new_shipped[k] = new_shipped.get(k, 0) + q
        fully = all(new_shipped.get((oi.color, oi.size), 0) >= oi.qty
                    for oi in order.items.all())
        shp.status = Shipment.Status.CONFIRMED if fully else Shipment.Status.PARTIAL
        shp.total_qty = sum(r.qty for r in rows)
        shp.boxes = shp.packing_boxes.count()
        shp.audited_by = user
        shp.audited_at = timezone.now()
        shp.save(update_fields=['status', 'total_qty', 'boxes', 'audited_by',
                                'audited_at', 'updated_at'])

    audit(company, AuditLog.Type.OTHER, operator=user,
          after={'shipment_no': shp.shipment_no, 'status': shp.status,
                 'total_qty': shp.total_qty, 'amount': str(amount),
                 'receivable_no': receivable.receivable_no},
          message=(f'出货审核：【{shp.shipment_no}】{order.order_no} '
                   f'{shp.total_qty} 件 {amount} 元 → {shp.get_status_display()}'
                   f'（应收 {receivable.receivable_no} 自动生成）'))
    return shp, receivable


# ============ 作废 ============

def void_shipment(company, shipment, user):
    """作废出货单：软删 + audit 留痕；已生成的应收不自动撤（撤单不自动撤应收）。

    响应携带 warning 字段（联动提示）+ 挂在该单上的应收清单（含已收金额），
    资金处理（作废/冲抵应收）由人工到应收模块办理。
    """
    from rest_framework.exceptions import ValidationError

    from apps.ops.models import AuditLog
    from apps.ops.services import audit

    with transaction.atomic():
        shp = (Shipment.all_objects.select_for_update()
               .select_related('order')
               .get(pk=shipment.pk, company=company))
        if shp.is_deleted:
            raise ValidationError({'detail': '该出货单已作废'})
        from apps.finance.models import Receivable
        receivables = list(Receivable.objects.filter(
            company=company, shipment_id=shp.pk))
        from apps.manufacturing.exceptions import ValidationError as ManufacturingValidationError
        from apps.manufacturing.models import QuantityLedger
        from apps.manufacturing.services import reverse_quantity
        outbound_entries = list(QuantityLedger.objects.filter(
            company=company, source=f'shipment:{shp.pk}',
            event_type=QuantityLedger.Event.TRANSFERRED).order_by('-id'))
        for entry in outbound_entries:
            try:
                reverse_quantity(company, entry, operator=user,
                                 request_id=f'shipment-void:{shp.pk}:{entry.pk}',
                                 reason=f'出货单 {shp.shipment_no} 作废回补成品库存')
            except ManufacturingValidationError as exc:
                raise ValidationError({'detail': f'出货作废回补库存失败：{exc}'}) from None
        shp.is_deleted = True
        shp.save(update_fields=['is_deleted', 'updated_at'])

    warning = ''
    if receivables:
        received = [r for r in receivables if r.received_amount > 0]
        warning = ('撤单不自动撤应收：该出货单挂有应收 '
                   + '、'.join(r.receivable_no for r in receivables)
                   + '，请到应收模块处理'
                   + ('（其中 '
                      + '、'.join(f'{r.receivable_no} 已收 {q2(r.received_amount)} 元'
                                  for r in received)
                      + '，须先撤销核销）' if received else '（均未收款）'))
    else:
        warning = '该出货单未生成应收（未审核或金额为 0），作废无资金联动'
    audit(company, AuditLog.Type.OTHER, operator=user,
          before={'shipment_no': shp.shipment_no, 'status': shp.status,
                  'total_qty': shp.total_qty},
          after={'voided': True,
                 'receivables': [r.receivable_no for r in receivables]},
          message=(f'出货作废：【{shp.shipment_no}】'
                   + (f'（挂应收 {len(receivables)} 笔，未自动撤销）'
                      if receivables else '')))
    return shp, receivables, warning
