"""通用交接领域服务。

裁片创建同时核算新旧待交接预占；接收按明细实收数量原子写入数量流水。
"""
from hashlib import sha256
import json
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User, UserCompany
from apps.catalog.models import Ticket
from apps.core.permissions import has_perm
from apps.manufacturing.models import ProductionStage, RouteNode
from apps.manufacturing import exceptions as manufacturing_exceptions
from apps.manufacturing.services import assert_can_transition, get_balance, transfer_stage

from .exceptions import IdempotencyError, NotFound, ValidationError
from .models import Handover, HandoverEvent, HandoverItem, HandoverKind, HandoverPolicy, HandoverRequest


def _fingerprint(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return sha256(encoded).hexdigest()


def _request_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ValidationError('request_id 须为 1~64 字符的非空字符串')
    return value


def _id(value, label='ID', *, allow_none=False):
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise ValidationError(f'{label}非法')
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f'{label}非法') from None
    if result <= 0:
        raise ValidationError(f'{label}必须是正整数')
    return result


def _positive(value, label='quantity'):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f'{label}必须是正整数')
    return value


def _nonnegative(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError('received_quantity 必须是非负整数')
    return value


def _text(value, label, *, required=False, max_length=255):
    if value is None:
        value = ''
    if not isinstance(value, str) or len(value) > max_length:
        raise ValidationError(f'{label}格式非法')
    if required and not value.strip():
        raise ValidationError(f'{label}必填')
    return value.strip() if required else value


def _active_membership(company, user, label='用户'):
    try:
        membership = UserCompany.objects.select_related('post', 'user').get(
            company=company, user=user, status=UserCompany.Status.ACTIVE)
    except UserCompany.DoesNotExist:
        raise NotFound(f'{label}不是本公司在职成员') from None
    if membership.is_suspended:
        raise ValidationError(f'{label}已停职')
    return membership


def _node(company, node_id, label):
    try:
        return RouteNode.objects.select_related('stage').get(pk=node_id, company=company)
    except RouteNode.DoesNotExist:
        raise NotFound(f'{label}不存在') from None


def _new_no(prefix):
    return f'{prefix}{uuid4().hex[:24].upper()}'


def generic_pending_quantity(company, source_node, ticket):
    """通用裁片待接收预占；供旧裁片接口共同核算，防止双入口超分。"""
    return (HandoverItem.objects.filter(
        company=company, ticket=ticket, handover__company=company,
        handover__kind=HandoverKind.CUTTING, handover__source_node=source_node,
        handover__status=Handover.Status.PENDING, handover__is_deleted=False)
        .aggregate(total=Sum('quantity'))['total'] or 0)


def _legacy_pending_quantity(company, source_node, ticket):
    # 延迟导入避免 cutting 旧服务复用通用预占时形成模块循环。
    from apps.cutting.models import CuttingHandover
    return (CuttingHandover.objects.filter(
        company=company, ticket=ticket, source_node=source_node,
        status=CuttingHandover.Status.PENDING)
        .aggregate(total=Sum('quantity'))['total'] or 0)


def _validate_cutting_creation(company, source, target, tickets, targets):
    """锁路线后按菲票汇总本批需求，新旧 pending 共同占用真实可转量。"""
    if source.stage.stage_type != ProductionStage.StageType.CUTTING:
        raise ValidationError('来源节点必须是裁剪节点')
    if target.route_id != source.route_id:
        raise ValidationError('目标节点必须与来源节点属于同一路线')
    if target.sequence <= source.sequence:
        raise ValidationError('目标节点必须是来源的后续节点')
    requested = {}
    for row in targets:
        for item in row['items']:
            ticket_id = item['ticket']
            requested[ticket_id] = requested.get(ticket_id, 0) + item['quantity']
    try:
        for ticket_id, quantity in requested.items():
            ticket = tickets[ticket_id]
            balance = get_balance(company, source, ticket, lock=True)
            assert_can_transition(company, source, target, ticket, quantity)
            reserved = (generic_pending_quantity(company, source, ticket)
                        + _legacy_pending_quantity(company, source, ticket))
            if reserved + quantity > balance['transferable']:
                raise ValidationError('交接数量超过扣除新旧待接收预占后的可转量')
    except manufacturing_exceptions.ValidationError as exc:
        raise ValidationError(str(exc)) from None


def effective_policy(company, kind, source_node, target_node):
    """返回最具体节点策略；source+target 优先，未配置返回未保存的 off。"""
    candidates = HandoverPolicy.objects.filter(company=company, kind=kind)
    matched = [row for row in candidates
               if row.source_node_id in (None, source_node.pk)
               and row.target_node_id in (None, target_node.pk)]
    if not matched:
        return HandoverPolicy(company=company, kind=kind, mode=HandoverPolicy.Mode.OFF)
    priority = {(False, False): 0, (False, True): 1, (True, False): 2, (True, True): 3}
    return max(matched, key=lambda row: priority[(row.source_node_id is not None,
                                                 row.target_node_id is not None)])


def _existing_request(company, request_id, fingerprint, action, *, handover_id=None):
    existing = (HandoverRequest.objects.select_for_update()
                .filter(company=company, request_id=request_id).first())
    if existing is None:
        return None
    if (existing.request_fingerprint != fingerprint or existing.action != action
            or (handover_id is not None and existing.handover_id != handover_id)):
        raise IdempotencyError('request_id 已用于不同请求')
    return existing


def _normalize_create(kind, source_node, target_node, remark, targets):
    if kind not in HandoverKind.values:
        raise ValidationError('kind 当前仅支持 cutting')
    normalized = {
        'kind': kind,
        'source_node': _id(source_node, 'source_node'),
        'target_node': _id(target_node, 'target_node'),
        'remark': _text(remark, 'remark'),
        'targets': [],
    }
    if not isinstance(targets, list) or not targets:
        raise ValidationError('targets 至少一项')
    seen_tickets = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValidationError('target 格式非法')
        receiver = _id(target.get('receiver'), 'receiver', allow_none=True)
        group = _id(target.get('target_group_no'), 'target_group_no', allow_none=True)
        if receiver is None and group is None:
            raise ValidationError('receiver 与 target_group_no 至少提供一项')
        raw_items = target.get('items')
        if not isinstance(raw_items, list) or not raw_items:
            raise ValidationError('items 至少一项')
        items = []
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValidationError('item 格式非法')
            ticket_id = _id(item.get('ticket'), 'ticket')
            if ticket_id in seen_tickets:
                raise ValidationError(f'菲票 {ticket_id} 在一次请求中不能跨 target 重复分配')
            seen_tickets.add(ticket_id)
            items.append({'ticket': ticket_id, 'quantity': _positive(item.get('quantity'))})
        normalized['targets'].append({'receiver': receiver, 'target_group_no': group, 'items': items})
    return normalized


def _validate_create_context(company, data, created_by):
    membership = _active_membership(company, created_by, '交出人')
    if not has_perm(membership, 'handover.create'):
        raise PermissionDenied('没有交接创建权限')
    source = _node(company, data['source_node'], '来源节点')
    target = _node(company, data['target_node'], '目标节点')
    ticket_ids = {item['ticket'] for row in data['targets'] for item in row['items']}
    tickets = Ticket.objects.filter(company=company, pk__in=ticket_ids).in_bulk()
    if len(tickets) != len(ticket_ids):
        raise NotFound('包含不存在或不属于本公司的菲票')
    receivers = {row['receiver'] for row in data['targets'] if row['receiver'] is not None}
    users = User.objects.filter(pk__in=receivers).in_bulk()
    if len(users) != len(receivers):
        raise NotFound('接收人不存在')
    for user in users.values():
        _active_membership(company, user, '接收人')
    return source, target, tickets, users


def create_handovers(company, *, kind, source_node, target_node, remark='', targets,
                     request_id, created_by):
    """原子创建同批多张独立交接；相同完整请求返回原批次。"""
    request_id = _request_id(request_id)
    data = _normalize_create(kind, source_node, target_node, remark, targets)
    fingerprint = _fingerprint({'action': 'create', **data})
    # 先校验操作者，避免以已知 request_id 越权读取原批次。
    _validate_create_context(company, data, created_by)
    try:
        with transaction.atomic():
            existing = _existing_request(company, request_id, fingerprint, 'create')
            if existing is not None:
                return list(Handover.objects.filter(company=company, batch_no=existing.batch_no)
                            .order_by('id'))
            source, target, tickets, users = _validate_create_context(company, data, created_by)
            if kind == HandoverKind.CUTTING:
                _validate_cutting_creation(company, source, target, tickets, data['targets'])
            batch_no = _new_no('B')
            handovers = []
            for row in data['targets']:
                handover = Handover.objects.create(
                    company=company, handover_no=_new_no('H'), batch_no=batch_no, kind=kind,
                    source_node=source, target_node=target, target_group_no=row['target_group_no'],
                    receiver=users.get(row['receiver']), created_by=created_by, remark=data['remark'])
                for item in row['items']:
                    HandoverItem.objects.create(company=company, handover=handover,
                                                ticket=tickets[item['ticket']],
                                                quantity=item['quantity'])
                HandoverEvent.objects.create(
                    company=company, handover=handover, action=HandoverEvent.Action.CREATED,
                    actor=created_by,
                    after={'status': Handover.Status.PENDING, 'receiver': handover.receiver_id,
                           'target_group_no': handover.target_group_no})
                handovers.append(handover)
            HandoverRequest.objects.create(company=company, request_id=request_id,
                                           request_fingerprint=fingerprint, action='create',
                                           batch_no=batch_no)
            return handovers
    except IntegrityError:
        # 唯一约束收口并发重复请求；若不是幂等冲突则保留原始数据库异常。
        existing = HandoverRequest.objects.filter(company=company, request_id=request_id).first()
        if existing is None:
            raise
        if existing.request_fingerprint != fingerprint or existing.action != 'create':
            raise IdempotencyError('request_id 已用于不同请求') from None
        return list(Handover.objects.filter(company=company, batch_no=existing.batch_no).order_by('id'))


def _locked_handover(company, handover_id):
    try:
        return (Handover.objects.select_for_update(of=('self',)).select_related('receiver', 'created_by')
                .get(pk=_id(handover_id, 'handover_id'), company=company))
    except Handover.DoesNotExist:
        raise NotFound('交接单不存在') from None


def _authorize_recipient(company, handover, actor):
    membership = _active_membership(company, actor, '接收人')
    if not has_perm(membership, 'handover.receive'):
        raise PermissionDenied('没有交接接收权限')
    if handover.received_by_id == actor.pk:
        return membership
    if handover.receiver_id is not None:
        if handover.receiver_id != actor.pk:
            raise PermissionDenied('仅指定接收人本人可处理该交接')
    elif handover.target_group_no != membership.group_no:
        raise PermissionDenied('仅目标组内具有接收权限的在职成员可处理该交接')
    return membership


def _handover_snapshot(handover):
    return {
        'status': handover.status,
        'receiver': handover.receiver_id,
        'target_group_no': handover.target_group_no,
        'received_by': handover.received_by_id,
        'received_quantity': handover.received_quantity,
    }


def receive_handover(company, handover_id, *, request_id, actor, reason='', items=None):
    """接收整单；仅传差异行，其余行默认全收。整个动作与事件原子提交。"""
    request_id = _request_id(request_id)
    reason = _text(reason, 'reason')
    raw_items = [] if items is None else items
    if not isinstance(raw_items, list):
        raise ValidationError('items 格式非法')
    normalized = []
    seen = set()
    for row in raw_items:
        if not isinstance(row, dict):
            raise ValidationError('差异明细格式非法')
        item_id = _id(row.get('id'), 'item_id')
        if item_id in seen:
            raise ValidationError('差异明细不能重复')
        seen.add(item_id)
        difference_type = row.get('difference_type') or ''
        if difference_type not in ['', *HandoverItem.DifferenceType.values]:
            raise ValidationError('difference_type 非法')
        normalized.append({'id': item_id,
                           'received_quantity': _nonnegative(row.get('received_quantity')),
                           'difference_type': difference_type,
                           'reason': _text(row.get('reason'), 'reason')})
    normalized.sort(key=lambda row: row['id'])
    fingerprint = _fingerprint({'action': 'receive', 'handover': _id(handover_id, 'handover_id'),
                                'reason': reason, 'items': normalized})
    with transaction.atomic():
        handover = _locked_handover(company, handover_id)
        _authorize_recipient(company, handover, actor)
        existing = _existing_request(company, request_id, fingerprint, 'receive',
                                     handover_id=handover.pk)
        if existing is not None:
            return handover
        if handover.status != Handover.Status.PENDING:
            raise ValidationError('仅待接收交接单可接收')
        rows = list(HandoverItem.objects.select_for_update().filter(
            company=company, handover=handover).order_by('id'))
        by_id = {row.pk: row for row in rows}
        if any(row['id'] not in by_id for row in normalized):
            raise ValidationError('差异明细不属于该交接单')
        differences = {row['id']: row for row in normalized}
        before = _handover_snapshot(handover)
        for item in rows:
            difference = differences.get(item.pk)
            if difference is None:
                item.received_quantity = item.quantity
                item.difference_type = ''
                item.reason = ''
            else:
                received_quantity = difference['received_quantity']
                item.received_quantity = received_quantity
                item.difference_type = difference['difference_type']
                item.reason = difference['reason']
                if ((received_quantity != item.quantity or item.difference_type)
                        and not item.reason.strip()):
                    raise ValidationError('有差异的明细必须填写 reason')
            if item.received_quantity:
                try:
                    balance = get_balance(company, handover.source_node, item.ticket, lock=True)
                    # 本单原预占可用于实收；超收及其他明细不得吃掉其它待交接预占。
                    other_reserved = max(0, generic_pending_quantity(
                        company, handover.source_node, item.ticket) + _legacy_pending_quantity(
                            company, handover.source_node, item.ticket) - item.quantity)
                    if item.received_quantity + other_reserved > balance['transferable']:
                        raise ValidationError('实收数量超过扣除其他待接收预占后的实际可转量')
                    outgoing, incoming = transfer_stage(
                        company, handover.source_node, handover.target_node, item.ticket,
                        item.received_quantity, operator=actor,
                        request_id=f'handover-{handover.pk}-item-{item.pk}',
                        source=f'handover_item:{item.pk}',
                        reason=item.reason or reason or handover.remark,
                        _confirmed_handover=True)
                except manufacturing_exceptions.ValidationError as exc:
                    raise ValidationError(str(exc)) from None
                item.out_ledger = outgoing
                item.in_ledger = incoming
            item.save(update_fields=['received_quantity', 'difference_type', 'reason',
                                     'out_ledger', 'in_ledger', 'updated_at'])
        handover.status = Handover.Status.RECEIVED
        handover.received_by = actor
        handover.received_at = timezone.now()
        handover.save(update_fields=['status', 'received_by', 'received_at', 'updated_at'])
        after = _handover_snapshot(handover)
        after['items'] = [{'id': row.pk, 'received_quantity': row.received_quantity,
                           'difference_type': row.difference_type, 'reason': row.reason}
                          for row in rows]
        HandoverEvent.objects.create(company=company, handover=handover,
                                     action=HandoverEvent.Action.RECEIVED, actor=actor,
                                     actual_user=actor, reason=reason, before=before, after=after)
        HandoverRequest.objects.create(company=company, request_id=request_id,
                                       request_fingerprint=fingerprint, action='receive',
                                       handover=handover)
        return handover


def reject_handover(company, handover_id, *, request_id, actor, reason):
    request_id = _request_id(request_id)
    reason = _text(reason, 'reason', required=True)
    fingerprint = _fingerprint({'action': 'reject', 'handover': _id(handover_id, 'handover_id'),
                                'reason': reason})
    with transaction.atomic():
        handover = _locked_handover(company, handover_id)
        _authorize_recipient(company, handover, actor)
        existing = _existing_request(company, request_id, fingerprint, 'reject',
                                     handover_id=handover.pk)
        if existing is not None:
            return handover
        if handover.status != Handover.Status.PENDING:
            raise ValidationError('仅待接收交接单可拒收')
        before = _handover_snapshot(handover)
        handover.status = Handover.Status.REJECTED
        handover.reject_reason = reason
        handover.save(update_fields=['status', 'reject_reason', 'updated_at'])
        HandoverEvent.objects.create(company=company, handover=handover,
                                     action=HandoverEvent.Action.REJECTED, actor=actor,
                                     reason=reason, before=before,
                                     after={**_handover_snapshot(handover), 'reject_reason': reason})
        HandoverRequest.objects.create(company=company, request_id=request_id,
                                       request_fingerprint=fingerprint, action='reject',
                                       handover=handover)
        return handover


def reassign_handover(company, handover_id, *, request_id, actor, receiver, reason):
    request_id = _request_id(request_id)
    reason = _text(reason, 'reason', required=True)
    receiver_id = _id(receiver, 'receiver')
    fingerprint = _fingerprint({'action': 'reassign', 'handover': _id(handover_id, 'handover_id'),
                                'receiver': receiver_id, 'reason': reason})
    with transaction.atomic():
        handover = _locked_handover(company, handover_id)
        membership = _active_membership(company, actor, '改派人')
        if not has_perm(membership, 'handover.manage'):
            raise PermissionDenied('没有交接改派权限')
        existing = _existing_request(company, request_id, fingerprint, 'reassign',
                                     handover_id=handover.pk)
        if existing is not None:
            return handover
        if handover.status != Handover.Status.PENDING:
            raise ValidationError('仅待接收交接单可改派')
        try:
            new_receiver = User.objects.get(pk=receiver_id)
        except User.DoesNotExist:
            raise NotFound('新接收人不存在') from None
        _active_membership(company, new_receiver, '新接收人')
        before = _handover_snapshot(handover)
        handover.receiver = new_receiver
        handover.save(update_fields=['receiver', 'updated_at'])
        HandoverEvent.objects.create(company=company, handover=handover,
                                     action=HandoverEvent.Action.REASSIGNED, actor=actor,
                                     reason=reason, before=before, after=_handover_snapshot(handover))
        HandoverRequest.objects.create(company=company, request_id=request_id,
                                       request_fingerprint=fingerprint, action='reassign',
                                       handover=handover)
        return handover


def save_policy(company, data, *, actor, policy=None):
    """保存策略配置本身；通用层不把 strict 解释成已启用的数量门禁。"""
    membership = _active_membership(company, actor, '策略更新人')
    if not has_perm(membership, 'handover.manage'):
        raise PermissionDenied('没有交接策略维护权限')
    kind = data.get('kind', HandoverKind.CUTTING)
    if kind not in HandoverKind.values:
        raise ValidationError('kind 当前仅支持 cutting')
    mode = data.get('mode', HandoverPolicy.Mode.OFF)
    if mode not in HandoverPolicy.Mode.values:
        raise ValidationError('mode 非法')
    timeout = _positive(data.get('timeout_minutes', 120), 'timeout_minutes')
    source_id = _id(data.get('source_node'), 'source_node', allow_none=True)
    target_id = _id(data.get('target_node'), 'target_node', allow_none=True)
    source = _node(company, source_id, '来源节点') if source_id else None
    target = _node(company, target_id, '目标节点') if target_id else None
    receiver = None
    receiver_id = _id(data.get('receiver'), 'receiver', allow_none=True)
    if receiver_id:
        try:
            receiver = User.objects.get(pk=receiver_id)
        except User.DoesNotExist:
            raise NotFound('接收人不存在') from None
        _active_membership(company, receiver, '接收人')
    group = _id(data.get('target_group_no'), 'target_group_no', allow_none=True)
    if policy is None:
        policy = HandoverPolicy(company=company)
    elif policy.company_id != company.id:
        raise NotFound('交接策略不存在')
    policy.kind = kind
    policy.mode = mode
    policy.source_node = source
    policy.target_node = target
    policy.target_group_no = group
    policy.receiver = receiver
    policy.timeout_minutes = timeout
    policy.updated_by = actor
    try:
        policy.save()
    except IntegrityError:
        raise ValidationError('该公司该交接类型及相同节点范围已有策略') from None
    return policy
