# 全业务流程端到端设计与测试门禁

## 1. 目标与边界

端到端测试不是逐接口返回 200，而是从空公司开始，以实际操作顺序驱动流程，并在每个跨模块边界核对业务事实：数量、状态、责任人、库存、计件工资、成本、应收应付和审计必须来自同一业务链。

测试只使用 Django 创建和销毁的测试数据库。禁止连接、预清理或修复现有业务库，禁止使用固定公司 ID、演示账号或真实 GLM/微信/短信服务。测试数据必须由套件自己创建，重复执行不依赖上次残留。

本期端到端门禁是后端真实 API 与独立数据库验证。小程序/浏览器编译、静态类型检查不冒充真机验收；第三方支付、短信、微信和真实 AI 服务用明确的外部故障替身测试，成功路径的实网验收另行记录。

## 2. 统一业务主线

```text
账号/建厂/岗位/员工
  → 客户/工序/款式/工艺/BOM
  → 报价/订单/产前准备
  → 采购/到货质检/入库/批次库存/领料
  → 床次/菲票/生产路线
  → 接收/报工/质检/返修/报废/交接/外发
  → 包装/成品入库/出货/应收核销
  → 计件工资/调整/结算/工资条/工资实付
  → 应付/付款/现金流水/成本利润
  → 看板/报表/通知/导出/审计
```

每条主线至少覆盖：正常完成、业务校验拒绝、重复请求、撤销或作废、跨公司访问、无权限操作。数量或金额事实一旦审核/结算，不允许用 PATCH/DELETE 伪造更正；必须走已有撤销、冲销或作废流程。

## 3. 场景矩阵

| 流程域 | 端到端场景与关键断言 | 主要测试入口 |
| --- | --- | --- |
| 认证与组织 | 登录、建厂、公司切换、员工邀请审核、岗位授权、离职/复职；匿名、非成员、末管理员保护 | `test_ops_accounts.py`、`test_e2e_full.py` |
| 基础档案与工艺 | 客户、工序、款式、工序价格快照、物料、BOM 损耗与激活、产前准备 | `test_prod_gap.py`、`test_tech.py`、`apps/preproduction` |
| 报价与订单 | 报价材料/人工计算、订单色码数量金额、改单受下游约束、订单审计 | `test_quotation.py`、`test_orders*.py`、`test_order_change_guard.py` |
| 采购与材料库存 | 采购→到货→质检→入库→批次；领料定额/超领；重复确认不重复扣账 | `test_warehouse_p1.py`、`test_warehouse_p2.py`、`apps/warehouse` |
| 床次与菲票 | 订单挂床、按色码拆扎、打印与追溯、扎级数量上限 | `test_prod_gap.py`、`test_bundle_progress.py`、`test_print_manage.py` |
| 生产与报工 | 路线实例化、首节点接收、报工、批量登记、撤销；不能超扎量、跳必过节点 | `apps/manufacturing`、`test_scan_enhance.py`、`test_release_regression.py` |
| 质检与异常 | 合格/返修/报废、检验确认幂等、返修回路、全路线在手数量约束 | `test_quality.py`、`apps/quality`、`apps/manufacturing` |
| 责任交接 | 一对多接收、短少/多出/拒收/改派、预占、超时通知、新旧入口共同防超分 | `apps/handover`、`test_handover_contract.py`、`test_handover_live_http.py` |
| 外发 | 外发单、发出/回收/差异与作废；供应商、款式、订单公司隔离 | `test_outsource.py`、`apps/subcontract`、`test_release_regression.py` |
| 包装与成品 | 包装审核、成品入库、箱/菲票数量约束、冲销后库存一致 | `apps/finished_goods`、`apps/manufacturing/tests/test_live_full_flow.py` |
| 出货与应收 | 已审核成品才能出货、不能超订单/可交量、出货审核生成应收、核销及作废保留事实 | `test_shipment.py`、`test_finance*.py`、`apps/shipment` |
| 工资 | 登记计价、员工工资、调整留痕、结算、工资条、实付与作废；不能重复支付或篡改流水 | `test_salary_edge.py`、`test_payslip.py`、`test_salary_payments.py`、`apps/salary` |
| 成本与财务 | 材料+人工+制费成本、收入/利润、应收应付、收付款、现金流水和对账不变量 | `test_cost.py`、`test_financial_invariants.py`、`apps/finance` |
| 看板与报表 | 报工/完成口径、订单/床次聚合、AI 数据集权限、异步导出和外部服务失败 | `test_kanban_suite.py`、`test_ai_report.py`、`apps/ai_report` |
| 全局安全 | 路由发现驱动全 API 匿名/非成员/无权限矩阵；对象级跨厂访问和导出隔离 | `test_tenant_permission_matrix.py`、`test_object_tenant_matrix.py`、`test_release_regression.py` |

共享数据对拍链 `test_e2e_consistency.py` 使用固定可手算数字贯通报价、订单、BOM、采购库存、生产登记、工资成本、出货应收和看板，避免各模块各自正确但边界口径不一致。`test_cross_domain_finance_e2e.py` 另以同一公司贯穿采购暂收/IQC/确认入库→应付→供应商付款，以及扫菲报工→工资结算→实付/作废/重新支付，并对拍统一经营现金流；其中基础生产档案是 ORM 夹具，不计作该套件已覆盖的端侧建档流程。

## 4. 横切门禁

### 幂等与并发

- 同载荷重试返回原事实，不重复增加库存、流水、通知或金额；不同载荷复用幂等号必须冲突。
- PostgreSQL 使用 `TransactionTestCase`、独立线程和独立连接，真实争抢同一资源。
- 至少覆盖：同菲票报工、材料领用重复确认、采购收货重复确认、订单并发出货、改单与出货、工资结算、应收核销，以及同一菲票的交接预占。
- SQLite 跳过行锁专项时必须如实显示，不得以 SQLite 结果替代 PostgreSQL 并发结论。

### 事务与异常恢复

- 一个明细失败时，按接口契约决定整批回滚或逐条返回；不得出现半张审核单、单边库存流水。
- 撤销/冲销/作废后核对净数量、状态和关联事实，原记录仍可追溯。
- 外部服务超时、拒绝和不可用不能留下伪造成功状态。

### 多租户与权限

- 路由发现驱动，新增公司 API 自动进入矩阵，而不是只维护一份容易漏项的手写路径表。
- 公司上下文、所有公司外键和导出均须限定当前公司；跨厂对象优先返回 404，避免泄露存在性。
- 后端始终是权限、金额、状态和数量的唯一事实源，前端隐藏按钮不能代替授权测试。

## 5. 分层执行

1. **明确目标全量回归**：以 `run_postgres_regression.py` 的 `TEST_TARGETS` 为唯一清单，覆盖 `apps` 下真实 TestCase、根目录真实 TestCase、跨域资金链和 PostgreSQL 并发专项。旧脚本式 `test_*.py` 只能由 `test_full_functional` 隔离执行，禁止裸 `manage.py test --noinput` 直接导入。
2. **共享数据全链对拍**：`test_full_functional.FullFunctionalTests.test_full_erp_chain`，失败时查看具体断言，不接受只打印 FAIL 的假成功。
3. **新生产真实 HTTP 全链**：`apps.manufacturing.tests.test_live_full_flow`，覆盖产前到出货、撤销、作废和安全边界。
4. **PostgreSQL 专项**：`test_postgres_concurrency` 与 `apps.handover.tests.test_postgres_concurrency`，验证真实锁与资源争抢。
5. **全 API 安全矩阵**：租户、权限、对象级矩阵和发布回归。
6. **迁移升级**：`check_migration_upgrade.py` 只能连接专用空库，验证历史结构带数据升级。
7. **端侧验收**：小程序/浏览器按关键角色执行同一主线，截图和结果另存；不得以 API 通过宣称真机通过。

## 6. 通过标准

- 所有已执行用例退出码为 0；失败、错误、非预期跳过都必须列出，不能用后续成功掩盖。
- 流程断言必须同时检查响应和持久化事实；只检查状态码或只检查日志不算端到端通过。
- 每个测试库在结束后销毁，隔离开发库不被全功能套件写入。
- 测试报告中分别列出 SQLite/PostgreSQL、API/真实 HTTP、自动化/人工端侧验收，禁止混称。

## 7. 2026-11-15 实际验证记录

- 本机 PostgreSQL 17.5、隔离角色和 Django 自动创建/销毁测试库：按 `TEST_TARGETS` 执行 **424 项，442.372 秒，OK，退出码 0**。该数字不包含被禁止直接导入的 28 个旧脚本所产生的伪测试/导入错误；这些脚本由 `test_full_functional` 在完整清单内隔离执行。
- 共享 ERP 主链、新制造真实 HTTP 全链、跨域采购/工资资金链、扫菲幂等与真实并发、微信网页 OAuth、全 API 权限矩阵、真实对象跨厂矩阵均包含在上述运行中。
- 独立空库 `yisao_migration_gate` 完成历史结构带数据升级，核对 6 个历史升级点、7 类旧业务数据和 3 组软删除条件唯一约束后通过；演练库已删除。`makemigrations --check --dry-run` 为 `No changes detected`。
- 真实 Web 生产全链在独立 LiveServer 中另执行通过，浏览器实际操作与后端业务事实一致。
- 本次没有执行微信真机、实体蓝牙打印、真实短信/微信支付或真实 GLM 成功路径验收，不得把上述后端自动化结果扩展解释为这些端侧/外部服务已经通过。
