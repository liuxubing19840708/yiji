# -*- coding: utf-8 -*-
"""全链路数据一致性审计（一次性脚本）：款式→床次→菲票→登记→看板/工资逐环节对账。

只读，不改任何数据。逐项输出 OK / 异常清单。
"""
from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone

from apps.accounts.models import Company, UserCompany
from apps.catalog.models import Process, Style, Ticket
from apps.core.utils import build_ticket_qr
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record

issues = []          # (级别, 环节, 描述)
def flag(level, stage, msg):
    issues.append((level, stage, msg))
    print(f'  [{level}] {stage}: {msg}')

def ok(stage, msg):
    print(f'  [OK] {stage}: {msg}')

now = timezone.now()
month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

for co in Company.objects.all():
    print(f'\n===== 公司 {co.name}({co.company_no}) id={co.id} =====')
    styles = list(Style.objects.filter(company=co))
    prods = list(Production.objects.filter(company=co).select_related('style'))
    tickets = list(Ticket.objects.filter(company=co).select_related('production', 'production__style'))
    records = list(Record.objects.filter(company=co).select_related('ticket', 'process', 'user'))
    ok('规模', f'款式 {len(styles)} · 床次 {len(prods)} · 菲票 {len(tickets)} · 登记 {len(records)}')

    # ---- 环节1: 床次 ↔ 菲票（数量账） ----
    qty_by_prod = {}
    for t in tickets:
        qty_by_prod[t.production_id] = qty_by_prod.get(t.production_id, 0) + t.qty
    for p in prods:
        t_sum = qty_by_prod.get(p.id, 0)
        if t_sum > p.total_qty:
            flag('ERROR', '床次→菲票', f'床次{p.bed_no}({p.style.style_no}) 菲票合计 {t_sum} > 裁货 {p.total_qty}')
        elif p.is_cut_done and t_sum < p.total_qty:
            flag('WARN', '床次→菲票', f'床次{p.bed_no} 已裁完但菲票合计 {t_sum} < 裁货 {p.total_qty}')
    ok('床次→菲票', '数量账核对完成')

    # ---- 环节2: 菲票字段一致性（bed_no / qr_code 重建比对 / 唯一性） ----
    qr_seen = {}
    for t in tickets:
        p = t.production
        if p.company_id != t.company_id:
            flag('ERROR', '菲票跨厂', f'菲票 id={t.id} company={t.company_id} 但床次属 company={p.company_id}')
        if t.bed_no != p.bed_no:
            flag('ERROR', '菲票字段', f'菲票 id={t.id} bed_no={t.bed_no} 与床次 {p.bed_no} 不一致')
        expect = build_ticket_qr(co.company_no, t.bed_no, p.style.style_no, t.bundle_no, t.color, t.size, t.qty)
        if t.qr_code != expect:
            flag('WARN', '二维码', f'菲票 id={t.id} qr={t.qr_code[:60]} 与重建 {expect[:60]} 不一致')
        if t.qr_code in qr_seen:
            flag('ERROR', '二维码', f'菲票 id={t.id} 与 id={qr_seen[t.qr_code]} 二维码内容重复')
        qr_seen[t.qr_code] = t.id
    ok('菲票字段', f'{len(tickets)} 张核对完成')

    # ---- 环节3: 登记 ↔ 菲票（跨厂/未配置工序/超量/单价快照） ----
    proc_cfg = {}  # production_id -> ({process_id: price}, style_id, style_proc_ids)
    for pp in ProductionProcess.objects.filter(production__company=co):
        proc_cfg.setdefault(pp.production_id, {})[pp.process_id] = pp.price
    style_proc_ids = {}
    for sp in Style.objects.filter(company=co).prefetch_related('processes'):
        style_proc_ids[sp.id] = {x.process_id for x in sp.processes.all()}
    for r in records:
        t = r.ticket
        p = t.production
        if t.company_id != r.company_id:
            flag('ERROR', '登记跨厂', f'登记 id={r.id} company={r.company_id} 但菲票属 company={t.company_id}')
            continue
        cfg = proc_cfg.get(p.id) or {}
        allowed = set(cfg) or style_proc_ids.get(p.style_id, set())
        if r.process_id not in allowed:
            flag('WARN', '未配置工序', f'登记 id={r.id} 工序{r.process_id}({r.process.name}) 未在床次{p.bed_no}/款式配置中')
        if r.qty > t.qty:
            flag('ERROR', '登记超量', f'登记 id={r.id} qty={r.qty} > 菲票{t.bed_no}#{t.bundle_no} 数量{t.qty}')
        if r.process_id in cfg and r.price != cfg[r.process_id]:
            flag('WARN', '单价快照', f'登记 id={r.id} price={r.price} ≠ 订单工序价 {cfg[r.process_id]}')
        # 被登记人须为在职成员
        if not UserCompany.objects.filter(company_id=r.company_id, user_id=r.user_id).exists():
            flag('WARN', '登记人', f'登记 id={r.id} 用户{r.user_id} 非本公司成员（历史/离职）')
    ok('登记→菲票', f'{len(records)} 条核对完成')

    # ---- 环节4: 完成口径重算（每扎 distinct 工序 ≥ 配置数 = 完成） ----
    reg = {}
    for r in records:
        key = r.ticket_id
        reg.setdefault(key, {'pn': set(), 'qty': 0})
        reg[key]['pn'].add(r.process_id)
        reg[key]['qty'] += r.qty
    done_tickets = 0
    for t in tickets:
        cfg_count = len(proc_cfg.get(t.production_id) or {}) \
            or len(style_proc_ids.get(t.production.style_id, set()))
        info = reg.get(t.id)
        if info and info['pn'] and len(info['pn']) >= cfg_count and cfg_count:
            done_tickets += 1
    ok('完成口径', f'菲票 {len(tickets)} 张，已配满工序 {done_tickets} 张')

    # ---- 环节5: 看板/工资 API 对账（用测试客户端走真实接口） ----
    from django.test import Client
    c = Client()
    tok = c.post('/api/auth/login', {'phone': '13800000001', 'password': 'yisao1234'},
                 content_type='application/json').json().get('access')
    if not tok:
        print('  [SKIP] 无法登录测试账号，跳过 API 对账')
        continue
    H = {'HTTP_AUTHORIZATION': f'Bearer {tok}', 'HTTP_X_COMPANY_ID': str(co.id)}
    d = c.get('/api/dashboard', {'range': 'month'}, **H)
    if d.status_code == 200:
        s = d.json()['summary']
        raw_reg = sum(r.qty for r in records if r.registered_at >= month_start)
        raw_tickets = len({r.ticket_id for r in records if r.registered_at >= month_start})
        raw_active = sum(1 for p in prods if not p.is_cut_done)
        raw_cut = sum(p.total_qty for p in prods)
        raw_order = sum(t.qty for t in tickets)
        checks = [
            ('登记件数', s['registered_qty'], raw_reg),
            ('经手菲票', s['ticket_count'], raw_tickets),
            ('在产床次', s['active_beds'], raw_active),
            ('裁货总量', s['cut_total'], raw_cut),
            ('订单总量', s['order_total'], raw_order),
        ]
        for name, api, raw in checks:
            if api != raw:
                flag('ERROR', f'看板对账·{name}', f'API={api} 数据库={raw}')
        ok('看板对账', 'summary 五项与数据库一致' if all(a == r for _, a, r in checks) else '见上方异常')
    else:
        print(f'  [SKIP] dashboard 非 200：{d.status_code}')

    sal = c.get('/api/salary/company', **H)
    if sal.status_code == 200:
        data = sal.json()
        rows = data.get('results') or data.get('rows') or data.get('employees') or []
        raw_amt = {}
        for r in records:
            if r.registered_at >= month_start and r.settlement_id is None:
                raw_amt[r.user_id] = raw_amt.get(r.user_id, 0) + float(r.qty) * float(r.price)
        mismatch = 0
        for row in rows:
            uid = row.get('user_id') or row.get('id')
            api_amt = float(row.get('amount') or row.get('salary') or row.get('total') or 0)
            raw_v = round(raw_amt.get(uid, 0), 2)
            if abs(api_amt - raw_v) > 0.05 and raw_v > 0:
                mismatch += 1
                flag('WARN', '工资对账', f'用户 {row.get("name") or uid} API={api_amt} 直算={raw_v}（可能有调整项/口径差异）')
        if not mismatch:
            ok('工资对账', f'{len(rows)} 名员工金额与登记直算一致（未结算部分）')
    else:
        print(f'  [SKIP] salary/company 非 200：{sal.status_code}')

print(f'\n===== 汇总：{len(issues)} 条待关注（ERROR {sum(1 for i in issues if i[0]=="ERROR")} / WARN {sum(1 for i in issues if i[0]=="WARN")}） =====')
