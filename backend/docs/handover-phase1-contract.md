# 通用责任交接与裁片适配契约

范围：通用交接、裁片数量流水适配与小程序交接界面。旧 `CuttingHandover` API 和历史继续保留。
本期不开发外发、成品入库、工资、客户签收，不自动启用，不修改或补造历史事实。

## 核心口径

- 一次批量发起可生成多张独立 Handover；每张只有一个责任接收单位。
- `batch_no` 用于汇总，不建立有独立业务状态的批次主单。
- 一张菲票在一次请求中只能分配给一个接收单；不得按名称猜测接收班组。
- 未指定 receiver 时，target_group_no 内具有 handover.receive 权限的在职成员可接收；记录实际 receiver。
- 指定 receiver 时，本人可接收；handover.manage 可改派，不得无痕替换实际接收人。
- 普通状态：pending / received / rejected / cancelled。数量差异独立记录，不把差异等同于状态。
- 已接收后禁止 PATCH/DELETE 原事实；更正/冲销属于后续接入范围，不得提供伪删除接口。
- 裁片建单按来源节点可转量预占；旧 `CuttingHandover` 与新 `Handover` 的 pending 数量共同防超分。
- 接收按每个 item 的实际 `received_quantity` 原子写 transferred/received 双方流水，并保留明细到双方流水的关联；短少留来源，实收 0 不转账，拒收不写流水。
- 改派只改变 pending 单的责任接收人，不释放预占；拒收才释放该单预占。
- 公司隔离、服务端时间、实际操作账号、指定和实际责任人必须保留。
- 现有公司策略默认 off；warn 不阻断，strict 阻断绕过交接的普通裁片转出。通用接收和旧交接签收属于已确认交接出口。
- `timeout_minutes` 由服务端计算待接收超时；warn/strict 通过 `notify_handover_timeouts` 管理命令生成站内通知，off 不通知。
- 通知按“交接单＋接收人＋本地自然日”去重，调度事务内锁单并复查状态；改派后的新接收人可收到自己的通知。指定人或目标组无有效接收权限时通知管理岗。

## 权限

- handover.view：查看本公司授权范围内交接。
- handover.create：发起交接。
- handover.receive：接收本人被指定或本人目标组可接收的交接。
- handover.manage：策略维护、改派及异常管理。
- 管理员 '*' 按现有权限体系生效；所有查询仍必须限定 company。

## API

所有路径以 `/api` 为前缀，使用现有 Bearer + X-Company-Id 上下文。

### GET/POST `/handovers/policies`
### PUT `/handovers/policies/{id}`

策略字段：

```json
{
  "id": 1,
  "kind": "cutting",
  "mode": "off",
  "source_node": null,
  "target_node": null,
  "target_group_no": null,
  "receiver": null,
  "timeout_minutes": 120,
  "updated_by_name": ""
}
```

POST 创建，PUT 全量更新。kind 当前仅允许 cutting。mode=off/warn/strict。
支持公司默认、仅来源、仅目标、来源+目标四类规则；节点越具体优先级越高。没有默认规则时 GET 额外返回未持久化的 `id=null / mode=off` 默认策略。

### GET/POST `/handovers/`

GET 参数：`box=mine|created`、`status`、`batch_no`、`kind`。
返回分页结构沿用现有 DefaultPagination；每条使用下方 Handover 字段。

POST 请求：

```json
{
  "kind": "cutting",
  "request_id": "client-unique-id",
  "source_node": 1,
  "target_node": 2,
  "remark": "",
  "targets": [
    {
      "receiver": 10,
      "target_group_no": 1,
      "items": [{"ticket": 100, "quantity": 50}]
    },
    {
      "receiver": null,
      "target_group_no": 2,
      "items": [{"ticket": 101, "quantity": 60}]
    }
  ]
}
```

- targets 至少一项；items 至少一项；quantity 为正整数。
- receiver 与 target_group_no 至少提供一项，也可同时提供。
- 同一 ticket 不得出现在多个 target。
- 同一 request_id + 相同完整请求返回原批次；不同载荷报幂等冲突。
- 返回 `{"batch_no":"...","items":[Handover,...]}`。

Handover 字段：

```json
{
  "id": 1,
  "handover_no": "H...",
  "batch_no": "B...",
  "kind": "cutting",
  "status": "pending",
  "source_node": 1,
  "source_node_name": "裁剪",
  "target_node": 2,
  "target_node_name": "缝制",
  "target_group_no": 1,
  "receiver": 10,
  "receiver_name": "接收人",
  "created_by": 8,
  "created_by_name": "交出人",
  "received_by": null,
  "received_by_name": null,
  "received_at": null,
  "reject_reason": "",
  "has_discrepancy": false,
  "total_quantity": 50,
  "received_quantity": 0,
  "policy_mode": "warn",
  "timeout_minutes": 120,
  "due_at": "服务端计算的接收时限",
  "is_overdue": false,
  "overdue_minutes": 0,
  "remark": "",
  "created_at": "服务端时间",
  "items": [
    {
      "id": 1,
      "ticket": 100,
      "ticket_label": "床次/扎号/色码",
      "quantity": 50,
      "received_quantity": 0,
      "difference_type": "",
      "reason": ""
    }
  ],
  "events": []
}
```

### GET `/handovers/{id}/`

返回单张 Handover，events 按时间正序；事件字段至少：

```json
{
  "id": 1,
  "action": "created|received|rejected|reassigned|cancelled",
  "actor": 1,
  "actor_name": "操作账号",
  "actual_user": null,
  "actual_user_name": null,
  "reason": "",
  "before": {},
  "after": {},
  "created_at": "服务端时间"
}
```

### POST `/handovers/{id}/receive`

无差异请求：

```json
{"request_id":"receive-client-id"}
```

有差异时只传有差异的 item，其余默认全收：

```json
{
  "request_id":"receive-client-id",
  "reason":"整批差异说明，可选",
  "items":[
    {"id":1,"received_quantity":45,"difference_type":"shortage","reason":"短少5件"}
  ]
}
```

- received_quantity 可为 0；shortage 时短少部分留来源，overage 时可大于 quantity，但不得超过来源真实可用量；有差异必须 reason。
- difference_type=shortage/overage/damage/wrong_item/quality/other。
- 接收成功状态为 received；received_by 为实际接收人；重复同载荷返回原事实，不重复追加事件。
- 返回 Handover。

### POST `/handovers/{id}/reject`

`{"request_id":"...","reason":"必填"}`。仅 pending 可拒收，返回 Handover。

### POST `/handovers/{id}/reassign`

`{"request_id":"...","receiver":12,"reason":"必填"}`。仅 manage、仅 pending；新接收人必须是本公司在职且未停职成员。保留改派前后事件。

### GET `/handovers/batches/{batch_no}/`

返回 `{"batch_no":"...","items":[Handover,...],"summary":{"handover_count":3,"received_count":1,"pending_count":1,"rejected_count":1,"total_quantity":0,"received_quantity":0}}`。
summary 全部由交接单实时汇总，不维护第二份状态。

## 超时通知调度

```bash
python manage.py notify_handover_timeouts --noinput
```

建议生产计划任务每 5~15 分钟执行一次。命令只创建通知，不改变交接状态、不自动接收、不释放预占。
本次仅实现和验证命令，不代表已在生产环境安装或常驻运行计划任务。

## 文件所有权

- Agent A：`backend/apps/handover/**`，不修改 settings.py、config/urls.py、cutting 或其它既有业务文件。
- Agent B：`frontend/services/handover.ts`、`frontend/packages/admin/handovers/**`，不修改 app.json、订单/裁床既有页面。
- Agent C：`backend/test_handover_contract.py`，不修改业务实现。
- 主线程：settings/urls/app.json 注册、第二批 cutting 适配、共享文件、集成和验证。

## 测试与限制

- 测试使用 Django 测试数据库，不触碰开发业务库。
- 本机 PostgreSQL 17.5 使用既有 `D:\tools\pgsql` 和既有数据目录，未重新初始化或清空；自动启动的 Windows 服务为 `postgresql-17-saofei`，仅监听本机 5432。
- 本机认证已启用 SCRAM-SHA-256；测试角色 `saofei_handover` 为非超级用户，只允许登录和建测试库。密码保存在项目外的受限目录，不进入代码、文档或日志。
- 隔离库 `saofei_handover_dev` 不承载现有业务数据。Django 测试会自行创建并销毁测试库；不得把测试通过描述为既有业务库已迁移。
- PostgreSQL 验证包含新旧交接、制造、契约、真实 HTTP、超时通知及两个线程争抢同一菲票的真实并发预占。2026-11-15 共 77 项测试通过。
- 带可空外键 `select_related` 的锁定查询使用 `select_for_update(of=('self',))`，避免 PostgreSQL 拒绝锁定外连接的可空侧。
- 不提交、不推送。
- Agent A 若因 app 尚未注册不能运行测试，先完成代码和 `manage.py makemigrations handover` 所需文件，由主线程注册后统一执行。
- Agent B 不以静态类型检查冒充真机验收。
- Agent C 的测试若先于实现失败属于预期，不通过放宽断言迁就实现。
