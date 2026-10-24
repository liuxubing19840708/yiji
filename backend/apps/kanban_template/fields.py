# -*- coding: utf-8 -*-
"""可绑定字段注册表：模板列的 field/formula 只能引用这里的 key。

渲染与求值共用同一份白名单（安全边界第一层）。
2026-10-18 全量盘点：凡是 plan_rows.build_plan_rows(detail=True) 数据集里
能取到的字段全部注册（按类目分组，配置端下拉按类目展示）；
取不到/不可渲染的字段（如组排行——只有全厂粒度）不注册，不承诺不可渲染的列。

kind:
  style  款号级（块内所有颜色行同值）
  row    行级（每颜色行各自）
  agg    聚合数值（可直接参与四则运算）
  join   拼接文案（不参与数值公式）
  derive 由行内其它字段推导（公式可直接引用）
  todo   数据源未建（列可配置，渲染一律占位符）
"""
from types import SimpleNamespace

def _f(label, kind, category, desc=''):
    return SimpleNamespace(label=label, kind=kind, category=category, desc=desc)

C_STYLE = '款式'
C_PROD = '生产/床次'
C_TICKET = '菲票聚合'
C_ORDER = '订单'
C_TIME = '时间'
C_TODO = '占位（数据源未建）'

KANBAN_FIELDS = {
    # ── 款式 ──────────────────────────────────────────────
    'style_no':            _f('款号', 'style', C_STYLE, '款式档案款号'),
    'style_name':          _f('款式名称', 'style', C_STYLE, '款式档案名称'),
    'style_customer_name': _f('客户', 'style', C_STYLE, '款式档案关联的客户名称'),
    'style_price':         _f('款式单价', 'style', C_STYLE, '款式总单价（各工序单价之和）'),
    'style_remark':        _f('款式备注', 'style', C_STYLE, '款式档案备注'),
    'order_date':          _f('下单日期', 'style', C_STYLE, '该款最早床次的创建日期'),
    # ── 生产/床次 ─────────────────────────────────────────
    'beds_text':       _f('床次', 'join', C_PROD, '该色涉及床次「床号×该色菲票数」逐床一行'),
    'bed_count':       _f('床次数', 'agg', C_PROD, '该色涉及的床次数量'),
    'plan_qty':        _f('计划裁货数', 'agg', C_PROD, '该色涉及床次的计划裁货数量合计'),
    'cut_qty':         _f('已裁数量', 'agg', C_PROD, '该色涉及床次的实裁合计（混色床整床计入各色行）'),
    'cut_total':       _f('已裁合计', 'agg', C_PROD, '款号级已裁合计（同款各色行同值）'),
    'fabric':          _f('面料', 'join', C_PROD, '该色涉及床次的面料（多床去重换行）'),
    'source_order_no': _f('来源单号', 'join', C_PROD, '客户订单号/来源单号（多床去重换行）'),
    'order_type':      _f('订单类型', 'join', C_PROD, '自产/外发（多床去重顿号连接）'),
    'customer_name':   _f('床次客户', 'join', C_PROD, '床次订单上登记的客户（多床去重顿号连接）'),
    'bed_created_date': _f('建床日期', 'row', C_PROD, '该色涉及床次中最早的建床日期'),
    'is_cut_done_all': _f('裁完状态', 'row', C_PROD, '该色涉及床次全部标记裁完时显示「是」'),
    # ── 菲票聚合 ──────────────────────────────────────────
    'color':           _f('颜色', 'row', C_TICKET, '菲票颜色'),
    # 「订单数量」→「菲票数量」（P1 名词统一，防与真订单量撞名）：
    # 只改显示名，field_key 与公式语义不动，用户自建列不受影响
    'order_qty':       _f('菲票数量', 'agg', C_TICKET, '该款该色的菲票数量合计'),
    'ticket_count':    _f('扎数', 'agg', C_TICKET, '该款该色的菲票张数（一票一扎）'),
    'size_text':       _f('尺码集合', 'join', C_TICKET, '该色菲票涉及的全部尺码（顿号连接）'),
    'process_total':   _f('工序总数', 'agg', C_TICKET, '该款配置的订单工序数量'),
    'process_done':    _f('已完成工序数', 'agg', C_TICKET, '该色菲票上有登记记录的工序数'),
    'process_percent': _f('进度百分比', 'agg', C_TICKET, '已完成工序数 ÷ 工序总数 × 100（保留 1 位小数）'),
    # ── 订单（P1 契约 §四：注册表加「订单」类目；数据源由 plans.order_no 与
    #    订单域（SA1）提供，未挂单/未就绪时渲染空串，不炸） ──
    'order_no':       _f('订单号', 'row', C_ORDER, '该款最早挂单的客户订单号（多单取最早；无单为空）'),
    'order_total':    _f('订单数量', 'agg', C_ORDER, '该款挂单订单该色的订单数量合计（多单累加；无单为 0）'),
    'delivery_date':  _f('交期', 'row', C_ORDER, '挂单订单的交期（YYYY-MM-DD；无单/无交期为空）'),
    'order_progress': _f('订单完成率', 'agg', C_ORDER, '挂单订单的登记完成率百分比（分母=订单量，口径见 stats.order_item_progress）'),
    # ── 时间（由下单日期推导，公式可直接引用） ────────────
    'order_ym':      _f('下单年月', 'derive', C_TIME, '下单日期所属年月（YYYY-MM）'),
    'in_this_month': _f('本月下单', 'derive', C_TIME, '下单日期在本月内时显示「是」'),
    'in_this_week':  _f('本周下单', 'derive', C_TIME, '下单日期在本周（周一至今）内时显示「是」'),
    # ── 占位（数据源未建，一期渲染「—」） ─────────────────
    'schedule':    _f('排单情况', 'todo', C_TODO, '排单数据源未建，渲染占位「—」'),
    'offline_qty': _f('下线数量', 'todo', C_TODO, '下线数据源未建，渲染占位「—」'),
    'ship_qty':    _f('出货', 'todo', C_TODO, '出货数据源未建，渲染占位「—」'),
    'tail_qty':    _f('尾部成品', 'todo', C_TODO, '尾部成品数据源未建，渲染占位「—」'),
}

_CATEGORY_ORDER = [C_STYLE, C_PROD, C_TICKET, C_ORDER, C_TIME, C_TODO]


def field_label(key: str) -> str:
    f = KANBAN_FIELDS.get(key)
    return f.label if f else key


def fields_payload() -> list:
    """配置端拖拽面板数据源（GET /api/kanban/fields/），按类目分组排序。"""
    order = {c: i for i, c in enumerate(_CATEGORY_ORDER)}
    return [{'key': k, 'label': f.label, 'kind': f.kind,
             'category': f.category, 'desc': f.desc}
            for k, f in sorted(
                KANBAN_FIELDS.items(),
                key=lambda kv: (order.get(kv[1].category, 99), kv[0]))]
