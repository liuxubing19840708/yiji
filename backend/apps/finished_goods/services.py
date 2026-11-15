"""包装/入库领域服务；库存写入只经 manufacturing 公开服务。"""
from collections import defaultdict
from hashlib import sha256
import json
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum

from apps.catalog.models import Ticket
from apps.manufacturing.models import ProductionRoute, ProductionStage, QuantityLedger, RouteNode
from apps.manufacturing.services import (complete_stage, get_balance, get_route_for_ticket,
                                         post_quantity, reverse_quantity, transfer_stage)
from .exceptions import IdempotencyError, NotFound, ValidationError
from .models import (DocumentStatus, FinishedInbound, FinishedInboundLine, PackingBox,
                     PackingBoxLine, PackingTask, mark_approved, mark_reversed)


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError('数量必须是正整数')
    return value


def _request_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValidationError('request_id 须为 1~64 字符的非空字符串')
    return value


def _fingerprint(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return sha256(encoded).hexdigest()


def _id(value):
    if isinstance(value, bool):
        raise ValidationError('ID 非法')
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError('ID 非法') from None


def _route_context(company, route_id, *, lock=False):
    routes = ProductionRoute.objects.select_for_update() if lock else ProductionRoute.objects
    try:
        route = routes.get(pk=_id(route_id), company=company, is_template=False)
    except ProductionRoute.DoesNotExist:
        raise NotFound('生产路线不存在') from None
    nodes = list(RouteNode.objects.filter(company=company, route=route,
                                          stage__stage_type__in=[
                                              ProductionStage.StageType.PACKING,
                                              ProductionStage.StageType.FINISHED_INBOUND,
                                          ]).select_related('stage').order_by('sequence', 'id'))
    packing = [n for n in nodes if n.stage.stage_type == ProductionStage.StageType.PACKING]
    inbound = [n for n in nodes if n.stage.stage_type == ProductionStage.StageType.FINISHED_INBOUND]
    if len(packing) != 1 or len(inbound) != 1:
        raise ValidationError('路线必须且只能有一个 packing 节点和一个 finished_inbound 节点')
    if inbound[0].sequence <= packing[0].sequence:
        raise ValidationError('finished_inbound 节点必须位于 packing 节点之后')
    return route, packing[0], inbound[0]


def _ticket(company, ticket_id, route):
    try:
        ticket = Ticket.objects.select_related('production').get(pk=_id(ticket_id), company=company)
    except Ticket.DoesNotExist:
        raise NotFound('菲票不存在') from None
    actual_route = get_route_for_ticket(company, ticket)
    if actual_route.pk != route.pk:
        raise ValidationError('菲票不属于当前生产路线')
    return ticket


def _normalize_boxes(boxes):
    if not isinstance(boxes, list) or not boxes:
        raise ValidationError('boxes 至少需要一个包装箱')
    normalized = []
    seen_boxes = set()
    for raw in boxes:
        if not isinstance(raw, dict):
            raise ValidationError('包装箱格式非法')
        box_no = raw.get('box_no', '')
        if not isinstance(box_no, str) or not box_no.strip() or len(box_no.strip()) > 64:
            raise ValidationError('箱号须为 1~64 字符')
        box_no = box_no.strip()
        if box_no in seen_boxes:
            raise ValidationError(f'箱号重复：{box_no}')
        seen_boxes.add(box_no)
        raw_lines = raw.get('items', raw.get('lines'))
        if not isinstance(raw_lines, list) or not raw_lines:
            raise ValidationError(f'箱 {box_no} 缺少箱内明细')
        seen_tickets = set()
        lines = []
        for item in raw_lines:
            if not isinstance(item, dict):
                raise ValidationError('箱内明细格式非法')
            ticket_id = _id(item.get('ticket_id', item.get('ticket')))
            if ticket_id in seen_tickets:
                raise ValidationError(f'箱 {box_no} 内菲票重复')
            seen_tickets.add(ticket_id)
            lines.append({'ticket_id': ticket_id,
                          'quantity': _positive(item.get('quantity', item.get('qty')))})
        normalized.append({'box_no': box_no,
                           'items': sorted(lines, key=lambda row: row['ticket_id'])})
    return sorted(normalized, key=lambda row: row['box_no'])


def _normalize_inbound_lines(lines):
    if not isinstance(lines, list) or not lines:
        raise ValidationError('入库明细不能为空')
    normalized = []
    seen = set()
    for raw in lines:
        if not isinstance(raw, dict):
            raise ValidationError('入库明细格式非法')
        box_id = _id(raw.get('box_id', raw.get('box')))
        ticket_id = _id(raw.get('ticket_id', raw.get('ticket')))
        key = (box_id, ticket_id)
        if key in seen:
            raise ValidationError('同一箱同一菲票的入库明细重复')
        seen.add(key)
        normalized.append({'box_id': box_id, 'ticket_id': ticket_id,
                           'quantity': _positive(raw.get('quantity', raw.get('qty')))})
    return sorted(normalized, key=lambda row: (row['box_id'], row['ticket_id']))


def _packing_quantities(task):
    quantities = defaultdict(int)
    for row in PackingBoxLine.objects.filter(company=task.company, box__task=task).values(
            'ticket_id').annotate(total=Sum('quantity')):
        quantities[row['ticket_id']] += row['total']
    return dict(quantities)


def _assert_packing_available(company, node, quantities):
    for ticket_id, quantity in quantities.items():
        try:
            ticket = Ticket.objects.get(pk=ticket_id, company=company)
        except Ticket.DoesNotExist:
            raise NotFound('菲票不存在') from None
        balance = get_balance(company, node, ticket)
        if quantity > balance['qualification_remaining']:
            raise ValidationError(f'菲票 {ticket.bundle_no} 包装数量超过 packing 待合格量 '
                                  f'{balance["qualification_remaining"]}')


def create_packing_task(company, *, route_id, boxes, request_id, operator=None,
                        task_no='', remark=''):
    """建立待审核包装任务；同公司同 request_id 同载荷返回原任务。"""
    request_id = _request_id(request_id)
    boxes = _normalize_boxes(boxes)
    fingerprint = _fingerprint({'route_id': _id(route_id), 'boxes': boxes,
                                'task_no': task_no or '', 'remark': remark or '',
                                'operator_id': getattr(operator, 'pk', None)})
    existing = PackingTask.objects.filter(company=company, request_id=request_id).first()
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyError('request_id 已用于不同包装请求')
        return existing
    with transaction.atomic():
        route, packing_node, inbound_node = _route_context(company, route_id, lock=True)
        quantities = defaultdict(int)
        tickets = {}
        for box in boxes:
            for item in box['items']:
                ticket = tickets.get(item['ticket_id'])
                if ticket is None:
                    ticket = _ticket(company, item['ticket_id'], route)
                    tickets[ticket.pk] = ticket
                quantities[ticket.pk] += item['quantity']
        _assert_packing_available(company, packing_node, quantities)
        task = PackingTask.objects.create(
            company=company, task_no=task_no or f'PK-{uuid4().hex[:20].upper()}', route=route,
            packing_node=packing_node, inbound_node=inbound_node, request_id=request_id,
            request_fingerprint=fingerprint, remark=remark or '', creator=operator)
        for box in boxes:
            packing_box = PackingBox.objects.create(company=company, task=task,
                                                    box_no=box['box_no'])
            for item in box['items']:
                ticket = tickets[item['ticket_id']]
                PackingBoxLine.objects.create(company=company, box=packing_box, ticket=ticket,
                                              color=ticket.color, size=ticket.size,
                                              quantity=item['quantity'])
        return task


def approve_packing_task(company, task_id, *, request_id, operator=None):
    """包装审核：逐菲票写 packing 合格里程碑，整单原子提交。"""
    request_id = _request_id(request_id)
    with transaction.atomic():
        try:
            task = (PackingTask.objects.select_for_update().select_related('route')
                    .get(pk=_id(task_id), company=company))
        except PackingTask.DoesNotExist:
            raise NotFound('包装任务不存在') from None
        if PackingTask.objects.filter(company=company, approval_request_id=request_id).exclude(
                pk=task.pk).exists():
            raise IdempotencyError('request_id 已用于其他包装审核')
        if task.status != DocumentStatus.DRAFT:
            if task.status != DocumentStatus.APPROVED:
                raise ValidationError('已冲销包装任务不能再次审核')
            if task.approval_request_id != request_id:
                raise IdempotencyError('包装任务已使用其他 request_id 审核')
            return task
        route, packing_node, inbound_node = _route_context(company, task.route_id, lock=True)
        if task.packing_node_id != packing_node.pk or task.inbound_node_id != inbound_node.pk:
            raise ValidationError('包装任务的节点快照与路线不一致')
        quantities = _packing_quantities(task)
        if not quantities:
            raise ValidationError('包装任务没有箱内明细')
        _assert_packing_available(company, packing_node, quantities)
        for ticket_id in sorted(quantities):
            ticket = _ticket(company, ticket_id, route)
            complete_stage(company, packing_node, ticket, quantities[ticket_id], operator=operator,
                           request_id=f'fgp:{task.pk}:{ticket.pk}',
                           source=f'finished_goods.packing_task:{task.task_no}',
                           reason='包装审核合格')
        return mark_approved(task, operator=operator, request_id=request_id)


def _reserved_inbound_quantities(company, keys, *, exclude_document_id=None):
    """按箱+菲票对账；待审核单也预占，审核当前单时排除其自身。"""
    result = defaultdict(int)
    query = Q()
    for box_id, ticket_id in keys:
        query |= Q(box_id=box_id, ticket_id=ticket_id)
    lines = FinishedInboundLine.objects.filter(
        query, company=company, document__status__in=[DocumentStatus.DRAFT, DocumentStatus.APPROVED])
    if exclude_document_id is not None:
        lines = lines.exclude(document_id=exclude_document_id)
    rows = lines.values('box_id', 'ticket_id').annotate(total=Sum('quantity'))
    for row in rows:
        result[(row['box_id'], row['ticket_id'])] += row['total']
    return result


def _validate_inbound_lines(company, route, lines, *, exclude_document_id=None):
    if not lines:
        raise ValidationError('入库明细不能为空')
    box_line_keys = []
    resolved = []
    for line in lines:
        try:
            box_line = (PackingBoxLine.objects.select_for_update()
                        .select_related('box__task', 'ticket')
                        .get(company=company, box_id=line['box_id'],
                             ticket_id=line['ticket_id'], box__task__route=route,
                             box__task__status=DocumentStatus.APPROVED))
        except PackingBoxLine.DoesNotExist:
            raise ValidationError('入库明细必须匹配本路线已审核箱内的 ticket/color/size 明细') from None
        box_line_keys.append((box_line.box_id, box_line.ticket_id))
        resolved.append((line, box_line))
    received = _reserved_inbound_quantities(
        company, box_line_keys, exclude_document_id=exclude_document_id)
    for line, box_line in resolved:
        remaining = box_line.quantity - received[(box_line.box_id, box_line.ticket_id)]
        if line['quantity'] > remaining:
            raise ValidationError(f'箱 {box_line.box.box_no} 菲票 {box_line.ticket.bundle_no} '
                                  f'入库数量超过已包装可分配量 {remaining}（待审核入库单也会预占）')
    return resolved


def create_finished_inbound(company, *, route_id, lines, request_id, operator=None,
                            document_no='', warehouse_name='', location_name='', remark=''):
    """建立待审核入库单；只保存对账明细，不写数量流水。"""
    request_id = _request_id(request_id)
    lines = _normalize_inbound_lines(lines)
    fingerprint = _fingerprint({
        'route_id': _id(route_id), 'lines': lines, 'document_no': document_no or '',
        'warehouse_name': warehouse_name or '', 'location_name': location_name or '',
        'remark': remark or '', 'operator_id': getattr(operator, 'pk', None)})
    existing = FinishedInbound.objects.filter(company=company, request_id=request_id).first()
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyError('request_id 已用于不同入库请求')
        return existing
    with transaction.atomic():
        route, packing_node, inbound_node = _route_context(company, route_id, lock=True)
        resolved = _validate_inbound_lines(company, route, lines)
        document = FinishedInbound.objects.create(
            company=company, document_no=document_no or f'FI-{uuid4().hex[:20].upper()}',
            route=route, packing_node=packing_node, inbound_node=inbound_node,
            warehouse_name=warehouse_name or '', location_name=location_name or '',
            request_id=request_id, request_fingerprint=fingerprint, remark=remark or '',
            creator=operator)
        for line, box_line in resolved:
            FinishedInboundLine.objects.create(
                company=company, document=document, box=box_line.box, ticket=box_line.ticket,
                color=box_line.color, size=box_line.size, quantity=line['quantity'])
        return document


def approve_finished_inbound(company, document_id, *, request_id, operator=None):
    """入库审核：仅从 packing transfer_stage 到 finished_inbound。"""
    request_id = _request_id(request_id)
    with transaction.atomic():
        try:
            document = (FinishedInbound.objects.select_for_update()
                        .get(pk=_id(document_id), company=company))
        except FinishedInbound.DoesNotExist:
            raise NotFound('成品入库单不存在') from None
        if FinishedInbound.objects.filter(company=company, approval_request_id=request_id).exclude(
                pk=document.pk).exists():
            raise IdempotencyError('request_id 已用于其他入库审核')
        if document.status != DocumentStatus.DRAFT:
            if document.status != DocumentStatus.APPROVED:
                raise ValidationError('已冲销入库单不能再次审核')
            if document.approval_request_id != request_id:
                raise IdempotencyError('入库单已使用其他 request_id 审核')
            return document
        route, packing_node, inbound_node = _route_context(company, document.route_id, lock=True)
        if (document.packing_node_id != packing_node.pk
                or document.inbound_node_id != inbound_node.pk):
            raise ValidationError('入库单的节点快照与路线不一致')
        lines = list(FinishedInboundLine.objects.filter(company=company, document=document)
                     .values('box_id', 'ticket_id', 'quantity').order_by('box_id', 'ticket_id'))
        resolved = _validate_inbound_lines(
            company, route, lines, exclude_document_id=document.pk)
        quantities = defaultdict(int)
        for line, box_line in resolved:
            quantities[box_line.ticket_id] += line['quantity']
        for ticket_id in sorted(quantities):
            ticket = _ticket(company, ticket_id, route)
            transfer_stage(company, packing_node, inbound_node, ticket, quantities[ticket_id],
                           operator=operator, request_id=f'fgi:{document.pk}:{ticket.pk}',
                           source=f'finished_goods.inbound:{document.document_no}',
                           reason='成品入库审核')
        return mark_approved(document, operator=operator, request_id=request_id)


def _reversal_reason(reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 255:
        raise ValidationError('冲销原因须为 1~255 字符')
    return reason.strip()


def _check_reversal_id(model, company, document, request_id, reason):
    if model.objects.filter(company=company, reversal_request_id=request_id).exclude(
            pk=document.pk).exists():
        raise IdempotencyError('request_id 已用于其他单证冲销')
    if document.status == DocumentStatus.REVERSED:
        if document.reversal_request_id != request_id or document.reversal_reason != reason:
            raise IdempotencyError('单证已使用其他 request_id 或原因冲销')
        return False
    return True


def reverse_packing_task(company, task_id, *, request_id, reason, operator=None):
    """包装专用冲销；存在有效后续入库单时必须先处理后续单。"""
    request_id = _request_id(request_id)
    reason = _reversal_reason(reason)
    with transaction.atomic():
        try:
            task = PackingTask.objects.select_for_update().get(
                pk=_id(task_id), company=company)
        except PackingTask.DoesNotExist:
            raise NotFound('包装任务不存在') from None
        if not _check_reversal_id(PackingTask, company, task, request_id, reason):
            return task
        if FinishedInboundLine.objects.filter(
                company=company, box__task=task,
                document__status__in=[DocumentStatus.DRAFT, DocumentStatus.APPROVED]).exists():
            raise ValidationError('该包装已有有效入库单，请先冲销或取消后续入库单')
        if task.status == DocumentStatus.APPROVED:
            entries = list(QuantityLedger.objects.filter(
                company=company, source=f'finished_goods.packing_task:{task.task_no}',
                event_type=QuantityLedger.Event.QUALIFIED).order_by('-id'))
            for entry in entries:
                reverse_quantity(company, entry, operator=operator,
                                 request_id=f'fgp-reverse:{task.pk}:{entry.pk}', reason=reason)
        return mark_reversed(task, operator=operator, request_id=request_id, reason=reason)


def reverse_finished_inbound(company, document_id, *, request_id, reason, operator=None):
    """入库专用冲销；先冲接收端再冲转出端，出货后禁止直接冲销。"""
    request_id = _request_id(request_id)
    reason = _reversal_reason(reason)
    with transaction.atomic():
        try:
            document = FinishedInbound.objects.select_for_update().get(
                pk=_id(document_id), company=company)
        except FinishedInbound.DoesNotExist:
            raise NotFound('成品入库单不存在') from None
        if not _check_reversal_id(FinishedInbound, company, document, request_id, reason):
            return document
        if document.status == DocumentStatus.APPROVED:
            ticket_ids = FinishedInboundLine.objects.filter(
                company=company, document=document).values_list('ticket_id', flat=True)
            if QuantityLedger.objects.filter(
                    company=company, route_id=document.route_id, ticket_id__in=ticket_ids,
                    event_type=QuantityLedger.Event.TRANSFERRED, reversal__isnull=True,
                    source__startswith='shipment:').exists():
                raise ValidationError('该路线菲票已发生出货，请先作废对应出货单')
            entries = list(QuantityLedger.objects.filter(
                company=company, source=f'finished_goods.inbound:{document.document_no}',
                event_type__in=[QuantityLedger.Event.RECEIVED, QuantityLedger.Event.TRANSFERRED],
            ).order_by('event_type', '-id'))
            for entry in entries:
                reverse_quantity(company, entry, operator=operator,
                                 request_id=f'fgi-reverse:{document.pk}:{entry.pk}', reason=reason)
        return mark_reversed(document, operator=operator, request_id=request_id, reason=reason)


def release_finished_stock(company, node, ticket, quantity, *, operator, request_id, source):
    """成品出库终点：只从 finished_inbound 写统一 transferred 流水，不伪造下游收货。"""
    _positive(quantity)
    try:
        node = RouteNode.objects.select_related('stage').get(
            pk=node.pk, company=company,
            stage__stage_type=ProductionStage.StageType.FINISHED_INBOUND)
    except RouteNode.DoesNotExist:
        raise ValidationError('成品出库只能从 finished_inbound 节点扣减') from None
    return post_quantity(company, node, ticket, QuantityLedger.Event.TRANSFERRED, quantity,
                         operator=operator, request_id=request_id, source=source,
                         reason='出货审核扣减成品现货')


def get_inventory(company, *, route_id=None, order_id=None, ticket_id=None, production_id=None,
                  bed_no=None, color=None, size=None, include_zero=False):
    """成品现货唯一口径：finished_inbound 节点逐路线逐菲票净 available。"""
    nodes = (RouteNode.objects.filter(company=company, route__is_template=False,
                                      stage__stage_type=ProductionStage.StageType.FINISHED_INBOUND)
             .select_related('route', 'stage').order_by('route_id', 'sequence', 'id'))
    if route_id is not None:
        nodes = nodes.filter(route_id=_id(route_id))
    if order_id is not None:
        nodes = nodes.filter(Q(route__order_id=_id(order_id))
                             | Q(route__production__order_id=_id(order_id)))
    rows = []
    for node in nodes:
        tickets = Ticket.objects.filter(company=company).select_related('production__style')
        if node.route.production_id:
            tickets = tickets.filter(production_id=node.route.production_id)
        else:
            tickets = tickets.filter(production__order_id=node.route.order_id)
        if ticket_id is not None:
            tickets = tickets.filter(pk=_id(ticket_id))
        if production_id is not None:
            tickets = tickets.filter(production_id=_id(production_id))
        if bed_no:
            tickets = tickets.filter(bed_no=bed_no)
        if color is not None:
            tickets = tickets.filter(color=color)
        if size is not None:
            tickets = tickets.filter(size=size)
        for ticket in tickets.order_by('bed_no', 'bundle_no', 'id'):
            balance = get_balance(company, node, ticket)
            if not include_zero and balance['available'] == 0:
                continue
            rows.append({
                'route_id': node.route_id, 'node_id': node.pk, 'ticket_id': ticket.pk,
                'production_id': ticket.production_id,
                'style_no': getattr(ticket.production.style, 'style_no', '') or '',
                'bed_no': ticket.bed_no, 'bundle_no': ticket.bundle_no,
                'color': ticket.color, 'size': ticket.size,
                'available': balance['available'], 'received': balance['received'],
                'transferred': balance['transferred'], 'scrapped': balance['scrapped'],
                'reversals': balance['reversals'],
            })
    return rows
