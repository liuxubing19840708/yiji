"""统一生产数量服务。

余额公式（均取冲销折叠后的净额）：
  inputs = received + rework
  available = inputs - transferred - scrapped
  qualification_remaining = inputs - qualified - scrapped - rework_transferred
  transferable = min(available, qualified - forward_transferred)  # 返修出账不占用合格转出

qualified 是“多少投入已判合格”的里程碑，不再次扣 available；后续 transferred
才扣库存。因此 received=10, qualified=10, transferred=10 后 available=0，而非 -10。
返修退回使用 transferred(原节点) + rework(目标节点)，补裁是首节点新的 received；
报废使用 scrapped。任何冲销只新增 reversal，不修改原行。
"""
from hashlib import sha256
import json
from uuid import uuid4

from django.db import transaction

from apps.catalog.models import Ticket
from .exceptions import (IdempotencyError, NotFound, QuantityError, TransitionError,
                         ValidationError)
from .models import ProductionRoute, ProductionStage, QuantityLedger, RouteNode, StageExecution

_IN_EVENTS = (QuantityLedger.Event.RECEIVED, QuantityLedger.Event.REWORK)
_OUT_EVENTS = (QuantityLedger.Event.TRANSFERRED, QuantityLedger.Event.SCRAPPED)
_ACTION_BY_EVENT = {
    QuantityLedger.Event.RECEIVED: StageExecution.Action.RECEIVE,
    QuantityLedger.Event.QUALIFIED: StageExecution.Action.COMPLETE,
    QuantityLedger.Event.REWORK: StageExecution.Action.RECEIVE,
    QuantityLedger.Event.SCRAPPED: StageExecution.Action.SCRAP,
    QuantityLedger.Event.TRANSFERRED: StageExecution.Action.TRANSFER,
}


def _positive(quantity):
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise QuantityError('quantity 必须是正整数')
    return quantity


def _locked_route(company, route_id):
    try:
        return ProductionRoute.objects.select_for_update().get(pk=route_id, company=company)
    except ProductionRoute.DoesNotExist:
        raise NotFound('路线不存在') from None


def _context(company, node, ticket, *, lock=False):
    """所有对象按 company 重查；锁路线可串行化同一路线的余额写入。"""
    routes = ProductionRoute.objects
    nodes = RouteNode.objects
    tickets = Ticket.objects
    if lock:
        # 先锁路线，再读节点和菲票；所有写服务采用同一顺序，避免死锁顺序不一致。
        route = _locked_route(company, node.route_id)
    else:
        try:
            route = routes.get(pk=node.route_id, company=company)
        except ProductionRoute.DoesNotExist:
            raise NotFound('路线不存在') from None
    try:
        node = nodes.select_related('stage').get(pk=node.pk, company=company, route=route)
        ticket = tickets.select_related('production').get(pk=ticket.pk, company=company)
    except (RouteNode.DoesNotExist, Ticket.DoesNotExist):
        raise NotFound('节点或菲票不存在') from None
    if route.is_template:
        raise ValidationError('模板路线不能登记数量')
    if route.production_id:
        if ticket.production_id != route.production_id:
            raise ValidationError('菲票不属于路线床次')
    elif not ticket.production.order_id or ticket.production.order_id != route.order_id:
        raise ValidationError('菲票不属于路线订单')
    return route, node, ticket


def _fingerprint(event_type, node, ticket, quantity, operator, source, related_entry,
                 reversal_of, reason):
    payload = {
        'event_type': event_type,
        'node_id': node.pk,
        'ticket_id': ticket.pk,
        'quantity': quantity,
        'operator_id': getattr(operator, 'pk', None),
        'source': source or '',
        'related_entry_id': getattr(related_entry, 'pk', None),
        'reversal_of_id': getattr(reversal_of, 'pk', None),
        'reason': reason or '',
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return sha256(encoded).hexdigest()


def _idempotent(company, request_id, fingerprint):
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValidationError('request_id 不能为空')
    if len(request_id) > 64:
        raise ValidationError('request_id 不能超过 64 字符')
    existing = (QuantityLedger.objects.select_for_update()
                .filter(company=company, request_id=request_id).first())
    if existing is None:
        return None
    if existing.request_fingerprint != fingerprint:
        raise IdempotencyError('request_id 已用于不同请求')
    return existing


def _is_rework_transfer(entry):
    """返修出账与正常合格转出分开计数；冲销沿用原流水的执行动作。"""
    original = entry.reversal_of if entry.event_type == QuantityLedger.Event.REVERSAL else entry
    if original.event_type != QuantityLedger.Event.TRANSFERRED:
        return False
    try:
        return original.execution.action == StageExecution.Action.RETURN
    except StageExecution.DoesNotExist:
        return False


def _balance(company, node, ticket):
    totals = {key: 0 for key in ('received', 'qualified', 'rework', 'scrapped', 'transferred')}
    reversal_count = 0
    rework_transferred = 0
    entries = (QuantityLedger.objects.filter(company=company, node=node, ticket=ticket)
               .select_related('reversal_of__execution', 'execution').order_by('id'))
    for entry in entries:
        sign = -1 if entry.event_type == QuantityLedger.Event.REVERSAL else 1
        if _is_rework_transfer(entry):
            rework_transferred += sign * entry.quantity
        if entry.event_type == QuantityLedger.Event.REVERSAL:
            original_type = entry.reversal_of.event_type
            if original_type in totals:
                totals[original_type] -= entry.quantity
            reversal_count += 1
        else:
            totals[entry.event_type] += entry.quantity
    inputs = totals['received'] + totals['rework']
    available = inputs - totals['transferred'] - totals['scrapped']
    forward_transferred = totals['transferred'] - rework_transferred
    qualification_remaining = inputs - totals['qualified'] - totals['scrapped'] - rework_transferred
    transferable = max(0, min(available, totals['qualified'] - forward_transferred))
    return {
        **totals,
        'inputs': inputs,
        'available': available,
        'rework_transferred': rework_transferred,
        'forward_transferred': forward_transferred,
        'qualification_remaining': qualification_remaining,
        'transferable': transferable,
        'reversals': reversal_count,
    }


def get_balance(company, node, ticket, *, lock=False):
    """返回单扎单节点净余额；lock=True 用于调用方需要读时一致性的场景。"""
    with transaction.atomic():
        _, node, ticket = _context(company, node, ticket, lock=lock)
        return _balance(company, node, ticket)


def _route_available(company, route, ticket):
    """全路线在手数量：入站为正、转出/报废为负，合格里程碑不影响库存。

    单节点封顶不足以防止首节点在转出后反复补接；同一菲票所有节点的
    在手总量也不能超过菲票数量。补裁只能填补报废等已经释放的数量，
    超额补裁后续应通过受审批的菲票增补流程调整 ticket.qty。
    """
    total = 0
    entries = (QuantityLedger.objects.filter(company=company, route=route, ticket=ticket)
               .select_related('reversal_of').order_by('id'))
    for entry in entries:
        event_type = entry.event_type
        sign = 1
        if event_type == QuantityLedger.Event.REVERSAL:
            event_type = entry.reversal_of.event_type
            sign = -1
        if event_type in _IN_EVENTS:
            total += sign * entry.quantity
        elif event_type in _OUT_EVENTS:
            total -= sign * entry.quantity
    return total


def _assert_balance(event_type, quantity, balance, ticket_qty):
    inputs = balance['inputs']
    if event_type in _IN_EVENTS and balance['available'] + quantity > ticket_qty:
        # 不能用累计 received+rework 封顶，否则返修回路永远无法返回曾接收满扎的节点。
        raise QuantityError(f'节点在手数量不能超过菲票数量 {ticket_qty}')
    if event_type == QuantityLedger.Event.QUALIFIED:
        if quantity > balance['qualification_remaining']:
            raise QuantityError('合格数量超过待判定数量')
    elif event_type in _OUT_EVENTS:
        if quantity > balance['available']:
            raise QuantityError('出站数量超过节点可用量')
        if (event_type == QuantityLedger.Event.SCRAPPED
                and quantity > balance['qualification_remaining']):
            raise QuantityError('报废与合格之和不能超过累计入站')


def _record_execution(company, node, ticket, action, quantity, ledger, operator):
    execution, _ = StageExecution.objects.get_or_create(
        ledger=ledger,
        defaults={'company': company, 'node': node, 'ticket': ticket, 'action': action,
                  'quantity': quantity, 'operator': operator},
    )
    return execution


def post_quantity(company, node, ticket, event_type, quantity, *, operator=None,
                  request_id, source='', related_entry=None, reason=''):
    """写一条正数流水并返回它；相同公司+request_id+相同载荷返回原行。

    这是余额原语，不检查路线跳序；正常业务流转请用 receive/complete/transfer/
    return/scrap 包装服务。event_type=reversal 只能由 reverse_quantity 写入。
    """
    _positive(quantity)
    if event_type not in QuantityLedger.Event.values or event_type == QuantityLedger.Event.REVERSAL:
        raise ValidationError('event_type 非法；冲销请调用 reverse_quantity')
    with transaction.atomic():
        fingerprint = _fingerprint(event_type, node, ticket, quantity, operator, source,
                                   related_entry, None, reason)
        existing = _idempotent(company, request_id, fingerprint)
        if existing is not None:
            return existing
        route, node, ticket = _context(company, node, ticket, lock=True)
        # 二次检查保留给未来内部调用，公开入口上面已完成幂等检查。
        existing = _idempotent(company, request_id, fingerprint)
        if existing is not None:
            return existing
        if related_entry is not None:
            try:
                related_entry = QuantityLedger.objects.get(
                    pk=related_entry.pk, company=company, route=route, ticket=ticket)
            except QuantityLedger.DoesNotExist:
                raise ValidationError('关联流水与当前公司、路线或菲票不符') from None
        if (event_type in _IN_EVENTS
                and _route_available(company, route, ticket) + quantity > ticket.qty):
            raise QuantityError(f'全路线在手数量不能超过菲票数量 {ticket.qty}')
        balance = _balance(company, node, ticket)
        _assert_balance(event_type, quantity, balance, ticket.qty)
        ledger = QuantityLedger.objects.create(
            company=company, route=route, node=node,
            order=ticket.production.order, production=ticket.production, ticket=ticket,
            color=ticket.color, size=ticket.size, event_type=event_type, quantity=quantity,
            source=source or '', operator=operator, related_entry=related_entry,
            request_id=request_id, request_fingerprint=fingerprint, reason=reason or '')
        _record_execution(company, node, ticket, _ACTION_BY_EVENT[event_type], quantity,
                          ledger, operator)
        return ledger


def _check_reversal_balance(original, balance, ticket_qty):
    event_type = original.event_type
    quantity = original.quantity
    if event_type in _IN_EVENTS:
        balance['received' if event_type == QuantityLedger.Event.RECEIVED else 'rework'] -= quantity
    elif event_type in _OUT_EVENTS:
        balance['transferred' if event_type == QuantityLedger.Event.TRANSFERRED else 'scrapped'] -= quantity
        if event_type == QuantityLedger.Event.TRANSFERRED and _is_rework_transfer(original):
            balance['rework_transferred'] -= quantity
        elif event_type == QuantityLedger.Event.TRANSFERRED:
            balance['forward_transferred'] -= quantity
    elif event_type == QuantityLedger.Event.QUALIFIED:
        balance['qualified'] -= quantity
    if any(balance[key] < 0 for key in
           ('received', 'qualified', 'rework', 'scrapped', 'transferred')):
        raise QuantityError('冲销会导致事件净额为负')
    inputs = balance['received'] + balance['rework']
    available = inputs - balance['transferred'] - balance['scrapped']
    if available < 0:
        raise QuantityError('后续出站已消费该流水，请先冲销后续流水')
    if balance['qualified'] + balance['scrapped'] + balance['rework_transferred'] > inputs:
        raise QuantityError('冲销会导致合格/返修/报废超过累计入站，请先冲销后续流水')
    if (event_type == QuantityLedger.Event.QUALIFIED
            and balance['forward_transferred'] > balance['qualified']
            and balance['forward_transferred'] <= balance['qualified'] + quantity):
        raise QuantityError('该合格数量已转出，请先冲销后续转出流水')


def reverse_quantity(company, ledger, *, operator=None, request_id, reason=''):
    """新增一条全额反向冲销；部分调整应先冲销原行再写正确的新行。"""
    with transaction.atomic():
        try:
            original = QuantityLedger.objects.select_for_update().get(pk=ledger.pk,
                                                                       company=company)
        except QuantityLedger.DoesNotExist:
            raise NotFound('流水不存在') from None
        if original.event_type == QuantityLedger.Event.REVERSAL:
            raise ValidationError('冲销流水不能再次冲销')
        fingerprint = _fingerprint(QuantityLedger.Event.REVERSAL, original.node,
                                   original.ticket, original.quantity, operator,
                                   original.source, original.related_entry, original, reason)
        existing = _idempotent(company, request_id, fingerprint)
        if existing is not None:
            return existing
        route, node, ticket = _context(company, original.node, original.ticket, lock=True)
        existing = _idempotent(company, request_id, fingerprint)
        if existing is not None:
            return existing
        if hasattr(original, 'reversal'):
            raise ValidationError('该流水已冲销')
        if QuantityLedger.objects.filter(related_entry=original, reversal__isnull=True).exclude(
                event_type=QuantityLedger.Event.REVERSAL).exists():
            raise QuantityError('成对流水的接收端尚未冲销，请先冲销关联接收流水')
        balance = _balance(company, node, ticket)
        _check_reversal_balance(original, balance, ticket.qty)
        if (original.event_type in _OUT_EVENTS
                and _route_available(company, route, ticket) + original.quantity > ticket.qty):
            raise QuantityError('冲销会超过菲票全路线在手数量，请先冲销后续补裁或接收流水')
        return QuantityLedger.objects.create(
            company=company, route=route, node=node, order=original.order,
            production=original.production, ticket=ticket, color=original.color,
            size=original.size, event_type=QuantityLedger.Event.REVERSAL,
            quantity=original.quantity, source=original.source, operator=operator,
            related_entry=original.related_entry, reversal_of=original,
            request_id=request_id, request_fingerprint=fingerprint, reason=reason or '')


def _nodes(route):
    return list(RouteNode.objects.filter(route=route, company=route.company)
                .select_related('stage').order_by('sequence', 'id'))


def _parallel_group(nodes, node):
    index = next(i for i, item in enumerate(nodes) if item.pk == node.pk)
    if not node.allow_parallel:
        return {node.pk}
    start = index
    while start > 0 and nodes[start].allow_parallel:
        start -= 1
    # 前一个 allow_parallel=False 是组的共同前驱，不属于并行组。
    if start < index and not nodes[start].allow_parallel:
        start += 1
    end = index
    while end + 1 < len(nodes) and nodes[end + 1].allow_parallel:
        end += 1
    return {item.pk for item in nodes[start:end + 1]}


def assert_can_transition(company, from_node, to_node, ticket, quantity, *,
                          allow_rework=False):
    """校验正常向前流转或显式返修退回，成功返回 True，失败抛 TransitionError。

    并行组内可互为前驱；离开并行组时，组内其他必过节点须已有至少 quantity
    的净合格数。普通转出消耗 qualified-forward_transferred，退回仅消耗未判合格数量。
    """
    _positive(quantity)
    with transaction.atomic():
        route, from_node, ticket = _context(company, from_node, ticket, lock=True)
        try:
            to_node = RouteNode.objects.get(pk=to_node.pk, company=company, route=route)
        except RouteNode.DoesNotExist:
            raise TransitionError('目标节点不在同一路线') from None
        if from_node.pk == to_node.pk:
            raise TransitionError('来源与目标节点不能相同')
        backward = to_node.sequence < from_node.sequence
        if backward and not allow_rework:
            raise TransitionError('逆向流转必须使用 return_stage 返修退回')
        if not backward:
            nodes = _nodes(route)
            group = _parallel_group(nodes, to_node if to_node.allow_parallel else from_node)
            for middle in nodes:
                if not from_node.sequence < middle.sequence < to_node.sequence:
                    continue
                if middle.pk in group:
                    # 并行成员不互相构成先后门槛；离开组时才检查其合格产出。
                    if not to_node.allow_parallel and middle.required and not middle.allow_skip:
                        if _balance(company, middle, ticket)['qualified'] < quantity:
                            raise TransitionError(f'并行必过节点 {middle.sequence} 尚未完工')
                    continue
                if middle.required and not middle.allow_skip:
                    raise TransitionError(f'不能跳过必过节点 {middle.sequence}')
        balance = _balance(company, from_node, ticket)
        if balance['available'] < quantity:
            raise TransitionError('来源节点可用量不足')
        if not backward and balance['transferable'] < quantity:
            raise TransitionError('正常转出不能超过未转出的合格数量')
        return True


def receive_stage(company, node, ticket, quantity, *, operator=None, request_id,
                  source='', reason=''):
    """首节点接收（含补裁）；下游接收由 transfer_stage 自动写入。"""
    with transaction.atomic():
        route, node, _ = _context(company, node, ticket, lock=True)
        first = RouteNode.objects.filter(route=route).order_by('sequence').first()
        if first is None or first.pk != node.pk:
            raise TransitionError('只有首节点可直接接收，请使用 transfer_stage')
        return post_quantity(company, node, ticket, QuantityLedger.Event.RECEIVED, quantity,
                             operator=operator, request_id=request_id, source=source,
                             reason=reason)


def complete_stage(company, node, ticket, quantity, *, operator=None, request_id,
                   source='', reason=''):
    return post_quantity(company, node, ticket, QuantityLedger.Event.QUALIFIED, quantity,
                         operator=operator, request_id=request_id, source=source, reason=reason)


def _validate_pair_request_id(request_id):
    # :out / :in 后缀占 4 个字符，基础键最长 60，派生后仍满足 64 字符约束。
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 60:
        raise ValidationError('成对流转 request_id 须为 1~60 字符的非空字符串')


def _assert_cutting_handover_gate(company, from_node, to_node):
    """strict 只拦截裁剪来源的普通转出；确认交接由两个签收服务显式放行。"""
    if from_node.stage.stage_type != ProductionStage.StageType.CUTTING:
        return
    # 延迟导入避免 handover 适配层复用本服务时形成模块循环。
    from apps.handover.models import HandoverKind, HandoverPolicy
    from apps.handover.services import effective_policy
    policy = effective_policy(company, HandoverKind.CUTTING, from_node, to_node)
    if policy.mode == HandoverPolicy.Mode.STRICT:
        raise TransitionError('当前裁片节点要求通过交接单签收后转出')


def transfer_stage(company, from_node, to_node, ticket, quantity, *, operator=None,
                   request_id, source='', reason='', _confirmed_handover=False):
    """原子写入 transferred + received，request_id 派生两个独立幂等键。

    _confirmed_handover 仅供通用/旧裁片签收服务使用，不是公开业务参数。
    """
    _validate_pair_request_id(request_id)
    with transaction.atomic():
        pair_exists = QuantityLedger.objects.filter(
            company=company, request_id__in=[f'{request_id}:out', f'{request_id}:in']).count() == 2
        if not pair_exists:
            if not _confirmed_handover:
                _assert_cutting_handover_gate(company, from_node, to_node)
            assert_can_transition(company, from_node, to_node, ticket, quantity)
        outgoing = post_quantity(company, from_node, ticket, QuantityLedger.Event.TRANSFERRED,
                                 quantity, operator=operator, request_id=f'{request_id}:out',
                                 source=source, reason=reason)
        incoming = post_quantity(company, to_node, ticket, QuantityLedger.Event.RECEIVED,
                                 quantity, operator=operator, request_id=f'{request_id}:in',
                                 source=source, related_entry=outgoing, reason=reason)
        return outgoing, incoming


def return_stage(company, from_node, to_node, ticket, quantity, *, operator=None,
                 request_id, source='', reason='返修退回'):
    """返修回路：原节点转出，任一前序节点返修入站；无需先判合格。"""
    _validate_pair_request_id(request_id)
    if to_node.sequence >= from_node.sequence:
        raise TransitionError('返修退回的目标必须是前序节点')
    with transaction.atomic():
        pair_exists = QuantityLedger.objects.filter(
            company=company, request_id__in=[f'{request_id}:out', f'{request_id}:in']).count() == 2
        if not pair_exists:
            assert_can_transition(company, from_node, to_node, ticket, quantity, allow_rework=True)
            balance = get_balance(company, from_node, ticket, lock=True)
            if quantity > balance['qualification_remaining']:
                raise QuantityError('返修数量超过当前未判合格数量；已合格数量请走正常流转或检验复检')
        outgoing = post_quantity(company, from_node, ticket, QuantityLedger.Event.TRANSFERRED,
                                 quantity, operator=operator, request_id=f'{request_id}:out',
                                 source=source, reason=reason)
        outgoing_execution = StageExecution.objects.get(ledger=outgoing)
        outgoing_execution.action = StageExecution.Action.RETURN
        outgoing_execution.save(update_fields=['action'])
        outgoing.execution = outgoing_execution
        incoming = post_quantity(company, to_node, ticket, QuantityLedger.Event.REWORK,
                                 quantity, operator=operator, request_id=f'{request_id}:in',
                                 source=source, related_entry=outgoing, reason=reason)
        return outgoing, incoming


def scrap_stage(company, node, ticket, quantity, *, operator=None, request_id,
                source='', reason='报废'):
    return post_quantity(company, node, ticket, QuantityLedger.Event.SCRAPPED, quantity,
                         operator=operator, request_id=request_id, source=source, reason=reason)


def instantiate_route(template, *, company, order=None, production=None, code=None, name=None):
    """从模板原子克隆路线及节点配置；目标必须且只能为订单或床次。"""
    if (order is None) == (production is None):
        raise ValidationError('order 与 production 必须且只能提供一个')
    target = order or production
    if target.company_id != company.pk:
        raise NotFound('订单或床次不存在')
    with transaction.atomic():
        try:
            template = (ProductionRoute.objects.select_for_update()
                        .get(pk=template.pk, company=company, is_template=True))
        except ProductionRoute.DoesNotExist:
            raise NotFound('路线模板不存在') from None
        if not template.is_active:
            raise ValidationError('路线模板已停用，不能创建新实例')
        route = ProductionRoute.objects.create(
            company=company, code=code or f'R-{uuid4().hex}', name=name or template.name,
            is_template=False, order=order, production=production, source_template=template)
        RouteNode.objects.bulk_create([
            RouteNode(company=company, route=route, stage=node.stage,
                      stage_code=node.stage_code, stage_name=node.stage_name,
                      stage_type=node.stage_type, sequence=node.sequence, required=node.required,
                      allow_parallel=node.allow_parallel, allow_skip=node.allow_skip)
            for node in template.nodes.select_related('stage').all()
        ])
        return route


def get_route_for_ticket(company, ticket):
    """床次实例优先，其次订单实例；均按 company 限定。"""
    try:
        ticket = Ticket.objects.select_related('production').get(pk=ticket.pk, company=company)
    except Ticket.DoesNotExist:
        raise NotFound('菲票不存在') from None
    route = ProductionRoute.objects.filter(company=company,
                                           production=ticket.production).first()
    if route is None and ticket.production.order_id:
        route = ProductionRoute.objects.filter(company=company,
                                               order_id=ticket.production.order_id).first()
    if route is None:
        raise NotFound('菲票没有可用路线')
    return route


def get_ticket_node_balances(company, route, *, ticket=None):
    """每扎×每节点只读视图；尚未发生流水的组合也返回全零余额。"""
    try:
        route = ProductionRoute.objects.get(pk=route.pk, company=company, is_template=False)
    except ProductionRoute.DoesNotExist:
        raise NotFound('路线不存在') from None
    tickets = Ticket.objects.filter(company=company)
    if route.production_id:
        tickets = tickets.filter(production_id=route.production_id)
    else:
        tickets = tickets.filter(production__order_id=route.order_id)
    if ticket is not None:
        tickets = tickets.filter(pk=ticket.pk)
    tickets = list(tickets.order_by('bed_no', 'bundle_no', 'id'))
    nodes = _nodes(route)
    rows = []
    for item in tickets:
        for node in nodes:
            rows.append({'ticket_id': item.pk, 'bundle_no': item.bundle_no,
                         'color': item.color, 'size': item.size, 'node_id': node.pk,
                         'sequence': node.sequence, 'stage_type': node.stage.stage_type,
                         'balance': _balance(company, node, item)})
    return rows
