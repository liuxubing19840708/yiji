# -*- coding: utf-8 -*-
"""AI 自由问答数据块库（2026-10-25 方案 B 按需选块）。

架构：GLM 第一跳只选「数据块 key」（绝不碰数据），后端按 key 精查并只把
命中的那一块发给 GLM 组织回答——敏感数据只在被问到时才出厂、每次只出一块。
  - 每块独立 _safe 兜底，单块异常不拖垮整体；
  - 敏感块（员工明细/仓库/成本/财务/采购/出货/结算/报价）仅管理员岗携带，
    普工命中返回 denied（模型会礼貌拒答）；
  - attach_entities：问句点名实体（订单号/床次/扎号/款号/员工）时确定性正则
    + ORM 精查明细，不靠模型猜。
数字全部来自 ORM 聚合，模型只负责组织语言。
"""
import re
from datetime import timedelta

from django.db.models import DecimalField, F, Q, Sum
from django.utils import timezone

from apps.accounts.models import Post, UserCompany
from apps.catalog.models import Style, Ticket
from apps.orders.models import Order
from apps.orders.views import _aggregate_stats
from apps.production.models import Production
from apps.records.models import Record

from .services import Q2, _output_totals, resolve_range

D = DecimalField(max_digits=14, decimal_places=3)


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return {'error': '该数据块暂不可用'}


def _record_base(company):
    return Record.objects.filter(company=company)


# ============================================================
# 数据块构建器（每个 = 一块；签名统一 (company, user, is_admin, date_range)）
# ============================================================

def _block_output(company, user, is_admin, date_range):
    """产量：今日/本月/问句区间 + 近30天日产量 + 工序/款式/颜色维度。"""
    today = timezone.localdate()
    ctx = {}
    qs_today = _record_base(company).filter(registered_at__date=today)
    qs_month = _record_base(company).filter(registered_at__year=today.year,
                                            registered_at__month=today.month)
    t = _output_totals(qs_today)
    m = _output_totals(qs_month)
    ctx['today'] = {'qty': t[0], 'count': t[1], 'people': t[2], 'amount': Q2(t[3])}
    ctx['month'] = {'qty': m[0], 'count': m[1], 'people': m[2], 'amount': Q2(m[3])}

    start, end, label = resolve_range(date_range)
    rng = _output_totals(_record_base(company).filter(
        registered_at__date__gte=start, registered_at__date__lte=end))
    ctx['asked_range'] = {'label': label, 'qty': rng[0], 'count': rng[1],
                          'people': rng[2], 'amount': Q2(rng[3])}

    since30 = today - timedelta(days=29)
    daily = (_record_base(company).filter(registered_at__date__gte=since30)
             .values('registered_at__date').annotate(qty=Sum('qty'))
             .order_by('registered_at__date'))
    ctx['daily_30d'] = [{'date': str(r['registered_at__date']), 'qty': int(r['qty'] or 0)}
                        for r in daily]

    proc = (qs_month.values('process__name')
            .annotate(qty_sum=Sum('qty'),
                      amount_sum=Sum(F('qty') * F('price'), output_field=D))
            .order_by('-qty_sum')[:15])
    ctx['process_dims_month'] = [{'process': (r['process__name'] or '未指定'),
                                  'qty': int(r['qty_sum'] or 0),
                                  'amount': Q2(r['amount_sum'])}
                                 for r in proc]
    sty = (qs_month.values('ticket__production__style__style_no',
                           'ticket__production__style__name')
           .annotate(qty=Sum('qty')).order_by('-qty')[:15])
    ctx['style_dims_month'] = [{'style': r['ticket__production__style__style_no'] or '—',
                                'name': r['ticket__production__style__name'] or '',
                                'qty': int(r['qty'] or 0)} for r in sty]
    col = (qs_month.values('ticket__color').annotate(qty=Sum('qty'))
           .order_by('-qty')[:15])
    ctx['color_dims_month'] = [{'color': r['ticket__color'] or '未分色',
                                'qty': int(r['qty'] or 0)} for r in col]
    return ctx


def _block_my_info(company, user, is_admin, date_range):
    """提问人本人的岗位/角色/薪酬（全员可查，只含本人）。"""
    today = timezone.localdate()
    uc = (UserCompany.objects.filter(user=user, company=company)
          .select_related('post').first())
    base = _record_base(company).filter(user=user)
    mo = _output_totals(base.filter(registered_at__year=today.year,
                                    registered_at__month=today.month))
    dd = _output_totals(base.filter(registered_at__date=today))
    return {'my_role': {'post': (uc.post.name if uc and uc.post else '未设岗位'),
                        'is_admin': bool(uc and uc.post and uc.post.is_admin)},
            'my_salary': {'month_qty': mo[0], 'month_amount': Q2(mo[3]),
                          'today_qty': dd[0], 'today_amount': Q2(dd[3])}}


def _block_employees(company, user, is_admin, date_range):
    """员工明细（仅管理员）：组织概况 + 全员工月度/今日产量工资。"""
    if not is_admin:
        return {'denied': '员工明细仅管理员可查'}
    today = timezone.localdate()
    act = UserCompany.objects.filter(company=company, is_deleted=False,
                                     status=UserCompany.Status.ACTIVE)
    ctx = {'org': {'active_members': act.count()}}
    posts = Post.objects.filter(company=company, is_deleted=False)
    ctx['org']['posts'] = [{'name': p.name, 'is_admin': p.is_admin,
                            'members': act.filter(post=p).count()} for p in posts[:20]]
    qs_month = _record_base(company).filter(registered_at__year=today.year,
                                            registered_at__month=today.month)
    emp = (qs_month.values('user__id', 'user__real_name')
           .annotate(qty_sum=Sum('qty'),
                     amount_sum=Sum(F('qty') * F('price'), output_field=D))
           .order_by('-qty_sum')[:30])
    ctx['employees_month_top30'] = [
        {'name': (r['user__real_name'] or f'员工{r["user__id"]}'),
         'qty': int(r['qty_sum'] or 0), 'amount': Q2(r['amount_sum'])} for r in emp]
    et = (qs_month.filter(registered_at__date=today)
          .values('user__id', 'user__real_name')
          .annotate(qty_sum=Sum('qty')).order_by('-qty_sum')[:30])
    ctx['employees_today'] = [{'name': (r['user__real_name'] or f'员工{r["user__id"]}'),
                               'qty': int(r['qty_sum'] or 0)} for r in et]
    return ctx


def _order_rows(company, orders):
    stats = _aggregate_stats(company, [o.id for o in orders])
    today = timezone.localdate()
    rows = []
    for o in orders:
        total, registered, progress = stats.get(o.id, (0, 0, 0.0))
        light = ''
        if o.delivery_date:
            days = (o.delivery_date - today).days
            light = '已过期' if (o.status == Order.Status.PRODUCING
                                 and days < 0 and progress < 100) else f'剩{days}天'
        rows.append({'order_no': o.order_no,
                     'customer': o.customer.name if o.customer else '',
                     'style': o.style.style_no if o.style else '',
                     'status': o.status, 'delivery_date': (str(o.delivery_date)
                                                           if o.delivery_date else None),
                     'registered': registered, 'total': total, 'deadline_state': light})
    return rows


def _block_orders(company, user, is_admin, date_range):
    orders = list(Order.objects.filter(company=company)
                  .select_related('customer', 'style').order_by('-id')[:50])
    if not orders:
        return {'orders_recent': []}
    rows = _order_rows(company, orders)
    deadline = [r for r in rows if r['deadline_state'] == '已过期'
                or (r['deadline_state'] and '剩' in r['deadline_state']
                    and int(str(r['deadline_state']).replace('剩', '').replace('天', '')) <= 7)]
    return {'orders_recent': rows, 'deadline_orders': deadline,
            'order_count_total': Order.objects.filter(company=company).count()}


def _block_beds(company, user, is_admin, date_range):
    """床次：最近 30 床进度（登记/裁货/锁定/裁完）。"""
    beds = list(Production.objects.filter(company=company)
                .select_related('style').order_by('-id')[:30])
    if not beds:
        return {'beds_recent': []}
    per_bed = dict(Record.objects.filter(
        company=company, ticket__production_id__in=[b.id for b in beds])
        .values_list('ticket__production_id').annotate(qty=Sum('qty')))
    rows = [{'bed_no': b.bed_no, 'style': b.style.style_no if b.style else '',
             'total_qty': b.total_qty, 'registered': int(per_bed.get(b.id, 0)),
             'cut_done': b.is_cut_done, 'locked': b.is_locked} for b in beds]
    return {'beds_recent': rows,
            'bed_count_total': Production.objects.filter(company=company).count()}


def _block_tickets(company, user, is_admin, date_range):
    """菲票概览计数（具体某扎走实体精查）。"""
    today = timezone.localdate()
    qs = Ticket.objects.filter(company=company)
    return {'ticket_count_total': qs.count(),
            'ticket_printed': qs.filter(is_printed=True).count(),
            'ticket_today_touched': _record_base(company).filter(
                registered_at__date=today).values('ticket').distinct().count()}


def _split_admin_blocks(company):
    """管理员专属域拆成的独立块（惰性 import + 各自兜底）。"""
    today = timezone.localdate()
    out = {}

    def _stock():
        lots = list(__import__('apps.warehouse.models', fromlist=['Lot']).Lot.objects
                    .filter(company=company, qty__gt=0).order_by('-qty')[:20])
        mi = __import__('apps.warehouse.models', fromlist=['MaterialIssue']).MaterialIssue
        return {'stock_lots_top20': [{'lot_no': l.lot_no, 'fabric': l.fabric_name,
                                      'color': l.color, 'qty': str(l.qty), 'unit': l.unit}
                                     for l in lots],
                'issues_month_count': mi.objects.filter(
                    company=company, issue_at__year=today.year,
                    issue_at__month=today.month).count()}

    def _cost():
        oc = list(__import__('apps.cost.models', fromlist=['OrderCost']).OrderCost.objects
                  .filter(company=company).select_related('order').order_by('-id')[:10])
        return {'order_costs_recent': [{'order_no': (c.order.order_no if c.order else ''),
                                        'total_cost': str(c.total_cost), 'status': c.status}
                                       for c in oc]}

    def _finance():
        rv = __import__('apps.finance.models', fromlist=['Receivable']).Receivable
        agg = rv.objects.filter(company=company).aggregate(
            amount=Sum('amount'), received=Sum('received_amount'))
        rvs = list(rv.objects.filter(company=company).select_related('customer')
                   .order_by('-id')[:10])
        return {'receivables_summary': {
                    'count': rv.objects.filter(company=company).count(),
                    'amount': Q2(agg['amount'] or 0),
                    'received': Q2(agg['received'] or 0),
                    'outstanding': Q2((agg['amount'] or 0) - (agg['received'] or 0))},
                'receivables_recent': [{'customer': (r.customer.name if r.customer else ''),
                                        'amount': str(r.amount),
                                        'received': str(r.received_amount),
                                        'status': r.status} for r in rvs]}

    def _purchase():
        po = list(__import__('apps.warehouse.models', fromlist=['PurchaseOrder'])
                  .PurchaseOrder.objects.filter(company=company)
                  .select_related('supplier').order_by('-id')[:10])
        return {'purchases_recent': [{'order_no': p.order_no,
                                      'supplier': (p.supplier.name if p.supplier else ''),
                                      'status': p.status} for p in po]}

    def _shipment():
        sh = list(__import__('apps.shipment.models', fromlist=['Shipment']).Shipment.objects
                  .filter(company=company).order_by('-id')[:10])
        return {'shipments_recent': [{'shipment_no': s.order_no, 'status': s.status,
                                      'total_qty': s.total_qty} for s in sh]}

    def _settlement():
        st = list(__import__('apps.salary.models', fromlist=['Settlement']).Settlement.objects
                  .filter(company=company).order_by('-id')[:5])
        sa = __import__('apps.salary.models', fromlist=['SettleApply']).SettleApply
        return {'settlements_recent': [{'period': f'{s.period_start}~{s.period_end}',
                                        'amount': str(s.amount), 'status': s.status,
                                        'employee_count': s.employee_count} for s in st],
                'settle_applies_pending': sa.objects.filter(
                    company=company, status=sa.Status.PENDING).count()}

    def _quotation():
        try:
            qm = __import__('apps.quotation.models', fromlist=['Quotation']).Quotation
            return {'quotations_recent': [{'quote_no': q.quote_no, 'status': q.status}
                                          for q in qm.objects.filter(company=company)
                                          .order_by('-id')[:5]]}
        except Exception:
            return {'quotations_recent': []}

    out['stock'] = _stock
    out['cost'] = _cost
    out['finance'] = _finance
    out['purchase'] = _purchase
    out['shipment'] = _shipment
    out['settlement'] = _settlement
    out['quotation'] = _quotation
    return out


# ============================================================
# 数据块注册表（GLM 第一跳的选择清单；敏感块标 admin）
# ============================================================

BLOCK_REGISTRY = [
    ('output',     '产量统计（今日/本月/指定区间/近30天每日/工序·款式·颜色维度）', False),
    ('my_info',    '提问人本人的岗位与薪酬', False),
    ('employees',  '员工明细与组织概况（全员工产量工资）', True),
    ('orders',     '订单（近50张进度/交期预警）', False),
    ('beds',       '床次（最近30床进度）', False),
    ('tickets',    '菲票概览计数', False),
    ('stock',      '仓库（在库面料批次/本月领料）', True),
    ('cost',       '订单成本（最近10张）', True),
    ('finance',    '财务应收（汇总+最近10条）', True),
    ('purchase',   '采购单（最近10张）', True),
    ('shipment',   '出货单（最近10张）', True),
    ('settlement', '薪酬结算单（最近5张+待审数）', True),
    ('quotation',  '报价单（最近5张）', True),
]

BLOCK_SELECT_PROMPT = (
    '你是服装工厂系统的数据路由器。根据用户问题从下面的数据块中选最需要的 1~2 个'
    '（跨块问题如「哪张订单成本最高」要选 orders+cost 两个），'
    '只输出一行 JSON：{"blocks":["<key>",...],"date_range":"<today|yesterday|'
    'before_yesterday|this_week|last_week|this_month|last_month|this_year|last_year>"}。\n'
    + '\n'.join(f'{k}|{d}' for k, d, _a in BLOCK_REGISTRY)
    + '\n问题涉及时间区间就填对应 date_range（默认 today）；'
      '问具体某订单/某床次/某扎/某款/某人时也选相应最贴近的块，明细由系统附上。\n'
      '如果问题与任何数据块无关（纯闲聊/操作咨询），输出 {"blocks":[]}'
)


def select_and_build(blocks, company, user, is_admin: bool,
                     question: str, date_range: str = 'today', extra_text: str = ''):
    """按选中的块（1~2 个，str 或 list）精查数据 + 附带问句命中的实体明细。

    extra_text：追问场景下上一轮用户原话——当前问句没提取到实体时用它兜底
    （「查一下SO20251024001」→「那它呢」）。
    """
    if isinstance(blocks, str):
        blocks = [blocks]
    valid_keys = {k for k, _d, _a in BLOCK_REGISTRY}
    keys = [b for b in (blocks or []) if b in valid_keys][:2]
    if not keys:
        return None
    data = {}
    for key in keys:
        admin_only = next(b[2] for b in BLOCK_REGISTRY if b[0] == key)
        if admin_only and not is_admin:
            data[key] = {'denied': '该数据仅管理员可查'}
        elif key == 'output':
            data[key] = _safe(_block_output, company, user, is_admin, date_range)
        elif key == 'my_info':
            data[key] = _safe(_block_my_info, company, user, is_admin, date_range)
        elif key == 'employees':
            data[key] = _safe(_block_employees, company, user, is_admin, date_range)
        elif key == 'orders':
            data[key] = _safe(_block_orders, company, user, is_admin, date_range)
        elif key == 'beds':
            data[key] = _safe(_block_beds, company, user, is_admin, date_range)
        elif key == 'tickets':
            data[key] = _safe(_block_tickets, company, user, is_admin, date_range)
        else:
            fn = _split_admin_blocks(company).get(key)
            data[key] = _safe(fn) if fn else {'error': '未知数据块'}

    pack = {'blocks': keys, 'data': data}
    attach_entities(pack, company, user, is_admin, question, extra_text)
    return pack


# ============================================================
# 实体定向查询（点查询：具体订单/床次/菲票/款号/员工）
# ============================================================

# 注意不用 \b：中文与数字同属 \w，「单20261025001做到哪了」里 \b 不成立
_ORDER_NO_RE = re.compile(r'(?<!\d)(?:SO)?(\d{8}\d{3})(?!\d)', re.I)
_BED_RE = re.compile(r'(\d+)\s*号床|床\s*[#号:：]?\s*(\d+)')
_BUNDLE_RE = re.compile(r'(\d+)\s*床\D*?(\d+)\s*扎|(\d+)\s*扎')
_STYLE_RE = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{1,4}\d{2,8})(?![A-Za-z0-9])')


def _extract_entities(company, user, is_admin, text):
    """对一段文本跑实体正则 + ORM 精查；返回 ent dict（可能为空）。"""
    ent = {}

    m = _ORDER_NO_RE.search(text)
    if m:
        o = (Order.objects.filter(company=company)
             .filter(Q(order_no=m.group()) | Q(order_no=f'SO{m.group(1)}')).first())
        if o:
            ent['order_detail'] = _order_rows(company, [o])[0]
            # 订单→床次→菲票联动（「这单的菲票有几张」类问题）
            prods = list(Production.objects.filter(company=company, order=o)[:5])
            if prods:
                tks = Ticket.objects.filter(company=company,
                                            production__in=prods)
                ent['order_beds'] = [{'bed_no': p.bed_no, 'total_qty': p.total_qty,
                                      'cut_done': p.is_cut_done} for p in prods]
                ent['order_ticket_count'] = tks.count()
                ent['order_tickets'] = [
                    {'bed_no': t.bed_no, 'bundle': t.bundle_no, 'color': t.color,
                     'size': t.size, 'qty': t.qty, 'printed': t.is_printed}
                    for t in tks[:20]]

    bm = _BED_RE.search(text)
    if bm:
        bed_no = bm.group(1) or bm.group(2)
        beds = list(Production.objects.filter(company=company, bed_no=bed_no)
                    .select_related('style')[:5])
        if beds:
            rec = (Record.objects.filter(company=company, ticket__production__in=beds)
                   .values('ticket__production_id').annotate(qty=Sum('qty')))
            stats = {r['ticket__production_id']: int(r['qty'] or 0) for r in rec}
            ent['bed_detail'] = [{'bed_no': b.bed_no,
                                  'style': b.style.style_no if b.style else '',
                                  'total_qty': b.total_qty,
                                  'registered': stats.get(b.id, 0),
                                  'cut_done': b.is_cut_done,
                                  'locked': b.is_locked} for b in beds]
            tks = Ticket.objects.filter(company=company, production__in=beds)[:20]
            ent['bed_detail_tickets'] = [
                {'bundle': t.bundle_no, 'color': t.color, 'size': t.size,
                 'qty': t.qty, 'printed': t.is_printed} for t in tks]

    sm = _BUNDLE_RE.search(text)
    if sm and 'bed_detail' not in ent:
        bundle = sm.group(2) or sm.group(1) or sm.group(3)
        tks = Ticket.objects.filter(company=company, bundle_no=bundle)[:10]
        if tks:
            ent['ticket_detail'] = [{'bed_no': t.bed_no, 'bundle': t.bundle_no,
                                     'color': t.color, 'size': t.size, 'qty': t.qty,
                                     'printed': t.is_printed} for t in tks]

    for sm2 in _STYLE_RE.finditer(text):
        style = Style.objects.filter(company=company,
                                     style_no__iexact=sm2.group(1)).first()
        if style:
            regs = int(Record.objects.filter(
                company=company, ticket__production__style=style)
                .aggregate(q=Sum('qty'))['q'] or 0)
            ent['style_detail'] = {'style_no': style.style_no, 'name': style.name,
                                   'total_registered': regs}
            break

    if is_admin:
        names = (UserCompany.objects.filter(company=company, is_deleted=False,
                                            status=UserCompany.Status.ACTIVE,
                                            user__real_name__isnull=False)
                 .values_list('user__real_name', flat=True)[:80])
        hit = next((n for n in names if n and n in text), None)
        if hit:
            uc2 = (UserCompany.objects.filter(company=company, user__real_name=hit,
                                              is_deleted=False,
                                              status=UserCompany.Status.ACTIVE)
                   .select_related('user', 'post').first())
            if uc2:
                today = timezone.localdate()
                recs = _record_base(company).filter(user=uc2.user)
                mo = _output_totals(recs.filter(registered_at__year=today.year,
                                                registered_at__month=today.month))
                dd = _output_totals(recs.filter(registered_at__date=today))
                last = list(recs.select_related('process')
                            .order_by('-registered_at')[:5])
                ent['employee_detail'] = {
                    'name': hit, 'post': (uc2.post.name if uc2.post else '未设岗位'),
                    'month': {'qty': mo[0], 'amount': Q2(mo[3])},
                    'today': {'qty': dd[0], 'amount': Q2(dd[3])},
                    'recent_records': [
                        {'date': str(r.registered_at.date()),
                         'process': (r.process.name if r.process else ''),
                         'qty': r.qty} for r in last]}

    return ent


def attach_entities(pack, company, user, is_admin, question, extra_text=''):
    """按问句命中实体追加明细（确定性正则 + ORM 精查，不依赖模型）；
    问句没提到任何实体且给了 extra_text（上一轮用户原话）时，用上一轮兜底
    ——支撑「查一下SOxxx」→「那它呢」的代词追问。"""
    ent = _extract_entities(company, user, is_admin, question)
    if not ent and extra_text:
        ent = _extract_entities(company, user, is_admin, extra_text[:200])
    if ent:
        pack['matched_entities'] = ent
    return pack
