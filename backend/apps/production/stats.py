# -*- coding: utf-8 -*-
"""完成度/进度唯一口径（§2.9「stats.py 口径唯一」P0 地基）。

背景：重构前「完工=配满工序 / 在制=已登未满 / 已裁 / 登记」四组口径散落在
5 处重复实现（工作台 WorkbenchView、床次列表 _production_record_stats、
dashboard 汇总卡、计划表 plan_rows 工序进度、款式统计 attach_style_stats），
且各处存在口径分叉（详见本文件收编记录）。消费方一律改调这里，改口径只动本文件。

口径约定（写入设计，一期不动主体）：
  - 登记量 = 合格量（qty_defect 次品预留列，一期不消费，未来只改分子）；
  - 床次配置工序数 pc = 订单工序（ProductionProcess）数，为 0 时退回款式工序
    （StyleProcess）数【逐床次独立回落，不互相覆盖】；
  - 完工（扎级）= 该扎 distinct 已登记工序数 ≥ pc；pc=0 视为不可能完工
    （无配置工序的床次不产生完工扎，防 0 分母与虚高）；
  - 在制（扎级）= 已有登记但未配满工序；
  - 进度%（床次级）= 各扎 min(已登工序数, pc)/pc×100 的平均（单扎封顶 100 防
    脏数据 pn>pc）——登记件数是全部工序累加口径，直接除裁货数会超 100%
    （历史 630% bug）。

收编记录（2026-10-20，重构前 golden 快照见 backend/test_stats_refactor.py）：
  1. records/views.py WorkbenchView 床次进度 ←→ bed_progress_stats（逐位相等）；
  2. production/views.py _production_record_stats ←→ bed_progress_stats。
     原实现 Record 过滤用 company__productions__id__in 跨表 JOIN，同一厂每张床
     都会把登记行放大 N 倍（N=厂内床次数），列表页 registered_qty 虚高（golden
     前快照 585/150，统一后 195/50，与工作台一致）——收编即修复（STAT-010 要求
     四消费方口径相等，join 放大与位级保留互斥，取与工作台一致的多数派口径）；
  3. production/views.py DashboardView 汇总卡 done/working 拆分 ←→
     production_pc_map + done_working_qty。原 pc_map 缺失回落循环把「有订单工序
     床次」的 pc 一并覆盖成款式工序数（混入 0 工序床次时全厂 pc 被改写），
     golden 前快照 done/working=120/125，统一后 170/75——收编即修复（同上）；
  4. production/plan_rows.py 款×色工序进度 ←→ style_color_process_stats（逐位相等）；
  5. catalog/views.py attach_style_stats 款式统计行 ←→ style_ticket_stats（逐位相等）。
"""
from django.db.models import Count, Sum


def production_pc_map(production_ids):
    """床次 → 配置工序数（订单工序优先，逐床次独立退回款式工序；批量防 N+1）。

    消费方：床次进度（bed_progress_stats）、dashboard 汇总卡 done/working 拆分。
    """
    from apps.catalog.models import StyleProcess
    from apps.production.models import Production, ProductionProcess

    pids = list(production_ids)
    if not pids:
        return {}
    pc_map = {row['production_id']: row['c'] for row in
              ProductionProcess.objects.filter(production_id__in=pids)
              .order_by()  # ProductionProcess.Meta.ordering=['id'] 会混入 GROUP BY
              .values('production_id').annotate(c=Count('id'))}
    missing = {pid for pid in pids if pid not in pc_map}
    if missing:
        pid_style = dict(Production.objects.filter(id__in=missing)
                         .values_list('id', 'style_id'))
        style_pc = {row['style_id']: row['c'] for row in
                    StyleProcess.objects.filter(style_id__in=set(pid_style.values()))
                    .order_by()  # 同上，防排序字段混入 GROUP BY
                    .values('style_id').annotate(c=Count('id'))}
        for pid, sid in pid_style.items():
            pc_map[pid] = style_pc.get(sid, 0)
    return pc_map


def production_process_count(production):
    """单床次配置工序数（production_pc_map 的单条便捷版）。

    消费方：production/views.py _process_count（按扎查看/扫码解析的 process_count）。
    """
    return production_pc_map([production.id]).get(production.id, 0)


def ticket_record_rows(company, records_qs=None, production_ids=None):
    """登记按（床次, 菲票）聚合：[{ticket__production_id, ticket_id, qty, pn}]。

    - records_qs 优先（dashboard 范围过滤场景直接传入已切片的记录集）；
      否则按 company（+ 可选 production_ids）过滤；
    - qty = 登记件数合计（各工序累加口径），pn = distinct 已登记工序数；
    - order_by() 必须前置：Record.Meta.ordering 引用菲票字段会混入 GROUP BY。
    """
    from apps.records.models import Record

    if records_qs is not None:
        qs = records_qs
    else:
        qs = Record.objects.all()
        if company is not None:  # company=None：按 production_ids 圈定即可（床次进度）
            qs = qs.filter(company=company)
    if production_ids is not None:
        qs = qs.filter(ticket__production_id__in=production_ids)
    return list(qs.order_by()
                .values('ticket__production_id', 'ticket_id')
                .annotate(qty=Sum('qty'), pn=Count('process_id', distinct=True)))


def bed_progress_stats(company, production_ids):
    """床次进度唯一口径：{pid: (登记件数, 完工扎数, 扎总数, 进度%)}。

    消费方：工作台 WorkbenchView、床次列表/详情 _attach_stats
    （原 production/views.py _production_record_stats，join 放大 bug 见模块头收编记录 2）。
    """
    from apps.catalog.models import Ticket

    pids = list(production_ids)
    if not pids:
        return {}
    ticket_total = {row['production_id']: row['c'] for row in
                    Ticket.objects.filter(production_id__in=pids)
                    .values('production_id').annotate(c=Count('id'))}
    if not ticket_total:
        return {pid: (0, 0, 0, 0.0) for pid in pids}

    pc_map = production_pc_map(pids)
    qty_map, done_map, prog_map = {}, {}, {}
    for row in ticket_record_rows(company, production_ids=pids):
        pid = row['ticket__production_id']
        qty_map[pid] = qty_map.get(pid, 0) + int(row['qty'] or 0)
        pc = pc_map.get(pid) or 0
        if pc:
            # 单扎工序覆盖率：封顶 100（防御脏数据 pn>pc），按扎累加后取平均
            prog_map[pid] = prog_map.get(pid, 0.0) + min(row['pn'], pc) / pc * 100
        if pc and row['pn'] >= pc:
            done_map[pid] = done_map.get(pid, 0) + 1
    return {pid: (qty_map.get(pid, 0), done_map.get(pid, 0),
                  ticket_total.get(pid, 0),
                  round(prog_map.get(pid, 0.0) / ticket_total[pid], 1)
                  if ticket_total.get(pid) else 0.0)
            for pid in pids}


def done_working_qty(rows, pc_map):
    """dashboard 汇总卡：范围内（完成件数, 工作中件数）。

    完成口径：distinct 已登记工序数 ≥ 配置工序数（pc=0 不算完工）；
    完成 + 工作中 = 范围内已登记件数（登记件数为各工序累加口径）。
    """
    total_done = total_working = 0
    for row in rows:
        qty = int(row['qty'] or 0)
        pc = pc_map.get(row['ticket__production_id']) or 0
        if pc and row['pn'] >= pc:
            total_done += qty
        else:
            total_working += qty
    return total_done, total_working


def bundle_ticket_stats(company, production):
    """单床次按扎登记聚合：{ticket_id: row}，row 含 pn（distinct 工序数）/qty。

    消费方：按扎查看 ProductionBundleListView、扫码解析 ScanParseView
    （两处原各自内联同一条聚合，收编防分叉）。
    """
    from apps.records.models import Record

    rows = (Record.objects.filter(company=company, ticket__production=production)
            .order_by()  # order_by() 必须前置：避免 Meta.ordering 混入 GROUP BY
            .values('ticket_id')
            .annotate(pn=Count('process_id', distinct=True), qty=Sum('qty')))
    return {row['ticket_id']: row for row in rows}


def style_color_process_stats(company, style_ids):
    """款×色工序进度底座（计划表 detail 渲染用）。

    返回 (proc_total, proc_done)：
      proc_total: {style_id: set(process_id)}  款内全部床次订单工序并集；
      proc_done:  {(style_id, color): set(process_id)}  该款该色菲票上有
                  登记记录的工序去重。
    消费方：production/plan_rows.py _attach_detail（原内联实现逐位等价搬迁）。
    """
    from apps.production.models import ProductionProcess
    from apps.records.models import Record

    proc_total = {sid: set() for sid in style_ids}
    for sid, proc_id in (ProductionProcess.objects
                         .filter(production__company=company,
                                 production__is_deleted=False,
                                 production__style_id__in=style_ids)
                         .values_list('production__style_id', 'process_id')):
        proc_total[sid].add(proc_id)
    proc_done = {}
    for sid, color, proc_id in (Record.objects
                                .filter(company=company,
                                        ticket__production__style_id__in=style_ids)
                                .values_list('ticket__production__style_id',
                                             'ticket__color', 'process_id')
                                .distinct()):
        proc_done.setdefault((sid, color or ''), set()).add(proc_id)
    return proc_total, proc_done


def style_ticket_stats(company, style_ids):
    """款式列表统计行口径：裁货数/菲票数/工序数/完工扎数。

    返回 (cut_map, ticket_map, pc_map, done_map)：
      cut_map    {style_id: 菲票 qty 合计（=裁货数口径）}
      ticket_map {style_id: 菲票张数}
      pc_map     {style_id: 款式工序数（注意：款式级口径，非床次级）}
      done_map   {style_id: 完工扎数（该扎 distinct 工序数 ≥ 款式工序数）}
    消费方：catalog/views.py attach_style_stats（原内联实现逐位等价搬迁，
    未改「款式工序数」分母口径——床次级口径见 production_pc_map）。
    """
    from apps.catalog.models import StyleProcess, Ticket
    from apps.records.models import Record

    base = Ticket.objects.filter(company=company, production__style_id__in=style_ids)
    cut_map = {row['production__style_id']: row['cut'] or 0 for row in
               base.values('production__style_id').annotate(cut=Sum('qty'))}
    ticket_map = {row['production__style_id']: row['cnt'] for row in
                  base.values('production__style_id').annotate(cnt=Count('id'))}
    pc_map = {row['style_id']: row['pc'] for row in
              StyleProcess.objects.filter(style_id__in=style_ids)
              .values('style_id').annotate(pc=Count('process_id'))}
    done_map = {}
    # order_by() 必须前置：Record.Meta.ordering 引用菲票字段会混入 GROUP BY
    rows = (Record.objects.filter(company=company,
                                  ticket__production__style_id__in=style_ids)
            .order_by()
            .values('ticket_id', 'ticket__production__style_id')
            .annotate(pn=Count('process_id', distinct=True)))
    for row in rows:
        sid = row['ticket__production__style_id']
        pc = pc_map.get(sid, 0)
        if pc and row['pn'] >= pc:
            done_map[sid] = done_map.get(sid, 0) + 1
    return cut_map, ticket_map, pc_map, done_map


def order_item_progress(order):
    """订单色码矩阵完成度（§2.5.2，分子分母唯一口径，订单域端点直接下发）。

    分子 = 挂单床次（production.order_id = order）菲票登记按（颜色,尺码）聚合；
    分母 = OrderItem.qty。进度% 保留 1 位小数；分母为 0 时进度记 0。

    返回 {'items': [{color, size, qty, registered_qty, progress}],
          'total_qty', 'registered_qty', 'progress'}
    （registered_qty/progress 为矩阵格汇总口径；挂单床次登记落在矩阵外的
    颜色尺码不进格也不进汇总，防与 OrderItem 分母口径错配虚高）。

    注意：apps.orders 由 SA1 并行交付，调用方需自行保证其可导入
    （本函数不做 try 兜底，暴露真实错误便于 INT 期发现）。
    """
    from apps.orders.models import OrderItem
    from apps.records.models import Record

    items = list(OrderItem.objects.filter(order=order)
                 .order_by('id').values('color', 'size', 'qty'))
    reg_map = {}
    for row in (Record.objects.filter(company=order.company_id,
                                      ticket__production__order=order)
                .order_by()  # order_by() 必须前置：Meta.ordering 会混入 GROUP BY
                .values('ticket__color', 'ticket__size')
                .annotate(s=Sum('qty'))):
        reg_map[(row['ticket__color'] or '', row['ticket__size'] or '')] = \
            int(row['s'] or 0)
    rows, total_qty, total_reg = [], 0, 0
    for it in items:
        qty = int(it['qty'] or 0)
        reg = reg_map.get((it['color'] or '', it['size'] or ''), 0)
        total_qty += qty
        total_reg += reg
        rows.append({'color': it['color'] or '', 'size': it['size'] or '',
                     'qty': qty, 'registered_qty': reg,
                     'progress': round(reg / qty * 100, 1) if qty else 0})
    return {'items': rows, 'total_qty': total_qty, 'registered_qty': total_reg,
            'progress': round(total_reg / total_qty * 100, 1) if total_qty else 0}


def style_order_no_map(company, style_ids):
    """款号 → 最早挂单订单号（多单取最早；无单/订单域未就绪 → 空串）。

    「最早」= 挂单床次上订单 created_at 最早（同刻取订单 id 小者）；
    只认本公司未删除订单（历史无单床次不进任何订单——挂单进度算重防线）。
    订单域（apps.orders，SA1 并行交付）未安装或 Production.order 列未迁移时
    回落空 dict（计划表 order_no 全空串，存量行为不变）。
    """
    try:
        from apps.orders.models import Order  # noqa: F401  延迟 import（合并前可能不存在）
        from apps.production.models import Production
        Production._meta.get_field('order')
    except Exception:  # ModuleNotFoundError / FieldDoesNotExist：订单域未就绪
        return {}
    best = {}
    rows = (Production.objects.filter(
        company=company, style_id__in=style_ids,
        order__isnull=False, order__is_deleted=False)
        .values('style_id', 'order_id', 'order__order_no', 'order__created_at'))
    for r in rows:
        key = (r['order__created_at'], r['order_id'])
        cur = best.get(r['style_id'])
        if cur is None or key < cur[0]:
            best[r['style_id']] = (key, r['order__order_no'] or '')
    return {sid: v[1] for sid, v in best.items()}
