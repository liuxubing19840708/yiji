# 可配置生产看板 —— 开源调研与方案设计

> **【实施状态 2026-10-18：P0/P1/P2 核心已全部落地并通过测试，见文末「实施记录与差异」章节】**
>
> 调研日期：2026-10-18。所有 GitHub star / 发版时间均实测自 api.github.com 与 registry.npmmirror.com，非记忆数据。
> 适用系统：「扫菲」制衣厂生产管理系统（Django 5.2 + DRF 后端、微信小程序前端）。
> 目标：客户在 Web 管理端导入 Excel 表头 → 拖拽关联系统字段 + 公式组合 → 保存为看板模板 → 小程序按模板渲染，数据随时间筛选自动刷新。

---

## 0. 一句话结论

**不做「整包引入电子表格/低代码平台」的幻想**：电子表格组件（Univer 等）全部依赖浏览器 DOM/Canvas，跑不进微信小程序原生页；低代码平台（NocoDB/Teable 等）是独立 Web 系统，无法嵌入现有 Django+小程序架构。**可行路线是「自研窄配置器 + 现成开源零件」**：

- **后端**：Django 新增 2~3 个模型存模板；公式用 **simpleeval**（Python，受限求值，禁 eval）；
- **Web 配置端**：新增一个**独立 Vue 3 轻前端**（构建产物由 Django 托管），拖拽用 **vue-draggable-plus**（MIT），Excel 表头解析在后端用 **openpyxl** 完成（前端无需 SheetJS）；
- **小程序端**：把现有 `packages/admin/kanban` 的写死表格**泛化为「列定义驱动」的通用表格渲染器**，不引入任何第三方表格库。

预估总工作量 **17~23 人日**，分三期交付（见第 6 节）。

---

## 1. 调研结论与推荐

### 1.1 类 Excel 表格 / 电子表格组件

| 项目 | License | Star（实测 2026-10-18） | 最近动态 | 小程序适配 | 结论 |
|---|---|---|---|---|---|
| **Univer**（dream-num/univer，Luckysheet 官方继任） | Apache-2.0 | 22,652 | 非常活跃：最近一次 npm 发版 @univerjs/core 1.1.0 为 2026-10-17，累计 735 个版本；仓库当日仍有提交 | ❌ 依赖浏览器 DOM + Canvas 渲染，微信小程序原生页无 DOM，只能塞进 `web-view`（体验差、要配 H5 域名） | **不用**。性能强但体积大（核心+插件 MB 级），且我们只需「复刻表头」，引入整表编辑器属于杀鸡用牛刀 |
| **FortuneSheet**（ruilisi/fortune-sheet） | MIT | 3,736 | 维护放缓：最新发版 @fortune-sheet/react 1.0.4 为 2025-11-06，之后 11 个月无发版 | ❌ 同上，DOM/Canvas | 不用。Univer 的轻量替代品，但活跃度已不足 |
| **Luckysheet**（dream-num/Luckysheet） | MIT | 16,634 | **仓库已 archived（归档只读）**，最后推送 2025-08，官方声明由 Univer 接替 | ❌ 同上 | **排除**。确认停维护 |
| **Handsontable** | 自有商业许可（GitHub 显示 NOASSERTION）：源码可见但商用需付费授权，仅非商用免费 | 22,067 | 活跃（2026-10 仍有提交） | ❌ DOM | **排除**。商业 license 与本项目定位冲突 |
| **x-spreadsheet**（myliang/x-spreadsheet） | MIT | 14,581 | **停更**：最后推送 2024-08，412 个 open issue 无人处理 | ❌ Canvas | 排除 |
| **jspreadsheet / CE** | CE 版 MIT（pro 版商业） | 7,232 | 活跃（2026-10 有提交） | ❌ DOM | 可用但不必要，理由同 Univer |
| **vxe-table**（x-extends/vxe-table） | MIT | 8,628 | **非常活跃**：vxe-table 4.22.5 发版于 2026-10-17 | ❌ DOM（但小程序不需要它） | ✅ **推荐用于 Web 配置端**：做模板预览表、列宽/顺序编辑，Vue 生态、中文文档好 |
| **AntV S2**（antvis/S2） | MIT | 1,697 | 活跃：@antv/s2 2.7.2 发版 2026-06 | ❌ Canvas（@antv/s2 桌面版依赖 DOM） | 不用。透视表定位，与「复刻客户 Excel 明细表头」不匹配 |

**结论（第 1 层）**：所有类 Excel 组件都不能进小程序原生页（这是微信运行时的硬限制，不是选型问题），小程序端必然**自研轻量表格渲染**。唯一值得拿来用的是 **vxe-table**（Web 配置端的预览/编辑表格，可选——用原生 `el-table` 也够）。

### 1.2 低代码 / 无代码平台

| 项目 | License | Star | 最近动态 | 匹配度评估 |
|---|---|---|---|---|
| **Teable**（teableio/teable） | 核心源码可得（AGPL-3.0 基础 + 商业企业版双许可，LICENSE 头部自述 "Teable Project Licensing"） | 21,894 | 活跃（2026-10 有提交） | 「字段+视图+公式列+筛选」概念与我们的需求最像，**借鉴其公式列与视图配置的数据结构**；但它是 Postgres+Next.js 的独立 Web 平台，要跑一套完整容器栈，无法嵌入 Django 单体，更无法进小程序 |
| **NocoDB**（nocodb/nocodb） | **Sustainable Use License**（非 OSI 开源，2026-01 更新的 LICENSE.md 明示；企业目录另收费） | 65,293 | 活跃 | license 即不友好；同样无法嵌入 |
| **Baserow**（baserow/baserow） | 前端代码 MIT、premium/enterprise 目录独立商业许可（LICENSE 明示分区） | 6,168 | 活跃 | 同上，只能借鉴 |
| **APITable / AITable**（apitable/apitable） | AGPL-3.0 | 15,635 | 2026-09 后放缓 | AGPL 传染性 + 平台过重 |
| **Appsmith**（appsmithorg/appsmith） | Apache-2.0 | 41,079 | 活跃 | 它解决的是「拖拽搭应用页」，我们解决的是「配置一列数据怎么算」，可借鉴其 binding 表达式（`{{ }}`）的交互，整包不可嵌 |
| **ToolJet**（ToolJet/ToolJet） | AGPL-3.0 | 41,104 | 活跃 | 同 Appsmith |
| **Budibase**（Budibase/budibase） | server 为 GPL-3.0，各包 license 分散（仓库 LICENSE.md 明示"每包各自 license"） | 28,329 | 活跃 | 同上 |

**结论（第 2 层）**：全部是**独立 Web 平台**——自带数据库、自带前端、以「连接你的数据库生成管理界面」为产品形态，与「Django 单体 + 微信小程序」架构天然冲突（要嘛整套迁去它们，要嘛放弃）；且 AGPL/商业许可占多数。**明确结论：只能借鉴思路，不引入任何一家。** 值得抄的三点：
1. Teable/NocoDB 的「公式列」交互：列定义里选「公式」时给变量自动补全 + 常用函数面板；
2. Appsmith 的 binding：绑定表达式在人机两边是同一段字符串，后端为唯一求值方；
3. NocoDB/Baserow 的视图配置 JSON 化（filter/sort/groupby 都是声明式 JSON），与我们「筛选条件集」设计一致。

### 1.3 拖拽关联 / 字段映射 UI

| 库 | License | Star | 最近发版 | 说明 |
|---|---|---|---|---|
| **dnd-kit**（clauderic/dnd-kit，React） | MIT | 17,723 | @dnd-kit/core 6.3.1；仓库 2026-10 活跃 | React 生态最强拖拽；但若配置端选 Vue 则用不上 |
| **vue-draggable-plus**（Alfred-Skyblue/vue-draggable-plus） | MIT | 4,011 | 0.6.1 发版 2026-01-11；2026-03 有提交 | ✅ **推荐**。基于 SortableJS 封装的 Vue2/Vue3 双支持实现，维护中的首选（vue.draggable.next 已近两年无提交：2023-09） |
| **SortableJS**（底层引擎） | MIT | 31,196 | 活跃 | vue-draggable-plus 的底层，无需直接用 |
| react-sortablejs（SortableJS/react-sortablejs） | MIT | 2,169 | 最后提交 2023-12，放缓 | 仅当配置端选 React 时备选 |
| **Flatfile** | 主体已转商业 SaaS（早期 flat-file/XLSX JSONSchema 导入向导已不再是完整开源产品） | — | — | ❌ 无可整包使用的开源部分；但其「上传→自动识别列→人工确认映射」三步向导交互**值得照抄** |

**结论（第 3 层）**：配置端 = Vue 3 + Element Plus + **vue-draggable-plus**（拖拽映射 UI：左边"客户 Excel 列"列表 ↔ 右边"系统字段"列表，双向连线/拖拽配对）。这类映射 UI 没有开箱即用的成品组件，交互自研（约 2~3 人日），拖拽引擎用现成库。

### 1.4 Excel 解析

| 库 | License | Star / 版本 | 动态 | 用法 |
|---|---|---|---|---|
| **SheetJS CE**（SheetJS/sheetjs） | Apache-2.0 | 36,343；npm 官方发版已迁至自家 CDN（cdn.sheetjs.com），GitHub 镜像最后推送 2024-04 | 浏览器端解析 xlsx 的标准选择 |
| **exceljs**（exceljs/exceljs） | MIT | 15,489；exceljs 4.4.0 发布 2023-10 | 解析+**写** xlsx 都强 |
| **openpyxl**（Python） | MIT | — | 纯 Python，Django 同栈 | ✅ **推荐** |

**结论（第 4 层）**：我们**只需要读第一行/前几行表头**，不需要在浏览器里解析。最省的方案是**后端用 openpyxl 直接解析上传的 xlsx**（Django 原生文件上传 + openpyxl 读 header，返回列名数组），前端零解析依赖。SheetJS CE / exceljs 仅在以下情况启用：客户 Excel 表头有**多行合并表头**（如两级表头「1组-XX」），需要在浏览器里先可视化帮用户"选哪一行是表头"——届时用 SheetJS CE（Apache-2.0，商用无忧）在前端做预解析预览。

### 1.5 公式安全求值（补充调研）

- **simpleeval**（danthedeckie/simpleeval）：614 star，最近 2026-10 仍在维护，Python 侧最常用的"受限 eval"库——白名单运算符/函数/名字，天然禁掉属性访问与 dunder，**推荐作为公式求值内核**（外层再做一层字段白名单校验，双保险）。
- 替代：手写受限 AST 求值器（`ast.parse` + 只放行 `BinOp/UnaryOp/Name/Constant/Compare` 节点），零依赖，约 100 行，控制力最强。**建议：先 simpleeval，遇性能/扩展瓶颈再自写 AST**（两者对上层 DSL 设计无影响）。

### 1.6 总表：候选 × license × 活跃度 × 匹配度 × 集成成本

| 候选 | License | 活跃度 | 需求匹配度 | 集成成本 | 决定 |
|---|---|---|---|---|---|
| Univer | Apache-2.0 | ★★★★★ | ★★（进不了小程序） | 高（MB 级+学习成本） | ❌ |
| Luckysheet | MIT | ✖ 已归档 | — | — | ❌ |
| FortuneSheet | MIT | ★★（放缓） | ★★ | 中 | ❌ |
| Handsontable | 商业 | ★★★★ | ★★ | 高（费用） | ❌ |
| vxe-table | MIT | ★★★★★ | ★★★★（仅配置端预览） | 低 | ✅ 可选 |
| NocoDB / Teable / Baserow / APITable / Budibase | 自定义/AGPL 混合 | ★★★★ | ★（整包不可嵌） | 极高 | ❌ 仅借鉴 |
| Appsmith / ToolJet | Apache-2.0 / AGPL-3.0 | ★★★★★ | ★ | 极高 | ❌ 仅借鉴 |
| vue-draggable-plus | MIT | ★★★★ | ★★★★★（拖拽映射） | 低 | ✅ 引入 |
| dnd-kit | MIT | ★★★★★ | ★★★★（仅当 React） | 低 | 备选 |
| openpyxl | MIT | ★★★★ | ★★★★★（读表头） | 极低 | ✅ 引入 |
| SheetJS CE | Apache-2.0 | ★★★ | ★★★（多行表头预览才需要） | 中 | 备选 |
| simpleeval | MIT（仓库标注） | ★★★★ | ★★★★★（公式求值） | 极低 | ✅ 引入 |

---

## 2. 总体架构

**推荐：独立 Vue 3 轻前端（配置端）+ Django 模板 API + 小程序自研渲染器。** 不推荐 Django template 页面，理由见 2.2。

```
┌─ Web 配置端（新增，Vue3 + Vite + Element Plus + vue-draggable-plus）
│   ① 上传 Excel → 后端 openpyxl 解析表头回显
│   ② 拖拽映射：Excel 列 ↔ 系统字段/公式
│   ③ 试跑预览（调 /preview 接口，vxe-table/el-table 展示）
│   ④ 保存模板
│           │ 构建产物 dist/ 由 Django STATICFILES 托管
│           ▼
├─ Django 单体（现有 backend，新增 apps/kanban 应用）
│   KanbanTemplate / KanbanColumn 模型
│   模板 CRUD + 表头导入 + 试跑 + 渲染 API
│   公式求值：simpleeval + 字段白名单（后端唯一求值方）
│           │ JSON
│           ▼
└─ 微信小程序（现有 frontend，packages/admin/kanban 泛化）
    GET /api/kanban-render?template_id&range= → 按模板列定义渲染
    现有 buildGroups/合并格/底色交替逻辑参数化复用
```

### 2.2 配置端形态：为什么是独立轻前端而不是 Django template

- **推荐**：新建 `frontend-admin/`（Vue 3 + Vite + Element Plus），`npm run build` 产物放 Django 的静态目录（如 `static/admin/`），Django 加一条 `TemplateView` 路由托管 `index.html`，登录复用现有 DRF Token（页面上登录一次存 localStorage）。**与单体共存方式 = 静态托管 + 同源 API**，不增加部署面。
- 不推荐 Django template 页面：字段拖拽映射、双向连线、自动补全、实时试跑这类交互，用原生模板页 + jQuery 的开发成本反而高于一个小 Vue 工程，且无法复用现成开源拖拽库（vue-draggable-plus 要求 Vue 运行时）。
- 备选：若团队强烈不想引入 Node 构建，可退化为「纯 JS 单页」（SortableJS 有原生 JS 版本），但 Element Plus/vxe-table 都用不上，长期维护更差。**决策点见第 7 节 Q1。**

### 2.3 小程序渲染端

现有 `packages/admin/kanban` 的表格是「写死 10 列 + buildGroups 按款号分块合并」的硬编码。泛化思路：把**列定义**从模板接口下发，wxml 循环 `columns` 渲染表头与单元格，行数据统一为 `{key, cells: [{col_id, text, merge?, alt?}]}`。详情见第 5 节。

---

## 3. 数据模型草案

新增 Django app：`apps/kanban`。三张表 + 一个「可绑定字段注册表」（代码内声明，不落库）。

### 3.1 模型

```python
class KanbanTemplate(BaseModel):
    """看板模板（公司维度）。"""
    company = models.ForeignKey('accounts.Company', on_delete=models.CASCADE,
                                related_name='kanban_templates')
    name = models.CharField('模板名称', max_length=64)          # 如「张生专用-1组版」
    dataset = models.CharField('行数据源', max_length=32, default='dashboard_plans')
        # 行数据源枚举：dashboard_plans（现 dashboard 的 plans，行=款+日期+颜色）
        # 预留：beds（床次粒度）、tickets（菲票粒度）——P2 再开
    filters = models.JSONField('筛选条件集', default=dict, blank=True)
        # 声明式 JSON，例：
        # {"order_type": ["自产"], "is_locked": false,
        #  "date_field": "order_date",   # 该模板按哪个日期跟 range 联动
        #  "customer_groups": [1, 3]}
    is_default = models.BooleanField('默认模板', default=False)
    version = models.PositiveIntegerField('版本号', default=1)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['company', 'name'], name='uq_kanban_template_company_name')]


class KanbanColumn(BaseModel):
    """模板列定义（有序，seq 排序）。"""
    BIND_TYPE = (('field', '系统字段'), ('formula', '公式'), ('static', '静态文本'))
    template = models.ForeignKey(KanbanTemplate, on_delete=models.CASCADE,
                                 related_name='columns')
    seq = models.PositiveIntegerField('列序')
    title = models.CharField('列标题（复刻客户 Excel 表头）', max_length=64)
    bind_type = models.CharField('绑定类型', max_length=16, choices=BIND_TYPE,
                                 default='field')
    field_key = models.CharField('系统字段 key', max_length=64, blank=True, default='')
        # bind_type=field 时必填，如 style_no / color / cut_qty / group_1_qty
    expr = models.CharField('公式表达式', max_length=256, blank=True, default='')
        # bind_type=formula 时必填，如 "cut_qty - order_qty"、"sum(beds[*].qty)"
    static_text = models.CharField('静态文本', max_length=64, blank=True, default='')
    extra = models.JSONField('展示配置', default=dict, blank=True)
        # 列级展示：{"width": 90, "numfmt": "int", "merge": "block", "placeholder": "—"}
        #   merge: "block"=随款号块合并（现 kanban 的款号/已裁合计列）, "none"=不合并
        #   placeholder: 无数据时占位符（解决「排单情况/出货」暂无数据源的列）
    class Meta:
        ordering = ['seq']
```

### 3.2 可绑定字段注册表（代码内声明）

后端维护一个「字段字典」，配置端通过 `GET /api/kanban-fields/` 拉取用于拖拽面板，渲染与求值共用同一份白名单：

```python
KANBAN_FIELDS = {
    # key: (中文标签, 取值方式, 支持的行粒度)
    'style_no':    ('款号',   'style',  'plans'),
    'style_name':  ('款式名称', 'style', 'plans'),
    'color':       ('颜色',   'row',    'plans'),
    'order_qty':   ('订单数量', 'agg',   'plans'),   # 该款该色菲票合计
    'bed_no':      ('床次',   'join',   'plans'),   # 多床拼接文本
    'bed_qty':     ('单床裁货数', 'join','plans'),
    'cut_qty':     ('已裁数量', 'agg',   'plans'),
    'cut_total':   ('已裁合计', 'agg',   'plans'),
    'order_date':  ('下单日期', 'style', 'plans'),
    'registered_qty': ('下线数量(登记件数)', 'agg_records', 'plans'),  # 新聚合：Record 关联款/色
    'done_qty':    ('完成件数', 'agg_records', 'plans'),
    # 预留位（数据源未建，模板里可配但渲染出占位符）：
    'ship_qty':    ('出货',  'todo', 'plans'),
    'tail_qty':    ('尾部成品', 'todo', 'plans'),
    'schedule':    ('排单情况', 'todo', 'plans'),
}
```

> 「1组XX」这类列 = `group_{no}_qty`（按员工组编号聚合登记数），注册表按公司已有的 `group_no` 动态生成，配置端拉字段字典时即包含 `1组件数 / 2组件数…`。

### 3.3 公式表达式的存储与安全求值

**存储**：纯字符串 DSL，例如：

```
cut_qty - order_qty            # 两字段相减
cut_qty / order_qty * 100      # 完成率
round((registered_qty / cut_qty) * 100, 1)   # 进度%
sum(beds[*].qty)               # 床次数组聚合
```

**安全求值（后端唯一求值方，禁止 eval）**，三层防护：
1. **解析校验**（保存模板时）：`ast.parse(expr)` 后遍历 AST，只放行 `Expression/BinOp/UnaryOp/Name/Constant/Compare/Call` 白名单节点；非法即拒（`add/sub/mul/div/Compare` + `round/sum/min/max/abs` 内置函数白名单）。拒绝一切 `Attribute/Subscript(动态属性)/Lambda/ifexp`。
2. **求值**（渲染时）：通过校验的表达式用 **simpleeval**（`EvalWithCompoundNames` + 自定义 `NAMES=行字段值字典`、`FUNCTIONS=白名单函数`）求值；simpleeval 本身禁属性访问、禁 dunder。
3. **兜底**：求值异常 / 除零 → 单元格落 `placeholder`（默认 `—`），不影响整行渲染；字段白名单校验在 simpleeval 之前做（表达式里出现的 `Name` 必须在 `KANBAN_FIELDS` 中）。

> 为什么不用 JSON 存公式树：客户需求以「两字段相减」「求和」为主，字符串 DSL 更直观可读、配置端可展示原文；AST 校验成本可控。若 P2 后出现嵌套复杂公式，再考虑结构化存储（决策点 Q5）。

---

## 4. API 设计草案

全部挂现有 `CompanyContextMixin`（公司隔离 + 在职成员校验），新增前缀 `/api/kanban/`：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/kanban/fields/` | 可绑定字段字典（含按公司动态生成的组编号字段），配置端拖拽面板数据源 |
| GET/POST | `/api/kanban/templates/` | 模板列表 / 新建（POST body 含 columns 数组，事务保存并写版本号） |
| GET/PUT/DELETE | `/api/kanban/templates/{id}/` | 模板详情 / 全量更新（列定义整体替换）/ 删除 |
| POST | `/api/kanban/import-header/` | 上传 xlsx（multipart），openpyxl 读第一行（可选第二行）返回 `{columns: [{col_index, title, merged_span}]}`；**只读不落库** |
| POST | `/api/kanban/templates/{id}/preview/` | 试跑：body 传 `{range, start_date, end_date, filters_override?}`，返回与渲染接口同构的数据（复用 dashboard 聚合），配置端表格直接展示 |
| GET | `/api/kanban/render/` | **小程序渲染接口**：`?template_id=&range=today|yesterday|week|month|last_month|custom&start_date=&end_date=`。响应结构见下 |
| PUT | `/api/kanban/templates/{id}/default/` | 设为该公司默认模板（小程序端不传 template_id 时取 is_default） |

**渲染接口响应（草稿）**：

```json
{
  "template": {"id": 1, "name": "1组版", "version": 3},
  "columns": [
    {"id": 1, "seq": 0, "title": "款号", "merge": "block", "width": 90},
    {"id": 2, "seq": 1, "title": "已裁数量", "merge": "none"},
    {"id": 3, "seq": 2, "title": "下线数量", "merge": "none", "placeholder": "—"}
  ],
  "groups": [
    {"date": "2026-10-15", "label": "10月15日", "blocks": [
      {"key": "12-2026-10-15", "alt": true, "rows": [
        {"key": "12-红色",
         "cells": [{"col": 1, "text": "A123", "merge_row": true},
                   {"col": 2, "text": "320"},
                   {"col": 3, "text": "280"}]}
      ]}
    ]}
  ],
  "summary": {...}   // 复用现有 dashboard summary，可选保留
}
```

> 后端实现：把现 `DashboardView` 中「plans 聚合」一段抽成 `services.build_plan_rows(company, plan_start, plan_end, filters)` 纯函数；原 dashboard 接口与新 render 接口都调用它，避免口径分叉。行数据出来后，按模板列定义逐列填充（field 直取 / formula 用 simpleeval 求值 / static 原样），`todo` 类字段落 placeholder。

---

## 5. 小程序端渲染方案

**结论：现有写法可以泛化，不必重写页面骨架，只改「列」与「单元格」两层为数据驱动。**

现状（`frontend/packages/admin/kanban/index.ts`）：`buildGroups()` 把 plans 按下单日期分组成 `DateGroupVM → StyleBlockVM（款块，含真合并格）→ PlanRowVM`，wxml 按 10 个写死列渲染，`alt` 做块奇偶底色交替。

泛化为三步：

1. **列数据驱动**：wxml 表头改为 `<block wx:for="{{columns}}" wx:key="id">`，与表体同序循环；列宽、placeholder、numfmt 都从 columns 读。每个 `PlanRowVM.cells` 改为「按列 id 对齐」的数组（渲染接口已对齐好，小程序零计算）。
2. **合并单元格的表达**：模板列的 `extra.merge` 决定该列是「随款块合并」（`merge: "block"`，如款号/已裁合计——沿用现在「块左侧一格拉伸块高垂直居中」的实现）还是普通列。合并能力**不做任意 Excel 合并**（如横向跨列），只支持现有「纵向随块合并」这一种，够覆盖当前页面形态；横向合并属过度设计（决策点 Q6）。
3. **底色交替等样式**：沿用 `blocks[].alt`（全表块序奇偶）；模板 `extra` 预留 `column_bg`（列底色），P2 再开。每日定时刷新沿用现有 30s 时钟 + 下拉刷新 + range tab 逻辑，**这部分完全不动**。

小程序侧工作量可控的原因：wxml 结构（三段嵌套循环 + 横向 scroll-view）不变，变的只是列的来源。真正新增的是「多模板选择入口」（模板名 tab 或下拉）。

---

## 6. 分期实施计划

| 期 | 内容 | 交付物 | 估时 |
|---|---|---|---|
| **P0 最小可用**（字段选择+排序+公式基础） | ① `apps/kanban` 模型 + 迁移；② `KANBAN_FIELDS` 注册表 + fields 接口；③ 模板 CRUD API（formula 求值用 simpleeval + AST 白名单校验）；④ render 接口（抽公共聚合函数）；⑤ 小程序 kanban 泛化为列驱动 + 模板选择入口；⑥ 配置端**先不做**——P0 用 Django admin 或直接 POST JSON 建模板验证闭环 | 一个能配置列/顺序/简单公式的看板 | **6~8 人日**（后端 4~5，小程序 2~3） |
| **P1 Excel 表头导入 + 拖拽映射** | ① 独立 Vue3 配置端脚手架 + Django 静态托管；② import-header 接口（openpyxl，含两级表头检测）；③ 拖拽映射 UI（vue-draggable-plus：Excel 列 ↔ 字段面板，未匹配列默认为静态文本）；④ preview 试跑表格；⑤ 保存模板 | 客户拿来 Excel 就能配出模板 | **7~9 人日**（前端 5~6，后端 2~3） |
| **P2 公式组合增强 + 多模板** | ① 公式编辑器（变量自动补全 + 常用函数面板 + 实时试算）；② 新聚合字段（下线数量/完成件数按款色、组编号字段动态生成）；③ 多模板管理页 + 小程序模板切换 + 默认模板；④ 列级样式（列底色/数字格式） | 完整方案上线 | **4~6 人日**（前端 2~3，后端 2~3） |

合计 **17~23 人日**。P0 独立可交付（先用 JSON 建 1~2 个真实客户模板灰度验证数据口径），P1 后交付到客户手中自助配置。

---

## 7. 风险与决策点（请拍板）

1. **Q1 配置端技术栈**：接受新增一个 Vue 3 轻前端（Node 构建一次、产物静态托管）吗？若坚持不引 Node，配置端退化为 Django template + 原生 SortableJS，工期 +2~3 人日且交互体验降级。
2. **Q2 行数据源粒度**：P0 只做 `dashboard_plans`（行=款号+日期+颜色）。是否需要「床次粒度」「菲票粒度」看板？若需要，P2 要新增聚合器，+3~4 人日。
3. **Q3 无数据源的列（排单情况/出货/尾部成品）**：方案是「模板可配、渲染显示占位符」。这些业务数据要不要在 P2 真建数据源（如出货运单表）？若建，每个新数据源是独立需求（各 3~5 人日），不建议挤进本期。
4. **Q4 Excel 表头形态**：客户的 Excel 是否普遍存在**两级表头/合并表头**（如「1组」跨多列）？决定 import-header 是否需要 SheetJS 前端预解析 + 表头行选择交互（+2 人日）。建议先收集 3~5 份真实客户 Excel 再定。
5. **Q5 公式复杂度上限**：当前 DSL 只支持四则+比较+5 个聚合函数。若客户要「条件列」（如果…则…），需要扩展 DSL（`if` 函数）或改结构化存储，+2~3 人日；建议一期不承诺。
6. **Q6 小程序合并单元格能力边界**：只做「纵向随款块合并」，不做任意 Excel 式合并/横向跨列，可以吗？（任意合并需要换渲染方案——自绘 canvas 表格或 web-view 嵌 H5，成本翻倍。）
7. **Q7 模板权限**：谁能建/改模板（仅管理员？）以及模板是否允许客户方的人自己登录 Web 端配置（涉及账号体系开放给公司管理员之外的角色）。
8. **Q8 旧看板去留**：新模板看板上线后，现有写死 10 列 kanban 是保留为「默认模板的一种」（推荐：初始化脚本把它灌成每个公司的默认模板，UI 无感切换）还是直接下线。

---

## 附：主要参考仓库

- Univer：https://github.com/dream-num/univer （Apache-2.0）
- Luckysheet（已归档）：https://github.com/dream-num/Luckysheet
- FortuneSheet：https://github.com/ruilisi/fortune-sheet （MIT）
- Handsontable：https://github.com/handsontable/handsontable （商业许可）
- vxe-table：https://github.com/x-extends/vxe-table （MIT）
- AntV S2：https://github.com/antvis/S2 （MIT）
- Teable：https://github.com/teableio/teable ；NocoDB：https://github.com/nocodb/nocodb ；Baserow：https://github.com/baserow/baserow ；APITable：https://github.com/apitable/apitable ；Appsmith：https://github.com/appsmithorg/appsmith ；ToolJet：https://github.com/ToolJet/ToolJet ；Budibase：https://github.com/Budibase/budibase
- vue-draggable-plus：https://github.com/Alfred-Skyblue/vue-draggable-plus （MIT）
- dnd-kit：https://github.com/clauderic/dnd-kit （MIT）；SortableJS：https://github.com/SortableJS/Sortable （MIT）
- SheetJS CE：https://github.com/SheetJS/sheetjs （Apache-2.0）；exceljs：https://github.com/exceljs/exceljs （MIT）
- simpleeval：https://github.com/danthedeckie/simpleeval
- 本项目现状代码：`D:\project\扫菲\backend\apps\production\views.py`（DashboardView 聚合）、`D:\project\扫菲\frontend\packages\admin\kanban\index.ts`（现看板渲染）


---

## 8. 实施记录与差异（2026-10-18）

**状态：P0 + P1 + P2 核心全部落地。** 五套既有回归全绿（e2e_fullchain 111 / wave2_regression 53 / e2e_edge 24 / e2e_branches 54 / trust_verify 10），contract_check.py 0 未匹配，新增幂等套件 `backend/kanban_template_tests.py` 两连跑 37/37 全过，后端已重启并 curl 冒烟通过（渲染接口与旧 plans 等价：`EQUIVALENT: True`）。

### 8.1 用户拍板结论（原 8 个决策点）

| # | 决策 | 落地情况 |
|---|---|---|
| 1 | 配置端 = Vue3 轻前端 + vue-draggable-plus | ✅ `frontend-admin/`（Vue3+Vite，产物 gzip≈46KB），构建输出 `backend/static/kanban_admin/`，由 Django 托管在 `/api/kanban-admin` |
| 2 | 行粒度 = 款号+下单日期+颜色 | ✅ dataset 固定 `dashboard_plans` |
| 3 | 排单/下线/出货/尾部成品一期占位 | ✅ 字段注册表 kind=todo，渲染一律「—」 |
| 4 | Excel 两级/合并表头 | ✅ openpyxl 解析（自动探测表头行=非空格数最多的第一行 + 支持指定行；两级判定=上一行横向合并块，parent/col_span 下发） |
| 5 | 公式含条件表达式 | ✅ simpleeval + AST 白名单：四则/一元/比较/and/or/三元 `a if c else b`/`IF(cond,a,b)`/`round/sum/min/max/abs`；注入用例（`__import__`、属性访问、字符串拼接、任意函数、未知字段）全部 400 |
| 6 | 小程序任意合并 | ✅ 列驱动渲染器：merge=block（随款块整列一格）/ value（块内连续同值合并 rowspan）；两级表头（列带 group）→ 组头 colspan + 无组列跨两行 rowspan；行高固定 88rpx 保证跨列段对齐，真实格边框画线 |
| 7 | 模板权限 = 仅本公司管理员 | ✅ 模板 CRUD/导入/试跑全部 require_admin；fields/render 登录成员可读 |
| 8 | 旧看板无感切换 | ✅ `manage.py seed_kanban_templates`（幂等）把经典十列灌成每公司默认模板；无模板/渲染失败回落代码内置默认模板，绝不允许白屏（前端另有客户端兜底：渲染接口异常时用旧 dashboard plans 拼默认列） |

### 8.2 改动文件清单

**后端（新 app `apps/kanban_template`，未改任何现有业务表）**
- `apps/kanban_template/`：models.py（KanbanTemplate/KanbanColumn）、fields.py（字段注册表）、formula.py（DSL 校验+simpleeval 求值）、excel.py（表头解析）、services.py（渲染核心/内置默认模板/回落壳）、views.py（7 个 API）、urls.py、web.py（SPA 静态托管）、apps.py、migrations/0001、management/commands/seed_kanban_templates.py
- 现有文件最小改动：`config/settings.py`（INSTALLED_APPS +1 行）、`config/urls.py`（include +1 行）、`apps/production/views.py`（plans 内联聚合抽取为函数调用，逻辑零改动）、`apps/production/plan_rows.py`（新文件：plan_window + build_plan_rows，dashboard 与看板渲染共用同口径）

**小程序（frontend）**
- `services/kanban-template.ts`（新）：渲染接口 + 客户端回落
- `packages/admin/kanban/index.ts / .wxml / .wxss`：表格改为列驱动（旧 buildGroups/写死 10 列移除；顶部汇总卡/排行/工序占比仍走 /api/dashboard）

**配置端（frontend-admin，新目录）**
- package.json / vite.config.js / index.html / src/{main.js,App.vue,style.css} / README.md；构建产物 `backend/static/kanban_admin/`

**测试**
- `backend/kanban_template_tests.py`（新，幂等两连跑 37/37）：CRUD 权限 403、字段注册表、公式求值（四则/条件/百分比）与 5 类注入拦截、Excel 两级表头解析（自动探测+指定行）、渲染按模板输出、todo 占位、求值异常落占位、无模板回落内置默认、seed 幂等

### 8.3 与设计文档的差异

1. **渲染接口多了列展示配置**：columns 下发增加 `align`（left/right/center）与床次文案改 `
` 分行（小程序 pre-line 逐行，等价旧观感）——设计稿未列，实施时补。
2. **合并模型简化为两种**：`block`（随款块整列）与 `value`（块内连续同值 rowspan）；设计稿的「任意 Excel 合并」按拍板第 6 条收敛为「任意纵向合并 + 表头两级 colspan/rowspan」，不支持横向跨列合并单元格。
3. **小程序表体行高固定（88rpx）**：为保证任意列段对齐；床次超过 2 行/色会被裁剪（旧版为自然行高）。如客户床次数普遍 >2，需调 `ROW_H` 常量。
4. **字段注册表未做「组编号字段」（group_N_qty）动态生成**：组数据只有全厂粒度（组排行），无款色粒度数据源，一期不提供，避免承诺不可渲染的列。
5. **fields 接口权限放宽为登录成员**（设计稿未明确；模板写操作仍仅管理员）。
6. **preview 接口支持内联列定义**（未保存即可试跑），比设计稿的 template_id-only 更顺手。

### 8.4 遗留风险 / 后续建议

- 小程序端合并渲染（列驱动+两级表头）尚未真机/桥接验证（automator 探针按指示跳过，主会话负责真机验证）；重点看：两级表头对齐、床次 2 行裁剪、块交替底色。
- 公式 DSL 的 `sum(beds[*].qty)` 数组聚合未实现（设计稿 P2 展望项），当前床次数参与公式需用 cut_qty 等标量字段。
- 配置端目前仅「列序拖拽」用了 vue-draggable-plus；「Excel 列 ↔ 字段连线」以逐列下拉选择代替（演示闭环完整，连线交互可在后续迭代增强）。
- KanbanTemplate.filters（筛选条件集）字段已建模但渲染端未消费（一期所有筛选走 range 时间窗），属预留。

---

## 9. P3 迭代记录（2026-10-18 第二批三项改进）

**状态：全部落地并验证。** kanban_template_tests.py 两连跑 50/50 全过；五套既有回归全绿（e2e_fullchain 111 / wave2_regression 53 / e2e_edge 24 / e2e_branches 54 / audit_fixes 34 + traceability_tests 36）；contract_check.py 0 未匹配；tsc --noEmit 0 错误；automator 探针 `uitest/probe_kanban.js` 9/9 全过（截图 `uitest/shots/probe_kanban.png`）。

### 9.1 真合并格（合并区域内部无横线）

- 渲染端边框方案重做：行线只画到每段最后一格为止（`.pt-seg .pt-cell:last-child` 去下边框），块边界改由 `.pt-block` 元素统一下边框（表末块不画）——合并段（单格跨多行）内部天然无横线，视觉上是一整格；垂直居中由 `.pt-cell` 的 align-items:center 保证，水平对齐跟随列 align（删除原 `.pt-seg-merged` 强制水平居中规则）。
- 两级表头组头 colspan 复核：组头本来就是单格跨列，无内部线，无需改动。
- 配置端「试跑预览」改为**真实 rowspan**（原实现为隐藏格假合并，会显内部线）：block=整块一格、value=连续同值一格，预览观感与设备端一致。

### 9.2 自适应列宽（width=0/空/'auto'）

- 归一契约（services._normalize_width）：extra 无 width 键（存量模板）→150 保持原样；0/''/'auto' → 0 = 自适应；数值原样。存量「默认看板」种子列已按 seq 一次性回填经典十列宽度（30 列）。
- 小程序 computeWidths：按内容估宽（CJK 24rpx/字、ASCII 13rpx/字符、内边距 28，护栏 90–480rpx，含表头与全行采样、多行文案取最长行），全部自适应列合计不足屏宽时剩余空间均摊补齐；固定宽列不参与。配置端宽度输入留空=自适应（placeholder「自适应」提示），保存为 0。

### 9.3 全量字段注册表（31 项，按类目分组）

- fields.py 全量注册（每项 label+类目+说明）：款式（款号/款式名称/客户/款式单价/款式备注/下单日期）、生产/床次（床次、床次数、计划裁货数、已裁数量、已裁合计、面料、来源单号、订单类型、床次客户、建床日期、裁完状态）、菲票聚合（颜色、订单数量、扎数、尺码集合、工序总数/已完成工序数/进度百分比）、时间（下单年月、本月/本周下单，由下单日期推导）、占位（排单/下线/出货/尾部成品）。组排行等无款色粒度数据源的字段仍不注册（不承诺不可渲染的列）。
- plan_rows.build_plan_rows 新增 detail=True 模式（看板渲染用；dashboard 保持轻量）：5 组批量查询（床次扩展信息 values 一条、款式扩展一条、菲票扎数/尺码聚合两条、订单工序并集一条、已完成工序 distinct 一条），无 N+1。
- 公式 DSL 放开 ≤64 字符符串常量（支持 `IF(order_type=='外发', ...)`）；渲染层加 200 字符文本截断护栏（防 `'x'*n` 类大 payload）；超长常量保存期拒绝。
- fields 接口下发 category+desc，配置端字段下拉按类目 optgroup 分组。

### 9.4 P3 改动文件

- 后端：`apps/kanban_template/fields.py`（全量注册表）、`formula.py`（字符串常量）、`services.py`（宽度归一/时间推导/detail 渲染/文本截断/内置列宽）、`views.py`（格式修复）；`apps/production/plan_rows.py`（detail 模式 + _attach_detail 批量查询）
- 小程序：`packages/admin/kanban/index.ts`（computeWidths + 移除 merged 类）、`index.wxml`（去 pt-seg-merged）、`index.wxss`（边框方案重做）
- 配置端：`frontend-admin/src/App.vue`（真实 rowspan 预览/宽度空=自适应/类目分组下拉），已重新构建至 `backend/static/kanban_admin/`
- 测试：`backend/kanban_template_tests.py` 扩至 50 项（S7：注册表覆盖 plan_qty/source_order_no/fabric/进度等、detail 渲染值断言、字符串常量公式求值、超长常量拒绝、宽度归一 [0,0,150,0,210]）；`uitest/probe_kanban.js`（新，automator 探针）

### 9.5 探针结果（automator，ws://localhost:9420）

- P1 reLaunch 首页编译验证 ✅；P2 reLaunch 看板页 ✅；P3 渲染出数据 ✅
- P5 自适应列宽生效：width=0 三列渲染为 [247,233,239] rpx（估宽+均摊）✅
- P6 block 合并段=单格（cells=1，无内部横线）✅；P7 非合并列正常分行对照 ✅；截图留档 ✅
- 探针模板自动清理、原默认模板自动恢复 ✅
