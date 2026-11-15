# -*- coding: utf-8 -*-
"""同步与异步共用的受治理计划校验、ORM 执行和性能采集。"""
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied, ValidationError

from .catalog import can_use, get_dataset
from .plan_schema import validate_plan
from .query_engine import execute_plan


def execute_governed_plan(raw_plan, user_company, company, max_limit):
    raw_dataset = raw_plan.get('dataset') if isinstance(raw_plan, dict) else None
    if not isinstance(raw_dataset, str) or not raw_dataset:
        raise ValidationError({'plan': '必须指定 dataset'})
    dataset = get_dataset(raw_dataset)
    if dataset is None:
        raise ValidationError({'plan': f'不支持的数据集：{raw_dataset}'})
    if not can_use(user_company, dataset.permission):
        raise PermissionDenied(f'无权查询数据集：{dataset.label}')
    plan = validate_plan(raw_plan, dataset, user_company, max_limit=max_limit)
    with CaptureQueriesContext(connection) as captured:
        result = execute_plan(plan, dataset, company)
        if plan.get('comparison'):
            from .comparison_services import build_comparison, shifted_plan
            previous_plan, current_range, previous_range = shifted_plan(plan, dataset)
            previous = execute_plan(previous_plan, dataset, company)
            result.comparison = build_comparison(
                plan, result, previous, current_range, previous_range)
            result.scope.append(
                f'期间对比：本期 {current_range[0]}~{current_range[1]}；'
                f'对比期 {previous_range[0]}~{previous_range[1]}')
    sql_time_ms = round(sum(float(query['time']) for query in captured) * 1000)
    return dataset, plan, result, {
        'sql_count': len(captured),
        'sql_time_ms': sql_time_ms,
        'slow': sql_time_ms >= settings.AI_REPORT_SLOW_MS,
    }
