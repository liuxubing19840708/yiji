# -*- coding: utf-8 -*-
"""生产计划表 plans 聚合（自 DashboardView 抽出，供 dashboard 与看板模板渲染共用，
保证两个入口口径一致）。

行粒度 = 款号+下单日期+颜色：每个款式每个颜色一行，同一 (款号,下单日期) 的颜色行
连续排在一起；只含 order_date（该款最早床次创建日期）落在 [plan_start, plan_end]
内的款式。排序：下单日期 → 款号 → 颜色。
"""
from datetime import timedelta

from django.db.models import Count, Min, Sum

from django.utils import timezone

from apps.catalog.models import Style, Ticket
from apps.production.models import Production
from apps.production.stats import style_color_process_stats, style_order_no_map


def plan_window(range_key, start, end):
    """range key → 计划表过滤窗口 (plan_start, plan_end)。

    今日/昨日=单日；本周=周一~今天；自定义=闭区间；上月=整月；
    本月（默认含非法值回落）=1 日~月末（含未来下单）。
    """
    today = timezone.localdate()
    if range_key == 'today':
        return today, today
    if range_key == 'yesterday':
        d = today - timedelta(days=1)
        return d, d
    if range_key == 'week':
        return start, end  # 本周一 ~ 今天（不含未来下单）
    if range_key == 'custom':
        return start, end  # 自定义闭区间
    if range_key == 'last_month':
        return (today.replace(day=1) - timedelta(days=1)).replace(day=1), \
            today.replace(day=1) - timedelta(days=1)
    # month（默认本月，含非法值回落）：1 日~月末（含未来下单）
    plan_start = today.replace(day=1)
    return plan_start, (plan_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def _attach_detail(company, plans, prods):
    """detail 模式：为 plans 各行补齐看板全量字段（批量查询，不引入 N+1）。

    补充字段：款式（客户/单价/备注）、床次（计划数/面料/来源单号/订单类型/
    床次客户/建床日期/床次数/裁完状态）、菲票聚合（扎数/尺码集合/工序进度）。
    """
    if not plans:
        return
    pids = {p.id for p in prods}

    # 1) 床次级扩展信息：一条 values 带齐（含 customer 名称 join）
    prod_extra = {
        r['id']: r for r in Production.objects.filter(company=company, pk__in=pids)
        .values('id', 'plan_qty', 'fabric', 'source_order_no', 'order_type',
                'customer__name', 'created_at', 'is_cut_done')
    }
    # 2) 款式级扩展信息：客户/单价/备注（一条查询）
    sid_all = {r['style_id'] for r in plans}
    style_extra = {
        r['id']: r for r in Style.objects.filter(company=company, pk__in=sid_all)
        .values('id', 'price', 'remark', 'customer__name')
    }
    # 3) 菲票聚合扩展：扎数 + 尺码集合（按 款+颜色 两条聚合）
    ticket_agg = {}
    for r in (Ticket.objects.filter(company=company,
                                    production__style_id__in=sid_all)
              .order_by()
              .values('production__style_id', 'color')
              .annotate(n=Count('id'))):
        ticket_agg[(r['production__style_id'], r['color'] or '')] = r['n']
    size_acc = {}  # (style_id, color) -> set(sizes)
    for r in (Ticket.objects.filter(company=company,
                                    production__style_id__in=sid_all)
              .order_by()
              .values_list('production__style_id', 'color', 'size')
              .distinct()):
        size_acc.setdefault((r[0], r[1] or ''), set()).add(r[2])

    # 4)+5) 工序总数 / 已完成工序：口径唯一（§2.9 stats.py），
    #       收编至 stats.style_color_process_stats（原内联实现逐位等价搬迁）
    proc_total, proc_done = style_color_process_stats(company, sid_all)

    for row in plans:
        sid, color = row['style_id'], row['color']
        beds_ids = list(row.get('_bed_ids', []))
        sx = style_extra.get(sid, {})
        row['style_customer_name'] = sx.get('customer__name') or ''
        row['style_price'] = sx.get('price')
        row['style_remark'] = sx.get('remark') or ''
        row['bed_count'] = len(beds_ids)
        bed_rows = [prod_extra.get(bid, {}) for bid in beds_ids]
        row['plan_qty'] = sum(int(b.get('plan_qty') or 0) for b in bed_rows)
        row['fabric'] = '\n'.join(dict.fromkeys(
            b.get('fabric') for b in bed_rows if b.get('fabric')))
        row['source_order_no'] = '\n'.join(dict.fromkeys(
            b.get('source_order_no') for b in bed_rows if b.get('source_order_no')))
        row['order_type'] = '、'.join(dict.fromkeys(
            b.get('order_type') for b in bed_rows if b.get('order_type')))
        row['customer_name'] = '、'.join(dict.fromkeys(
            b.get('customer__name') for b in bed_rows if b.get('customer__name')))
        created = [b.get('created_at') for b in bed_rows if b.get('created_at')]
        row['bed_created_date'] = (timezone.localdate(min(created)).strftime('%Y-%m-%d')
                                   if created else '')
        row['is_cut_done_all'] = '是' if bed_rows and all(
            b.get('is_cut_done') for b in bed_rows) else '否'
        row['ticket_count'] = ticket_agg.get((sid, color), 0)
        row['size_text'] = '、'.join(sorted(s for s in size_acc.get((sid, color), set())
                                           if s))
        done = proc_done.get((sid, color), set())
        total_ids = proc_total.get(sid, set())
        row['process_total'] = len(total_ids)
        row['process_done'] = len(done & total_ids) if total_ids else len(done)
        row['process_percent'] = (round(row['process_done'] / row['process_total'] * 100, 1)
                                  if row['process_total'] else 0)
    for r in plans:
        r.pop('_bed_ids', None)


def build_plan_rows(company, plan_start, plan_end, prods=None, detail=False):
    """构建 plans 列表（与原 DashboardView 内联实现逐字段等价）。

    prods：可选传入已查好的床次列表（DashboardView 复用同一查询），
    缺省自行查询。
    """
    plans = []
    if prods is None:
        prods = list(Production.objects.filter(company=company)
                     .select_related('style').order_by('style_id', 'id'))
    if not prods:
        return plans

    style_map = {}  # style_id -> 款号级信息（每款一行组）
    for p in prods:
        row = style_map.get(p.style_id)
        if row is None:
            row = {
                'style_no': p.style.style_no,
                'style_name': p.style.name,
                'prod_qty': {},   # 床次id -> 裁货数量（款内全部床次，去重）
            }
            style_map[p.style_id] = row
        row['prod_qty'][p.id] = int(p.total_qty or 0)

    style_ids = list(style_map)

    # 下单日期：该款最早床次的创建日期
    date_map = {r['style_id']: timezone.localdate(r['first']) for r in
                Production.objects.filter(company=company, style_id__in=style_ids)
                .order_by()
                .values('style_id').annotate(first=Min('created_at'))}

    # 计划表按 range 过滤：只保留 order_date 落在所选区间内的款式
    style_ids = [sid for sid in style_ids
                 if plan_start <= date_map.get(sid) <= plan_end]

    # 菲票按（款, 床次, 颜色）一次聚合：每色行的订单数量与涉及床次。
    # 一床混多色时该床在每个色的行里都出现，数量只计该色菲票数；
    # 已裁数量按「该色涉及床次的裁货合计」口径（混色床整床数量计入每个色行）
    prod_bed_no = {p.id: p.bed_no for p in prods}
    color_acc = {}  # (style_id, color) -> {qty: 该色订单数量, beds: {床次id: 该色菲票数}}
    for r in (Ticket.objects.filter(
            company=company, production__style_id__in=style_ids)
            .order_by()
            .values('production__style_id', 'production_id', 'color')
            .annotate(qty=Sum('qty'))):
        acc = color_acc.setdefault(
            (r['production__style_id'], r['color'] or ''),
            {'qty': 0, 'beds': {}})
        qty_r = int(r['qty'] or 0)
        acc['qty'] += qty_r
        pid_r = r['production_id']
        acc['beds'][pid_r] = acc['beds'].get(pid_r, 0) + qty_r

    # 已裁合计（款号级）= 该款号+该下单日期下所有颜色行的「已裁数量」之和
    # （列内自洽：同款各行已裁数量加起来=已裁合计；混色床整床数计入各色行）
    cut_total_map = {}
    for (sid_c, _color), acc in color_acc.items():
        beds_qty = style_map[sid_c]['prod_qty']
        cut_total_map[sid_c] = cut_total_map.get(sid_c, 0) + sum(
            beds_qty[pid] for pid in acc['beds'])

    # 挂单订单号（P1 契约 §四：plans 行加 order_no = 该款最早挂单订单号，多单取最早，
    # 无单空串；订单域未就绪/无挂单时全空串，存量行为不变）
    order_no_map = style_order_no_map(company, style_ids)

    # 订单数量（订单域 P4）：该款挂单订单的色码明细按颜色汇总的订单量。
    # 行粒度=款+色 → 取该色 OrderItem.qty 合计（多单同色累加）；
    # 无挂单/订单域未就绪 → 0（渲染「0」与旧口径无冲突）
    order_total_map = {}
    try:
        from apps.orders.models import OrderItem
        for r in (OrderItem.objects.filter(
                order__company=company, order__is_deleted=False,
                order__style_id__in=style_ids)
                .order_by()  # order_by() 前置：防 Meta.ordering 混入 GROUP BY
                .values('order__style_id', 'color')
                .annotate(q=Sum('qty'))):
            order_total_map[(r['order__style_id'], r['color'] or '')] = int(r['q'] or 0)
    except Exception:
        order_total_map = {}  # 订单域未安装：全 0，存量行为不变

    for sid in style_ids:
        sm = style_map[sid]
        order_date = date_map.get(sid).strftime('%Y-%m-%d') \
            if date_map.get(sid) else ''
        # 每个颜色一行：床次按建床先后（床次 id 序）排
        for (_sid, color), acc in sorted(
                (item for item in color_acc.items() if item[0][0] == sid),
                key=lambda item: item[0][1]):
            plans.append({
                'style_id': sid,
                'style_no': sm['style_no'],
                'style_name': sm['style_name'],
                'order_date': order_date,
                'color': color,
                'order_no': order_no_map.get(sid, ''),
                'order_qty': acc['qty'],  # 该款该色的菲票 qty 合计
                'order_total': order_total_map.get((sid, color), 0),  # 该款该色的订单数量
                # 床次列：该色涉及床次「床号×该色菲票数」
                'beds': [{'bed_no': prod_bed_no[pid], 'qty': q}
                         for pid, q in sorted(acc['beds'].items())],
                # 已裁数量：该色涉及床次的裁货合计（混色床整床计入各色行）
                'cut_qty': sum(sm['prod_qty'][pid] for pid in acc['beds']),
                # 已裁合计（款号级）：同款各颜色行重复显示同一个值
                'cut_total': cut_total_map.get(sid, 0),
                '_bed_ids': list(acc['beds']),  # detail 模式用，渲染前剥掉
            })
    if detail:
        _attach_detail(company, plans, prods)
    for r in plans:
        r.pop('_bed_ids', None)
    # 计划表排序：按下单日期先后从上到下（Excel 观感），同日内按款号、颜色；
    # 前端再按 order_date 分组成日期小标题
    plans.sort(key=lambda r: (r['order_date'], r['style_no'],
                              r['color'], r['style_id']))
    return plans
