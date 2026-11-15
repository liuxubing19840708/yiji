# AI 动态报表：QueryPlan 与自然语言 Planner

## 当前范围

主链路为：自然语言或设计器 → 受治理 QueryPlan → 服务端权限和结构校验 → Django ORM → 表格/图表/同步或异步 XLSX。大模型不直接连接业务库，不执行 SQL。

已注册数据集：

- `piecework_records`：计件登记、预估计件工资、登记笔数、参与人数和菲票数。
- `orders`：订单数、订单量、已裁量、登记量；一对多明细按订单预聚合。
- `productions`：床次数、计划/实际裁货、菲票和登记量。
- `shipments`：出货单数、审核单数、实际出货量、单据数量和箱数。
- `warehouse_lots`：批次原单位入库、确认领退料及当前流水库存；不同单位不得混加。
- `purchase_items`：采购行、采购量、确认到料/合格/不合格、逐行待到料、可换算采购金额和金额待补参数行数。
- `salary_settlements`：有效/撤销结算快照金额、结算笔数和覆盖人次；不是实际支付工资。
- `salary_applications`：各状态结算申请笔数；申请金额不作为最终发放金额。
- `salary_payments`：有效实付工资、支付笔数和实付员工数；按实际支付时间统计，作废支付不计入。
- `finance_receivables`：应收、累计已收、未收余额及应收核销率；无到期日，不提供账龄或逾期天数。
- `finance_receipts`：按实际回款时间统计有效回款；撤销核销和作废应收默认排除。
- `finance_payables`：财务确认的采购应付、累计已付、未付余额及应付核销率。
- `supplier_payments`：有效供应商实际付款；可关联应付，也可记录尚未关联应付的实际付款。
- `cash_entries`：手工费用支出、其他收入及其他收支净额，不重复包含工资、客户回款或供应商付款。
- `material_stock`：物料主单位库存、可换算入出库及未覆盖流水行数。
- `finance_cash_flow`：统一经营现金流，合并五类有效实际流水，支持日/周/月、来源和收支方向分组筛选。

当前共 16 个数据集。应收核销率=累计已收/应收，应付核销率=累计已付/应付；分母为零返回空。结算快照、结算申请和实付工资是不同口径，不得相互替代。完整利润仍不可用，因为当前没有完整的成本、税务和会计期间结转口径。

采购跨单位金额按当前物料克重、幅宽折算；缺物料、缺参数或未知单位的行不伪造金额，必须同时查看待补参数行数。采购完成不等于已入库或已付款。统一经营现金流同时由 `finance_cash_flow` 数据集和 `GET /api/finance/cash-flow` 提供，均要求 `finance.manage` 和 `salary.view`（`ai.full_data` 按全域规则覆盖）。AI 数据集分别通过 ORM 聚合客户回款、其他收入、实付工资、供应商付款和费用后合并，支持日/周/月、来源、方向、期间对比、图表及导出；流出为正数，净现金流=流入−流出，不是利润表。该数据集永久排除作废或撤销流水，`include_deleted=true` 会被拒绝。

## QueryPlan 与展示

- 数据集、字段、指标、聚合、筛选、排序均使用白名单 key；未知 key、ORM path、`company_id` 和 SQL 键直接拒绝。
- `presentation` 支持 `table`、`bar`、`line`。图表必须一个指标、至少一个维度；折线图首维度必须为日/周/月。图表和明细表使用同一次执行结果，不建立第二套统计口径。
- 可选 `comparison=previous/year_ago` 分别表示等长上一期和去年同期；必须恰有一个主时间字段的 `between` 日期范围，且不能与 `as_of` 同用。
- 对比结果按维度合并，输出本期、对比期、差额和变化率。变化率=`(本期−对比期)/|对比期|`；对比期为零或任一期为空时变化率为空。任一期分组截断时 `available=false`，不提供误导性的全量对比。XLSX 另附“期间对比”工作表。
- 本地多轮规则支持明确的“取消客户筛选”“把客户改为……”和清空筛选；复杂继承、歧义识别仍由 GLM 生成候选计划，服务端再次校验。
- `material_stock` 支持可选 `as_of=YYYY-MM-DD`，日期不得晚于服务器本地今天，其他数据集使用会被拒绝。
- `as_of` 库存是“截至业务日期、按当前留存流水重算”：不是审计快照，不能恢复历史软删状态、历史确认状态或历史物料参数。
- 主单位库存使用当前物料参数进行 kg↔米换算；未知单位和缺参数只计入未覆盖流水数。未关联物料的流水不进入物料数据集。原批次指标不改为换算单位。

示例：

```json
{
  "dataset": "piecework_records",
  "metrics": [{"key": "registered_qty", "aggregation": "sum"}],
  "dimensions": ["registered_day"],
  "filters": [{"field": "registered_at", "operator": "between", "value": ["2026-11-01", "2026-11-30"]}],
  "order_by": [{"field": "registered_day", "direction": "asc"}],
  "limit": 100,
  "include_deleted": false,
  "presentation": "line"
}
```

## API

- `GET /api/ai/report/catalog`：当前岗位可用数据集、字段、指标及 `export/share/full_data` 能力；`advanced_sql` 恒为 `false`。
- `POST /api/ai/report/execute`：预览，最多 500 行；响应含 SQL 条数、SQL 耗时和慢查询标记。
- `POST /api/ai/report/export`：原同步 XLSX 接口，最多 5000 行，要求 `ai.export`。
- `GET/POST /api/ai/report/exports`：本人异步任务列表/创建；创建返回 202，不在 Web 请求内生成文件。
- `GET /api/ai/report/exports/<uuid>`：本人任务状态。
- `GET /api/ai/report/exports/<uuid>/download`：私有文件下载；重新检查导出权限、数据权限、计划结构和过期状态。
- `GET/POST /api/ai/report/saved`：本人及可读取共享报表列表、创建私有报表；每人每公司最多 100 张。
- `GET/PATCH/PUT/DELETE /api/ai/report/saved/<id>`：读取；仅所有者可改名、覆盖或删除，非所有者管理统一 404。
- `POST /api/ai/report/saved/<id>/execute`：按接收者实时权限执行并写审计。
- `POST /api/ai/report/saved/<id>/share`：所有者设置 `private/company/selected`；指定分享提交 `user_ids/post_ids`。
- `GET /api/ai/report/saved/targets`：本公司有效在职成员和岗位，要求 `ai.share`。
- `POST /api/ai/report/query`：`mode=query_plan` 时走 Planner 和同一执行链路。
- `GET/POST /api/ai/report/schedules`：本人定时任务列表/创建。
- `GET/PATCH/PUT/DELETE /api/ai/report/schedules/<id>`：本人任务读取、启停、修改或删除。
- `GET /api/finance/cash-flow?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&group_by=day|month`：统一经营现金流汇总。

## 分享与权限

- `ai.full_data` 可覆盖数据集和字段权限，并允许显式包含已删除数据；管理员自动持有。
- `ai.export` 控制同步/异步导出；`ai.share` 控制新增或修改公司内分享。撤销分享权限后，所有者仍可将报表收回为私有。
- 公司共享不等于数据授权。共享列表逐条校验接收者实时数据集、字段、指标和软删除权限；无权限时不返回报表名称、计划或筛选值。
- 指定 ACL 目标必须是本公司有效在职成员或有效岗位；跨公司目标拒绝。接收者只能读取、执行和另存副本，不能覆盖、删除或修改分享。
- 公司范围由 `CompanyContextMixin` 和执行器共同强制；跨公司查询属于未来独立平台权限，公司管理员不会隐式获得。
- 薪酬、采购、应收等敏感数据集分别要求 `salary.view`、`warehouse.manage`、`finance.view` 或 `ai.full_data`。

## 异步导出部署

任务持久化在 `ai_report_aiexporttask`，不依赖裸线程或测试事务中的 `on_commit`。生产环境必须由服务管理器持续运行：

```bash
python manage.py process_ai_exports --poll 2
# 单次诊断/测试：python manage.py process_ai_exports --once
```

文件写入 `AI_EXPORT_ROOT`（默认 `backend/private_ai_exports`），不能由 nginx 或 `/media/` 直接暴露。默认 24 小时过期，可通过 `AI_EXPORT_RETENTION_HOURS` 配置。worker 使用事务认领，PostgreSQL 下使用 `SKIP LOCKED`；重启后未处理的 pending 任务仍保留。

## 定时报表部署

```bash
python manage.py process_ai_schedules --poll 2
# 单次诊断/测试：python manage.py process_ai_schedules --once
```

定时 worker 与导出 worker 是两个独立常驻进程，生产环境均须由服务管理器托管并连接同一数据库。支持 daily/weekly/monthly、本地时区和下次执行时间；月任务跳过不存在日期。每次执行重新检查所有者在职状态、`ai.query`、数据权限和计划结构；分享接收者逐人校验。通知只含报表或任务引用，不保存结果、筛选值或异常详情。失败也推进执行时间并持久化状态，避免轮询轰炸。

## 性能与 Planner 评测

- 同步、同步导出和异步 worker 统一经过 `execute_governed_plan`，记录 SQL 条数、SQL 时间和慢查询标记。
- `AI_REPORT_SLOW_MS` 默认 1000ms。慢日志只记录数据集和性能元数据，不记录 SQL、绑定参数、筛选值或业务结果。
- 当前不缓存业务结果，避免权限变化和经营数据更新后的陈旧结果；目录字段映射使用对象级缓存。索引调整留待真实 PostgreSQL `EXPLAIN` 后决定，不在 SQLite 上盲目添加。
- `planner_evaluation.py` 是固定离线评测集，覆盖多轮、歧义、坏 JSON、SQL 注入式计划、跨公司、目录外字段、越权和图表结构。测试全程 mock GLM，无外部调用。

## 测试

```bash
backend/.venv/Scripts/python.exe backend/manage.py makemigrations --check --dry-run
backend/.venv/Scripts/python.exe backend/manage.py test apps.ai_report --verbosity 2
npm --prefix frontend-web run build
npm --prefix frontend-web run test:e2e -- e2e/ai-report-designer.spec.js
```

浏览器测试需要隔离账号 `WEB_PHONE/WEB_PASSWORD` 和 `WEB_BASE_URL`；异步用例另需启动同隔离库的 worker 并设置 `WEB_AI_WORKER=1`。不得用真实业务账号或真实 GLM 代替隔离测试。

## 尚未开放

1. PostgreSQL 安装和真实执行验证按当前要求暂停；SQLite 通过不代表 PostgreSQL 执行计划或兼容性已验证。
2. 高级只读 SQL 继续关闭。只有 PostgreSQL 只读账号、强制 RLS、SQL AST、超时、行数/内存限制和独立资源隔离全部具备后才可另行评审，当前没有隐藏原始 SQL 端点。
3. 完整会计利润、无认证依据的完成率、无员工归属依据的未付工资和历史审计快照仍不可用，Planner 应返回 `plan=null`，不得猜测。
4. 跨公司平台级查询、邮件/Webhook 推送和银行/支付平台对账尚未开放；站内定时通知不等于已交付外部文件。
