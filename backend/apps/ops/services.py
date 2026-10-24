"""运营支撑服务：通知 + 操作日志。

accounts.services 通过
    from apps.ops.services import notify
    from apps.ops.models import CompanySetting
延迟导入调用，签名保持稳定。
"""
from apps.accounts.models import Company, User
from apps.ops.models import AuditLog, Notification


def notify(company: Company, user_ids, code: str, title: str, message: str) -> int:
    """批量创建站内通知。

    message 必须由调用方插值完成后再传入（禁止存模板，见文档业务规则）。
    """
    ids = {uid for uid in (user_ids or []) if uid}
    if not ids:
        return 0
    users = User.objects.filter(id__in=ids)
    Notification.objects.bulk_create([
        Notification(company=company, user=user, code=code, title=title, message=message)
        for user in users
    ])
    return len(users)


def audit(company: Company, log_type: str, *, operator=None, target_user=None,
          bed_no: str = '', style_no: str = '', bundle_no: str = '',
          before=None, after=None, message: str = '') -> AuditLog:
    """写一条操作日志（敏感操作必落，message 已插值）。"""
    return AuditLog.objects.create(
        company=company, type=log_type,
        operator=operator or None, target_user=target_user or None,
        bed_no=bed_no or '', style_no=style_no or '', bundle_no=bundle_no or '',
        before=before, after=after, message=message or '')
