"""分阶段生产门禁：off 保持历史行为，warn 留审计，strict 服务端硬拦截。"""
from rest_framework.exceptions import ValidationError

from apps.ops.models import AuditLog, CompanySetting
from apps.ops.services import audit


def _mode(company, key):
    value = CompanySetting.get_map(company).get(key, 'off')
    if value not in ('off', 'warn', 'strict'):
        raise ValidationError({'detail': f'门禁配置 {key} 非法，仅支持 off/warn/strict'})
    return value


def _warn(company, user, message):
    audit(company, AuditLog.Type.OTHER, operator=user,
          after={'warning': True}, message=message)


def enforce_preproduction_gate(company, production, user):
    """在编菲事务内锁订单和当前工艺版本，再检查有效审批。"""
    mode = _mode(company, 'preproduction_gate_mode')
    if mode == 'off':
        return
    from apps.orders.models import Order
    from apps.preproduction.models import TechPackVersion
    from apps.preproduction.services import PreproductionNotApproved, assert_preproduction_approved

    if not production.order_id:
        message = f'床次【{production.bed_no}】未关联客户订单，无法核验产前审批'
        if mode == 'strict':
            raise ValidationError({'detail': message})
        _warn(company, user, f'产前门禁警告：{message}；本次按兼容模式允许编菲')
        return
    order = Order.objects.select_for_update().get(pk=production.order_id, company=company)
    # 与工艺编辑、审批共用版本行锁；真实 PostgreSQL 争用仍需单独验证。
    TechPackVersion.objects.select_for_update().filter(
        company=company, order=order, is_current=True).first()
    try:
        assert_preproduction_approved(order)
    except PreproductionNotApproved as exc:
        if mode == 'strict':
            raise ValidationError({'detail': str(exc)}) from None
        _warn(company, user,
              f'产前门禁警告：订单【{order.order_no}】{exc}；本次按兼容模式允许编菲')


def enforce_manufacturing_gate(company, ticket, user):
    """已有生产路线的菲票禁止再走旧 Record 报工，避免形成第二套数量事实。"""
    mode = _mode(company, 'manufacturing_gate_mode')
    if mode == 'off':
        return
    from apps.manufacturing.models import ProductionRoute
    from django.db.models import Q
    production = ticket.production
    route_query = Q(production=production)
    if production.order_id is not None:
        route_query |= Q(order_id=production.order_id)
    has_route = ProductionRoute.objects.filter(company=company, is_template=False).filter(
        route_query).exists()
    if not has_route:
        return
    message = (f'菲票【{ticket.bed_no}/{ticket.bundle_no}】已启用生产节点路线，'
               '请通过节点动作/质检/包装入库报工，禁止重复写入旧计件记录')
    if mode == 'strict':
        raise ValidationError({'detail': message})
    _warn(company, user, f'生产节点门禁警告：{message}；本次按兼容模式允许旧报工')
