"""账号业务：创建公司、短信验证码。"""
import json
import random
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.utils import q2

from .models import Company, Post, SMSCode, User, UserCompany

SMS_TTL_MINUTES = 5
SMS_RESEND_INTERVAL = 60          # 秒
SMS_DAILY_LIMIT = 20              # 每天每号上限

# Plan A 二期：系统岗位改用细粒度权限点（语义等价换算，仅影响新创建公司）。
# 存量公司旧岗位数据不动——has_perm 的 legacy coarse key 兼容路径天然支持旧值。
# 换算依据 _LEGACY_COVERED 逐点展开（legacy 覆盖只含低危点，与旧默认可达面完全一致）：
#   'styles'     → styles.manage + styles.price（styles.delete 高危不送）
#   'production' → records.create + production.manage + trace.view + warehouse.manage
#                  + orders.manage（2026-10-20 P1 订单管理，legacy_key='production'，零回归）
#                  （production.delete/lock、records.revoke、scan.assist 等高危/后加点不送）
#   'working'    → working.view
#   'my_salary'  → my_salary.view
SYSTEM_POSTS = [
    {'name': '管理员', 'permissions': ['*'], 'is_admin': True},
    {'name': '裁床', 'permissions': [
        'styles.manage', 'styles.price',
        'records.create', 'production.manage', 'orders.manage',
        'trace.view', 'warehouse.manage',
        'working.view', 'my_salary.view']},
    {'name': '车间', 'permissions': ['my_salary.view', 'working.view']},
]


# 服装行业常见工序（2026-10-18 用户需求）：新公司创建时自动预置，单价 0 = 未上价
# （未上价工序扫菲被拦截，管理员在款式/订单工序里上价即可）。「整件」默认固定工序，
# 整件工厂只留它即可扫码直登。
COMMON_PROCESSES = [
    ('裁剪', False), ('车缝', False), ('锁边', False), ('烫工', False),
    ('钉扣', False), ('锁眼', False), ('洗水', False), ('质检', False),
    ('整件', True), ('包装', False),
]


@transaction.atomic
def create_company_with_owner(user: User, name: str, city: str = '') -> tuple:
    """创建公司：公司 + 3 个系统岗位 + 创建人管理员关系 + 默认设置。"""
    company = Company.objects.create(
        name=name, city=city, company_no=Company.next_company_no(), creator=user,
    )
    posts = Post.objects.bulk_create([
        Post(company=company, name=p['name'], permissions=p['permissions'],
             is_admin=p.get('is_admin', False), is_system=True)
        for p in SYSTEM_POSTS
    ])
    admin_post = next(p for p in posts if p.is_admin)
    UserCompany.objects.create(
        user=user, company=company, post=admin_post,
        status=UserCompany.Status.ACTIVE, hired_date=timezone.localdate(),
    )
    # 延迟导入避免循环依赖
    from apps.ops.models import CompanySetting
    CompanySetting.ensure_defaults(company)
    # 预置常见工序档案（sort 从 1 起按产线顺序；后续新建工序排在其后）
    from apps.catalog.models import Process
    Process.objects.bulk_create([
        Process(company=company, name=n, is_fixed=fixed, sort=i + 1)
        for i, (n, fixed) in enumerate(COMMON_PROCESSES)
    ])
    return company, admin_post


def send_sms_code(phone: str) -> None:
    """生成并“发送”验证码。当前为控制台桩：接入真实短信服务商时替换 _deliver。"""
    now = timezone.now()
    if SMSCode.objects.filter(
            phone=phone, created_at__gte=now - timedelta(seconds=SMS_RESEND_INTERVAL)
    ).exists():
        raise ValidationError('发送太频繁，请稍后再试')
    if SMSCode.objects.filter(
            phone=phone, created_at__gte=now - timedelta(days=1)
    ).count() >= SMS_DAILY_LIMIT:
        raise ValidationError('今日发送次数已达上限')
    code = f'{random.randint(0, 999999):06d}'
    SMSCode.objects.create(phone=phone, code=code, expires_at=now + timedelta(minutes=SMS_TTL_MINUTES))
    _deliver(phone, code)


def _deliver(phone: str, code: str) -> None:
    """短信发送桩：开发环境打印到控制台。"""
    print(f'[SMS] -> {phone}  验证码: {code}（{SMS_TTL_MINUTES} 分钟内有效）')


def verify_sms_code(phone: str, code: str) -> None:
    """校验并消费验证码；失败抛 ValidationError。"""
    now = timezone.now()
    sms = (SMSCode.objects
           .filter(phone=phone, used_at__isnull=True, expires_at__gt=now)
           .order_by('-created_at').first())
    if sms is None or sms.code != str(code):
        raise ValidationError('验证码错误或已过期')
    sms.used_at = now
    sms.save(update_fields=['used_at', 'updated_at'])


def normalize_fixed_salary(value) -> object:
    if value is None:
        return None
    return q2(value)


# ---- 微信小程序码（公司二维码「扫码加入」真实链路） ----

_wx_token_cache = {'token': '', 'expires_at': 0.0}


def get_wx_access_token() -> str:
    """获取稳定版 access_token（进程内缓存，提前 5 分钟过期）。"""
    import time
    import urllib.error
    import urllib.request
    from django.conf import settings
    from rest_framework.exceptions import ValidationError
    now = time.time()
    if _wx_token_cache['token'] and now < _wx_token_cache['expires_at']:
        return _wx_token_cache['token']
    url = (f'https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential'
           f'&appid={settings.WECHAT_APPID}&secret={settings.WECHAT_SECRET}')
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if not isinstance(data, dict):
            raise ValueError('微信凭证响应不是 JSON 对象')
        expires_in = int(data.get('expires_in', 7200))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValidationError(f'获取小程序凭证失败：网络错误 {exc}') from exc
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValidationError('获取小程序凭证失败：响应格式异常') from exc
    if 'access_token' not in data:
        raise ValidationError(f"获取小程序凭证失败：{data.get('errmsg', '未知错误')}")
    token = data['access_token']
    if not isinstance(token, str) or not token:
        raise ValidationError('获取小程序凭证失败：响应格式异常')
    # 所有字段校验完成后再整体更新，避免非法 expires_in 留下半份缓存。
    _wx_token_cache.update(token=token, expires_at=now + expires_in - 300)
    return token
