"""节点检验单服务。

检验单只保存检验事实；所有数量变化都在确认的外层事务内委派给
manufacturing，确保检验状态与数量流水同生同灭。
"""
from hashlib import sha256
import json

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.models import Ticket
from apps.manufacturing import services as manufacturing
from apps.manufacturing.exceptions import IdempotencyError, NotFound, ValidationError
from apps.manufacturing.models import RouteNode
from .models import QualityInspection


def _positive_integer(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f'{field} 必须是非负整数')
    return value


def _ticket_matches_route(ticket, route):
    if route.is_template:
        return False
    if route.production_id:
        return ticket.production_id == route.production_id
    return bool(route.order_id and ticket.production.order_id == route.order_id)


def create_inspection(company, *, ticket, node, inspection_type, inspected, qualified=0,
                      rework=0, scrapped=0, rework_node=None, reinspection_of=None,
                      reason='', inspector, create_request_id=''):
    """创建待确认检验单，所有关联对象都按 company 重查。"""
    if inspector is None:
        raise ValidationError('检验人不能为空')
    values = {
        'inspected': inspected, 'qualified': qualified,
        'rework': rework, 'scrapped': scrapped,
    }
    for field, value in values.items():
        _positive_integer(value, field)
    if inspected <= 0:
        raise ValidationError('inspected 必须是正整数')
    if inspected != qualified + rework + scrapped:
        raise ValidationError('inspected 必须等于 qualified + rework + scrapped')
    if inspection_type not in QualityInspection.InspectionType.values:
        raise ValidationError('检验类型非法')
    if rework > 0 and not str(reason).strip():
        raise ValidationError('返修原因必填')
    if scrapped > 0 and not str(reason).strip():
        raise ValidationError('报废原因必填')

    try:
        ticket = Ticket.objects.select_related('production').get(pk=ticket.pk, company=company)
        node = (RouteNode.objects.select_related('stage', 'route')
                .get(pk=node.pk, company=company))
    except (Ticket.DoesNotExist, RouteNode.DoesNotExist):
        raise NotFound('菲票或检验节点不存在') from None
    route = node.route
    if not _ticket_matches_route(ticket, route):
        raise ValidationError('菲票不属于检验节点路线')

    is_reinspection = inspection_type == QualityInspection.InspectionType.REINSPECTION
    if is_reinspection:
        if reinspection_of is None:
            raise ValidationError('二次复检必须关联原检验单')
        try:
            previous = QualityInspection.objects.get(
                pk=reinspection_of.pk, company=company,
                status=QualityInspection.Status.CONFIRMED)
        except QualityInspection.DoesNotExist:
            raise ValidationError('原检验单不存在或尚未确认') from None
        if previous.rework <= 0:
            raise ValidationError('二次复检的原检验单必须包含返修数量')
        if previous.ticket_id != ticket.pk or previous.node_id != node.pk:
            raise ValidationError('二次复检必须与原检验单使用同一菲票和节点')
    else:
        if reinspection_of is not None:
            raise ValidationError('普通检验不能关联原检验单，请使用 reinspection 类型')
        if node.stage.stage_type != inspection_type:
            raise ValidationError('检验类型与路线节点类型不匹配')

    if rework:
        if rework_node is None:
            raise ValidationError('返修数量大于零时必须指定前序节点')
        try:
            rework_node = RouteNode.objects.get(pk=rework_node.pk, company=company, route=route)
        except RouteNode.DoesNotExist:
            raise ValidationError('返修目标节点不在同一路线') from None
        if rework_node.sequence >= node.sequence:
            raise ValidationError('返修目标必须是明确的前序节点')
    elif rework_node is not None:
        raise ValidationError('无返修数量时不能指定返修目标节点')

    if not isinstance(create_request_id, str) or len(create_request_id) > 64:
        raise ValidationError('create_request_id 最长 64 字符')
    create_request_id = create_request_id.strip()
    payload = {'ticket': ticket.pk, 'node': node.pk, 'inspection_type': inspection_type,
               **values, 'rework_node': rework_node.pk if rework else None,
               'reinspection_of': previous.pk if is_reinspection else None,
               'reason': str(reason).strip(), 'inspector': inspector.pk}
    fingerprint = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if create_request_id:
        existing = QualityInspection.objects.filter(
            company=company, create_request_id=create_request_id).first()
        if existing is not None:
            if existing.create_fingerprint != fingerprint:
                raise IdempotencyError('create_request_id 已用于不同检验请求')
            return existing
    try:
        with transaction.atomic():
            return QualityInspection.objects.create(
                company=company, ticket=ticket, route=route, node=node,
                inspection_type=inspection_type, reason=str(reason).strip(), inspector=inspector,
                rework_node=rework_node if rework else None,
                reinspection_of=previous if is_reinspection else None,
                create_request_id=create_request_id, create_fingerprint=fingerprint, **values)
    except IntegrityError:
        if create_request_id:
            existing = QualityInspection.objects.filter(
                company=company, create_request_id=create_request_id).first()
            if existing is not None and existing.create_fingerprint == fingerprint:
                return existing
            raise IdempotencyError('create_request_id 已用于不同检验请求') from None
        raise


def confirm_inspection(company, inspection, *, request_id, operator):
    """幂等确认；complete/return/scrap 与状态更新处于同一外层事务。"""
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 60:
        raise ValidationError('request_id 须为 1~60 字符的非空字符串')
    if operator is None:
        raise ValidationError('确认人不能为空')
    with transaction.atomic():
        try:
            inspection = (QualityInspection.objects.select_for_update(of=('self',))
                          .select_related('node', 'rework_node', 'ticket')
                          .get(pk=inspection.pk, company=company))
        except QualityInspection.DoesNotExist:
            raise NotFound('检验单不存在') from None
        if inspection.status == QualityInspection.Status.CONFIRMED:
            if inspection.request_id != request_id:
                raise IdempotencyError('检验单已使用其他 request_id 确认')
            return inspection

        source = f'quality_inspection:{inspection.pk}'
        kwargs = {'operator': operator, 'source': source, 'reason': inspection.reason}
        if inspection.qualified:
            manufacturing.complete_stage(
                company, inspection.node, inspection.ticket, inspection.qualified,
                request_id=f'{request_id}:q', **kwargs)
        if inspection.rework:
            # 基础键原样交给 return_stage，派生 :out/:in 后仍不超过 64 字符。
            manufacturing.return_stage(
                company, inspection.node, inspection.rework_node, inspection.ticket,
                inspection.rework, request_id=request_id, **kwargs)
        if inspection.scrapped:
            manufacturing.scrap_stage(
                company, inspection.node, inspection.ticket, inspection.scrapped,
                request_id=f'{request_id}:s', **kwargs)
        inspection.status = QualityInspection.Status.CONFIRMED
        inspection.request_id = request_id
        inspection.confirmed_by = operator
        inspection.confirmed_at = timezone.now()
        try:
            inspection.save(update_fields=[
                'status', 'request_id', 'confirmed_by', 'confirmed_at', 'updated_at'])
        except IntegrityError:
            raise IdempotencyError('request_id 已用于其他检验单') from None
        return inspection
