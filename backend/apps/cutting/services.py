"""裁片交接服务：待签收预占校验，签收时委托 manufacturing 原子转账。"""
from hashlib import sha256
import json

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import User, UserCompany
from apps.catalog.models import Ticket
from apps.core.permissions import has_perm
from apps.manufacturing.models import ProductionStage, RouteNode
from apps.manufacturing.services import assert_can_transition, get_balance, transfer_stage

from .exceptions import IdempotencyError, NotFound, QuantityError, ValidationError
from .models import CuttingHandover


def _positive(quantity):
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise QuantityError('quantity 必须是正整数')


def _request_id(request_id):
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 64:
        raise ValidationError('request_id 须为 1~64 字符的非空字符串')
    return request_id


def _fingerprint(ticket_id, source_node_id, target_node_id, quantity, receiver_id, remark):
    payload = {
        'ticket_id': ticket_id, 'source_node_id': source_node_id,
        'target_node_id': target_node_id, 'quantity': quantity,
        'receiver_id': receiver_id, 'remark': remark or '',
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return sha256(encoded).hexdigest()


def _idempotent(company, request_id, fingerprint):
    existing = (CuttingHandover.objects.select_for_update()
                .filter(company=company, request_id=request_id).first())
    if existing is None:
        return None
    if existing.request_fingerprint != fingerprint:
        raise IdempotencyError('request_id 已用于不同请求')
    return existing


def _active_membership(company, user, label='用户'):
    try:
        membership = UserCompany.objects.select_related('post', 'user').get(
            company=company, user=user, status=UserCompany.Status.ACTIVE)
    except UserCompany.DoesNotExist:
        raise NotFound(f'{label}不是本公司在职成员') from None
    if membership.is_suspended:
        raise ValidationError(f'{label}已停职')
    return membership


def _create(company, *, ticket_id, source_node_id, target_node_id, quantity, receiver_id,
            request_id, remark, created_by):
    fingerprint = _fingerprint(ticket_id, source_node_id, target_node_id, quantity,
                               receiver_id, remark)
    with transaction.atomic():
        existing = _idempotent(company, request_id, fingerprint)
        if existing is not None:
            return existing
        try:
            ticket = Ticket.objects.get(pk=ticket_id, company=company)
            source = (RouteNode.objects.select_related('stage', 'route')
                      .get(pk=source_node_id, company=company))
            target = RouteNode.objects.get(pk=target_node_id, company=company)
            receiver = User.objects.get(pk=receiver_id)
        except (Ticket.DoesNotExist, RouteNode.DoesNotExist, User.DoesNotExist):
            raise NotFound('菲票、节点或接收人不存在') from None
        if source.stage.stage_type != ProductionStage.StageType.CUTTING:
            raise ValidationError('来源节点必须是裁剪节点')
        if target.route_id != source.route_id:
            raise ValidationError('目标节点必须与来源节点属于同一路线')
        if target.sequence <= source.sequence:
            raise ValidationError('目标节点必须是来源的后续节点')
        _active_membership(company, receiver, '接收人')
        _active_membership(company, created_by, '下发人')

        # 锁路线后再读余额和预占；manufacturing 的写入也锁同一路线。
        balance = get_balance(company, source, ticket, lock=True)
        assert_can_transition(company, source, target, ticket, quantity)
        reserved = (CuttingHandover.objects.filter(
            company=company, ticket=ticket, source_node=source,
            status=CuttingHandover.Status.PENDING)
            .aggregate(total=Sum('quantity'))['total'] or 0)
        # 通用接口与旧接口共享同一来源余额，任一侧的 pending 都不能被另一侧绕过。
        from apps.handover.services import generic_pending_quantity
        reserved += generic_pending_quantity(company, source, ticket)
        if reserved + quantity > balance['transferable']:
            raise QuantityError('预占数量超过扣除其他待签收单后的可转量')
        return CuttingHandover.objects.create(
            company=company, ticket=ticket, source_node=source, target_node=target,
            quantity=quantity, receiver=receiver, request_id=request_id,
            request_fingerprint=fingerprint, remark=remark or '', created_by=created_by)


def create_handover(company, *, ticket_id, source_node_id, target_node_id, quantity,
                    receiver_id, request_id, remark='', created_by):
    """创建待签收单，不写任何数量流水；相同幂等号与载荷返回原单。"""
    _request_id(request_id)
    _positive(quantity)
    fingerprint = _fingerprint(ticket_id, source_node_id, target_node_id, quantity,
                               receiver_id, remark)
    try:
        return _create(company=company, ticket_id=ticket_id, source_node_id=source_node_id,
                       target_node_id=target_node_id, quantity=quantity, receiver_id=receiver_id,
                       request_id=request_id, remark=remark, created_by=created_by)
    except IntegrityError:
        # 并发建单由公司+幂等号唯一约束收口，失败事务外重读原单。
        existing = CuttingHandover.objects.filter(company=company, request_id=request_id).first()
        if existing is None:
            raise
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyError('request_id 已用于不同请求') from None
        return existing


def _handover_for_update(company, handover_id):
    try:
        return (CuttingHandover.objects.select_for_update(of=('self',))
                .select_related('source_node', 'target_node', 'ticket', 'receiver')
                .get(pk=handover_id, company=company))
    except CuttingHandover.DoesNotExist:
        raise NotFound('裁片下发单不存在') from None


def _authorize_signer(company, actor, receiver):
    membership = _active_membership(company, actor, '签收人')
    if actor.pk != receiver.pk and not has_perm(membership, 'cutting.manage'):
        raise PermissionDenied('仅指定接收人本人或具有裁片交接管理权限的人员可签收')


def sign_handover(company, handover_id, *, actor):
    """本人优先签收；显式 cutting.manage 可代签。重复签收不重复流水。"""
    with transaction.atomic():
        # 先锁路线，再锁交接单，与建单的锁顺序一致。
        candidate = CuttingHandover.objects.filter(pk=handover_id, company=company).first()
        if candidate is None:
            raise NotFound('裁片下发单不存在')
        get_balance(company, candidate.source_node, candidate.ticket, lock=True)
        handover = _handover_for_update(company, handover_id)
        _authorize_signer(company, actor, handover.receiver)
        if handover.status == CuttingHandover.Status.SIGNED:
            return handover
        if handover.status != CuttingHandover.Status.PENDING:
            raise ValidationError('仅待签收单可签收')
        outgoing, incoming = transfer_stage(
            company, handover.source_node, handover.target_node, handover.ticket,
            handover.quantity, operator=actor, request_id=f'cutting-handover-{handover.pk}',
            source=f'cutting_handover:{handover.pk}', reason=handover.remark,
            _confirmed_handover=True)
        handover.status = CuttingHandover.Status.SIGNED
        handover.signed_by = actor
        handover.signed_at = timezone.now()
        handover.outgoing_ledger = outgoing
        handover.incoming_ledger = incoming
        handover.save(update_fields=['status', 'signed_by', 'signed_at', 'outgoing_ledger',
                                    'incoming_ledger', 'updated_at'])
        return handover


def cancel_handover(company, handover_id, *, actor):
    """只取消无流水的待签收单；已取消重试返回原事实。"""
    _active_membership(company, actor, '取消人')
    with transaction.atomic():
        handover = _handover_for_update(company, handover_id)
        if handover.status == CuttingHandover.Status.CANCELLED:
            return handover
        if handover.status != CuttingHandover.Status.PENDING:
            raise ValidationError('仅待签收单可取消')
        if handover.outgoing_ledger_id or handover.incoming_ledger_id:
            raise ValidationError('已产生数量流水的下发单不能取消')
        handover.status = CuttingHandover.Status.CANCELLED
        handover.cancelled_by = actor
        handover.cancelled_at = timezone.now()
        handover.save(update_fields=['status', 'cancelled_by', 'cancelled_at', 'updated_at'])
        return handover
