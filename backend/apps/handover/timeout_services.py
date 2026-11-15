"""交接超时计算与站内通知；时间口径全部由服务端确定。"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import UserCompany
from apps.core.permissions import has_perm
from apps.ops.models import Notification
from apps.ops.services import notify

from .models import Handover, HandoverPolicy
from .services import effective_policy

NOTIFICATION_CODE = 'handover_timeout'


def timeout_info(handover, *, now=None, policy=None):
    """返回当前策略下的超时事实；received/rejected/cancelled 不算待接收超时。"""
    now = now or timezone.now()
    policy = policy or getattr(handover, '_handover_policy', None) or effective_policy(
        handover.company, handover.kind, handover.source_node, handover.target_node)
    due_at = handover.created_at + timedelta(minutes=policy.timeout_minutes)
    overdue_seconds = (now - due_at).total_seconds()
    is_pending = handover.status == Handover.Status.PENDING
    return {
        'policy_mode': policy.mode,
        'timeout_minutes': policy.timeout_minutes,
        'due_at': due_at,
        'is_overdue': bool(is_pending and policy.mode != HandoverPolicy.Mode.OFF
                           and overdue_seconds >= 0),
        'overdue_minutes': max(0, int(overdue_seconds // 60)) if is_pending else 0,
    }


def attach_timeout_info(handovers, *, now=None):
    """为序列化器附加只读超时信息，避免列表逐行查询策略。"""
    rows = list(handovers)
    company_ids = {row.company_id for row in rows}
    policies = list(HandoverPolicy.objects.filter(company_id__in=company_ids))
    for handover in rows:
        candidates = [row for row in policies
                      if row.company_id == handover.company_id and row.kind == handover.kind
                      and row.source_node_id in (None, handover.source_node_id)
                      and row.target_node_id in (None, handover.target_node_id)]
        if candidates:
            priority = {(False, False): 0, (False, True): 1,
                        (True, False): 2, (True, True): 3}
            policy = max(candidates, key=lambda row: priority[(
                row.source_node_id is not None, row.target_node_id is not None)])
        else:
            policy = HandoverPolicy(company=handover.company, kind=handover.kind,
                                    mode=HandoverPolicy.Mode.OFF)
        handover._handover_policy = policy
        handover._handover_timeout = timeout_info(handover, now=now, policy=policy)
    return rows


def _recipient_ids(handover):
    memberships = UserCompany.objects.filter(
        company=handover.company, status=UserCompany.Status.ACTIVE,
        is_suspended=False, user__is_active=True,
    ).select_related('post', 'user')
    if handover.receiver_id is not None:
        memberships = memberships.filter(user_id=handover.receiver_id)
        permission = 'handover.receive'
    else:
        memberships = memberships.filter(group_no=handover.target_group_no)
        permission = 'handover.receive'
    recipients = [row.user_id for row in memberships if has_perm(row, permission)]
    if recipients:
        return recipients, False
    # 指定人离职/停职/无权限或目标组无人可接收时，管理岗必须看到异常出口。
    managers = UserCompany.objects.filter(
        company=handover.company, status=UserCompany.Status.ACTIVE,
        is_suspended=False, user__is_active=True,
    ).select_related('post', 'user')
    return [row.user_id for row in managers if has_perm(row, 'handover.manage')], True


def _notify_one(handover_id, *, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    with transaction.atomic():
        handover = (Handover.objects.select_for_update()
                    .select_related('company', 'source_node__stage', 'target_node__stage')
                    .filter(pk=handover_id, status=Handover.Status.PENDING).first())
        if handover is None:
            return 0, '已处理'
        info = timeout_info(handover, now=now)
        if not info['is_overdue']:
            return 0, '未超时或策略关闭'
        recipients, fallback = _recipient_ids(handover)
        if not recipients:
            return 0, '无有效接收人或管理岗'
        title = f'【{handover.handover_no}】交接超时待接收'
        existing = set(Notification.objects.filter(
            company=handover.company, code=NOTIFICATION_CODE, title=title,
            user_id__in=recipients, created_at__date=today,
        ).values_list('user_id', flat=True))
        new_recipients = [user_id for user_id in recipients if user_id not in existing]
        if not new_recipients:
            return 0, '当日已通知'
        source = handover.source_node.stage_name or handover.source_node.stage.name
        target = handover.target_node.stage_name or handover.target_node.stage.name
        message = (f'交接单 {handover.handover_no}：{source} → {target}，'
                   f'应交 {handover.total_quantity} 件，已超时 {info["overdue_minutes"]} 分钟。'
                   f'{"当前无有效接收人，请改派或处理。" if fallback else "请清点实物后接收，异常请拒收或联系管理人员。"}')
        sent = notify(handover.company, new_recipients, NOTIFICATION_CODE, title, message)
        return sent, f'已通知{sent}人'


def notify_handover_timeouts(*, now=None):
    """扫描全部公司的待接收单；每张单独立事务和逐接收人当日去重。"""
    now = now or timezone.now()
    pending_ids = list(Handover.objects.filter(
        status=Handover.Status.PENDING).order_by('id').values_list('id', flat=True))
    sent = 0
    skipped = 0
    for handover_id in pending_ids:
        count, _ = _notify_one(handover_id, now=now)
        if count:
            sent += count
        else:
            skipped += 1
    return {'scanned': len(pending_ids), 'sent': sent, 'skipped': skipped}
