"""受治理计划的确定性期间平移与对比结果合并。"""
import copy
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from .query_engine import _json_value, date_from_iso


def shifted_plan(plan, dataset):
    mode = plan['comparison']
    previous = copy.deepcopy(plan)
    previous.pop('comparison', None)
    for item in previous['filters']:
        if item['field'] != dataset.time_field or item['operator'] != 'between':
            continue
        start, end = map(date_from_iso, item['value'])
        if mode == 'previous':
            days = (end - start).days + 1
            old_start, old_end = start - timedelta(days=days), end - timedelta(days=days)
        else:
            old_start = _year_ago(start)
            old_end = _year_ago(end)
        item['value'] = [old_start.isoformat(), old_end.isoformat()]
        return previous, [start.isoformat(), end.isoformat()], item['value']
    raise ValueError('已校验的对比计划缺少主时间范围')


def _year_ago(value):
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def build_comparison(plan, current, previous, current_range, previous_range):
    """按维度合并两期；截断时明确禁止把不完整分组解释为全量对比。"""
    if current.truncated or previous.truncated:
        return {
            'mode': plan['comparison'], 'current_range': current_range,
            'previous_range': previous_range, 'available': False,
            'message': '本期或上期分组结果已截断，请缩小范围或提高行数上限后对比。',
            'rows': [],
        }
    dimensions = plan['dimensions']
    metrics = [item['key'] for item in plan['metrics']]
    current_map = {tuple(row.get(key) for key in dimensions): row for row in current.rows}
    previous_map = {tuple(row.get(key) for key in dimensions): row for row in previous.rows}
    keys = list(current_map)
    keys.extend(key for key in previous_map if key not in current_map)
    rows = []
    for key in keys:
        now, old = current_map.get(key, {}), previous_map.get(key, {})
        values = {}
        for metric in metrics:
            a, b = _decimal(now.get(metric)), _decimal(old.get(metric))
            delta = None if a is None or b is None else a - b
            rate = None if not b or delta is None else delta / abs(b)
            values[metric] = {
                'current': _json_value(now.get(metric)),
                'previous': _json_value(old.get(metric)),
                'delta': _json_value(delta), 'change_rate': _json_value(rate),
            }
        rows.append({'dimensions': [_json_value(value) for value in key], 'metrics': values})
    return {
        'mode': plan['comparison'], 'current_range': current_range,
        'previous_range': previous_range, 'available': True,
        'message': '变化率=(本期−上期)/|上期|；上期为 0 或任一期为空时变化率为空。',
        'rows': rows,
    }
