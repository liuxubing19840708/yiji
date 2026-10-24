# 质量全链路追溯 —— 开源调研与方案设计

> 调研日期：2026-10-18。所有 GitHub star / license / 最近动态均实测自 api.github.com、registry.npmmirror.com、pypi.org，非记忆数据。
> 适用系统：「扫菲」制衣厂生产管理系统（Django 5.2 + DRF 后端、微信小程序前端）。
> 目标：客户反馈色差或质量问题时，工厂能凭**来源单号 + 缸号**快速定位到具体面料批次、床次、菲票、经手员工与工序时间线——「问题定位不再靠翻本子」。对外贸工厂，追溯能力也是争取客户信任的基础（验货、批次文件、追溯要求）。
> 范围（用户已拍板）：**一期只打通生产链**（款式→床次/打菲→菲票→扫菲工序/员工）；面料采购/仓储由**新建轻量仓库模块**承接（方案本期设计、可与追溯分期落地）；外贸批次文件导出、验货对接放二期。

---

## 0. 一句话结论

**现有数据已经自带 80% 的追溯骨架，缺的只是「缸号/来源单号」两个锚点和三个查询接口**：款式 Style → 床次 Production → 菲票 Ticket（二维码）→ 扫菲 Record（工序/员工/时间）是一条已经打通的外键链，追溯查询接口是在这条链上「顺着查/逆着查」，不需要推翻任何现有模型。开源侧**没有能整包拿来的制衣追溯系统**（搜到的纺织 MES 全是个位数 star 玩具项目），唯一值得深读的建模参考是 ERPNext 的 Batch + Serial and Batch Bundle；二维码、打印、扫码解析全部复用现有自研链路。新增工作量：**P0 生产链追溯 8~10 人日、P1 轻量仓库 9~12 人日、P2 外贸批次文件 7~10 人日**。

---

## 1. 开源调研（实测数据）

### 1.1 制衣/纺织 MES 与通用制造追溯

| 项目 | License | Star（实测 2026-10-18） | 最近动态 | 结论 |
|---|---|---|---|---|
| **frappe/erpnext** | GPL-3.0 | 40,285 | **非常活跃**：仓库当日（2026-10-18）仍有推送 | **只能借鉴建模，不能抄代码**（GPL 传染 + 技术栈 Python/Frappe 但与其自带 ORM 深绑）。它的 `Batch`（batch_id 每物料唯一、含生产日期/来源单据引用）+ `Serial and Batch Bundle`（每笔库存流水与批次绑定的中间表，实测读取 version-15 doctype JSON 确认字段：`voucher_type/voucher_no/item_code/warehouse/entries`）+ `Stock Ledger Entry` 是开源界最成熟的 lot-genealogy 实现，本方案第 3 节的「流水单 + 行 + 血缘挂接」即借鉴此结构 |
| ricefishtech/industry4.0-mes | 无 license（GitHub 未标） | 513 | 2026-02 有推送 | Java 栈整包 MES。无 license 视为保留所有权利，代码不可用；页面/单据划分可扫一眼，**仅借鉴** |
| metaxk-company/free-mes | GPL-3.0 | 435 | 2026-03 有推送 | PLpgSQL 栈，GPL 传染，**只能借鉴**其「工序流转 + 批次」概念 |
| GitHub 搜「garment mes traceability」 | — | **0 结果**（实测 search API） | — | 制衣垂直方向无开源成品，**自研是唯一路线** |
| GitHub 搜「textile mes」其余结果 | 多无 license | 0~2 star | — | 玩具/学生项目，**不适用** |
| YunaiV/ruoyi-vue-pro | MIT | 39,869 | 2026-10 活跃 | 内含 ERP/MES 模块的 Spring Boot 实现；技术栈不同，MIT 许可下可**借鉴**其单据-批次关联的接口划分，不引入 |

**结论**：没有任何一个开源项目能「拿来用」——制衣 MES 追溯是空白，通用 MES 要么技术栈不符、要么 GPL 传染。路线 = **借鉴 ERPNext 的批次-流水建模 + 自研 Django 实现**。

### 1.2 可借用的零件

| 零件 | 实测数据 | 结论 |
|---|---|---|
| 小程序二维码生成 | **已自研**：`frontend/utils/qrcode.ts` 纯本地实现（Byte 模式、纠错 M、版本 1-6，容量 14~106 字节，8 种掩码择优），打印与预览共用（`frontend/utils/bluetooth/templates.ts:150-158` 模块数估算、`frontend/components/label-preview/index.ts:169-193` 绘制） | **能用，无需引入**。备选 weapp-qrcode 1.0.0 / weapp-qrcode-canvas-2d 1.1.6（MIT，npmmirror 实测）仅在需要升级版本 7+ 大容量码时考虑 |
| 后端出码（P2 批次文件内嵌二维码图片时） | pypi 实测：**segno 1.6.6**（2025-03 发版，BSD-3-Clause）、qrcode 8.2（BSD，2025-05） | **P2 才用**，推荐 segno（纯 Python、无 PIL 依赖） |
| GS1 批次编码规范 | gs1/gs1-syntax-dictionary（21★，2026-08 有推送，实测）；规则：AI(01) 物料编码、**AI(10) 批号/缸号**、AI(11) 生产日期 | **借鉴编码规范不引 SDK**：缸号建议编码 `G+年两位+缸序`（如 `G2643` = 2026 年第 43 缸），人可读、可排序、天然去重；不采用 GS1 应用标识符（我们的码是自有 `|` 分隔格式，扫码端只有一个入口，无需对外部扫码枪兼容） |
| 条码库（zint 819★ 等） | 实测 | **不适用**：当前只有二维码需求，无一维条码场景 |
| lot-genealogy 数据建模 | ERPNext Batch / Serial and Batch Bundle（实测 doctype JSON，见 1.1） | **借鉴**，见第 3 节 |

---

## 2. 现状盘点（file:line 证据）

### 2.1 已经是「追溯节点」的现有数据

| 追溯要素 | 现有载体 | 证据 |
|---|---|---|
| 款式 | `catalog.Style`（style_no 公司内唯一，挂 customer） | `backend/apps/catalog/models.py:80-114`（唯一约束 :103） |
| 客户 | `catalog.Customer`（customer_no 自动编号 KH0001 顺延） | `backend/apps/catalog/models.py:28-50`；自动取号 `backend/apps/catalog/views.py:382-393` |
| 床次（打菲） | `production.Production`：**已挂 style 与 customer**，有 bed_no、total_qty、fabric、order_type（自产/外发） | `backend/apps/production/models.py:7-55`（style FK :26、customer FK :32、fabric :37、order_type :30、唯一约束 :47-49） |
| 菲票（扎） | `catalog.Ticket`：bed_no + bundle_no、**color、size、qty、fabric、piece_no**、qr_code；(company, bed_no, bundle_no) 唯一 | `backend/apps/catalog/models.py:140-177`（字段 :151-158、唯一约束 :168-171） |
| 扫菲记录 | `records.Record`：ticket + **process + user + operator + qty + registered_at**，同票同工序同人唯一 | `backend/apps/records/models.py:14-55`（唯一约束 :43-47） |
| 工序 | `catalog.Process` + 款式工序 `StyleProcess` + 订单工序 `ProductionProcess`（床次可覆盖单价） | `backend/apps/catalog/models.py:53-77`、:117-137；`backend/apps/production/models.py:58-78` |
| 二维码内容 | `build_ticket_qr` → `companyNo|bedNo|styleNo|bundleNo|color|size|qty` 七段竖线分隔串，编菲时写入 `Ticket.qr_code` | `backend/apps/core/utils.py:23-34`；写入点 `backend/apps/catalog/views.py:497-499` |
| 扫码解析入口 | `POST /scan/parse`（ScanParseView）：按 `|` 拆 7 段、校验公司、按 (bed_no, bundle_no) 命中菲票，返回票+床次状态 | `backend/apps/production/views.py:475-505`；路由 `backend/apps/production/urls.py:21` |
| 小程序扫码流程 | 扫菲页自动调起相机 → parseScan → 工序选择 → 件数 → createRecord（含离线队列、相册补扫、连续扫码） | `frontend/pages/scan/index.ts:130-154`（wx.scanCode）、:186-249（handleCode）、:318-355（submit） |
| 软删基建 | `BaseModel`：is_deleted + objects（过滤）/ all_objects（全量） | `backend/apps/core/models.py:16-36` |
| 打印链路 | TSPL2（佳博/芯烨/TSC，GBK 内码 TSS24）+ ESC/POS 双指令集；字段可选（PRINT_FIELDS）；预览组件与打印共用同一坐标引擎 computeLabelLayout | `frontend/utils/bluetooth/templates.ts:48-76`（ESC/POS）、:105-122（PRINT_FIELDS）、:224-267（布局）、:270-290（TSPL） |

**关键判断：款式→床次→菲票→扫菲记录的外键链是完整的**，正向（这扎货谁做的、什么工序、什么时间）和反向（这员工今天登了哪些扎）用现有 ORM 就能查。**缺的只是原材料维度的锚点。**

### 2.2 缺什么

1. **缸号（染缸批次）**：全系统无任何缸号字段。`Production.fabric` 和 `Ticket.fabric` 只是自由文本面料名（`backend/apps/production/models.py:37`、`backend/apps/catalog/models.py:156`），无法回答「这批货用的是哪一缸布」。
2. **来源单号（客户订单号）**：`Production` 只有 customer FK，没有客户订单号字段；`Ticket`/`Record` 亦无。客户拿单号来问，系统查不到。
3. **批次 ↔ 菲票/床次的关联表**：面料批次与生产批次之间的血缘没有载体（ERPNext 用 Stock Ledger Entry 承载，我们目前完全没有仓储环节）。
4. **出入库环节**：无采购/入库/领料模型，缸号与来源单号没有产生的入口（这正是用户认可的轻量仓库模块要补的）。
5. **追溯查询 API**：现有一个面向登记的 `GET /tickets`（`backend/apps/catalog/views.py:419-435`，按 production_id/bed_no/bundle_no 筛），没有「按缸号查受影响面」「按款式+颜色反查」的语义。
6. **一个已知技术债**：同族唯一约束多数未带 `is_deleted` 条件（见 3.5）。

### 2.3 打印链路能否扩展承载「来源单号/缸号/色号/数量」

**能，且改动极小**。理由：

- 标签字段是**声明式可配置**的：`FieldKey` 枚举 + `PRINT_FIELDS` 列表 + `fieldText()` 渲染分发（`frontend/utils/bluetooth/templates.ts:108-122`、:161-177），加字段 = 加两个枚举值 + 两个 case。
- `色号/数量` 已经是标签字段：`color`、`qty` 就在 PRINT_FIELDS 里（templates.ts:118-119），50×80 默认样式即含颜色与数量（:143-147）。
- 布局引擎自带**截断与超高停渲染**（templates.ts:252-264），加了长字段不会打花标签。
- `PrintTicketData` 接口只需加两个可选字段 `source_order_no?`、`lot_no?`（templates.ts:19-29）；预览组件 `label-preview` 走同一 `computeLabelLayout`（`frontend/components/label-preview/index.ts:96-98`），**预览零改动自动生效**。
- GBK 编码管线已就绪（templates.ts:329-336 encodeGbk），来源单号/缸号常规为 ASCII/数字，无字形风险。

### 2.4 二维码编码与解析入口能否复用

**能，且建议二维码内容一期不变**。`build_ticket_qr` 七段格式与 `ScanParseView` 的 `len(parts) != 7` 校验（`backend/apps/production/views.py:486`）是硬耦合；市场上已打印的存量菲票码都是七段。若把缸号/来源单号塞进码里（八段），存量票直接解析失败。正确做法：**码里只保留定位键（bed_no + bundle_no 已足够唯一定位菲票），缸号/来源单号由服务端经 菲票→床次→缸号 关联查出**——这正是 2.2 缺的关联表的价值。追溯查询入口复用 `POST /scan/parse`：给它加一个返回字段（追溯入口 id），扫一张菲票即可跳追溯详情页。

---

## 3. 追溯数据模型草案（Django 模型级，不写迁移）

### 3.1 缸号/批次登记 `Lot`（P0 落地，P1 由入库单自动产生）

```python
# apps/warehouse/models.py（新建 app；P0 先只启用 Lot，P1 补采购/入库/领料）
class Lot(BaseModel):
    """缸号/面料批次：原材料追溯的最小锚点。

    P0 阶段管理员手工登记（ Excel 里的缸号搬进系统）；
    P1 阶段由入库单行自动创建，source_in 自动回填。
    lot_type 区分「染缸批次」（一批一色）与「面料批次」（同款面料多缸的父批次）。
    """

    class LotType(models.TextChoices):
        DYE = 'dye', '染缸批次'
        FABRIC = 'fabric', '面料批次'

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='lots', verbose_name='公司')
    lot_no = models.CharField('缸号/批次号', max_length=64)          # 建议 G+年两位+缸序
    lot_type = models.CharField('批次类型', max_length=16, choices=LotType.choices,
                                default=LotType.DYE)
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='children', verbose_name='父批次',
                               help_text='面料批次 → 其下的染缸批次')
    fabric_name = models.CharField('面料名称', max_length=64, blank=True, default='')
    color = models.CharField('颜色', max_length=64, blank=True, default='')
    qty = models.DecimalField('数量', max_digits=12, decimal_places=2, default=0)
    unit = models.CharField('单位', max_length=16, blank=True, default='米')
    dye_date = models.DateField('染整日期', null=True, blank=True)
    source_in = models.ForeignKey('warehouse.StockInItem', on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name='lots',
                                  verbose_name='来源入库行', help_text='P1 回填')
    remark = models.CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        db_table = 'lot'
        constraints = [
            # 项目已知教训：唯一约束必须带 is_deleted 条件（软删行占用业务键）
            models.UniqueConstraint(fields=['company', 'lot_type', 'lot_no'],
                                    condition=Q(is_deleted=False),
                                    name='uq_lot_company_type_no_active'),
        ]
        indexes = [models.Index(fields=['company', 'lot_no'], name='idx_lot_company_no')]
```

### 3.2 生产链血缘：床次 ↔ 缸号 `ProductionLotLink`（P0 核心）

```python
class ProductionLotLink(BaseModel):
    """血缘表：床次用了哪些缸号（一床多缸 = 多行）。

    来源三种：P0 手工在床次上挂缸号；P1 领料单审核时自动生成（见 3.4 挂接点）；
    未来可能手工调整。菲票级粒度暂不做（待确认 Q3），菲票默认继承床次的缸号。
    """

    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='production_lot_links')
    production = models.ForeignKey('production.Production', on_delete=models.CASCADE,
                                   related_name='lot_links', verbose_name='床次')
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT,
                            related_name='production_links', verbose_name='缸号')
    qty = models.DecimalField('用布数量', max_digits=12, decimal_places=2, default=0,
                              help_text='该床次耗用此缸的数量，0=未细分')

    class Meta:
        db_table = 'production_lot_link'
        constraints = [
            models.UniqueConstraint(fields=['production', 'lot'],
                                    condition=Q(is_deleted=False),
                                    name='uq_prod_lot_link_pair_active'),
        ]
        indexes = [
            models.Index(fields=['company', 'lot'], name='idx_pll_company_lot'),
            models.Index(fields=['production'], name='idx_pll_production'),
        ]
```

### 3.3 来源单号：`Production.source_order_no`（P0 一行字段）

```python
# apps/production/models.py Production 增加（迁移另写）：
    source_order_no = models.CharField('来源单号/客户订单号', max_length=64,
                                       blank=True, default='', db_index=True)
```

放床次级不放菲票级：外贸一张客户订单通常对应一个或多个床次，菲票继承床次。是否需要升级为独立 `CustomerOrder` 模型（一单多床、订单维度汇总）→ **待确认 Q4**，P0 先用字符字段兜住。

### 3.4 轻量仓库模块（P1 落地，模型本期定稿）

```
采购单 PurchaseOrder ──< PurchaseItem          （谁、什么时候、买了什么布）
入库单 StockIn ──< StockInItem ──> Lot          （布进厂时产生缸号，血缘起点）
领料单 MaterialIssue ──< IssueItem ──> Lot + Production
                                   └──自动写 ProductionLotLink（血缘挂接点）
```

```python
class PurchaseOrder(BaseModel):
    company = FK('accounts.Company', CASCADE, related_name='purchase_orders')
    order_no = CharField('采购单号', max_length=32)          # CG+日期+序号，留空自动生成
    supplier_name = CharField('供应商', max_length=128, blank=True, default='')
    ordered_at = DateField('采购日期')
    status = CharField('状态', max_length=16, default='draft',
                       choices=TextChoices('draft 草稿', 'done 完成'))
    remark = CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        constraints = [UniqueConstraint(fields=['company', 'order_no'],
                       condition=Q(is_deleted=False), name='uq_po_company_no_active')]


class PurchaseItem(BaseModel):
    order = FK(PurchaseOrder, CASCADE, related_name='items')
    fabric_name = CharField('面料名称', max_length=64)
    color = CharField('颜色', max_length=64, blank=True, default='')
    qty = DecimalField('数量', max_digits=12, decimal_places=2)
    unit = CharField('单位', max_length=16, default='米')
    price = DecimalField('单价', max_digits=10, decimal_places=3, default=0,
                         help_text='P0 轻量版仅记录，不与薪酬/财务勾稽')


class StockIn(BaseModel):
    company = FK('accounts.Company', CASCADE, related_name='stock_ins')
    in_no = CharField('入库单号', max_length=32)
    source_type = CharField('来源', max_length=16, default='purchase',
                            choices=('purchase 采购入库', 'return 退货入库', 'other 其他'))
    purchase = FK(PurchaseOrder, SET_NULL, null=True, blank=True,
                  related_name='stock_ins', verbose_name='关联采购单')
    in_at = DateTimeField('入库时间', default=timezone.now)
    operator = FK('accounts.User', SET_NULL, null=True, blank=True, related_name='stock_ins')
    remark = CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        constraints = [UniqueConstraint(fields=['company', 'in_no'],
                       condition=Q(is_deleted=False), name='uq_stockin_company_no_active')]


class StockInItem(BaseModel):
    """入库行：缸号在此诞生——保存时自动 get_or_create 同 company+lot_type+dye 批次 Lot。"""
    stock_in = FK(StockIn, CASCADE, related_name='items')
    fabric_name = CharField('面料名称', max_length=64)
    color = CharField('颜色', max_length=64, blank=True, default='')
    lot_no = CharField('缸号', max_length=64, help_text='扫码/手输；空则按规则自动生成')
    qty = DecimalField('数量', max_digits=12, decimal_places=2)
    unit = CharField('单位', max_length=16, default='米')


class MaterialIssue(BaseModel):
    """领料单：车间从仓库领布到某个床次——追溯血缘的正式挂接动作。"""
    company = FK('accounts.Company', CASCADE, related_name='material_issues')
    issue_no = CharField('领料单号', max_length=32)
    issue_at = DateTimeField('领料时间', default=timezone.now)
    operator = FK('accounts.User', SET_NULL, null=True, blank=True, related_name='material_issues')
    status = CharField('状态', max_length=16, default='draft',
                       choices=('draft 草稿', 'confirmed 已确认'))
    remark = CharField('备注', max_length=255, blank=True, default='')

    class Meta:
        constraints = [UniqueConstraint(fields=['company', 'issue_no'],
                       condition=Q(is_deleted=False), name='uq_issue_company_no_active')]


class IssueItem(BaseModel):
    issue = FK(MaterialIssue, CASCADE, related_name='items')
    production = FK('production.Production', PROTECT, related_name='issue_items',
                    verbose_name='领用床次')
    lot = FK(Lot, PROTECT, related_name='issue_items', verbose_name='缸号')
    qty = DecimalField('数量', max_digits=12, decimal_places=2)

    class Meta:
        constraints = [UniqueConstraint(fields=['issue', 'lot', 'production'],
                       condition=Q(is_deleted=False), name='uq_issue_item_active')]
```

**与追溯的挂接点（唯一钩子）**：`MaterialIssue` 置为 confirmed 的事务里，为每个 `IssueItem` 执行 `ProductionLotLink.objects.get_or_create(production=…, lot=…, defaults={'qty': item.qty, 'company': …})`。追溯查询**只读 ProductionLotLink，不直接查仓库**——仓库模块整体缺席（P0）或推倒重来都不影响追溯主链。

### 3.5 软删与唯一约束（项目已知教训）

现状：全项目 11 处 `UniqueConstraint`，**仅 1 处**带 `is_deleted` 条件（`backend/apps/accounts/models.py:88` Post），其余 10 处无条件——`customer_group:21`、`customer:46`、`process:73`、`style:103`、`style_process:133`、`ticket:169`、`record:44`、`production:47`、`production_process:73`、`company_setting:21`。运行时靠 `_purge_deleted()` 先硬删软删行再建（如 `backend/apps/catalog/views.py:33-35`）绕开，属「能跑但裸奔」。

**本方案所有新表唯一约束一律带 `condition=Q(is_deleted=False)`**（见 3.1-3.4 草案），不再依赖 _purge_deleted 兜底；单号类（purchase/stock_in/issue）顺延规则参考客户编号自动取号实现（`backend/apps/catalog/views.py:382-393`，用 all_objects 扫空位）。

---

## 4. 双向追溯查询设计（API 草案）

新增 `apps/warehouse/views.py`（或独立 `apps/trace/`），全部走 `CompanyContextMixin` 公司隔离，权限：管理员或 `production` 权限（员工查自己相关，见待确认 Q6）。

### 4.1 正向：缸号/面料批次 → 受影响床次/菲票 → 客户

```
GET /trace/lot/<lot_id>
  → 200 {
      "lot": {"id": 12, "lot_no": "G2643", "lot_type": "dye", "color": "宝蓝",
              "fabric_name": "32支精棉", "dye_date": "2026-09-30",
              "source_order_no": null},
      "productions": [
        {"id": 88, "bed_no": "C0912", "style_no": "A1023", "style_name": "男童套装",
         "customer_name": "深圳童装出口公司", "source_order_no": "PO-2026-0917",
         "total_qty": 1200, "issue_qty": 850,
         "ticket_count": 96,
         "tickets_top": [{"bundle_no": 5, "color": "宝蓝", "size": "110", "qty": 60}]  // 前 N 条
       }],
      "impact": {"production_count": 3, "ticket_count": 210, "qty_total": 2580}
    }

GET /trace/lot?keyword=G2643          # 按缸号搜索（列表用）
GET /trace/lot/<lot_id>/timeline      # 该缸的时间线：入库(P1)→领料→各床次裁菲
```

实现路径：`Lot → production_links → Production（select_related style, customer）→ Ticket 聚合`，纯现有 ORM，无新表依赖。

### 4.2 反向：客户投诉某款某色 → 定位缸号、批次、员工、工序、时间线

```
GET /trace/reverse?style_no=A1023&color=宝蓝&date_from=&date_to=&customer_id=
  → 200 {
      "matches": [
        {"production_id": 88, "bed_no": "C0912", "style_no": "A1023", "color": "宝蓝",
         "lots": [{"id": 12, "lot_no": "G2643"}],        // 经 production_lot_links
         "source_order_no": "PO-2026-0917",
         "customer_name": "…"}
      ]}

GET /trace/ticket/<ticket_id>
  → 200 {
      "ticket": {"bed_no": "C0912", "bundle_no": 5, "color": "宝蓝", "size": "110", "qty": 60,
                 "piece_no": "", "fabric": "32支精棉", "qr_code": "100001|C0912|…"},
      "production": {"id": 88, "bed_no": "C0912", "style_no": "A1023",
                     "source_order_no": "PO-2026-0917", "customer_name": "…",
                     "order_type": "自产"},
      "lots": [{"id": 12, "lot_no": "G2643", "color": "宝蓝", "dye_date": "2026-09-30"}],
      "records": [                                     // 时间线：谁、哪道工序、何时、多少件
        {"process": "车领", "user": "张三", "operator": null, "qty": 60,
         "registered_at": "2026-10-05 09:12:00"},
        {"process": "扫线", "user": "李四", "operator": {"id": 7, "name": "王组长"},
         "qty": 60, "registered_at": "2026-10-05 15:40:00"}],
      "print": {"is_printed": true, "print_count": 2, "print_at": "2026-10-04 10:02:00"}
    }
```

`/trace/ticket/<id>` 是反向追溯的**终端接口**：客服拿到客户投诉的菲票（或扫码）即可一屏给出全链路。

### 4.3 复用扫码入口

`POST /scan/parse`（`backend/apps/production/views.py:475`）返回体增加一个字段 `"ticket_trace_id": <ticket_id>`（其实等于返回的 ticket.id，前端直接可用）。管理员端扫菲页增加「查看追溯」入口：扫任意一张菲票 → parseScan 成功后跳 `packages/admin/trace-detail` 页（新页，调 4.2 终端接口）。**二维码格式零改动，存量菲票码全部可用。**

---

## 5. 标签方案：菲票/出库标签加「来源单号/缸号/色号/数量」

**原则：只动数据层和字段枚举，不动布局引擎与打印指令生成。**

| 改动点 | 文件 | 内容 | 量级 |
|---|---|---|---|
| 1. 数据接口 | `frontend/utils/bluetooth/templates.ts:19-29` | `PrintTicketData` 增加可选 `source_order_no?: string; lot_no?: string` | 2 行 |
| 2. 字段枚举 | 同文件 `:108-111` `FieldKey` | 加 `'sourceOrderNo'`、`'lotNo'` 两个枚举值 | 1 行 |
| 3. 字段清单 | 同文件 `:112-122` `PRINT_FIELDS` | 加 `{key:'sourceOrderNo', label:'来源单号'}`、`{key:'lotNo', label:'缸号'}`（色号=color、数量=qty 已存在） | 2 行 |
| 4. 渲染 | 同文件 `fieldText()` `:161-177` | 两个 case：`来源单:${ticket.source_order_no}` / `缸号:${ticket.lot_no}`，空值自动跳过（现有逻辑） | 4 行 |
| 5. 取数 | 标签打印入口（`frontend/packages/admin/productions/index.ts`、cloud-printer 页构建 PrintTicketData 处） | ticket 序列化层带出：后端 `TicketSerializer` / 打印列表接口对 production 做注解（`source_order_no` 直读；`lot_no` 经 `production.lot_links` 取首个缸号，一床多缸时逗号拼或取主缸——**待确认 Q2/Q3**） | ~10 行 |
| 6. 预览 | `frontend/components/label-preview/index.ts` | **零改动**：走同一 computeLabelLayout（:96-98） | 0 |

- 现有公司默认样式（`DEFAULT_LABEL_STYLE` templates.ts:143-147）不变，新字段由管理端「标签样式」设置勾选加入 `fields`，对公司是**可选启用**，不存量迁移。
- 50×80 挂签竖版逐行居中 + 超高停渲染（templates.ts:252-264）天然容纳两行新增；横版标签超高时 company/date 先被牺牲，来源单号建议排在 qty 之后、company 之前。
- 二维码内容**不变**（见 2.4）：码仍是七段，标签上多印两行文本即可，扫码兼容存量。
- ESC/POS 小票模板 `buildEscPos`（templates.ts:48-76）同样加两行文本（可选，跟随字段配置）。

---

## 6. 分期计划与估时

### P0 生产链追溯打通用（估 8~10 人日）

| 项 | 内容 | 估时 |
|---|---|---|
| 模型 | `warehouse` 新 app + `Lot` + `ProductionLotLink`；`Production.source_order_no` 字段 | 1.5d |
| 挂缸界面数据 | 床次详情挂/解缸号、来源单号编辑接口（复用 ProductionDetail 更新路径） | 1d |
| 追溯 API | `GET /trace/lot/<id>`、`GET /trace/lot`（搜索）、`GET /trace/reverse`、`GET /trace/ticket/<id>`；scan/parse 加字段 | 2d |
| 小程序端 | `packages/admin/trace-detail` 追溯详情页（票→床次→缸号→员工时间线）、`packages/admin/productions` 挂缸入口、扫码进追溯入口 | 2.5d |
| 标签 | 第 5 节六处改动 + 标签样式设置页两个新字段选项 | 1d |
| 测试回归 | e2e 分支补追溯链路用例、打印回归 | 1d |
| **小计** | | **9d（8~10）** |

P0 交付后即可回答：客户投诉某款某色 → 反查缸号（手工录入的）→ 受影响床次/菲票 → 经手员工与时间线；标签带全 来源单号/缸号/色号/数量。

### P1 轻量仓库模块 + 缸号自动挂接（估 9~12 人日）

| 项 | 内容 | 估时 |
|---|---|---|
| 模型 | 采购单/入库单/领料单 5 模型 + 单号自动生成 + confirmed 钩子自动写 ProductionLotLink | 2d |
| 后端接口 | 三类单据 CRUD + 确认/作废 + 列表筛选（3 个 ViewSet 级） | 2.5d |
| 小程序端 | 采购、入库（扫码缸号/自动生成缸号）、领料（选床次+扫缸号）三个页面 + 仓库入口 | 3.5d |
| 打通 | 入库行 → Lot 自动创建；领料确认 → 血缘自动生成；追溯详情补「入库/领料时间线」 | 1.5d |
| 测试回归 | | 1.5d |
| **小计** | | **11d（9~12）** |

P1 交付后缸号不再手抄：布进厂产生缸号、领料进床次产生血缘，追溯数据自动沉淀。是否记库存余量/扣减 → **待确认 Q11**（默认只记流水不记结存，轻量优先）。

### P2 外贸批次文件 / 验货对接（估 7~10 人日）

| 项 | 内容 | 估时 |
|---|---|---|
| 批次文件导出 | 按客户订单/床次导出 Excel（面料批次、缸号、色号、数量、裁床/菲票明细、工时线）；后端 openpyxl | 2.5d |
| 二维码出码 | 批次文件内嵌二维码图片（pypi segno，BSD，实测 1.6.6/2025-03） | 1d |
| 验货记录 | 验货单模型（验货人、结论、缺陷项挂菲票/床次）+ 页面 | 3d |
| 对外分享 | 只读追溯链接/小程序分享卡片（面向客户验货员） | 1.5d |
| 测试回归 | | 1d |
| **小计** | | **9d（7~10）** |

---

## 7. 风险与「待用户确认」问题清单

**风险**

1. **颜色自由文本是血缘的最大噪音源**：`Ticket.color` 是手输文本（`catalog/models.py:153`），「宝蓝/宝兰/4636」会被当成三回事。缸号-颜色对应关系靠录入时人工保证，P0 不做色卡归一（成本高），靠追溯接口的模糊查询兜底（`color__icontains`）。
2. **追溯查询的性能面**：`/trace/reverse` 按 style+color 扫 Ticket，量大后依赖 `idx_ticket_company_bed`（catalog/models.py:172-174）以外的组合索引，实现时按 `production__style_id + color` 补索引；P0 数据量（单厂年万级菲票）无压力。
3. **外发订单的数据真实性**：`order_type=外发`（production/models.py:16-18）的床次，缸号/领料依赖外发厂回传，系统只能记录不能强制；一期按「有则记、无则空」处理。
4. **来源单号与客户订单的对应关系未经建模**：P0 用自由字符字段，若客户要求按订单维度出批次文件（P2），可能需要升级为 `CustomerOrder` 模型，属可控重构（字段含义不变）。

**待用户确认（业务规则不猜，逐条拍板）**

| # | 问题 | 影响设计点 |
|---|---|---|
| Q1 | **缸号粒度**：按「染缸一次染色」为一批（一批一色），还是按「面料批次」（同款面料进厂多缸合并记）？草案用 lot_type=dye/fabric + parent 兼容两种，需确认工厂实际口径 | Lot 结构 |
| Q2 | **一床多缸怎么记**：一个床次用两缸布常见吗？要不要记录每缸的用布数量（草案 qty 字段已留）？追溯告警时按床次粒度还是按数量比例估算受影响件数？ | ProductionLotLink.qty |
| Q3 | **一票跨缸允许吗**：会不会出现同一扎（菲票）跨两缸布？若存在则需菲票级血缘（TicketLotLink），床次级不够 | 血缘粒度 |
| Q4 | **来源单号 = 客户订单号吗**？还是工厂内部下单编号/面单号？要不要建独立客户订单模型（一单多床、订单汇总出批次文件）？ | source_order_no vs CustomerOrder |
| Q5 | **P0 缸号谁录入**：管理员在床次上手工挂，还是等 P1 入库自动产生？历史床次要不要补录（不补录则老订单反查不到缸号） | P0 落地方式 |
| Q6 | **追溯查询权限**：普通员工能查吗（比如只查自己登记过的），还是仅管理员/production 权限？对外分享（P2）给客户的链接要不要过期时间 | API 权限 |
| Q7 | **外发订单（order_type=外发）**是否纳入追溯？客户来料加工的布要不要记缸号？ | Lot 录入范围 |
| Q8 | **出库标签**：用户提到出库环节的标签——目前系统没有「出库」单据，出库标签是指菲票挂签跟随出货，还是 P2 要新增出货单+出货标签？ | P2 范围 |
| Q9 | **缸号编码规则**：接受建议的 `G+年+缸序` 自动生成，还是沿用布行/染厂给的原始缸号字符串（推荐后者为主、自动编号为辅）？ | Lot.lot_no |
| Q10 | **二维码是否升级承载缸号**：草案明确「不升级、服务端查」（存量票兼容），是否认可？若客户验货习惯是「只扫码出批次报告」，P2 可加一个码指向追溯页的短链，另议 | 标签/扫码 |
| Q11 | **轻量仓库要不要记库存结存**：只记采购/入库/领料流水（推荐，轻量），还是要面料库存余量与低于预警？ | P1 范围 |
| Q12 | **领料是否要审核流**：草稿→确认两态够吗，还是需要仓库管理员与车间双人确认？ | MaterialIssue.status |

---

## 十、已实施记录（2026-10-18 P0+P1 落地）

用户对 Q1-Q12 逐条拍板后实施完成。拍板结论：Q1 面料批次（不用 lot_type/parent）、Q2 一床多批次+记用量、Q3 床次级血缘（一扎不跨批，不建 TicketLotLink）、Q4 source_order_no 手填不建 CustomerOrder、Q5 P0 建床/编菲页填写+床次详情补填改（历史不补录）、Q6 管理员+production 权限岗、Q7 外发/来料一期不纳入、Q8 挂签加两字段+二维码不变、Q9 沿用供应商原始编号手填、Q10 不升级（服务端查）、Q11 只记流水不记结存、Q12 单人确认即生效。

### 10.1 后端

| 项 | 内容 |
|---|---|
| 新应用 | `apps/warehouse`（8 个模型：Lot / ProductionLotLink / PurchaseOrder / PurchaseItem / StockIn / StockInItem / MaterialIssue / IssueItem） |
| 迁移 | `warehouse/0001_initial`、`warehouse/0002_stockinitem_lot`、`production/0003_production_source_order_no`（存量表仅此一处改动） |
| 字段 | `Production.source_order_no`（CharField 64，blank，db_index）；序列化器同步加入 |
| 唯一约束 | 均带 `condition=Q(is_deleted=False)`（吸取 11 处教训）；血缘表 `uq_prod_lot_link_pair_active`（production+lot）；领料明细 `uq_issue_item_active`（issue+production+lot） |
| 血缘写入 | 两条路径：① P0 手工挂批次 `PUT /api/productions/<id>/lot-links`（全量重建，`_purge_deleted` 复用）；② P1 领料确认钩子 `MaterialIssueConfirmView`（`all_objects.get_or_create` + 重复领料 qty 累加） |
| 批次诞生 | 入库行保存时 `Lot.all_objects.get_or_create`（沿用供应商原始编号），新批次回填 `source_in` |
| 单号 | 采购 CG / 入库 RK / 领料 LL + yyyymmdd + 序号，`_next_doc_no` 碰撞循环兜底 |
| 追溯 API | `GET /api/trace/lot`（关键字搜批次+影响统计）、`/api/trace/lot/<pk>`（正向）、`/api/trace/reverse`（按款号/颜色反查床次）、`/api/trace/ticket/<pk>`（终端：菲票+床次+批次+登记流水+打印信息）、`/api/trace/production/<pk>`；全部 `check_perm('production')`（Q6） |
| 删除保护 | Lot 有活跃血缘或领料引用时禁删；领料/入库确认后禁改禁删 |

### 10.2 前端（微信小程序）

| 项 | 内容 |
|---|---|
| 新页面 | `packages/admin/trace`（扫菲票码进终端追溯 + 反向/正向查询双 Tab）、`packages/admin/trace-detail`（票/批次/床次三模式）、`packages/admin/warehouse`（采购/入库/领料三 Tab 最小可用：列表+单行明细建单+确认） |
| 入口 | 首页宫格 admin 包 + 🔍追溯 / 📦仓库（`MODULE_PERMS` 均挂 production） |
| 床次详情 | 新增追溯卡片：来源单号保存、批次挂载/解除（picker+用量）、查看追溯入口 |
| 建床页 | 来源单号输入框（面料字段下方，选填） |
| 标签 | `utils/bluetooth/templates.ts`：`PrintTicketData` 增 `source_order_no/lot_no`，`PRINT_FIELDS` 增「来源单号/批次」两项（打印管理里勾选才打，默认样式不变）；菲票列表打印数据自动带上床次的来源单号与全部批次号（逗号连接） |
| 二维码 | 内容保持 7 段不变（Q10），旧票扫码由服务端 `ScanParseView` 解析后凭 ticket id 查终端追溯，零改动 |

### 10.3 测试

| 项 | 结果 |
|---|---|
| 新增 `backend/traceability_tests.py` | 36 项全过（批次 CRUD+权限 403、血缘手挂/领料钩子、四个追溯 API、旧 7 段码兼容、多批次用量、来源单号录入修改、边界），连续两轮 PASS=36 FAIL=0（幂等） |
| 存量回归 | e2e_fullchain 111 / wave2_regression 53 / e2e_edge 24 / e2e_branches 54 / trust_verify 10，全绿 |
| 契约 | contract_check.py 后端路由 99 条未匹配 0（warehouse/trace 接口已同步 `frontend/services/warehouse.ts`、`traceability.ts`） |
| 编译 | 小程序 `tsc --noEmit` 0 错误 |
| 冒烟（HTTP 实测） | 建批次 201 → 挂床次 200（批次号+用量落库）→ 正向追溯 200（含 source_order_no）→ 反查 200 → 旧 7 段码 `/scan/parse` 200 → 终端追溯 200 出批次血缘，全链路通 |

### 10.4 与设计差异

1. **Lot 简化**：去掉草案的 `lot_type/parent`（Q1 拍板面料批次单一口径），锚点即面料批次。
2. **ScanParseView 零改动**：原设计预留一处改动，实现时发现响应已含 ticket id，追溯页直接复用，7 段校验逻辑原样保留。
3. **入库/领料明细带 lot 外键**（草案只挂库存单据头），保证行级批次与血缘写入一一对应。
4. **ESC/POS 已随 2026-10-18 拍板下线**，追溯两字段只在 TSPL2 标签链路生效。
5. P1 仓库按「样式从简」交付：三单据仅列表+单行明细新建+确认，无编辑/多明细/结存（Q11/Q12 拍板口径）。

### 10.5 遗留风险

- `trace/reverse` 大数据量下需按 `production__style_id + color` 补组合索引（P0 量级无压力）。
- 外发订单（order_type=外发）的批次信息一期不采集，追溯对其显示为空。
- 历史床次无批次血缘（Q5 不补录），老订单终端追溯只有流水没有批次段。
- 小程序 trace/warehouse 三页未做真机 automator 探测（可选验收项），逻辑已被 tsc + 契约校验覆盖。
