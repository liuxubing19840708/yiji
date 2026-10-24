# 服装ERP全模块设计方案（对标华邃九大模块）

> 2026-10-24。依据《ERP九大模块调研报告.md》（8/8 论断经来源原文核对成立）+ 本系统现状代码盘点。
> 目标：在现有扫菲 SaaS（Django 5.2 + 微信小程序）上补齐全模块，**复用菲票扫码主链路，不动已稳定的核心域**。

---

## 一、现状盘点（九模块 × 已有/缺口矩阵）

| # | 模块 | 已有底子 | 缺口 |
|---|------|---------|------|
| 1 | 订单管理 | `orders.Order/OrderItem`（**颜色×尺码×数量矩阵已有**，同单同色码唯一约束）、订单向导、交期通知、完成度矩阵、款式多图 | 未交量/出货统计、订单评审、变更留痕、报价 |
| 2 | 技术管理 | `catalog.Style/Process/StyleProcess/ProcessTemplate`（工序工价库完整）、工艺字段、款式多图9张 | **BOM 全缺**、物料主档、尺寸表/工艺单 |
| 3 | 采购管理 | `warehouse.PurchaseOrder/PurchaseItem`（PO 基础已有） | 供应商档案、需求汇总(MRP-lite)、到料暂收/QC、交期跟催 |
| 4 | 仓储管理 | `warehouse.Lot`(面料批次)、`StockIn/StockInItem`、`MaterialIssue/IssueItem`（**床次×批次血缘已有**） | 库存余额视图、盘点、调拨、缸号色差控制、库存报警 |
| 5 | 生产计划 | `production.Production`（裁床/床次，即 cutting_bed 等价物） | 生产计划单（交期倒排）、产能粗平衡、插单记录 |
| 6 | 生产管理 | **最完整**：编菲打票/扫码登记/工序流转/打印历史(TicketPrintLog)/质量追溯/四类工资 | 委外单全流程、`price_snapshot` 工价快照、`source` 产量来源字段 |
| 7 | 出货管理 | 无 | 全新：出货单+装箱单+未交量校验+应收触发 |
| 8 | 质量管理 | 质量追溯模块（审计链，36用例）、疵点可挂床次/菲票 | 四类检验单（IQC/IPQC/FQC/OQC）、AQL 抽样、不合格处置闭环 |
| 9 | 财务成本 | `salary` 四类工资制（**人工成本源现成**）、结算单 wage_detail 快照 | 订单成本归集、应收应付、毛利报表 |

**核心判断**：调研确认"票=扎×工序"是行业主流数据模型（与 RFID 工票同构），本系统主链路无需改道；扩展全部走"新增 app/新增表 + 少量字段"，对存量数据零破坏。

## 二、分期路线（每期独立可交付、可回滚）

| 期 | 主题 | 内容 | 见效点 |
|----|------|------|--------|
| **P1 成本与定额** | 让老板看到订单毛利 | ①BOM 基础（bom/bom_item/material）②领料定额与超额预警（IssueItem 补 qty_req）③`cost_order_cost` 订单成本汇总表（人工=菲票、材料=领料，两源自动取数）④订单未交量与进度条 | 不新引单据流，改动最小，毛利即视 |
| **P2 采购与出货闭环** | 单据流打通 | ⑤供应商档案+需求汇总（BOM×订单−库存−在途）⑥到料暂收+IQC 简版⑦出货单+装箱单+未交量校验⑧应收立帐（出货→应收） | 采购-入库-出货-收款闭环 |
| **P3 计划与委外与质检** | 生产纵深 | ⑨生产计划单（交期倒排）⑩委外单全流程（发料/回货/加工费）⑪四类检验单+不合格处置 ⑫ price_snapshot 工价快照 | 外协厂管控、检验前置 |
| **P4 精益完整版** | 择机立项 | APS 排产、AQL 自动抽样、缸号条码+同缸出库控制、共耗料/制费分摊、业财税对接 | 完整版对标 |

---

## 三、P1 详案（本设计文档的主体）

### 3.1 物料主档 + BOM（技术管理 MVP）

新 app：`apps/tech`（避开 catalog 过重）。

```
Material 物料主档
  company / code(分类码-流水码, 同公司唯一) / name / category(fabric面料/lining里料/accessory辅料/packing包装)
  attrs JSON {成分, 克重, 门幅, 色号…} / unit / price_reference / status(active/停用)

Bom 版式BOM（金蝶式"一个款式一个BOM"，MVP 不做版本链，version 字段预留）
  company / style → catalog.Style / version(默认1) / status(draft/active/deactivated)
  UNIQUE(company, style, version) condition=is_deleted=False
  一个款式仅一个 active（应用层校验，激活时旧 active 自动降级）

BomItem
  bom / material / kind(main面料/lining里料/accessory辅料)
  unit_usage DECIMAL(12,4)  净用量/单耗（按样版排料图）
  loss_rate DECIMAL(5,2)    损耗率%
  → 损耗后用量 = unit_usage × (1+loss_rate/100)  【计算属性，不落库】
  color/size 空串=通码（MVP 不做色码维 BOM，P4 再拆）
  remark / position(部位)
```

**关键规则**（调研核实）：单耗与损耗率**必须分列**，采购量=单耗×(1+损耗率)——不做合计数字段。BOM 生效（active）是 P2 需求汇总的前置条件，P1 内仅作领料定额来源。

### 3.2 领料定额与超额预警（仓储管理增强）

`IssueItem` 增三列（可空，存量行为 NULL=不启用定额）：

```
bom_item → tech.BomItem NULL   定额来源行
qty_req  DECIMAL(12,2) NULL    定额数量 = 单耗×(1+损耗率)×该床次计划件数
over_reason CHAR(255) ''       超额原因（over 时必填）
```

- 确认（confirmed）时校验：`qty > qty_req` 即超额 → 返回 400 带 `over_ratio`，前端二次确认弹窗（带原因输入）后以 `allow_over=1` 重提放行；预警不阻断但全程留痕。
- 每床次每物料已领量在 Production 上做只读聚合，编菲页/看板可展示"已领/定额"。

### 3.3 订单成本汇总表（财务与成本 MVP）

新 app：`apps/cost`。

```
OrderCost 订单成本（每订单一行，重算式汇总表，非流水）
  company / order → orders.Order UNIQUE(company, order, is_deleted=False)
  material_cost    = Σ 该订单各床次 MaterialIssue.confirmed 行 qty × lot.unit_cost
  labor_cost       = Σ 该订单各床次菲票 Record 数量 × StyleProcess 工价（复用 salary.compute_wage 同源口径）
  outsourcing_cost = 0（P3 委外接入后自动纳入）
  overhead_cost    = 手工录入（制费/管理费，MVP 不做自动分摊）
  total_cost / output_qty(完工入库件数) / unit_cost = total/output_qty
  revenue          = Σ OrderItem.qty × Order.price
  gross_profit / gross_margin
  status(draft/computed) / computed_at / computed_by
```

- **重算触发**：手动"重算"按钮 + 领料确认/登记落库后异步标记 stale（`is_stale=True` 显示提醒），MVP 不做实时。
- **一致性铁律**：labor_cost 与工资模块必须同源同口径——实现上抽公共函数（ wage.py 层面按床次过滤），禁止两套取数逻辑。
- 报表：单件成本、单件毛利、订单毛利排行；导出 CSV。

### 3.4 订单未交量与进度（订单管理增强）

`Order` 增只读聚合（response 计算字段，不落库）：

```
shipped_qty  = Σ 出货单审核行数（P2 上线前恒 0）
produced_qty = Σ 完工入库数（P2 上线前以"全部登记完成扎数×扎量"近似，口径写入契约文档）
undelivered_qty = Σ OrderItem.qty − shipped_qty
progress     = produced_qty / Σ OrderItem.qty（完成度矩阵已有数据，直接复用）
```

订单列表/详情加进度条与未交量列；交期预警（已有 deadline_notify）接入未交量数据源。

### 3.5 P1 契约草案（新增端点，均挂公司隔离 + 权限点）

```
POST/PUT/DELETE  /tech/materials[/id]        GET /tech/materials?keyword=&category=
POST/PUT/DELETE  /tech/boms[/id]             GET /tech/boms?style_id=
POST             /tech/boms/{id}/activate     （草稿→生效，旧 active 降级）
GET              /cost/orders?date_from=&date_to=&keyword=
POST             /cost/orders/{order_id}/recompute
PUT              /cost/orders/{order_id}/overhead   （录制费）
GET              /orders/{id}/progress              （并入订单详情响应亦可）
```

权限点沿用 Plan A 体系：`tech.material.*` / `tech.bom.*` / `cost.order.view|recompute` 三组共约 8 点，进 perm_points 下发与宫格显隐。

## 四、P2~P3 实体草案（详案随各期立项细化）

### 4.1 P2 采购补全（warehouse 扩展）

```
Supplier: company/code/name/联系人/电话/结算方式/评级字段预留
DemandSummary（不落库，视图计算）: BOM×订单未投产数量 − 现库存 − 在途 = 采购空差
PurchaseItem 增: demand_source(手工/汇总导入) / arrived_qty / qualified_qty
Receipt 到料暂收: PO 行 → 暂收量 → IQC(简版:合格/拒收) → 合格量生成 StockIn(type=purchase)
```

### 4.2 P2 出货管理（新 app `apps/shipment`）

```
Shipment: company/shipment_no/order/customer/ship_date/status(draft/confirmed/partial/shipped/signed)
          boxes/total_qty/logistics_no/audited_by
ShipmentItem: shipment/color/size/qty（≤ OrderItem.qty − 已出量，审核时校验）
PackingBox: shipment/box_no/color/size/qty/gross_wt/net_wt/box_spec
```

- 审核动作：校验未交量 → 扣成品库存（负库存拦截）→ 生成 `ar_receivable`。
- 部分出货：剩余量显示为"未交"，调研确认可沿用"拆分推迟"思路不自动拆单。
- 打印复用 TicketPrintLog 模式（print_key 幂等 + 打印历史）。

### 4.3 P2 应收（新 app `apps/finance`）

```
Receivable: company/customer/order/shipment/amount/received_amount/status(未收/部分/已结清)
ReceiptLine: receivable/amount/received_at/方式(现款/月结…)  → 核销更新 status
```

### 4.4 P3 生产计划（production 扩展）

```
ProductionPlan: company/plan_no/order/style/qty_planned/plan_start/plan_end/status(draft/released/partial/done/closed)/rush标记
下达动作 → 提示建裁床（Production）+ 领料建议清单（BOM×计划件数）
产能粗平衡：Σ计划件数 / 各组日产能（salary 工作量统计同源）展示负荷%
```

### 4.5 P3 委外（新 app `apps/subcontract`）

```
SubcontractOrder: company/no/factory_name(or 供应商)/order/工序IDs JSON/send_qty/received_qty
                  unit_price(按工序)/amount/send_date/due_date/return_date
                  status(draft/approved/partial_sent/sent/partial_return/returned/settled/closed)
SubcontractMaterial: 单/lot/material/qty_sent/qty_used/qty_returned/batch_no（三本账防超耗）
```

回货合格量 → StockIn(type=outsourcing)；加工费 = 合格量×单价 → cost_order_cost.outsourcing_cost + 应付。

### 4.6 P3 质量检验（新 app `apps/quality`，一个实体带 type 覆盖四场景）

```
Inspection: company/insp_no/type(IQC/inline中查/final尾查/fqc/oqc)
            ref(采购单|床次|出货单 多态 ref_type+ref_id)/inspector/判骨(pass/fail/pending)
            item_results JSON / photos
Defect: inspection/类型(色差/布面/缝制/后整理)/部位/数量/等级(CR/MAJ/MIN)/可修bool/photo
Disposition: inspection/action(返工/返修/退货/让步)/审批人/复检结果（让步必留审批痕）
```

联动：IQC fail 拦截入库；尾查 pass 是成品入库/出货前置；返修走菲票工序流转（现有机制）。

### 4.7 P3 字段小改（生产管理）

```
Record 增 source Char(16) default 'scan'   # scan/hanger/manual，为吊挂预留
Record 增 price_snapshot DECIMAL(10,4) NULL # 登记时点工价快照，防调价回溯；补写存量脚本按当期工价回填
Ticket 增 team_id NULL                      # 分配班组（防买卖工票，配合对账清单）
```

## 五、整体单据流（P1~P3 落地后）

```
订单(色码矩阵,已有) → BOM 生效(P1) → 领料按定额(P1,超额预警)
  → 裁床编菲打票(已有) → 扫码登记(已有, +price_snapshot/source) → 计件工资(已有四类)
  → cost_order_cost 重算(P1: 材料+人工, P3:+委外) → 订单毛利(P1)
P2: 需求汇总 → PO → 暂收 → IQC → 入库(缸号) → 应付(立帐)
    成品入库 → 出货单+装箱单 → ≤未交量校验 → 扣库存 → 应收 → 核销
P3: 生产计划单(交期倒排) → 裁床 → 委外发料/回货 → 检验单四类 → 处置闭环
```

## 六、工程约束（沿用本项目门禁，不可妥协）

1. **测试工厂**：动态建厂，真实厂（汉川旭东, id=86）零接触；smoke 守卫继续排除 86。
2. **覆盖门禁**：新代码分支覆盖 missing=0，全局分支 ≥80%；每期新套件（test_tech/test_cost/test_warehouse_gap/test_shipment/...）进 run_coverage.sh 正确挂载（脚本式走 shell exec，TestCase 式走 manage.py test）。
3. **契约测试**：新端点全部登记进契约套件（现 107/0），公司隔离 401/403 用例必配。
4. **权限**：新权限点进 perm_points 体系（effective_perms 下发 + 宫格显隐），legacy 岗位零回归。
5. **迁移纪律**：只加表/加可空列，禁止改既有列语义；存量行回填脚本与迁移同交付。
6. **前端**：tsc 0 + preview 0 门禁；新页面沿用分包（packages/admin/...），权限点控制显隐。
7. **8000 后端**：改动后 `--noreload` 须手动重启并 401 探活。

## 七、P1 自校验矩阵

| # | 校验项 | 结论 |
|---|--------|------|
| 1 | OrderItem 色码矩阵已具备，P1 无需动订单结构 | ✅（已核 models.py） |
| 2 | 单耗/损耗率分列，不落"含损耗合计"字段 | ✅ 调研论断核实 |
| 3 | labor_cost 与 salary 同源同口径（抽公共函数） | ✅ 设计约束明确 |
| 4 | IssueItem 加列全部可空，存量领料单零破坏 | ✅ |
| 5 | cost_order_cost 重算式（非流水），stale 标记防误导 | ✅ |
| 6 | 未交量在 P2 前口径声明（近似值写契约文档） | ✅ |
| 7 | 新权限点数量与命名进 perm_points 计划 | ✅ 约 8 点 |
| 8 | 版本管理 MVP 只留 version 字段不做版本链 | ✅ 明确划界 |
| 9 | 委外/出货/应收不在 P1 范围，防 scope 蔓延 | ✅ |
| 10 | 新套件挂载方式与各套件风格匹配 | ✅ §六.2 |

## 八、待用户拍板

1. **P1 范围确认**：BOM+定额领料+订单成本+未交量四件套是否立项。
2. 制费（overhead）MVP 用"手工录入总额"是否可接受（自动分摊在 P4）。
3. 未交量在出货模块上线前的近似口径（登记完成件数）是否认可。
4. P2~P4 顺序与优先级是否按本方案执行。
