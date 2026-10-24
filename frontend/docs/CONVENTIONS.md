# 前端开发规约（Wave 1/2 Subagent 必读）

> Wave 0 已交付：工程骨架、请求层、API 契约、custom tabBar+FAB、6 个通用组件。
> 本文件是各模块 agent 的「合同」：只许遵守，违反 = 合并时被打回。

## 1. 文件所有权（硬边界）

每个 agent 只允许 **新建** 自己模块目录下的文件，以及 **修改** 白名单内文件。
以下文件为共享资源，**禁止修改**（发现缺陷上报主会话统一修）：

- `app.json` / `app.ts` / `app.wxss`
- `utils/*`、`services/*`（API 契约冻结）
- `custom-tab-bar/*`
- `components/*`（可复用、不可改）
- `pages/*`（除被分配给你的页面）
- `project.config.json` / `tsconfig.json` / `package.json`

页面注册：新页面写好后，把路径清单交给主会话统一登记进 `app.json`。

## 2. 目录与命名

- tab 页在 `pages/<模块>/index.*`；业务页放对应分包：
  - `packages/salary/`（薪酬全套）
  - `packages/admin/`（款式/生产/编菲/员工/客户/日志/设置等）
- 一个页面一个目录 `index.{ts,wxml,wxss,json}`；页面 json 的 `usingComponents`
  按需引用，路径用绝对形式 `/components/xxx/index`。
- 组件内事件名用小写动词：`bind:change` / `bind:confirm` / `bind:submit`。

## 3. 网络与数据

- **一律走 `services/*`**，禁止页面里直接 `wx.request`；services 缺接口时上报主会话补。
- 金额字段后端为字符串（防浮点误差），**展示用 `utils/format.ts#money()`，页面内不得做金额加减运算**；汇总数值一律以后端返回为准。
- 分页响应 `Paginated<T>`（count/next/previous/results），触底加载下一页。
- 日期参数与后端一致：`date_from` / `date_to` / `quick`（today|yesterday|month）。
- 登记类提交（POST /records）传 `{ offline: true }`，弱网自动入离线队列。

## 4. 权限与公司上下文

- `X-Company-Id` 由请求层自动携带，页面**不要**手动拼 company_id 参数。
- 页面 `onShow` 第一行：`if (!requireLogin()) return;`
- 管理员判定用 `getApp<IAppOption>().globalData.isAdmin`；模块显示按权限过滤
  （宫格模块的 `visible` 字段），**服务端仍是最终校验方，前端只控制显示**（规格 §15）。
- 13 项系统设置开关（隐藏单价等）读 `services/ops.ts#fetchSettings()`，
  展示层遵守：隐藏扫菲单价/隐藏款式总单价/隐藏员工工资。

## 5. 视觉规范

- 颜色/圆角/阴影一律用 `app.wxss` 的 CSS 变量（`var(--color-primary)` 等），
  **禁止写死色值**。
- 卡片用 `.card`，主按钮 `.btn .btn-primary`（橙），保存 `.btn-info`（蓝），
  状态章 `.badge / .badge-green / .badge-red / .badge-gray`。
- 页面根节点 `<view class="page">`（自带底部留白，避让 tabBar+FAB）。
- 日期条用 `<date-range-bar>`（bind:change 拿 `{start, end, quick}`）。
- 数字录入用 `<numeric-keypad>`（件数 `decimal="{{false}}"`，金额 `decimal` 开）。

## 6. 类型

- 业务类型**只从 `services/types.ts` import**，缺字段上报主会话补，禁止页面里
  自建同名 interface。
- `IAppOption`（typings/index.d.ts）用于 `getApp<IAppOption>()`。

## 7. 提交验收标准（主会话合并前逐条检查）

1. `npm run lint`（tsc --noEmit）零错误。
2. 页面在微信开发者工具中能打开、无 WXML 报错（主会话统一验证）。
3. 未触碰所有权白名单外的文件（`git diff --stat` 核对）。
4. 空态、加载态、失败态三态齐全（列表页必须有 `empty-state`）。
5. 金额展示走 `money()`，无本地金额运算。

## 8. 离线容错（仅登记链路）

- 弱网时登记请求自动入队（request.ts 处理），页面只需对 reject 做静默处理
  （toast 已由请求层统一发「已存入本地待同步」）。
- 需要展示待同步数量时用 `utils/offline-queue.ts#offlineCount()`。
