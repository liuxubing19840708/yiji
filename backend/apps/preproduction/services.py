"""产前域唯一业务写入口。

API 与后续集成方均应调用本模块。权限、公司归属、状态机、审批门禁和审计统一在此处理；
get_active_approval / assert_preproduction_approved 不依赖 HTTP，可供生产域直接调用。
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import UserCompany
from apps.core.permissions import has_perm
from apps.orders.models import Order

from .models import (
    BriefingConfirmation, BriefingReceipt, BriefingTarget, CraftTemplate,
    PreproductionApproval, PreproductionAuditEvent, ReviewIssue, SampleRecord,
    SampleTask, TechPackVersion,
)

VIEW = 'preproduction.view'
MANAGE = 'preproduction.manage'
BRIEF = 'preproduction.brief'
APPROVE = 'preproduction.approve'


class PreproductionNotApproved(ValidationError):
    """订单没有满足当前工艺与产前闭环的审批。"""


def _permission(uc, key):
    if uc is None or uc.status != UserCompany.Status.ACTIVE or uc.is_suspended:
        raise PermissionDenied('需要有效的在职公司成员身份')
    if not has_perm(uc, key):
        raise PermissionDenied(f'缺少权限 {key}')


def _order(uc, order, key=MANAGE):
    _permission(uc, key)
    if not isinstance(order, Order) or order.is_deleted or order.company_id != uc.company_id:
        raise ValidationError('订单不存在或不属于当前公司')
    return order


def _pack(uc, pack, key=MANAGE, current=False):
    _permission(uc, key)
    if (not isinstance(pack, TechPackVersion) or pack.is_deleted
            or pack.company_id != uc.company_id or pack.order.is_deleted):
        raise ValidationError('工艺版本不存在或不属于当前公司')
    if current and not pack.is_current:
        raise ValidationError('只能操作订单的当前工艺版本')
    return pack


def _owned(uc, obj, pack_attr='tech_pack', key=MANAGE, current=True):
    pack = getattr(obj, pack_attr, None)
    if pack is None and hasattr(obj, 'task'):
        pack = obj.task.tech_pack
    if pack is None and hasattr(obj, 'target'):
        pack = obj.target.tech_pack
    if obj.is_deleted or pack is None:
        raise ValidationError('产前记录不存在')
    _pack(uc, pack, key=key, current=current)
    if obj.company_id != uc.company_id:
        raise ValidationError('产前记录不属于当前公司')
    return pack


def _decimal(value, label):
    try:
        value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f'{label}须为数字')
    if not value.is_finite():
        raise ValidationError(f'{label}须为有限数字')
    return format(value.normalize(), 'f')


def _measurements(raw):
    if not isinstance(raw, list):
        raise ValidationError('尺寸表须为列表')
    rows = []
    seen = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValidationError(f'尺寸表第{index}行须为对象')
        size = str(item.get('size') or '').strip()
        point = str(item.get('point') or '').strip()
        unit = str(item.get('unit') or 'cm').strip()
        if not size or not point or 'value' not in item:
            raise ValidationError(f'尺寸表第{index}行缺少尺码、部位或尺寸值')
        key = (size, point)
        if key in seen:
            raise ValidationError(f'尺寸表重复：{size}/{point}')
        seen.add(key)
        rows.append({
            'size': size, 'point': point, 'unit': unit,
            'value': _decimal(item['value'], f'尺寸表第{index}行尺寸值'),
            'tolerance_plus': _decimal(item.get('tolerance_plus', 0), '正公差'),
            'tolerance_minus': _decimal(item.get('tolerance_minus', 0), '负公差'),
        })
    return rows


def _paths(raw, label):
    if not isinstance(raw, list) or any(not isinstance(v, str) or not v.strip() for v in raw):
        raise ValidationError(f'{label}须为非空路径字符串数组')
    return [v.strip() for v in raw]


def _attachments(raw):
    if not isinstance(raw, list):
        raise ValidationError('工艺附件须为列表')
    result = []
    for item in raw:
        if not isinstance(item, dict) or not str(item.get('path') or '').strip():
            raise ValidationError('每个工艺附件须含 path')
        result.append({'name': str(item.get('name') or '').strip(),
                       'path': item['path'].strip(),
                       'note': str(item.get('note') or '').strip()})
    return result


def _tolerances(raw):
    if not isinstance(raw, dict):
        raise ValidationError('公差须为对象')
    result = {}
    for key, value in raw.items():
        if not str(key).strip():
            raise ValidationError('公差键不能为空')
        if isinstance(value, dict):
            result[str(key)] = {str(k): _decimal(v, '公差') for k, v in value.items()}
        else:
            result[str(key)] = _decimal(value, '公差')
    return result


def content_fingerprint(pack):
    """只哈希关键工艺内容；创建人、时间和状态不参与。"""
    payload = {
        'order_id': pack.order_id, 'style_id': pack.style_id,
        'pattern_no': pack.pattern_no, 'pattern_files': pack.pattern_files,
        'measurements': pack.measurements, 'tolerances': pack.tolerances,
        'attachments': pack.attachments, 'requirements': pack.requirements,
        'challenges': pack.challenges, 'common_mistakes': pack.common_mistakes,
        'quality_standards': pack.quality_standards,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _event(uc, action, obj, pack=None, before=None, after=None, message=''):
    user = uc.user
    return PreproductionAuditEvent.objects.create(
        company=uc.company, action=action, entity_type=obj._meta.model_name,
        entity_id=obj.pk, tech_pack=pack, actor=user,
        actor_name=user.real_name or user.username,
        before=before, after=after, message=message)


def _pack_data(data):
    return {
        'pattern_no': str(data.get('pattern_no') or '').strip(),
        'pattern_files': _paths(data.get('pattern_files', []), '纸样附件'),
        'measurements': _measurements(data.get('measurements', [])),
        'tolerances': _tolerances(data.get('tolerances', {})),
        'attachments': _attachments(data.get('attachments', [])),
        'requirements': str(data.get('requirements') or '').strip(),
        'challenges': str(data.get('challenges') or '').strip(),
        'common_mistakes': str(data.get('common_mistakes') or '').strip(),
        'quality_standards': str(data.get('quality_standards') or '').strip(),
    }


def _touch_pack(pack):
    """任何审批相关事实变化都推进修订号，防止撤销修改后旧审批复活。"""
    TechPackVersion.objects.filter(pk=pack.pk).update(
        revision=F('revision') + 1, updated_at=timezone.now())
    pack.refresh_from_db(fields=['revision', 'updated_at'])


def _template_values(kind, content):
    if kind not in CraftTemplate.Kind.values:
        raise ValidationError('不支持的工艺模板类型')
    if kind == CraftTemplate.Kind.MEASUREMENTS:
        rows = _measurements(content)
        if not rows:
            raise ValidationError('尺寸模板至少包含一行')
        return rows
    text = str(content or '').strip()
    if not text:
        raise ValidationError('工艺文本模板内容必填')
    if len(text) > 10000:
        raise ValidationError('工艺文本模板内容过长')
    return text


def list_craft_templates(uc, kind=''):
    _permission(uc, VIEW)
    from django.db.models import Q
    qs = CraftTemplate.objects.filter(Q(company=uc.company) | Q(company__isnull=True))
    if kind:
        if kind not in CraftTemplate.Kind.values:
            raise ValidationError('不支持的工艺模板类型')
        qs = qs.filter(kind=kind)
    return qs.select_related('created_by')


@transaction.atomic
def save_craft_template(uc, data, template=None):
    _permission(uc, MANAGE)
    kind = str(data.get('kind') or '').strip()
    name = str(data.get('name') or '').strip()
    if not name or len(name) > 80:
        raise ValidationError('模板名称必填且不能超过80字')
    values = _template_values(kind, data.get('content'))
    if template is not None:
        if (not isinstance(template, CraftTemplate) or template.is_deleted
                or template.company_id != uc.company_id):
            raise ValidationError('公司模板不存在或不属于当前公司')
        template.kind, template.name, template.content = kind, name, values
        try:
            template.save(update_fields=['kind', 'name', 'content', 'updated_at'])
        except IntegrityError:
            raise ValidationError('同名模板已存在，请更换名称')
        return template
    try:
        return CraftTemplate.objects.create(
            company=uc.company, kind=kind, name=name, content=values, created_by=uc.user)
    except IntegrityError:
        raise ValidationError('同名模板已存在，可直接更新原模板')


@transaction.atomic
def delete_craft_template(uc, template):
    _permission(uc, MANAGE)
    if (not isinstance(template, CraftTemplate) or template.is_deleted
            or template.company_id != uc.company_id):
        raise ValidationError('公司模板不存在或不属于当前公司')
    template.delete()


@transaction.atomic
def create_tech_pack(uc, order, data):
    """创建并切换订单当前工艺版本；旧版本保留历史但不再可审批。"""
    _order(uc, order)
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.style_id is None:
        raise ValidationError('订单须先关联款式')
    values = _pack_data(data)
    last = TechPackVersion.all_objects.filter(order=order).order_by('-version_no').first()
    raw_version = data.get('version_no')
    if raw_version in (None, ''):
        version_no = (last.version_no + 1) if last else 1
    else:
        try:
            version_no = int(raw_version)
        except (TypeError, ValueError, OverflowError):
            raise ValidationError('版本号须为正整数')
        if isinstance(raw_version, bool) or version_no <= 0 or str(raw_version).strip() != str(version_no):
            raise ValidationError('版本号须为正整数')
    TechPackVersion.objects.filter(order=order, is_current=True).update(is_current=False)
    if not values['pattern_no']:
        values['pattern_no'] = f'P{order.pk:06d}-V{version_no}'
    try:
        pack = TechPackVersion.objects.create(
            company=uc.company, order=order, style=order.style, version_no=version_no,
            content_hash='', created_by=uc.user, **values)
    except IntegrityError:
        raise ValidationError('该订单的版本号已存在，请更换版本号')
    pack.content_hash = content_fingerprint(pack)
    pack.save(update_fields=['content_hash', 'updated_at'])
    _event(uc, 'tech_pack.created', pack, pack, after={'version_no': pack.version_no,
                                                       'content_hash': pack.content_hash})
    return pack


@transaction.atomic
def update_tech_pack(uc, pack, data):
    _pack(uc, pack, current=True)
    pack = TechPackVersion.objects.select_for_update().get(pk=pack.pk)
    before = {'content_hash': pack.content_hash}
    values = _pack_data(data)
    for key, value in values.items():
        setattr(pack, key, value)
    pack.content_hash = content_fingerprint(pack)
    if pack.content_hash != before['content_hash']:
        pack.revision += 1
    pack.save()
    _event(uc, 'tech_pack.updated', pack, pack, before=before,
           after={'content_hash': pack.content_hash},
           message='关键工艺内容变化后原审批指纹不再匹配' if before != {'content_hash': pack.content_hash} else '')
    return pack


@transaction.atomic
def delete_tech_pack(uc, pack):
    _pack(uc, pack, current=True)
    pack.delete()
    _event(uc, 'tech_pack.deleted', pack, pack, before={'is_current': True},
           after={'is_deleted': True})
    return pack


@transaction.atomic
def create_sample_task(uc, pack, data):
    _pack(uc, pack, current=True)
    title = str(data.get('title') or '').strip()
    if not title:
        raise ValidationError('产前样任务名称必填')
    assignee = None
    if data.get('assignee_id'):
        assignee = _active_employee(uc, data['assignee_id'])
    task = SampleTask.objects.create(company=uc.company, tech_pack=pack, title=title,
                                     assignee=assignee, created_by=uc.user)
    _touch_pack(pack)
    _event(uc, 'sample_task.created', task, pack, after={'title': title, 'status': task.status})
    return task


@transaction.atomic
def record_sample_result(uc, task, result, note='', attachments=None):
    _owned(uc, task, pack_attr='tech_pack')
    task = SampleTask.objects.select_for_update().get(pk=task.pk)
    if result not in SampleRecord.Result.values:
        raise ValidationError('产前样结果仅支持 passed/failed/rework')
    if result != SampleRecord.Result.REWORK and not str(note).strip():
        raise ValidationError('合格/不合格检验须填写说明')
    record = SampleRecord.objects.create(
        company=uc.company, task=task, result=result, note=str(note).strip(),
        attachments=_paths(attachments or [], '样品附件'), operator=uc.user)
    before_status = task.status
    task.status = result
    task.save(update_fields=['status', 'updated_at'])
    _touch_pack(task.tech_pack)
    _event(uc, f'sample.{result}', record, task.tech_pack,
           before={'task_status': before_status}, after={'result': result, 'note': record.note})
    return record


@transaction.atomic
def delete_sample_task(uc, task):
    pack = _owned(uc, task)
    task.delete()
    _touch_pack(pack)
    _event(uc, 'sample_task.deleted', task, pack, after={'is_deleted': True})
    return task


@transaction.atomic
def create_review_issue(uc, pack, data):
    _pack(uc, pack, current=True)
    title = str(data.get('title') or '').strip()
    if not title:
        raise ValidationError('评审问题标题必填')
    issue = ReviewIssue.objects.create(company=uc.company, tech_pack=pack, title=title,
                                       detail=str(data.get('detail') or '').strip(), raised_by=uc.user)
    _touch_pack(pack)
    _event(uc, 'review_issue.created', issue, pack, after={'title': title, 'status': issue.status})
    return issue


@transaction.atomic
def rectify_review_issue(uc, issue, rectification):
    pack = _owned(uc, issue)
    if issue.status != ReviewIssue.Status.OPEN:
        raise ValidationError('仅待整改问题可提交整改')
    if not str(rectification).strip():
        raise ValidationError('整改说明必填')
    before = issue.status
    issue.status = ReviewIssue.Status.RECTIFIED
    issue.rectification = str(rectification).strip()
    issue.reviewed_by = None
    issue.reviewed_at = None
    issue.save()
    _touch_pack(pack)
    _event(uc, 'review_issue.rectified', issue, pack, before={'status': before},
           after={'status': issue.status, 'rectification': issue.rectification})
    return issue


@transaction.atomic
def review_issue(uc, issue, close, review_note=''):
    """复审：close=True 关闭；False 退回待整改。"""
    pack = _owned(uc, issue)
    if issue.status != ReviewIssue.Status.RECTIFIED:
        raise ValidationError('仅待复审问题可复审')
    before = issue.status
    issue.status = ReviewIssue.Status.CLOSED if close else ReviewIssue.Status.OPEN
    issue.review_note = str(review_note).strip()
    issue.reviewed_by = uc.user
    issue.reviewed_at = timezone.now()
    issue.save()
    _touch_pack(pack)
    _event(uc, 'review_issue.reviewed', issue, pack, before={'status': before},
           after={'status': issue.status, 'review_note': issue.review_note})
    return issue


@transaction.atomic
def resolve_review_issue(uc, issue, resolution):
    """轻量流程：处理人直接填写处理结果并解决，不经过独立复审。"""
    pack = _owned(uc, issue)
    if issue.status == ReviewIssue.Status.CLOSED:
        raise ValidationError('该问题已解决，请勿重复操作')
    if not str(resolution).strip():
        raise ValidationError('处理结果必填')
    before = issue.status
    issue.status = ReviewIssue.Status.CLOSED
    issue.rectification = str(resolution).strip()
    issue.review_note = ''
    issue.reviewed_by = uc.user
    issue.reviewed_at = timezone.now()
    issue.save()
    _touch_pack(pack)
    _event(uc, 'review_issue.resolved', issue, pack, before={'status': before},
           after={'status': issue.status, 'resolution': issue.rectification})
    return issue


@transaction.atomic
def reopen_review_issue(uc, issue, reason=''):
    """重开只改变当前闭环状态；原处理事实仍保留在审计事件中。"""
    pack = _owned(uc, issue)
    if issue.status != ReviewIssue.Status.CLOSED:
        raise ValidationError('仅已解决问题可重开')
    issue.status = ReviewIssue.Status.OPEN
    issue.review_note = str(reason).strip()
    issue.reviewed_by = None
    issue.reviewed_at = None
    issue.save()
    _touch_pack(pack)
    _event(uc, 'review_issue.reopened', issue, pack,
           before={'status': ReviewIssue.Status.CLOSED},
           after={'status': issue.status, 'reason': issue.review_note})
    return issue


@transaction.atomic
def delete_review_issue(uc, issue):
    pack = _owned(uc, issue)
    if issue.status == ReviewIssue.Status.CLOSED:
        raise ValidationError('已关闭评审问题不可删除')
    issue.delete()
    _touch_pack(pack)
    _event(uc, 'review_issue.deleted', issue, pack, after={'is_deleted': True})
    return issue


def _active_employee(uc, user_id):
    link = UserCompany.objects.filter(company=uc.company, user_id=user_id,
                                      status=UserCompany.Status.ACTIVE,
                                      is_suspended=False).select_related('user').first()
    if link is None:
        raise ValidationError('交底对象须为本公司在职且未停职员工')
    return link.user


@transaction.atomic
def add_briefing_target(uc, pack, employee_id, note=''):
    _pack(uc, pack, current=True)
    employee = _active_employee(uc, employee_id)
    if BriefingTarget.objects.filter(tech_pack=pack, employee=employee).exists():
        raise ValidationError('该员工已是本版本交底对象')
    try:
        target = BriefingTarget.objects.create(company=uc.company, tech_pack=pack, employee=employee,
                                               note=str(note).strip(), created_by=uc.user)
    except IntegrityError:
        raise ValidationError('该员工已是本版本交底对象')
    _event(uc, 'briefing_target.created', target, pack,
           after={'employee_id': employee.id, 'note': target.note})
    return target


@transaction.atomic
def sign_briefing(uc, target, remark=''):
    """签收无需 manage，但必须是在职本人；任何人均不能代签。"""
    # 签收只要求在职身份，不要求 manage/approve。
    if uc is None or uc.status != UserCompany.Status.ACTIVE or uc.is_suspended:
        raise PermissionDenied('需要有效的在职公司成员身份')
    if target.is_deleted or target.company_id != uc.company_id or not target.tech_pack.is_current:
        raise ValidationError('交底对象不存在或不是当前版本')
    if target.employee_id != uc.user_id:
        raise PermissionDenied('交底须由指定员工本人签收')
    if BriefingReceipt.objects.filter(target=target).exists():
        raise ValidationError('该交底已签收，请勿重复操作')
    receipt = BriefingReceipt.objects.create(company=uc.company, target=target,
                                             employee=uc.user, remark=str(remark).strip())
    _event(uc, 'briefing.signed', receipt, target.tech_pack,
           after={'employee_id': uc.user_id, 'signed_at': receipt.signed_at.isoformat()})
    return receipt


@transaction.atomic
def delete_briefing_target(uc, target):
    pack = _owned(uc, target)
    if hasattr(target, 'receipt'):
        raise ValidationError('已签收交底不可删除')
    target.delete()
    _event(uc, 'briefing_target.deleted', target, pack, after={'is_deleted': True})
    return target


@transaction.atomic
def confirm_briefing(uc, pack, note=''):
    """生产负责人确认当前工艺已完成班组交底，不等同于全员签收。"""
    _pack(uc, pack, key=BRIEF, current=True)
    pack = TechPackVersion.objects.select_for_update().get(pk=pack.pk)
    live_hash = content_fingerprint(pack)
    if pack.content_hash != live_hash:
        raise ValidationError('工艺内容指纹异常，请重新保存工艺资料')
    if BriefingConfirmation.objects.filter(tech_pack=pack, content_hash=live_hash).exists():
        raise ValidationError('当前工艺版本已完成交底确认，请勿重复操作')
    confirmation = BriefingConfirmation.objects.create(
        company=uc.company, tech_pack=pack, content_hash=live_hash,
        note=str(note).strip(), confirmed_by=uc.user)
    _touch_pack(pack)
    _event(uc, 'briefing.confirmed', confirmation, pack,
           after={'content_hash': live_hash, 'note': confirmation.note})
    return confirmation


def current_briefing_confirmation(pack):
    if pack is None:
        return None
    live_hash = content_fingerprint(pack)
    return BriefingConfirmation.objects.filter(
        tech_pack=pack, content_hash=live_hash).select_related('confirmed_by').first()


def readiness(pack):
    """返回审批缺口；也用于运行时复核既有审批，防止新增未闭环事项。"""
    problems = []
    if pack.is_deleted or not pack.is_current or pack.order.is_deleted:
        problems.append('工艺版本不是订单有效当前版本')
    if pack.style_id != pack.order.style_id:
        problems.append('订单款式已变化，须创建新工艺版本')
    if not pack.pattern_no or not pack.measurements or not pack.requirements:
        problems.append('纸样编号、结构化尺寸表和工艺要求须完整')
    latest_task = SampleTask.objects.filter(tech_pack=pack).order_by('-id').first()
    if latest_task is None:
        problems.append('尚未登记大货产前样')
    else:
        latest_record = latest_task.records.order_by('-occurred_at', '-id').first()
        if latest_record is None:
            problems.append('当前产前样尚未记录检验结果')
        elif latest_record.result != SampleRecord.Result.PASSED:
            problems.append('当前产前样须检验合格')
    if ReviewIssue.objects.filter(tech_pack=pack).exclude(status=ReviewIssue.Status.CLOSED).exists():
        problems.append('全部工艺问题须填写处理结果并解决')
    if current_briefing_confirmation(pack) is None:
        problems.append('生产负责人须确认当前工艺已完成班组交底')
    return problems


def approval_fingerprint(pack):
    """审批绑定完整产前事实快照；后续纠正问题也不会让旧审批自动恢复。"""
    latest_task = SampleTask.objects.filter(tech_pack=pack).order_by('-id').first()
    latest_record = (latest_task.records.order_by('-occurred_at', '-id').first()
                     if latest_task else None)
    briefing = current_briefing_confirmation(pack)
    payload = {
        'revision': pack.revision,
        'tech_content_hash': content_fingerprint(pack),
        'sample': None if latest_task is None else {
            'task_id': latest_task.id,
            'record_id': latest_record.id if latest_record else None,
            'result': latest_record.result if latest_record else None,
        },
        'issues': list(ReviewIssue.objects.filter(tech_pack=pack).order_by('id').values(
            'id', 'status', 'rectification', 'reviewed_at')),
        'briefing_confirmation_id': briefing.id if briefing else None,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def get_active_approval(order):
    """返回订单当前有效通过审批；无审批、版本/指纹/闭环变化均返回 None。"""
    if order is None or order.is_deleted:
        return None
    candidates = PreproductionApproval.objects.filter(
        order=order, status=PreproductionApproval.Status.APPROVED,
        tech_pack__is_current=True, tech_pack__is_deleted=False,
    ).select_related('tech_pack').order_by('-decided_at', '-id')
    for approval in candidates:
        live_hash = approval_fingerprint(approval.tech_pack)
        if approval.content_hash == live_hash and not readiness(approval.tech_pack):
            return approval
    return None


def assert_preproduction_approved(order):
    """生产/编菲门禁调用；成功返回审批，否则抛 PreproductionNotApproved。"""
    approval = get_active_approval(order)
    if approval is None:
        raise PreproductionNotApproved('订单尚无匹配当前工艺版本且产前闭环完成的有效审批')
    return approval


@transaction.atomic
def decide_approval(uc, pack, approved, reason=''):
    """审批绑定当前版本和当前指纹；重复通过/重复驳回均拒绝。"""
    _pack(uc, pack, key=APPROVE, current=True)
    pack = TechPackVersion.objects.select_for_update().get(pk=pack.pk)
    latest = PreproductionApproval.objects.filter(tech_pack=pack).first()
    desired = PreproductionApproval.Status.APPROVED if approved else PreproductionApproval.Status.REJECTED
    if approved and get_active_approval(pack.order) is not None:
        raise ValidationError('该工艺版本已有有效通过审批，请勿重复审批')
    if not approved and latest and latest.status == desired:
        raise ValidationError('该工艺版本已有相同驳回结果，请勿重复审批')
    if approved:
        problems = readiness(pack)
        if problems:
            raise ValidationError({'detail': '产前审批条件未满足', 'problems': problems})
    elif not str(reason).strip():
        raise ValidationError('驳回必须填写审批意见')
    # 重新审批或驳回已通过版本：旧审批明确失效，保留历史并释放部分唯一约束。
    stale = PreproductionApproval.objects.filter(
        tech_pack=pack, status=PreproductionApproval.Status.APPROVED).first()
    if stale:
        stale.status = PreproductionApproval.Status.INVALIDATED
        stale.save(update_fields=['status', 'updated_at'])
        _event(uc, 'approval.invalidated', stale, pack,
               before={'status': PreproductionApproval.Status.APPROVED,
                       'content_hash': stale.content_hash},
               after={'status': stale.status})
    approval = PreproductionApproval.objects.create(
        company=uc.company, order=pack.order, tech_pack=pack,
        content_hash=approval_fingerprint(pack), status=desired, reason=str(reason).strip(),
        decided_by=uc.user)
    _event(uc, 'approval.approved' if approved else 'approval.rejected', approval, pack,
           after={'status': desired, 'content_hash': approval.content_hash, 'reason': approval.reason})
    return approval
