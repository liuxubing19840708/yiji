# -*- coding: utf-8 -*-
"""QueryPlan 的严格结构与权限校验；输入和输出均保持 JSON 可序列化。"""
import json
import re
from datetime import date

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.permissions import has_perm

from .catalog import can_use

PLAN_KEYS = {
    'dataset', 'metrics', 'dimensions', 'filters', 'order_by', 'limit',
    'include_deleted', 'presentation', 'as_of', 'comparison',
}


def invalid(message):
    raise ValidationError({'plan': message})


def _list(value, name, maximum, allow_empty=True):
    if not isinstance(value, list):
        invalid(f'{name} 必须是数组')
    if len(value) > maximum:
        invalid(f'{name} 最多 {maximum} 项')
    if not allow_empty and not value:
        invalid(f'{name} 至少选择一项')
    return value


def _parse_date(value):
    if not isinstance(value, str):
        invalid('日期必须使用 YYYY-MM-DD 字符串')
    try:
        return date.fromisoformat(value)
    except ValueError:
        invalid(f'无效日期：{value}；应使用 YYYY-MM-DD')


def _as_of(raw, dataset):
    if raw is None:
        invalid('as_of 必须使用 YYYY-MM-DD 字符串')
    if not dataset.supports_as_of:
        invalid(f'数据集 {dataset.label} 不支持 as_of 历史库存')
    if not isinstance(raw, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
        invalid('as_of 必须使用 YYYY-MM-DD 字符串')
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        invalid(f'无效 as_of 日期：{raw}')
    if parsed > timezone.localdate():
        invalid('as_of 不能晚于今天')
    return raw


def _integer(value):
    if isinstance(value, bool):
        invalid('整数值不能是布尔值')
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    invalid(f'需要整数，实际为：{value}')


def _text(value):
    if isinstance(value, bool) or isinstance(value, (dict, list)) or value is None:
        invalid('文本筛选值格式不正确')
    text = str(value).strip()
    if not text:
        invalid('文本筛选值不能为空')
    if len(text) > 128:
        invalid('单个筛选值不能超过 128 字')
    return text


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ('true', '1', '是'):
            return True
        if normalized in ('false', '0', '否'):
            return False
    invalid('布尔筛选值必须是 true/false')


def _scalar(field, value):
    if field.type in ('integer', 'group'):
        normalized = _integer(value)
    elif field.type in ('date', 'datetime'):
        normalized = _parse_date(value).isoformat()
    elif field.type == 'boolean':
        normalized = _boolean(value)
    else:
        normalized = _text(value)
    if field.choices and normalized not in {choice[0] for choice in field.choices}:
        invalid(f'字段 {field.label} 的筛选值不在允许选项中')
    return normalized


def _normalize_filter(raw, dataset, user_company, full_data):
    if not isinstance(raw, dict) or set(raw) - {'field', 'operator', 'value'}:
        invalid('筛选项只能包含 field/operator/value')
    key = raw.get('field')
    if not isinstance(key, str):
        invalid('筛选字段 field 必须是字符串')
    field = dataset.field_map.get(key)
    if not field or not field.can_filter:
        invalid(f'不支持筛选字段：{key}')
    if not can_use(user_company, field.permission, full_data):
        raise PermissionDenied(f'无权使用筛选字段：{field.label}')
    operator = raw.get('operator', 'eq')
    if operator not in field.operators:
        invalid(f'字段 {field.label} 不支持操作符 {operator}')
    value = raw.get('value')
    if operator == 'isnull':
        if not isinstance(value, bool):
            invalid('isnull 的 value 必须是布尔值')
    elif operator == 'between':
        values = _list(value, 'between 值', 2, allow_empty=False)
        if len(values) != 2:
            invalid('between 必须提供开始和结束两个日期')
        start, end = (_parse_date(item) for item in values)
        if start > end:
            invalid('开始日期不能晚于结束日期')
        value = [start.isoformat(), end.isoformat()]
    elif operator == 'in':
        values = _list(value, 'in 值', 50, allow_empty=False)
        value = list(dict.fromkeys(_scalar(field, item) for item in values))
    else:
        value = _scalar(field, value)
    return {'field': field.key, 'operator': operator, 'value': value}


def validate_plan(raw, dataset, user_company, *, max_limit):
    """校验未知键、字段、聚合、筛选、排序、行数和数据范围。"""
    if not isinstance(raw, dict):
        invalid('plan 必须是 JSON 对象')
    try:
        serialized = json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        invalid('plan 必须是合法 JSON')
    if len(serialized) > 20000:
        invalid('plan 不能超过 20000 个字符')
    unknown = set(raw) - PLAN_KEYS
    if unknown:
        invalid(f'包含不支持的参数：{", ".join(sorted(unknown))}')
    if raw.get('dataset') != dataset.key:
        invalid('dataset 与执行数据集不一致')
    as_of = _as_of(raw['as_of'], dataset) if 'as_of' in raw else None

    full_data = has_perm(user_company, 'ai.full_data')
    dimensions = _list(raw.get('dimensions', []), 'dimensions', 4)
    if any(not isinstance(key, str) for key in dimensions):
        invalid('dimensions 必须全部是字符串')
    if len(set(dimensions)) != len(dimensions):
        invalid('dimensions 不能重复')
    for key in dimensions:
        field = dataset.field_map.get(key)
        if not field or not field.can_dimension:
            invalid(f'不支持分组字段：{key}')
        if not can_use(user_company, field.permission, full_data):
            raise PermissionDenied(f'无权使用分组字段：{field.label}')

    raw_metrics = _list(raw.get('metrics'), 'metrics', 6, allow_empty=False)
    metrics, seen_metrics = [], set()
    for item in raw_metrics:
        if isinstance(item, str):
            item = {'key': item}
        if not isinstance(item, dict) or set(item) - {'key', 'aggregation'}:
            invalid('指标只能包含 key/aggregation')
        key = item.get('key')
        if not isinstance(key, str):
            invalid('指标 key 必须是字符串')
        metric = dataset.metric_map.get(key)
        if not metric:
            invalid(f'不支持的指标：{key}')
        if key in seen_metrics:
            invalid(f'指标不能重复：{key}')
        seen_metrics.add(key)
        if not can_use(user_company, metric.permission, full_data):
            raise PermissionDenied(f'无权使用指标：{metric.label}')
        aggregation = item.get('aggregation', metric.default_aggregation)
        if aggregation not in metric.aggregations:
            invalid(f'指标 {metric.label} 不支持聚合方式 {aggregation}')
        metrics.append({'key': key, 'aggregation': aggregation})

    if (dataset.unit_dimension and dataset.unit_dimension not in dimensions
            and any(dataset.metric_map[item['key']].requires_unit for item in metrics)):
        invalid(f'数量指标必须按{dataset.field_map[dataset.unit_dimension].label}分组，避免不同单位混加')

    filters = [
        _normalize_filter(item, dataset, user_company, full_data)
        for item in _list(raw.get('filters', []), 'filters', 10)
    ]

    selected = set(dimensions) | seen_metrics
    order_by = []
    for item in _list(raw.get('order_by', []), 'order_by', 4):
        if not isinstance(item, dict) or set(item) - {'field', 'direction'}:
            invalid('排序项只能包含 field/direction')
        field = item.get('field')
        if not isinstance(field, str):
            invalid('排序字段 field 必须是字符串')
        if field not in selected:
            invalid(f'排序字段必须已选为分组或指标：{field}')
        direction = item.get('direction', 'desc')
        if direction not in ('asc', 'desc'):
            invalid('排序方向只能是 asc/desc')
        order_by.append({'field': field, 'direction': direction})

    limit = raw.get('limit', 100)
    if isinstance(limit, bool) or not isinstance(limit, int):
        invalid('limit 必须是整数')
    if not 1 <= limit <= max_limit:
        invalid(f'limit 必须在 1~{max_limit} 之间')
    include_deleted = raw.get('include_deleted', False)
    if not isinstance(include_deleted, bool):
        invalid('include_deleted 必须是布尔值')
    if include_deleted and not dataset.supports_include_deleted:
        invalid(f'数据集 {dataset.label} 不支持包含作废或撤销流水')
    if include_deleted and not full_data:
        raise PermissionDenied('仅全域数据权限可查询已删除或已撤销数据')
    presentation = raw.get('presentation', 'table')
    if presentation not in ('table', 'bar', 'line'):
        invalid('presentation 仅支持 table/bar/line')
    if presentation != 'table':
        if not dimensions:
            invalid('图表至少需要一个分组维度；全公司单一汇总请使用表格')
        if len(metrics) != 1:
            invalid('图表每次只展示一个指标，避免不同单位共用数值轴')
        if presentation == 'line' and dataset.field_map[dimensions[0]].type != 'date':
            invalid('折线图用于时间趋势，第一个分组维度必须是日、周或月')

    comparison = raw.get('comparison')
    if comparison is not None:
        if comparison not in ('previous', 'year_ago'):
            invalid('comparison 仅支持 previous（上一期）或 year_ago（去年同期）')
        if as_of is not None:
            invalid('历史库存 as_of 不支持期间对比')
        ranges = [item for item in filters
                  if item['field'] == dataset.time_field and item['operator'] == 'between']
        if len(ranges) != 1:
            invalid('期间对比必须对数据集主时间字段设置一个 between 日期范围')
        if any(item['field'] == dataset.time_field and item is not ranges[0]
               for item in filters):
            invalid('期间对比不允许额外的主时间筛选')

    normalized = {
        'dataset': dataset.key,
        'metrics': metrics,
        'dimensions': list(dimensions),
        'filters': filters,
        'order_by': order_by,
        'limit': limit,
        'include_deleted': include_deleted,
        'presentation': presentation,
    }
    if as_of is not None:
        normalized['as_of'] = as_of
    if comparison is not None:
        normalized['comparison'] = comparison
    return normalized
