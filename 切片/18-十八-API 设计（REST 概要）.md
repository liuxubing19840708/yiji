## 十八、API 设计（REST 概要）

```
POST /auth/login            帐号登录
POST /auth/sms-code         发送验证码
POST /auth/login-sms        验证码登录/注册
GET  /companies/mine        我的公司列表
POST /companies/join        扫码/编号申请加入
POST /companies/:id/switch  切换当前公司
GET  /dashboard             首页统计
GET/POST/PUT/DELETE /styles · /styles/:id/copy · /styles/:id/sync-process
POST /tickets/batch         编菲批量生成
GET  /productions · PUT /productions/:id/lock|cut-done · /split-mode
POST /scan/parse            解析菲票码
POST /records               扫菲登记（含 assist_user_id 协助）
DELETE /records/:id         撤销登记
GET  /salary/company        公司薪酬汇总
GET  /salary/employee/:uid  员工薪酬详情（tab= wage/count/fixed）
PUT  /salary/employee/:uid/config   固定工资/停职
POST /salary/adjust         扣除/奖励
POST /settlements           执行结算
GET  /settlements           结算记录 · POST /settlements/:id/revoke
POST /settle-applies        员工申请结算 · PUT /:id/review|pay|void
GET  /employees?tab=all|pending|resigned
POST /employees/:id/approve|resign
CRUD /posts · PUT /posts/:id/permissions
GET  /summary/completion    完成汇总（款式→工序→矩阵/员工）
GET  /audit-logs?type=&date=
GET/PUT /settings
GET  /notifications
```

---
