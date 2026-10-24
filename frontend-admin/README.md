# 看板模板配置端（frontend-admin）

Vue 3 轻前端：客户在此导入 Excel 表头 → 拖拽排序 + 绑定系统字段/公式 → 试跑预览 → 保存为看板模板，小程序端按模板渲染生产看板。

## 构建 / 部署

```bash
# Node 用便携版（D:\tools\nodejs），镜像用 npmmirror
set PATH=D:\tools\nodejs;%PATH%
npm config set registry https://registry.npmmirror.com
npm install
npm run build
```

构建产物输出到 `../backend/static/kanban_admin/`，由 Django 同源托管：

- 页面：`http://<host>:8000/api/kanban-admin`
- 资源：`/api/kanban-admin/assets/*`

## 访问 / 使用

1. 先用管理员账号调 `POST /api/auth/login`（phone+password）拿 `access` token（与小程序同一账号体系，需是该公司管理员岗位）。
2. 打开 `/api/kanban-admin`，顶部粘贴 Token、填公司 ID，点「连接」（token/公司 ID 会存 localStorage）。
3. 「刷新模板列表」→ 右侧编辑器：
   - ① 上传客户 `.xlsx`（自动解析表头行，支持两级/合并表头；解析只读不落库）；
   - ② 拖 `⠿` 调列序，逐列选绑定：系统字段（下拉）/ 公式（四则、比较、`IF(条件,a,b)`、`and/or`、三元 `a if c else b`、`round/sum/min/max/abs`）/ 静态文本；可设列宽、对齐、合并方式（不合并 / 随款块合并 / 同值合并）；
   - ③ 「试跑（本月）」实时看效果（调 `POST /api/kanban/preview`，不落库）；
   - ④ 填模板名、勾「设为默认模板」→ 保存。
4. 小程序看板自动按该公司 `is_default` 模板渲染（无模板或渲染失败由后端回落内置默认十列，不会白屏）。

## 本地开发

```bash
npm run dev   # http://localhost:5180，/api 代理到 127.0.0.1:8000
```

## 技术栈

Vue 3 + Vite + vue-draggable-plus（MIT，拖拽排序）。运行时零 UI 框架依赖（手写样式，产物 gzip 约 46KB）。
