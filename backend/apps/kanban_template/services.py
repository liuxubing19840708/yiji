# -*- coding: utf-8 -*-
"""看板模板渲染服务：行数据（复用 plans 聚合）× 模板列定义 → 渲染结构。

回落实底：无模板 / 渲染抛异常 → 用代码内置的「经典十列」模板渲染，
绝不白屏（用户拍板第 8 条）。
"""
import logging
from datetime import timedelta

from apps.kanban_template.fields import KANBAN_FIELDS
from apps.kanban_template.formula import eval_expr
from apps.production.plan_rows import build_plan_rows

logger = logging.getLogger(__name__)

DEFAULT_PLACEHOLDER = '—'
MAX_CELL_TEXT = 200  # 公式结果文本上限（防 'x'*n 之类大 payload 撑爆渲染）
# 下钻链接目标白名单（P0 下钻 §2.9：行/列 tap 预过滤跳转）。
# extra.link 仅接受这三个值，其它值（含旧模板缺失）渲染层一律回落空串，忽略不炸。
LINK_TARGETS = ('production', 'ticket', 'order')


def builtin_default_columns():
    """内置默认模板列（旧 10 列 + 订单号/订单数量两列）。返回列定义 dict 列表。"""
    def col(seq, title, bind, key='', merge='none', align='left',
            placeholder=DEFAULT_PLACEHOLDER, width=150):
        return {'id': -(seq + 1), 'seq': seq, 'title': title, 'bind_type': bind,
                'field_key': key, 'expr': '', 'static_text': '',
                'extra': {'merge': merge, 'placeholder': placeholder, 'align': align,
                          'width': width}}
    return [
        col(0, '款号', 'field', 'style_no', merge='block', width=190),
        col(1, '下单日期', 'field', 'order_date', merge='block', width=110),
        col(2, '颜色', 'field', 'color', width=110),
        col(3, '订单号', 'field', 'order_no', merge='block', width=150),
        col(4, '订单数量', 'field', 'order_total', align='right', width=130),
        # 「订单数量」→「菲票数量」（P1 名词统一）：只改内置默认列标题，
        # field_key 仍为 order_qty（公式语义不动），用户自建/存量模板列不受影响
        col(5, '菲票数量', 'field', 'order_qty', align='right', width=130),
        col(6, '床次', 'field', 'beds_text', width=180),
        col(7, '已裁数量', 'field', 'cut_qty', align='right', width=130),
        col(8, '已裁合计', 'field', 'cut_total', merge='block', align='right', width=130),
        col(9, '排单情况', 'field', 'schedule', merge='block', align='center', width=120),
        col(10, '下线数量', 'field', 'offline_qty', merge='block', align='center', width=120),
        col(11, '出货', 'field', 'ship_qty', merge='block', align='center', width=120),
        col(12, '尾部成品', 'field', 'tail_qty', merge='block', align='center', width=120),
    ]


def builtin_default_template():
    """内置默认模板对象（不落库；id=None）。"""
    return {'id': None, 'name': '默认看板', 'version': 0,
            'columns': builtin_default_columns()}


def _fmt_num(v):
    """数值展示：整数直接 int，浮点去掉无意义的 .0。"""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _time_derived(order_date):
    """由下单日期推导 order_ym / in_this_month / in_this_week。"""
    from datetime import datetime
    from django.utils import timezone
    ym = order_date[:7] if len(order_date) >= 7 else ''
    try:
        d = datetime.strptime(order_date, '%Y-%m-%d').date()
    except ValueError:
        return ym, '否', '否'
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    return (ym,
            '是' if ym == today.strftime('%Y-%m') else '否',
            '是' if monday <= d <= today else '否')


def _row_field_values(plan):
    """plan 行 → 字段注册表取值字典（含 join 文案与时间推导）。

    detail 字段（计划数/面料/工序进度等）由 build_plan_rows(detail=True) 提供；
    缺省时给安全默认值（渲染为空/0，不报错）。
    """
    beds = plan.get('beds') or []
    order_ym, in_month, in_week = _time_derived(plan.get('order_date', ''))
    return {
        # 款式
        'style_no': plan.get('style_no', ''),
        'style_name': plan.get('style_name', ''),
        'style_customer_name': plan.get('style_customer_name', ''),
        'style_price': plan.get('style_price') or 0,
        'style_remark': plan.get('style_remark', ''),
        'order_date': plan.get('order_date', ''),
        # 生产/床次
        'beds_text': '\n'.join(f"{b['bed_no']}床×{b['qty']}" for b in beds),
        'bed_count': plan.get('bed_count', 0),
        'plan_qty': plan.get('plan_qty', 0),
        'cut_qty': plan.get('cut_qty', 0),
        'cut_total': plan.get('cut_total', 0),
        'fabric': plan.get('fabric', ''),
        'source_order_no': plan.get('source_order_no', ''),
        'order_type': plan.get('order_type', ''),
        'customer_name': plan.get('customer_name', ''),
        'bed_created_date': plan.get('bed_created_date', ''),
        'is_cut_done_all': plan.get('is_cut_done_all', '否'),
        # 菲票聚合
        'color': plan.get('color', ''),
        'order_qty': plan.get('order_qty', 0),
        'ticket_count': plan.get('ticket_count', 0),
        'size_text': plan.get('size_text', ''),
        'process_total': plan.get('process_total', 0),
        'process_done': plan.get('process_done', 0),
        'process_percent': plan.get('process_percent', 0),
        # 订单（P1）：order_no 由 build_plan_rows 下发（最早挂单订单号）；
        # order_total = 该色挂单订单的订单数量合计（订单域 OrderItem 聚合）；
        # 交期/完成率待订单域数据源接线，未就绪时安全默认值（渲染空/0，不报错）
        'order_no': plan.get('order_no', ''),
        'order_total': plan.get('order_total', 0),
        'delivery_date': plan.get('delivery_date', ''),
        'order_progress': plan.get('order_progress', 0),
        # 时间推导
        'order_ym': order_ym,
        'in_this_month': in_month,
        'in_this_week': in_week,
        # 占位字段（kind=todo）渲染层直接落占位符，这里不取值
    }


def _cell_text_for_column(coldef, values):
    """单列求值（field/formula/static），返回 (text, is_todo)。"""
    extra = coldef.get('extra') or {}
    placeholder = extra.get('placeholder') or DEFAULT_PLACEHOLDER
    bind = coldef.get('bind_type') or 'field'
    if bind == 'static':
        return coldef.get('static_text', ''), False
    if bind == 'formula':
        expr = coldef.get('expr') or ''
        try:
            return str(_fmt_num(eval_expr(expr, values)))[:MAX_CELL_TEXT], False
        except Exception as e:  # 求值异常落占位（第 3 层兜底），不影响整行
            logger.warning('kanban formula failed: %s | %s', expr, e)
            return placeholder, False
    # field
    key = coldef.get('field_key') or ''
    f = KANBAN_FIELDS.get(key)
    if f is None:
        return placeholder, False
    if f.kind == 'todo':
        return placeholder, True
    v = _fmt_num(values.get(key, ''))
    return ('' if v is None else str(v)), False


def _normalize_width(raw):
    """列宽归一：缺省（存量模板无 width 键）→150 保持原样；
    0 / '' / 'auto'（不区分大小写）→0 = 自适应（渲染端按内容估宽）。"""
    if raw is None or raw == '':
        if raw is None:
            return 150  # width 键不存在（存量模板）：保持旧默认
        return 0        # 存了空串 = 用户清空 → 自适应
    if isinstance(raw, str):
        return 0 if raw.strip().lower() == 'auto' or raw.strip() == '' or \
            raw.strip() == '0' else (int(raw) if raw.strip().isdigit() else 150)
    if isinstance(raw, (int, float)):
        return int(raw) if raw else 0
    return 150


def _columns_payload(columns):
    """列定义下发（含展示配置；负数 id = 内置模板列）。

    width：数值 = 固定 rpx；0 = 自适应（渲染端按内容估宽 + 均摊剩余空间）。
    """
    out = []
    for c in sorted(columns, key=lambda c: (c.get('seq', 0), c.get('id', 0))):
        extra = c.get('extra') or {}
        # link：下钻目标，白名单外（含缺失/旧模板/非法值）一律回落空串，忽略不炸
        link = extra.get('link')
        out.append({
            'id': c.get('id'),
            'seq': c.get('seq', 0),
            'title': c.get('title', ''),
            'bind_type': c.get('bind_type', 'field'),
            'merge': extra.get('merge') or 'none',
            'group': extra.get('group') or '',   # 两级表头父级（表头 colspan/rowspan 用）
            'width': _normalize_width(extra.get('width')),
            'align': extra.get('align') or 'left',
            'placeholder': extra.get('placeholder') or DEFAULT_PLACEHOLDER,
            'link': link if link in LINK_TARGETS else '',
        })
    return out


def build_render_payload(company, template, range_key, start_date, end_date):
    """渲染主入口。template 为 KanbanTemplate 实例或 None（内置默认）。"""
    if template is None:
        tpl_meta = {'id': None, 'name': '默认看板', 'version': 0, 'is_default': True}
        coldefs = builtin_default_columns()
    else:
        tpl_meta = {'id': template.id, 'name': template.name,
                    'version': template.version, 'is_default': template.is_default}
        coldefs = [{
            'id': c.id, 'seq': c.seq, 'title': c.title,
            'bind_type': c.bind_type, 'field_key': c.field_key,
            'expr': c.expr, 'static_text': c.static_text, 'extra': c.extra or {},
        } for c in template.columns.filter(is_deleted=False)]
    return render_payload(company, tpl_meta, coldefs, range_key, start_date, end_date)


def render_payload(company, tpl_meta, coldefs, range_key, start_date, end_date):
    """渲染核心：列定义（dict 列表，DB 模板 / 内置模板 / 内联试跑三路共用）。"""
    from apps.production.plan_rows import plan_window
    start, end = _parse_range(range_key, start_date, end_date)
    plan_start, plan_end = plan_window(range_key, start, end)
    # 行数据（与 /api/dashboard 同一聚合函数，口径一致；detail=True 带全量看板字段）
    plans = build_plan_rows(company, plan_start, plan_end, detail=True)

    # 行 → 日期组 → 款块 → cells（按列序对齐）
    groups = []
    g_index, block_map = {}, {}
    block_serial = 0
    for plan in plans:
        values = _row_field_values(plan)
        date = plan.get('order_date') or ''
        if date not in g_index:
            g_index[date] = len(groups)
            groups.append({'date': date, 'label': _date_label(date), 'blocks': []})
        group = groups[g_index[date]]
        bkey = f"{plan['style_id']}-{date}"
        blk = block_map.get(bkey)
        if blk is None:
            blk = {'key': bkey, 'alt': block_serial % 2 == 1, 'rows': []}
            block_serial += 1
            block_map[bkey] = blk
            group['blocks'].append(blk)
        cells = []
        for c in coldefs:
            text, _todo = _cell_text_for_column(c, values)
            cells.append({'col': c.get('id'), 'text': text})
        blk['rows'].append({'key': f"{plan['style_id']}-{plan.get('color') or ''}",
                            'cells': cells})

    return {'template': tpl_meta, 'columns': _columns_payload(coldefs), 'groups': groups}


def _parse_range(range_key, start_date, end_date):
    """渲染接口的 range 解析（与 DashboardView 同规则：非法回落本月）。"""
    from datetime import datetime
    from django.utils import timezone
    from apps.production.views import _range_dates
    if range_key not in ('today', 'yesterday', 'week', 'month', 'last_month', 'custom'):
        range_key = 'month'
    start, end, _label = _range_dates(range_key)
    if range_key == 'custom':
        try:
            cs = datetime.strptime(start_date or '', '%Y-%m-%d').date()
            ce = datetime.strptime(end_date or '', '%Y-%m-%d').date()
            if cs > ce:
                cs, ce = ce, cs
            start, end = cs, ce
        except ValueError:
            start, end = _range_dates('month')[:2]
    return start, end


def _date_label(date):
    """2026-10-15 → 10月15日（与小程序原 groupLabel 同规则）。"""
    import re
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', date or '')
    return f'{int(m.group(2))}月{int(m.group(3))}日' if m else (date or '未填日期')


def render_with_fallback(company, range_key, start_date, end_date, template=None):
    """渲染入口切换的兜底壳：指定/默认模板渲染失败 → 回落内置默认模板。"""
    from apps.kanban_template.models import KanbanTemplate
    if template is None:
        template = (KanbanTemplate.objects.filter(company=company, is_default=True)
                    .select_related().first())
    try:
        return build_render_payload(company, template, range_key, start_date, end_date)
    except Exception:
        logger.exception('kanban render failed, fallback to builtin default')
        return build_render_payload(company, None, range_key, start_date, end_date)
