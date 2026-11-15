# -*- coding: utf-8 -*-
"""AI 报表问答核心（2026-10-25 新功能）。

架构拍板（方案 A：LLM 意图解析 + 白名单查询）：
  - LLM（智谱 GLM）只做「自然语言 → 结构化意图」，绝不碰库、不生成数字；
  - 意图命中白名单报表后，由本模块用 Django ORM 聚合出真实数据；
  - 意图解析失败 / 未配 key / 超出白名单 → 回落快捷问题清单（前端展示）；
  - 口径与既有页面一致：订单进度/交期灯复用 orders.views 的聚合函数，
    今日产量与扫菲工作台 my_today_amount 同口径（Σ qty×price，q2 两位小数）。

权限模型：全员可问公司级产量/订单进度/交期预警/待处理菲票（与管理员员工均可看
生产看板同一口径）；个人薪酬摘要恒为「本人」（不收 uid 参数，无越权面）。
"""
import json
import re
import time
from datetime import timedelta
from decimal import Decimal
from urllib import request as urlrequest

from django.conf import settings
from django.db.models import Count, DecimalField, F, Sum
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.views import _aggregate_stats
from apps.records.models import Record

Q2 = lambda v: f'{Decimal(v or 0):.2f}'  # noqa: E731  两位小数字符串（金额口径统一）


# ============================================================
# 白名单报表（新增报表 = 在此注册 + build_report 加分支）
# ============================================================

# 快捷问题（LLM 不可用时的兜底入口，也是前端首次进入的引导）
QUICK_QUESTIONS = [
    '今日产量多少',
    '本月产量多少',
    '订单进度怎么样',
    '哪些订单快到交期了',
    '我这个月做了多少工资',
]

# 交给 LLM 的白名单说明（key/含义/参数）。params 目前只支持 date_range 提示，
# 全部报表均无需必填参数——一期不做时间槽位解析，问「上月」也回落本月口径
# 并在 note 中说明（二期再开槽位）。
_REPORT_PROMPT_LINES = [
    'today_output|今日产量汇总（全公司今日登记件数/参与人数）|无需参数',
    'month_output|本月产量汇总（全公司本月登记件数/Top5员工）|无需参数',
    'order_progress|订单进度（生产中订单的完成率排行）|无需参数',
    'deadline_alert|交期预警（≤7天到期或已过期未完成的生产中订单）|无需参数',
    'my_salary|提问人本人的本月薪酬摘要|无需参数',
]

_SYSTEM_PROMPT = (
    '你是服装工厂管理系统的报表问答助手。用户会用自然语言问报表，'
    '你必须从下面的报表白名单中选一个最匹配的，只输出一行 JSON，不要输出任何其他内容：\n'
    + '\n'.join(_REPORT_PROMPT_LINES)
    + '\n输出格式：{"report_key":"<白名单key>","date_range":"<时间区间>"}\n'
    'date_range 取值：today/yesterday/before_yesterday/this_week/last_week/this_month/last_month/this_year/last_year，'
    '表示问题涉及的时间区间（问「昨天产量」填 yesterday，问「上月工资」填 last_month；'
    '没提时间默认 today）。order_progress 与 deadline_alert 是当前状态型报表，date_range 恒填 today。\n'
    '注意：my_salary 只指「提问人本人」的工资；问全厂/所有人/别人的工资、问岗位角色、'
    '问操作方法、闲聊，一律输出：{"report_key":""}\n'
    '这五张之外的数据问题也必须填 ""，系统会自动转自由问答去查全库数据——'
    '例如订单成本、仓库库存、应收应付、采购/出货/结算/报价、菲票或床次明细、'
    '某员工产量、组织人数等，都填 ""，禁止硬凑成 order_progress 或其他 key。'
    '拿不准时一律填 ""。\n'
    '对话历史里的「那昨天呢」这类指代，要结合历史推导出完整意图。'
)


# ============================================================
# 本地意图短路（常见问法关键词直接命中，零延迟零费用；GLM 只兜模糊问法）
# ============================================================

_TIME_WORDS = [
    ('before_yesterday', ('前天',)),
    ('yesterday', ('昨天', '昨日')),
    ('last_week', ('上周', '上个星期', '上一周')),
    ('this_week', ('本周', '这周', '这一周')),
    ('last_month', ('上月', '上个月')),
    ('this_month', ('本月', '这个月')),
    ('this_year', ('今年', '本年度')),
    ('last_year', ('去年', '上一年')),
    ('today', ('今天', '今日')),
]
_SALARY_WORDS = ('工资', '薪酬', '工钱', '赚了', '赚多少')
_PROGRESS_WORDS = ('进度', '完成率')
_DEADLINE_WORDS = ('交期', '到期', '逾期', '赶不上', '快到')
_OUTPUT_WORDS = ('产量', '干了多少', '做了多少', '登记了多少', '做了几件', '干了几件')


def local_intent(question: str):
    """关键词规则解析意图；无把握返回 None（交给 GLM）。命中即免 GLM 调用。

    工资规则收紧：my_salary 只指本人——问法须含「我/本人」；「本月总共工资」
    「全厂工资」这类全厂问法不劫持，交 GLM/自由问答。
    """
    rng = next((k for k, ws in _TIME_WORDS if any(w in question for w in ws)), 'today')
    if any(w in question for w in _DEADLINE_WORDS):
        return {'report_key': 'deadline_alert'}
    if any(w in question for w in _PROGRESS_WORDS):
        return {'report_key': 'order_progress'}
    if any(w in question for w in _SALARY_WORDS):
        if '我' in question or '本人' in question:
            return {'report_key': 'my_salary', 'date_range': rng}
        return None  # 全厂/他人工资问法：不在白名单，走 GLM → 自由问答
    if any(w in question for w in _OUTPUT_WORDS):
        key = 'month_output' if rng in ('this_month', 'last_month') else 'today_output'
        return {'report_key': key, 'date_range': rng}
    return None


def resolve_range(date_range: str):
    """date_range → (start_date, end_date, 中文标签)；非法值回落 today。"""
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    first_this = today.replace(day=1)
    first_last = (first_this - timedelta(days=1)).replace(day=1)
    last_last = first_this - timedelta(days=1)
    first_year = today.replace(month=1, day=1)
    table = {
        'today': (today, today, '今日'),
        'yesterday': (today - timedelta(days=1), today - timedelta(days=1), '昨日'),
        'before_yesterday': (today - timedelta(days=2), today - timedelta(days=2), '前天'),
        'this_week': (monday, today, '本周'),
        'last_week': (monday - timedelta(days=7), monday - timedelta(days=1), '上周'),
        'this_month': (first_this, today, '本月'),
        'last_month': (first_last, last_last, '上月'),
        'this_year': (first_year, today, '今年'),
        'last_year': (first_year.replace(year=today.year - 1),
                      first_year - timedelta(days=1), '去年'),
    }
    return table.get(date_range, table['today'])


def parse_intent(question: str, history=None):
    """调 GLM 解析意图 → {'report_key', 'date_range'}；任何异常返回 None（调用方回落）。

    只发问题文本与对话历史（每轮 ≤200 字、最多 5 轮），不发任何业务数据；
    模型输出强制单行 JSON，用正则提取第一段 {...} 防御性解析。
    """
    api_key = getattr(settings, 'GLM_API_KEY', '')
    if not api_key:
        return None
    try:
        messages = [{'role': 'system', 'content': _SYSTEM_PROMPT}]
        for turn in (history or [])[-5:]:
            content = str(turn.get('content') or '')[:200]
            role = 'assistant' if turn.get('role') == 'ai' else 'user'
            if content:
                messages.append({'role': role, 'content': content})
        messages.append({'role': 'user', 'content': question[:200]})
        payload = json.dumps({
            'model': getattr(settings, 'GLM_MODEL', 'glm-4-flash'),
            'messages': messages,
            'temperature': 0.1,
            'max_tokens': 64,
        }).encode('utf-8')
        req = urlrequest.Request(
            getattr(settings, 'GLM_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4')
            + '/chat/completions',
            data=payload,
            headers={'Authorization': f'Bearer {api_key}',
                     'Content-Type': 'application/json'},
            method='POST')
        with urlrequest.urlopen(req, timeout=getattr(settings, 'GLM_TIMEOUT_S', 8)) as resp:
            content = (json.loads(resp.read().decode('utf-8'))
                       ['choices'][0]['message']['content'] or '')
        m = re.search(r'\{.*\}', content, re.S)
        if not m:
            return None
        data = json.loads(m.group())
        key = str(data.get('report_key') or '')
        intent = {'report_key': key} if key else {'report_key': ''}
        intent['date_range'] = str(data.get('date_range') or 'today')
        return intent
    except Exception:
        return None


# ============================================================
# 自由问答（测试阶段 AI_FREE_MODE=1 时启用）
# ============================================================

_FREE_SYSTEM_PROMPT = (
    '你是服装工厂管理系统的报表助手。下面给你一份 JSON：'
    'blocks=命中的数据块名（1~2块），data=各块实时统计数据（键=块名），'
    'matched_entities=问句点名的订单/床次/菲票/款号/员工明细（如有）；'
    '若某块 data 里有 denied，说明提问人无权查这块，请礼貌说明仅管理员可查。\n'
    '硬性规则：只准依据给你的数据回答；若你选的数据块里没有回答该问题所需的数据，'
    '必须直说「当前查的数据里没有这项，请换个问法」，禁止编造数字或借用没给你的数据。\n'
    '常用字段对照：菲票张数=tickets.ticket_count_total（已打印=ticket_printed）；'
    '订单张数=orders.order_count_total；床次数=beds.bed_count_total；'
    '件数产量=*.qty；产值工资=*.amount；员工人数=org.active_members；'
    '某订单的菲票数=matched_entities.order_ticket_count。'
    '回答前先在所选块的 data 和 matched_entities 里逐字段找一遍，找到了就直接作答。\n'
    '回答简洁中文，控制在 250 字内。对话历史里的指代（如「那昨天呢」）'
    '要结合当前问题一起理解。若 matched_entities 有命中实体，优先围绕它回答。'
)


def _glm_chat(messages, *, temperature=0.3, max_tokens=512, extra_timeout=8):
    """GLM chat/completions 薄封装；返回文本内容，失败抛异常由调用方处理。"""
    api_key = getattr(settings, 'GLM_API_KEY', '')
    payload = json.dumps({
        'model': getattr(settings, 'GLM_MODEL', 'glm-4-flash'),
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }).encode('utf-8')
    req = urlrequest.Request(
        getattr(settings, 'GLM_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4')
        + '/chat/completions',
        data=payload,
        headers={'Authorization': f'Bearer {api_key}',
                 'Content-Type': 'application/json'},
        method='POST')
    with urlrequest.urlopen(
            req, timeout=getattr(settings, 'GLM_TIMEOUT_S', 8) + extra_timeout) as resp:
        return (json.loads(resp.read().decode('utf-8'))
                ['choices'][0]['message']['content'] or '').strip()


def _history_messages(history):
    """历史轮次清洗：≤5 轮、每轮 ≤200 字、ai→assistant。"""
    out = []
    for turn in (history or [])[-5:]:
        content = str(turn.get('content') or '')[:200]
        role = 'assistant' if turn.get('role') == 'ai' else 'user'
        if content:
            out.append({'role': role, 'content': content})
    return out


# 同问缓存：60 秒内同厂同人同问法直接复用（自由问答两跳成本高，重复问零调用）
_FREE_CACHE = {}
_FREE_CACHE_TTL = 60
_FREE_CACHE_MAX = 200


def _cache_get(key):
    hit = _FREE_CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    _FREE_CACHE.pop(key, None)
    return None


def _cache_put(key, val):
    if len(_FREE_CACHE) >= _FREE_CACHE_MAX:
        now = time.monotonic()
        for k in [k for k, v in _FREE_CACHE.items() if v[0] <= now]:
            _FREE_CACHE.pop(k, None)
        if len(_FREE_CACHE) >= _FREE_CACHE_MAX:  # 仍满则整表清（量小可接受）
            _FREE_CACHE.clear()
    _FREE_CACHE[key] = (time.monotonic() + _FREE_CACHE_TTL, val)


def _parse_block_select(raw: str):
    """一跳输出 → (合法块 key 列表≤2, date_range)；兼容旧 {"block":"x"} 单块格式。"""
    blocks, dr = [], ''
    try:
        obj = json.loads(raw.strip().splitlines()[-1]) or {}
        v = obj.get('blocks', obj.get('block', ''))
        blocks = v if isinstance(v, list) else [v]
        dr = str(obj.get('date_range') or '').strip()
    except Exception:
        m = re.search(r'"blocks?"\s*:\s*(\[[^\]]*\]|"[^"]*")', raw)
        if m:
            try:
                v = json.loads(m.group(1))
                blocks = v if isinstance(v, list) else [v]
            except Exception:
                blocks = []
    return [str(b).strip() for b in blocks if str(b).strip()][:2], dr


def free_answer(question: str, company, user, is_admin: bool,
                history=None, date_range: str = 'today', meta=None):
    """白名单外问题（方案 B 按需选块）：GLM 第一跳只选 1~2 个数据块 key（不碰数据），
    后端精查这些块 + 实体明细，第二跳只把这几块发给 GLM 组织回答。
    60 秒内同厂同人同问法直接命中缓存（零 GLM 调用）。
    meta：可选 dict，成功后回填 {'blocks': [...], 'cached': bool} 供留痕排障。
    任何失败返回 None（调用方回落快捷问题）。"""
    api_key = getattr(settings, 'GLM_API_KEY', '')
    if not api_key or not getattr(settings, 'AI_FREE_MODE', False):
        return None
    ckey = (company.id, user.id, question[:200])
    cached = _cache_get(ckey)
    if cached is not None:
        if meta is not None:
            meta.update({**cached[1], 'cached': True})
        return cached[0]
    try:
        from . import data_pack  # 惰性 import：data_pack 反向依赖本模块
        hist = _history_messages(history)
        # 第一跳：选 1~2 块（历史一并给，让「那订单呢」这类追问能正确路由）
        messages = [{'role': 'system', 'content': data_pack.BLOCK_SELECT_PROMPT}]
        messages += hist
        messages.append({'role': 'user', 'content': question[:200]})
        raw = _glm_chat(messages, temperature=0.1, max_tokens=64, extra_timeout=0)
        blocks, dr = _parse_block_select(raw)
        if not blocks:
            return None  # 与任何数据块无关 → 走快捷问题回落
        if dr:  # 一跳对时间区间的判断优先于本地默认
            date_range = dr
        # 第二跳：只带选中的块 + 实体明细（上一轮原话兜底代词追问）
        prev_q = next((t['content'] for t in reversed(hist) if t['role'] == 'user'), '')
        pack = data_pack.select_and_build(blocks, company, user, is_admin,
                                          question, date_range, extra_text=prev_q)
        if pack is None:
            return None
        messages = [{'role': 'system',
                     'content': _FREE_SYSTEM_PROMPT + '\n'
                     + json.dumps(pack, ensure_ascii=False)}]
        messages += hist
        messages.append({'role': 'user', 'content': question[:200]})
        answer = _glm_chat(messages, temperature=0.3, max_tokens=512) or None
        if answer:
            _cache_put(ckey, (answer, {'blocks': blocks}))
            if meta is not None:
                meta.update({'blocks': blocks, 'cached': False})
        return answer
    except Exception:
        return None


# ============================================================
# 报表构建（数字全部来自 ORM 聚合）
# ============================================================

def _output_totals(qs):
    """登记记录集 → (件数Σ, 登记次数, 参与人数, 预估工资Σ)。"""
    # 注意：别名不能叫 qty（与表达式里的 F('qty') 冲突，Django 会误判为嵌套聚合）
    agg = qs.aggregate(qty_sum=Sum('qty'), cnt=Count('id'),
                       people=Count('user', distinct=True),
                       amount=Sum(F('qty') * F('price'),
                                  output_field=DecimalField(max_digits=14, decimal_places=3)))
    return (int(agg['qty_sum'] or 0), int(agg['cnt'] or 0),
            int(agg['people'] or 0), agg['amount'] or 0)


def _report_output(company, date_range=None, with_top=False):
    """产量汇总（区间参数化：今日/昨日/本周/本月/上月共用一套口径）。"""
    start, end, label = resolve_range(date_range)
    qs = Record.objects.filter(company=company, registered_at__date__gte=start,
                               registered_at__date__lte=end)
    qty, cnt, people, amount = _output_totals(qs)
    rows = [['登记件数', f'{qty} 件'], ['登记笔数', f'{cnt} 笔'],
            ['参与人数', f'{people} 人'], ['预估产值', f'{Q2(amount)} 元']]
    note = '口径与扫菲工作台「今日预估工资」一致；按登记时间落在区间内统计。'
    if with_top:
        top = (qs.values('user__id', 'user__real_name')
               .annotate(qty_sum=Sum('qty'),
                         amount_sum=Sum(F('qty') * F('price'),
                                        output_field=DecimalField(max_digits=14, decimal_places=3)))
               .order_by('-qty_sum')[:5])
        rows += [[(t['user__real_name'] or f'员工{t["user__id"]}'),
                  f'{int(t["qty_sum"] or 0)} 件 / {Q2(t["amount_sum"])} 元'] for t in top]
        note = '口径：自然日区间；产值=Σ件数×登记单价（未结算口径）；附区间 Top5 员工。'
    return {
        'title': f'{label}产量',
        'summary': (f'全{label}登记 {qty} 件（{cnt} 笔登记，{people} 人参与），'
                    f'预估工价产值 {Q2(amount)} 元'),
        'headers': ['指标', '数值'],
        'rows': rows,
        'note': note,
    }


def _report_today_output(company, date_range=None):
    return _report_output(company, date_range or 'today')


def _report_month_output(company, date_range=None):
    return _report_output(company, date_range or 'this_month', with_top=True)


def _order_rows(company, orders):
    """订单行 → [单号, 客户/款号, 完成率, 交期, 灯]（进度口径复用 orders.views）。"""
    stats = _aggregate_stats(company, [o.id for o in orders])
    today = timezone.localdate()
    rows = []
    for o in orders:
        total, registered, progress = stats.get(o.id, (0, 0, 0.0))
        if o.delivery_date:
            days_left = (o.delivery_date - today).days
            date_text = f'{o.delivery_date:%m-%d}'
            if o.status == Order.Status.PRODUCING and days_left < 0 and progress < 100:
                light = '已过期'
            elif 0 <= days_left <= 7:
                light = f'剩 {days_left} 天'
            else:
                light = '正常'
        else:
            date_text, light = '—', '正常'
        rows.append([
            o.order_no,
            f'{o.customer.name if o.customer else "—"} / {o.style.style_no if o.style else "—"}',
            f'{progress:.0f}%（{registered}/{total}）',
            date_text, light,
        ])
    return rows


_ORDER_HEADERS = ['单号', '客户 / 款号', '完成率（登记/总量）', '交期', '交期状态']


def _report_order_progress(company, date_range=None):  # 状态型报表，区间参数忽略
    orders = list(Order.objects.filter(company=company, status=Order.Status.PRODUCING)
                  .select_related('customer', 'style').order_by('-id')[:10])
    if not orders:
        return {'title': '订单进度', 'summary': '当前没有生产中的订单',
                'headers': _ORDER_HEADERS, 'rows': [], 'note': ''}
    rows = _order_rows(company, orders)
    return {'title': '订单进度', 'summary': f'生产中订单 {len(orders)} 张（最多显示 10 张，按单号倒序）',
            'headers': _ORDER_HEADERS, 'rows': rows,
            'note': '完成率=登记件数/订单总量（与订单列表、看板同口径）。'}


def _report_deadline_alert(company, date_range=None):  # 状态型报表，区间参数忽略
    today = timezone.localdate()
    orders = list(Order.objects.filter(company=company, status=Order.Status.PRODUCING,
                                       delivery_date__isnull=False)
                  .filter(delivery_date__lte=today + timedelta(days=7))
                  .select_related('customer', 'style')
                  .order_by('delivery_date')[:10])
    if not orders:
        return {'title': '交期预警', 'summary': '7 天内没有到期或过期的生产中订单',
                'headers': _ORDER_HEADERS, 'rows': [], 'note': ''}
    rows = _order_rows(company, orders)
    return {'title': '交期预警', 'summary': f'{len(orders)} 张订单 7 天内到期或已过期（按交期升序）',
            'headers': _ORDER_HEADERS, 'rows': rows,
            'note': '预警范围：距交期 ≤7 天或已过期未完成；与订单列表交期灯同一阈值。'}


def _report_my_salary(company, user, date_range=None):
    start, end, label = resolve_range(date_range or 'this_month')
    base = Record.objects.filter(company=company, user=user)
    rng_qs = base.filter(registered_at__date__gte=start, registered_at__date__lte=end)
    m_qty, m_cnt, _, m_amount = _output_totals(rng_qs)
    today = timezone.localdate()
    d_qty, _, _, d_amount = _output_totals(base.filter(registered_at__date=today))
    return {
        'title': '我的薪酬摘要',
        'summary': f'{label}预估工资 {Q2(m_amount)} 元（{m_qty} 件），今日 {Q2(d_amount)} 元（{d_qty} 件）',
        'headers': ['指标', label, '今日'],
        'rows': [['登记件数', f'{m_qty} 件', f'{d_qty} 件'],
                 ['登记笔数', f'{m_cnt} 笔', '—'],
                 ['预估工资', f'{Q2(m_amount)} 元', f'{Q2(d_amount)} 元']],
        'note': '预估工资=Σ件数×登记单价（与扫菲工作台同口径，非结算金额）。',
    }


_REPORT_BUILDERS = {
    'today_output': _report_today_output,
    'month_output': _report_month_output,
    'order_progress': _report_order_progress,
    'deadline_alert': _report_deadline_alert,
}


def build_report(key: str, company, user, date_range=None):
    """执行白名单报表；key 必须已在 _REPORT_BUILDERS 注册（视图层先校验）。"""
    if key == 'my_salary':
        return _report_my_salary(company, user, date_range)
    return _REPORT_BUILDERS[key](company, date_range)
