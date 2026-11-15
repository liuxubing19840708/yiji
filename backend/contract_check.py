# -*- coding: utf-8 -*-
"""前后端契约比对：前端 services 里所有 http.<m>('<path>') vs 后端路由表。"""
import re
import io
import os
import glob

FRONT_GLOBS = [
    r'D:/project/扫菲/frontend/services/*.ts',
    r'D:/project/扫菲/frontend/packages/*/services/*.ts',
    r'D:/project/扫菲/frontend/utils/*.ts',
]

# ---- 前端：提取 http.<m>('<path>') ----
fe = set()
for pattern in FRONT_GLOBS:
    for f in glob.glob(pattern):
        s = io.open(f, encoding='utf-8').read()
        rx = r"http\.(get|post|put|del|delete|patch)(?:<[^>]*>)?\(\s*(['\"`])(/[^'\"`]+)\2"
        for m, _q, path in re.findall(rx, s):
            p = re.sub(r'\$\{[^}]+\}', '<p>', path).rstrip('/')
            p = p.lstrip('/')  # 前端路径相对 /api 基址
            fe.add((m.lower(), p))

# ---- 后端：收集全部路由 ----
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.urls import get_resolver

be = set()


def walk(res, prefix=''):
    for p in res.url_patterns:
        if hasattr(p, 'url_patterns'):
            walk(p, prefix + str(p.pattern))
        else:
            route = (prefix + str(p.pattern)).rstrip('/')
            if route.startswith('api/'):
                be.add(re.sub(r'<(?:int|str|slug):[^>]+>', '<p>', route[4:]))


walk(get_resolver())

print('=== 前端调用的接口 (%d 个路径) ===' % len(fe))
missing = []
for m, p in sorted(fe):
    ok = p in be
    print('%-5s /%s%s' % (m.upper(), p, '' if ok else '   <-- 后端无此路由!'))
    if not ok:
        missing.append((m, p))

print()
print('后端路由总数:', len(be))
print('未匹配:', len(missing))
for m, p in missing:
    print('  MISSING', m.upper(), '/' + p)

# ---- 后端先行注册、前端尚未消费的契约路由（SA3 扫菲增强）----
# 出现在下表的路由必须已存在于后端（方向相反的单向核对）。
BACKEND_FIRST = [
    ('get', 'tickets/lookup'),              # SA3 手输码兜底：GET /tickets/lookup
    # B1 工序模板 + 视频上传（2026-10-21 用户 8 项需求 #1/#2，后端先行）
    ('get', 'process-templates'),           # GET  /process-templates
    ('post', 'process-templates'),          # POST /process-templates
    ('get', 'process-templates/<p>'),       # GET  /process-templates/{id}
    ('put', 'process-templates/<p>'),       # PUT  /process-templates/{id}
    ('del', 'process-templates/<p>'),       # DELETE /process-templates/{id}
    ('post', 'uploads/video'),              # POST /uploads/video
    ('post', 'uploads/files'),              # 产前纸样、工艺单与样衣附件
    ('post', 'preproduction/tech-packs/<p>/briefing-confirmation'),
    ('put', 'preproduction/issues/<p>'),    # resolve/reopen 轻量问题处理
    # P1 领料定额化（SA2 warehouse 域，2026-10-24）
    ('get', 'issues/divergence'),           # GET /issues/divergence 超额领料差异报表
    # P1 tech 域（SA1 物料+BOM，2026-10-24，前端 SF 对接前登记）
    ('get', 'tech/materials'),              # GET  /tech/materials?keyword=&category=
    ('post', 'tech/materials'),             # POST /tech/materials
    ('post', 'tech/materials/import'),      # POST /tech/materials/import
    ('get', 'tech/materials/<p>'),          # GET  /tech/materials/{id}
    ('put', 'tech/materials/<p>'),          # PUT  /tech/materials/{id}
    ('del', 'tech/materials/<p>'),          # DELETE /tech/materials/{id}
    ('get', 'tech/boms'),                   # GET  /tech/boms?style_id=&version=
    ('post', 'tech/boms'),                  # POST /tech/boms
    ('post', 'tech/boms/import'),           # POST /tech/boms/import
    ('get', 'tech/boms/<p>'),               # GET  /tech/boms/{id}
    ('put', 'tech/boms/<p>'),               # PUT  /tech/boms/{id}
    ('del', 'tech/boms/<p>'),               # DELETE /tech/boms/{id}
    ('post', 'tech/boms/<p>/activate'),     # POST /tech/boms/{id}/activate
    ('post', 'tech/boms/<p>/fork'),         # POST /tech/boms/{id}/fork
    ('get', 'tech/boms/<p>/references'),    # GET  /tech/boms/{id}/references
    # P1 cost 域（SA3 订单成本，2026-10-24，前端 SF 对接前登记）
    ('get', 'cost/orders'),                 # GET  /cost/orders?date_from=&date_to=&keyword=&computed_at_from=
    ('get', 'cost/orders/pnl'),             # GET  /cost/orders/pnl?keyword=&date_from=&date_to=&status= 损益视图（2026-11-02）
    ('post', 'finance/customers/<p>/receipts-batch'),  # POST /finance/customers/{customer_id}/receipts-batch 批量核销（2026-11-02）
    ('post', 'cost/orders/<p>/recompute'),  # POST /cost/orders/{order_id}/recompute
    ('post', 'cost/orders/recompute-batch'),  # POST /cost/orders/recompute-batch
    ('put', 'cost/orders/<p>/overhead'),    # PUT  /cost/orders/{order_id}/overhead
    ('get', 'cost/wage-reconciliation'),    # GET  /cost/wage-reconciliation?date_from=&date_to=
    # P2 报价域（SA-P2-3 报价管理，2026-10-24，前端 SF-P2-2 对接前登记）
    ('get', 'quotation/quotations'),        # GET  /quotation/quotations?status=&customer_id=&style_id=&keyword=
    ('post', 'quotation/quotations'),       # POST /quotation/quotations
    ('get', 'quotation/quotations/<p>'),    # GET  /quotation/quotations/{id}
    ('put', 'quotation/quotations/<p>'),    # PUT  /quotation/quotations/{id}
    ('del', 'quotation/quotations/<p>'),    # DELETE /quotation/quotations/{id}（软删）
    ('post', 'quotation/quotations/<p>/mark-status'),  # POST mark-status（sent/won/lost，won 带 order_id）
    ('get', 'quotation/quotations/<p>/backfill-compare'),  # GET 回填对比（D3 软提醒只读）
    # P2 finance 应收域（SA-P2-2，2026-10-24，出货域 SA-P2-4 对接前登记）
    ('get', 'finance/receivables'),                    # GET  /finance/receivables?status=&customer_id=&keyword=&date_from=&date_to=
    ('post', 'finance/receivables'),                   # POST /finance/receivables 手工建应收（出货域自动建走同 service）
    ('post', 'finance/receivables/<p>/receipts'),      # POST /finance/receivables/{id}/receipts 核销（收款）
    ('post', 'finance/receivables/<p>/receipts/revoke'),  # POST /finance/receivables/{id}/receipts/revoke 撤销最近一条核销
    ('post', 'finance/receivables/<p>/void'),          # POST /finance/receivables/{id}/void 作废
    ('get', 'finance/receivables/<p>'),                # GET  /finance/receivables/{id} 详情（INT 补缺口 A，2026-10-24）
    ('get', 'finance/receivables/<p>/receipt-lines'),  # GET  /finance/receivables/{id}/receipt-lines 收款流水（INT 补缺口 B）
    ('get', 'finance/stock-valuation'),                # GET  /finance/stock-valuation?material_id=&date_to= 移动加权估值
    # P2 出货域（SA-P2-4，2026-10-24，前端 SF-P2-1 对接前登记）
    ('get', 'shipments'),                              # GET  /shipments?status=&order_id=&customer_id=&keyword=
    ('post', 'shipments'),                             # POST /shipments 建单（单号留空自动 SH）
    ('get', 'shipments/<p>'),                          # GET  /shipments/{id}
    ('put', 'shipments/<p>'),                          # PUT  /shipments/{id}（草稿全量改/已审核仅物流等）
    ('del', 'shipments/<p>'),                          # DELETE /shipments/{id}（仅草稿；已审核走作废）
    ('post', 'shipments/<p>/audit'),                   # POST /shipments/{id}/audit 审核（三道校验+自动建应收）
    ('post', 'shipments/<p>/void'),                    # POST /shipments/{id}/void 作废（warning 提示应收联动）
    ('get', 'shipments/<p>/undelivered'),              # GET  /shipments/{id}/undelivered 未交量矩阵（真口径）
    ('get', 'shipments/<p>/boxes'),                    # GET  /shipments/{id}/boxes 装箱行列表
    ('post', 'shipments/<p>/boxes'),                   # POST /shipments/{id}/boxes 加装箱行
    ('del', 'shipments/<p>/boxes/<p>'),                # DELETE /shipments/{id}/boxes/{box_id}
    ('get', 'shipments/<p>/print-logs'),               # GET  /shipments/{id}/print-logs 打印历史
    ('post', 'shipments/<p>/print-logs'),              # POST /shipments/{id}/print-logs 打印上报（print_key 幂等）
    # P2 采购补全域（SA-P2-1 warehouse 扩展，2026-10-24，前端 SF 对接前登记）
    ('get', 'suppliers'),                              # GET  /suppliers?keyword= 供应商列表
    ('post', 'suppliers'),                             # POST /suppliers
    ('get', 'suppliers/<p>'),                          # GET  /suppliers/{id}
    ('put', 'suppliers/<p>'),                          # PUT  /suppliers/{id}
    ('del', 'suppliers/<p>'),                          # DELETE /suppliers/{id}（软删，被引用拒删）
    ('get', 'receipts'),                               # GET  /receipts?status=&keyword= 到料暂收列表
    ('post', 'receipts'),                              # POST /receipts 暂收（单号留空自动 ZS）
    ('get', 'receipts/<p>'),                           # GET  /receipts/{id}
    ('put', 'receipts/<p>'),                           # PUT  /receipts/{id} 暂存修改+IQC 行级拆量（仅 draft）
    ('del', 'receipts/<p>'),                           # DELETE /receipts/{id}（仅 draft）
    ('post', 'receipts/<p>/confirm'),                  # POST /receipts/{id}/confirm 确认→合格量自动采购入库（P1 价链带价）
    ('get', 'demand-summary'),                         # GET  /demand-summary?style_id=&customer_id= 采购需求汇总（口径见视图 docstring）
    # 上线落地 D1 期初库存导入（SA-P2-5，2026-10-24，前端 SF 对接前登记）
    ('post', 'opening-stock/import'),                  # POST /opening-stock/import 期初库存 Excel 导入（all-or-nothing）
    # AI 报表问答（2026-10-25 新功能，后端已交付测试 13/13）
    ('post', 'ai/report/query'),                       # POST /ai/report/query 自然语言问报表（GLM 意图解析+白名单报表）
    ('get', 'orders/<p>/audit-logs'),                  # GET /orders/{id}/audit-logs 订单变更留痕（差距 #3，2026-11-03）
]
extra_missing = [(m, p) for m, p in BACKEND_FIRST if p not in be]
print('后端先行路由:', len(BACKEND_FIRST), '个，缺失:', len(extra_missing))
for m, p in extra_missing:
    print('  BACKEND-MISSING', m.upper(), '/' + p)
if extra_missing:
    missing.extend(extra_missing)
