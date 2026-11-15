# 衣计（扫菲）小程序前端

微信小程序（原生 + TypeScript）。后端见 `../backend/README.md`。

## 快速开始

```bash
npm install            # 开发工具链（typescript / miniprogram-api-typings）
npm run lint           # tsc --noEmit 类型检查
```

用微信开发者工具「导入项目」指向本目录（`project.config.json` 已就绪），
详情设置勾选「不校验合法域名」（本地联调 `http://127.0.0.1:8000`）。

后端启动：`cd ../backend && .venv/Scripts/python manage.py runserver`

## 结构

```
frontend/
├── app.{json,ts,wxss}      # 全局壳：设计令牌、globalData、分包
├── custom-tab-bar/         # 自定义 tabBar + 中央 AI问报 FAB
├── components/             # 通用组件（冻结，只读）
│   ├── date-range-bar/     # 日期区间条（今日/本月/上月 互斥快捷）
│   ├── stat-carousel/      # 统计卡轮播 + 日历选择页
│   ├── module-grid/        # 首页模块宫格（配置驱动 + 权限过滤）
│   ├── numeric-keypad/     # 数字软键盘（件数/金额）
│   ├── chips-editor/       # 颜色/尺码 chips
│   └── empty-state/        # 空态
├── services/               # API 契约：types.ts + 各域请求函数（冻结）
├── utils/                  # request（JWT/公司头/401/离线队列）、auth、format
├── pages/                  # 5 个 tab 页 + 登录页（骨架）
├── packages/salary/        # 薪酬分包（Wave 2 填充）
├── packages/admin/         # 管理分包（Wave 1/2 填充）
└── docs/CONVENTIONS.md     # 模块 agent 开发规约（必读）
```

## 开发阶段

- **Wave 0（已完成）**：骨架 + 请求层 + 组件库 + API 契约。
- **Wave 1（已完成）**：登录/公司/员工/岗位 · 客户/款式/生产管理 · 扫菲动线/工序/工作中 · 设置/日志/通知/我的（16 页 + services 契约全量对齐后端）。
- **Wave 2（已完成）**：编菲矩阵 · 薪酬全套 · 蓝牙/云打印。
- **Web 能力对齐（本次）**：资金管理（采购应付、供应商付款、费用/其他收入、经营现金流）、工资实付与多结算分摊，以及 AI 统一现金流数据集和定时报表运行中心。

资金与实付页面只调用既有受权限保护的接口：支付和收支作废必须填写原因，原始流水保留；现金流永久排除作废或撤销流水，不代表利润表。多结算分摊页面根据已认证结算快照和有效支付计算可选余额，提交时仍以后端行锁校验为准。

演示数据：`backend` 下 `python manage.py seed_demo`（账号 13800000001 / yisao1234）。

进度与分工见 `docs/CONVENTIONS.md` 与根目录计划文档。
