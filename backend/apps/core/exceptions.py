"""统一响应格式：业务异常包装为 {code, message}。

字段级报错（2026-10-21 用户 8 项需求 #4）：DRF 的 {'color': ['该字段是必填项。']}
类 400 在保留原 field 键的前提下补 message='颜色：该字段是必填项。'，
字段中文映射表见 FIELD_LABELS（覆盖 orders/catalog 常用字段）。
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions
from rest_framework.views import exception_handler

# 字段中文 label 映射（未命中时回退原字段名）
FIELD_LABELS = {
    'color': '颜色', 'size': '尺码', 'qty': '数量', 'style': '款式',
    'order_no': '订单号', 'delivery_date': '交期', 'customer': '客户',
    'remark': '备注', 'name': '名称', 'price': '工价', 'process': '工序',
    'process_id': '工序', 'process_items': '订单工序', 'items': '订单明细',
    'style_no': '款号', 'style_name': '款式名称', 'bed_no': '床次号',
    'customer_no': '客户编号', 'group': '客户分组', 'image': '图片',
    'images': '备注图片', 'video': '视频', 'craft_note': '工艺说明',
    'craft_images': '工艺图片', 'total_qty': '裁货数量', 'plan_qty': '计划裁货数',
    'fabric': '布料', 'piece_no': '件号', 'bundle_no': '扎号', 'ticket_ids': '菲票',
    'order_id': '客户订单', 'phone': '手机号', 'password': '密码',
    'username': '账号', 'sort': '排序', 'allow_skip': '允许跳序',
    'bundle_limit': '每扎上限', 'start_bundle_no': '起始扎号', 'status': '状态',
    'order_type': '订单类型', 'default_price': '默认单价', 'is_fixed': '固定工序',
}


def _localize_field_message(data):
    """字段级错误 → '字段中文：首个错误'；结构不规整（嵌套/混合）时返回 None 不动。

    只处理 {'field': ['错误串', ...]} 的纯扁平结构（DRF 校验 400 的标准形态），
    保证不碰已有 message/detail 键的响应与嵌套对象。
    """
    if not isinstance(data, dict):
        return None
    parts = []
    for key, value in data.items():
        if key in ('message', 'detail'):
            return None
        if isinstance(value, list) and value and all(isinstance(e, str) for e in value):
            label = FIELD_LABELS.get(key, key)
            parts.append(f'{label}：{value[0]}')
        else:
            return None
    return '；'.join(parts) if parts else None


def _as_drf_validation_error(exc):
    """把 Django 的 ValidationError 转成 DRF 的。

    DRF 默认异常处理只转换 Http404 / PermissionDenied，不认 Django 的
    ValidationError（视图代码里直接 raise 会变成 500），这里统一转换。
    """
    if hasattr(exc, 'error_dict'):
        detail = {}
        for field, errs in exc.error_dict.items():
            messages = []
            for err in errs:
                messages.extend(err.messages)
            detail[field] = messages
    else:
        detail = list(exc.messages)
    return exceptions.ValidationError(detail)


def api_exception_handler(exc, context):
    """保留 DRF 默认处理，把 error/detail 字段统一为 message。

    校验类 400（字段 → 错误列表的扁平 dict）额外补一条带中文字段名的
    message，字段键原样保留（前端逐字段标红 + 顶部提示两用）。
    """
    if isinstance(exc, DjangoValidationError):
        exc = _as_drf_validation_error(exc)
    response = exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict):
        if 'detail' in response.data:
            response.data['message'] = response.data.pop('detail')
        elif 'error' in response.data:
            response.data['message'] = response.data.pop('error')
        elif 'message' not in response.data:
            localized = _localize_field_message(response.data)
            if localized:
                response.data['message'] = localized
    return response
