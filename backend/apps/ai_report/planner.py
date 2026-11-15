# -*- coding: utf-8 -*-
"""自然语言 → 候选 QueryPlan。

本地规则只接管完全匹配的通用问法，避免忽略人名、款号等实体条件；复杂问法
交给 GLM。模型只看到当前岗位可用的目录，不看到业务结果。这里返回的仍是
候选计划，必须由 plan_schema.validate_plan 和 PlanExecutionMixin 再次校验执行。
"""
import copy
import json
import re

from django.conf import settings
from django.utils import timezone

from . import services
from .catalog import CATALOG_VERSION, catalog_for, get_dataset


def _plan(dataset_key, metric_keys, dimensions=(), filters=None, time_key=None,
          time_field=None):
    dataset = get_dataset(dataset_key)
    filters = list(filters or [])
    if time_key:
        start, end, _label = services.resolve_range(time_key)
        filters.append({
            'field': time_field or dataset.time_field,
            'operator': 'between',
            'value': [start.isoformat(), end.isoformat()],
        })
    metrics = [{
        'key': key,
        'aggregation': dataset.metric_map[key].default_aggregation,
    } for key in metric_keys]
    return {
        'dataset': dataset_key,
        'metrics': metrics,
        'dimensions': list(dimensions),
        'filters': filters,
        'order_by': ([{'field': metric_keys[0], 'direction': 'desc'}]
                     if dimensions else []),
        'limit': 100,
        'include_deleted': False,
        'presentation': 'table',
    }


_TIME_PREFIXES = (
    ('before_yesterday', ('前天',)),
    ('yesterday', ('昨天', '昨日')),
    ('last_week', ('上周', '上个星期', '上一周')),
    ('last_month', ('上月', '上个月')),
    ('this_week', ('本周', '这周', '这一周')),
    ('this_month', ('本月', '这个月')),
    ('last_year', ('去年', '上一年')),
    ('this_year', ('今年', '本年度')),
    ('today', ('今天', '今日')),
)


def _strip_time_prefix(question):
    for key, words in _TIME_PREFIXES:
        for word in words:
            if question.startswith(word):
                return key, question[len(word):]
    return None, question


def local_plan(question):
    """少量零费用通用问法；不做实体抽取，不做模糊猜测。"""
    compact = re.sub(r'[\s，,。？?！!：:；;]', '', question or '')
    time_key, body = _strip_time_prefix(compact)

    piecework = {
        '登记多少件': ((), ('registered_qty',)),
        '产量多少': ((), ('registered_qty',)),
        '完成了多少件': ((), ('registered_qty',)),
        '各工序登记多少件': (('process_name',), ('registered_qty',)),
        '按工序统计登记件数': (('process_name',), ('registered_qty',)),
        '各工序产量': (('process_name',), ('registered_qty',)),
        '各员工登记多少件': (('employee_name',), ('registered_qty',)),
        '按员工统计登记件数': (('employee_name',), ('registered_qty',)),
        '各员工的计件工资': (('employee_name',), ('estimated_wage',)),
        '各员工的计件工资是多少': (('employee_name',), ('estimated_wage',)),
        '各员工计件工资': (('employee_name',), ('estimated_wage',)),
        '各员工计件工资是多少': (('employee_name',), ('estimated_wage',)),
        '按员工统计计件工资': (('employee_name',), ('estimated_wage',)),
        '各生产组登记多少件': (('group_no',), ('registered_qty',)),
        '各颜色登记多少件': (('color',), ('registered_qty',)),
        '各尺码登记多少件': (('size',), ('registered_qty',)),
    }
    if body in piecework:
        dimensions, metrics = piecework[body]
        return _plan('piecework_records', metrics, dimensions,
                     time_key=time_key or 'today')

    if body in ('各客户订单数量和登记量', '按客户统计订单数量和登记量'):
        return _plan('orders', ('order_qty', 'registered_qty'), ('customer_name',),
                     time_key=time_key)
    if body in ('各客户订单数', '按客户统计订单数'):
        return _plan('orders', ('order_count',), ('customer_name',),
                     time_key=time_key)
    if body in ('各款号订单数量和已裁数量', '按款号统计订单数量和已裁数量'):
        return _plan('orders', ('order_qty', 'cut_qty'), ('style_no',),
                     time_key=time_key)
    if body in ('各状态订单数', '按状态统计订单数'):
        return _plan('orders', ('order_count',), ('status',), time_key=time_key)
    if body in ('生产中订单数量', '生产中的订单数量'):
        return _plan(
            'orders', ('order_qty',),
            filters=[{'field': 'status', 'operator': 'eq', 'value': 'producing'}],
            time_key=time_key)

    if body in ('实际出货多少', '实际出货多少件', '实际出货数量'):
        return _plan('shipments', ('shipped_qty',), time_key=time_key)
    if body in ('各客户实际出货数量', '按客户统计实际出货数量'):
        return _plan('shipments', ('shipped_qty',), ('customer_name',),
                     time_key=time_key)
    if body in ('各状态出货单数量', '按状态统计出货单数量'):
        return _plan('shipments', ('shipment_count', 'shipped_qty'), ('status',),
                     time_key=time_key)

    if not time_key and body in ('当前库存多少', '当前库存数量', '各单位当前库存'):
        return _plan('warehouse_lots', ('current_stock_qty',), ('unit',))
    if not time_key and body in ('各批次当前库存', '各批次库存数量'):
        return _plan('warehouse_lots', ('current_stock_qty',), ('lot_no', 'unit'))
    if not time_key and body in ('各面料当前库存', '按面料统计当前库存'):
        return _plan('warehouse_lots', ('current_stock_qty',), ('fabric_name', 'unit'))

    if not time_key and body in ('各物料主单位库存', '按物料统计主单位库存'):
        return _plan(
            'material_stock', ('current_stock_qty', 'uncovered_flow_count'),
            ('material_code', 'material_name', 'main_unit'))

    if body in ('各供应商采购数量', '按供应商统计采购数量'):
        return _plan('purchase_items', ('purchase_qty',), ('supplier_name', 'unit'),
                     time_key=time_key)
    if body in ('各物料待到料量', '各物料待收数量'):
        return _plan('purchase_items', ('pending_receipt_qty',), ('material_name', 'unit'),
                     time_key=time_key)
    if body in ('可换算采购金额和待补参数行数', '采购金额待补参数情况'):
        return _plan(
            'purchase_items', ('convertible_purchase_amount', 'amount_pending_line_count'),
            time_key=time_key)
    if body in ('经营现金流', '统一经营现金流', '现金流净额', '经营现金流净额'):
        return _plan('finance_cash_flow', ('income', 'outflow', 'net'),
                     time_key=time_key or 'this_month')
    if body in ('按月经营现金流', '每月经营现金流', '各月现金流净额'):
        return _plan('finance_cash_flow', ('income', 'outflow', 'net'),
                     ('occurred_month',), time_key=time_key or 'this_year')
    if body in ('按日经营现金流', '每日经营现金流', '各日现金流净额'):
        return _plan('finance_cash_flow', ('income', 'outflow', 'net'),
                     ('occurred_day',), time_key=time_key or 'this_month')
    if body in ('各来源经营现金流', '按来源统计经营现金流'):
        return _plan('finance_cash_flow', ('income', 'outflow', 'net'),
                     ('flow_type',), time_key=time_key or 'this_month')
    if body in ('各员工实付工资', '按员工统计实付工资', '各员工实际支付工资'):
        return _plan('salary_payments', ('paid_amount',), ('employee_name',),
                     time_key=time_key)
    if body in ('各供应商应付和未付余额', '按供应商统计应付余额', '各供应商未付余额'):
        return _plan('finance_payables', ('payable_amount', 'paid_amount', 'outstanding_amount'),
                     ('supplier_name',), time_key=time_key)
    if body in ('各供应商实际付款金额', '按供应商统计付款金额'):
        return _plan('supplier_payments', ('paid_amount',), ('supplier_name',),
                     time_key=time_key)
    if body in ('各类别费用支出', '按类别统计费用支出'):
        return _plan('cash_entries', ('expense_amount',), ('category',), time_key=time_key)
    if body in ('其他收支净额', '其他收入和费用支出'):
        return _plan('cash_entries', ('other_income_amount', 'expense_amount', 'other_net_amount'),
                     time_key=time_key)
    if body in ('应收核销率', '回款率'):
        return _plan('finance_receivables', ('collection_rate',), time_key=time_key)
    if body in ('应付核销率', '付款率'):
        return _plan('finance_payables', ('payment_rate',), time_key=time_key)
    if body in ('各员工有效结算金额', '按员工统计有效结算金额'):
        return _plan('salary_settlements', ('settled_amount',), ('employee_name',),
                     time_key=time_key)
    if body in ('各状态结算申请笔数', '各状态工资申请笔数'):
        return _plan('salary_applications', ('application_count',), ('status',),
                     time_key=time_key)
    if body in ('各客户应收余额', '按客户统计未收余额'):
        return _plan('finance_receivables', ('outstanding_amount',), ('customer_name',),
                     time_key=time_key)
    if body in ('各收款方式回款金额', '按收款方式统计回款金额'):
        return _plan('finance_receipts', ('received_amount',), ('method',),
                     time_key=time_key)

    if body in ('各款号床次裁货量和菲票数', '按款号统计床次裁货量和菲票数'):
        return _plan('productions', ('cut_qty', 'ticket_count'), ('style_no',),
                     time_key=time_key)
    if body in ('各床次裁货量和登记量', '按床次统计裁货量和登记量'):
        return _plan('productions', ('cut_qty', 'registered_qty'), ('bed_no',),
                     time_key=time_key)
    if body in ('未裁完床次的裁货量', '未裁完床次裁货量'):
        return _plan(
            'productions', ('cut_qty',),
            filters=[{'field': 'is_cut_done', 'operator': 'eq', 'value': False}],
            time_key=time_key)
    return None


def local_revision(question, previous_plan):
    """无 GLM 时处理明确的条件撤回/替换；歧义或新统计口径仍交给 Planner。"""
    if not isinstance(previous_plan, dict):
        return None
    compact = re.sub(r'[\s，,。？?！!：:；;]', '', question or '')
    revised = copy.deepcopy(previous_plan)
    if compact in ('取消所有筛选', '取消全部筛选', '清空筛选条件', '不要任何筛选'):
        revised['filters'] = []
        return revised
    cancel = re.fullmatch(r'(?:取消|去掉|删除)(.+?)(?:的)?(?:筛选|过滤|条件)', compact)
    replace = re.fullmatch(r'(?:把|将)(.+?)(?:的)?(?:筛选|条件)?(?:改为|换成|设为)(.+)', compact)
    if not cancel and not replace:
        return None
    dataset = get_dataset(previous_plan.get('dataset'))
    if dataset is None:
        return None
    subject = (cancel or replace).group(1)
    exact = [field for field in dataset.fields
             if field.can_filter and subject in (field.key, field.label)]
    candidates = exact or [field for field in dataset.fields
                           if field.can_filter and (field.label in subject or subject in field.label)]
    if len(candidates) != 1:
        return None
    field = candidates[0]
    filters = list(revised.get('filters', []))
    if cancel:
        revised['filters'] = [item for item in filters if item.get('field') != field.key]
        return revised
    if field.type in ('date', 'datetime'):
        return None  # 日期范围必须成对明确给出，不能从一句“改为”猜测边界。
    value = replace.group(2).strip()
    if not value:
        return None
    old = next((item for item in filters if item.get('field') == field.key), None)
    operator = old.get('operator', 'eq') if old else 'eq'
    if operator not in field.operators or operator in ('between', 'in', 'isnull'):
        operator = 'contains' if field.type == 'text' else 'eq'
    try:
        if field.type == 'integer':
            value = int(value)
        elif field.type == 'decimal':
            value = float(value)
        elif field.type == 'boolean':
            mapping = {'是': True, '否': False, 'true': True, 'false': False}
            if value.lower() not in mapping:
                return None
            value = mapping[value.lower()]
    except (TypeError, ValueError):
        return None
    item = {'field': field.key, 'operator': operator, 'value': value}
    revised['filters'] = [item if old and entry is old else entry for entry in filters]
    if not old:
        revised['filters'].append(item)
    return revised


def _planner_prompt(user_company, previous_plan=None):
    catalog = catalog_for(user_company)
    return (
        '你是服装 ERP 的查询计划生成器。把用户问题转换为 QueryPlan，只输出一个 JSON 对象，'
        '不要输出 Markdown、解释或 SQL。无法由目录准确表达、统计口径有歧义或问题与报表无关时，'
        '输出 {"plan":null}，禁止猜测。\n'
        f'当前本地日期：{timezone.localdate().isoformat()}。\n'
        '规则：只能使用目录中的 dataset、字段、指标、聚合和操作符；metrics 至少一项；'
        'dimensions 最多4项；filters 最多10项；limit 取1~100；presentation 为 table/bar/line，默认 table；'
        '只有用户明确要求柱状图或趋势图时才使用图表，图表必须恰有一个指标和至少一个维度，line 首维度必须是日/周/月。'
        '不得输出 company_id、ORM path、SQL 或目录外字段。日期过滤使用数据集 time_field，'
        'between 的值为 [YYYY-MM-DD,YYYY-MM-DD]；计件问题未给时间默认今天，其他数据集未给时间默认全部时间。'
        'include_deleted 只有用户明确要求包含已删除/已撤销数据时才为 true。'
        '仓库和采购数量必须保留单位维度，禁止把不同单位混加；批次日期筛选不是库存截至日期。'
        'as_of 仅在目录标明 supports_as_of=true 的物料主单位库存集使用，格式 YYYY-MM-DD 且不得晚于今天；'
        '它按当前留存流水重算截至业务日期库存，不是审计快照，不能恢复历史软删、确认状态或物料参数。'
        'warehouse_lots 只保留原单位当前流水库存，不支持 as_of，也不得把批次创建日期当截至日期。'
        '物料主单位库存只计入可换算数量，未覆盖流水行数必须与库存量一起返回和解读，不得猜未知单位或缺参数数量。'
        '可换算采购金额可能排除待补参数行，必须与金额待补参数行数一起解读。'
        '有效结算金额不是实付工资；申请金额不是实付快照。实付工资、采购应付、供应商付款和其他收支只能使用对应新数据集。'
        '应收核销率=累计已收/应收；应付核销率=累计已付/应付，分母为零返回空。'
        'comparison 仅在用户明确要求上一期或去年同期对比时使用 previous/year_ago，且必须提供主时间字段 between 范围；'
        '完整利润、无认证依据的订单/床次完成率、未按员工归属的未付工资以及目录未提供的库存口径必须返回 null。'
        '交期预警、订单或生产进度、我的/本人产量或工资等旧白名单语义也返回 null，'
        '不得把“我的”误算成全公司。排序字段必须已选为维度或指标。'
        '多轮问题可继承上一份服务端计划的条件，但当前问题明确改变条件时必须替换。\n'
        '输出形式：{"plan":{"dataset":"...","metrics":[{"key":"...","aggregation":"..."}],'
        '"dimensions":[],"filters":[],"order_by":[],"limit":100,"include_deleted":false,'
        '"presentation":"table"}}。\n'
        '当前岗位可用目录（只有目录元数据，没有业务数据）：\n'
        + json.dumps(catalog, ensure_ascii=False, default=str)
        + ('\n上一轮由服务端执行成功的规范化计划（不是业务结果）：\n'
           + json.dumps(previous_plan, ensure_ascii=False) if previous_plan else '')
    )


def llm_plan(question, history, user_company, previous_plan=None):
    """GLM 只生成候选 JSON；所有异常返回 None，由旧问报链路兜底。"""
    if not getattr(settings, 'GLM_API_KEY', ''):
        return None
    try:
        messages = [{'role': 'system', 'content': _planner_prompt(
            user_company, previous_plan)}]
        messages += services._history_messages(history)
        messages.append({'role': 'user', 'content': question[:200]})
        raw = services._glm_chat(
            messages, temperature=0, max_tokens=1400, extra_timeout=4)
        start, end = raw.find('{'), raw.rfind('}')
        if start < 0 or end <= start:
            return None
        payload = json.loads(raw[start:end + 1])
        if not isinstance(payload, dict):
            return None
        plan = payload.get('plan', payload if 'dataset' in payload else None)
        return plan if isinstance(plan, dict) else None
    except Exception:
        return None


def plan_question(question, history, user_company, previous_plan=None):
    plan = local_plan(question)
    if plan is not None:
        return plan, {'source': 'local', 'catalog_version': CATALOG_VERSION}
    plan = local_revision(question, previous_plan)
    if plan is not None:
        return plan, {'source': 'local_revision', 'catalog_version': CATALOG_VERSION}
    plan = llm_plan(question, history, user_company, previous_plan)
    if plan is not None:
        return plan, {'source': 'glm', 'catalog_version': CATALOG_VERSION}
    return None, None
