# 衣扫（扫菲）后端

服装厂计件工资微信小程序「衣扫」的 Django REST Framework 后端。

## 技术栈

- Python 3.12 + Django 5 + Django REST Framework
- 认证：SimpleJWT（微信 code2session 登录 + 手机号验证码登录）
- 数据库：MySQL（生产）/ SQLite（开发调试均可，见 settings）

## 目录结构

```
backend/
├── config/               # 项目配置（settings/urls/wsgi）
├── apps/
│   ├── core/             # 公共基座：BaseModel（软删除）、CompanyContextMixin、
│   │                     # 统一响应格式、分页、权限点、q2/q3 金额工具
│   ├── accounts/         # 账号：微信/验证码登录、公司、岗位、成员关系
│   ├── ops/              # 运营支撑：站内通知、操作日志、公司设置
│   ├── catalog/          # 档案：客户、工序、款式、菲票（编菲）
│   ├── production/       # 生产：床次订单（裁货）、订单工序与单价
│   ├── records/          # 扫菲：登记/撤销、工序选择器、工作台
│   └── salary/           # 薪酬结算：结算单、工资调整、结算申请流、完成情况汇总
└── manage.py
```

## 快速开始

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

登录后调用 `POST /api/auth/login`（微信 code）或验证码登录换取 JWT token；
后续请求携带 `Authorization: Bearer <token>` 和 `X-Company-Id: <公司ID>` 请求头。

### AI 异步导出与定时报表 worker

启用 Web 异步导出和定时报表后，除 Web 进程外还需用同一环境、同一数据库持续运行两个独立 worker：

```bash
python manage.py process_ai_exports --poll 2
python manage.py process_ai_schedules --poll 2
```

先执行 `python manage.py migrate`。生产环境应由 systemd/supervisor 等服务管理器托管并配置重启；不要依赖开发终端或裸线程。私有文件目录 `AI_EXPORT_ROOT` 不得通过 nginx 直接暴露。定时任务每次重新校验权限，通知不保存查询结果。详细接口、权限、过期清理和测试边界见 `apps/ai_report/QUERY_PLAN.md`。

定时报表运行中心提供 `GET /api/ai/report/schedules/runs`、`POST /api/ai/report/schedules/{id}/run` 和 `POST /api/ai/report/schedules/runs/{id}/retry`。后两者请求体必须包含 UUID `request_id`；同一任务的重复键返回首次运行而不重复执行或通知。启用、停用任务都可由所有者手动执行，失败的自然或手动运行可重试为新审计记录，成功运行不能重试。运行列表只含所有者自己的安全元数据，不返回计划、业务结果、行数、筛选值或异常文本。

## 接口约定

- 所有业务接口位于 `/api/` 前缀下，URL **不带尾斜杠**。
- 统一响应格式：业务字段直出，错误统一为 `{"message": "..."}` 或
  `{"<field>": ["错误信息"]}`（由 `apps.core.exceptions.api_exception_handler` 包装）。
- 分页：`?page=1&page_size=20`（上限 100），返回 `{count, next, previous, results}`。
- 金额口径：单价 3 位小数（`q3`），工资类金额 2 位小数（`q2`），四舍五入，
  响应中以字符串输出，避免浮点误差。
- 公司上下文：`CompanyContextMixin` 从 `X-Company-Id` 请求头（或 `company_id`
  参数）解析当前公司，并校验请求用户为在职成员；管理员操作需岗位为管理员。

## 模块速览

| 模块 | 主要接口 | 说明 |
|------|----------|------|
| accounts | `/api/auth/*`、`/api/company/*`、`/api/members/*` | 登录、公司增删改、成员审核/离职/复职、岗位 |
| catalog | `/api/customers/*`、`/api/processes/*`、`/api/styles/*`、`/api/tickets/*` | 客户/工序/款式档案、编菲（菲票批量生成与打印） |
| production | `/api/productions/*` | 床次订单（裁货）、订单工序单价覆盖、锁定/裁完 |
| records | `/api/records/*`、`/api/workbench` | 扫菲登记（唯一性约束防重复）、当日撤销、工序选择器 |
| ops | `/api/notifications/*`、`/api/audit-logs/*` | 站内通知、操作留痕 |
| salary | `/api/salary/*` | 见下 |

## 薪酬结算（salary）

- `GET /api/salary/company`（管理员）：公司工资汇总——已结算/未结算金额、
  无单价工序数、固定工资预发总额；支持 `date_from/date_to/quick`。
- `GET /api/salary/employee/<uid>`：员工工资明细（本人或管理员），
  按日/按床次/按工序三维汇总；`tab=fixed` 返回固定工资概览。
- `PUT /api/salary/employee/<uid>/config`（管理员）：设置固定工资及类型（按天/按月）。
- `POST /api/salary/adjust`（管理员）：工资调整（正加负减），写操作日志。
- `POST /api/salary/settlements`（管理员）：按期间把未结算登记锁定为结算单
  （金额快照），批量站内通知；`GET` 列表支持期间重叠过滤；
  `POST /api/salary/settlements/<pk>/revoke` 撤销（已发放期间禁止撤销）。
- 结算申请流：员工 `POST /api/salary/settle-applies` 发起（金额快照）→
  管理员 `PUT .../<pk>/review`（approve/reject）→ `PUT .../<pk>/pay` 发放
  （生成结算单）→ 员工可 `PUT .../<pk>/void` 作废本人待审核申请。
- `GET /api/salary/summary/completion`（管理员）：完成情况三级钻取
  款式 → 工序 → 菲票明细 + 颜色/尺码矩阵 + 员工汇总。
- `GET/POST /api/salary/payments`、`GET /api/salary/payments/<id>`：真实工资支付及员工/结算分摊；分摊合计须等于支付金额。
- `POST /api/salary/payments/<id>/void`：填写作废原因，保留支付事实并停止抵扣；不支持 PATCH/DELETE 支付流水。
- 原 `PUT /api/salary/settle-applies/<id>/pay` 保持兼容，同时生成可追溯支付及分摊。

## 应付与其他收支（finance）

- `GET/POST /api/finance/payables`、`GET /api/finance/payables/<id>`：财务确认应付、累计已付和未付余额。
- `POST /api/finance/payables/<id>/void`：无有效付款时才允许作废应付。
- `GET/POST /api/finance/supplier-payments`、`POST /api/finance/supplier-payments/<id>/void`：供应商付款、应付核销与作废回退，事务内防超付和外部流水号防重。
- `GET/POST /api/finance/cash-entries`、`POST /api/finance/cash-entries/<id>/void`：手工费用支出和其他收入，不重复记录工资、客户回款或供应商付款。
- `GET /api/finance/cash-flow`：按 `date_from/date_to/group_by=day|month` 汇总五类真实经营现金流，同时要求 `finance.manage` 和 `salary.view`；不是利润表。

## 业务规则要点

- 登记记录按（公司、菲票、工序、员工）唯一，重复登记直接拒绝。
- 已进入结算快照的登记禁止改动/撤销；撤销结算需期间内无已发放申请。
- 结算金额为快照（`Record.qty * Record.price` 四舍五入到分），之后改价不影响已结算数据。
- 敏感操作（改价、撤销、结算、发放、调整）均写入操作日志并站内通知相关员工。
- 所有业务模型软删除（`is_deleted`），查询默认过滤已删除数据。

## 全业务流程端到端测试

流程矩阵、跨模块数量/金额对拍、租户权限、并发和异常恢复设计见 `docs/e2e-all-process-design.md`。所有自动化测试必须使用 Django 隔离测试库，不得对开发库执行旧的硬编码公司清理脚本。

完整目标清单位于 `run_postgres_regression.py` 的 `TEST_TARGETS`：包括 `apps` 下真实 TestCase、根目录真实 TestCase、跨域资金链和 PostgreSQL 并发专项。旧的 `test_*.py` 脚本式套件只由 `test_full_functional` 在隔离事务中执行，不能用裸 `manage.py test --noinput` 直接导入。

```bash
python run_postgres_regression.py
```

该门禁使用一次性临时 PostgreSQL 实例，并另外执行独立空库的迁移升级检查。使用本机隔离测试角色时，可通过环境变量注入 `DB_ENGINE=postgres`、`DB_NAME`、`DB_USER`、`DB_PASSWORD` 后执行同一明确目标清单；密码不得写入项目、命令或日志。API/真实 HTTP、SQLite/PostgreSQL、自动化/真机验收结果必须分别报告。
