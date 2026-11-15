"""权限判定：基于岗位的 permissions JSON 列表。

权限点体系（2026-10-18 Plan A 一期）：
  - 细粒度权限点 = '<module>.<action>'（如 records.revoke 撤销他人登记），
    按 PERMISSION_GROUPS 分组（前端岗位编辑弹窗按组渲染）。
  - 兼容旧 coarse key（styles/customers/production/working/my_salary/'*'）：
    旧模块 key 覆盖该模块下所有 legacy=True 的点（即历史上该 key 就能做的事），
    行为零回归；高危点（删除/撤销/结算等）不获 legacy 覆盖，须显式授权或 '*'。
  - is_admin 岗位与 '*' 恒有全部权限。
"""

# 旧 coarse key（仅向后兼容，不再新增消费方）
PERMISSION_KEYS = ('styles', 'customers', 'production', 'working', 'my_salary', '*')

PERMISSION_GROUPS = [
    {'module': 'styles', 'label': '款式与工序', 'points': [
        {'key': 'styles.manage', 'label': '款式/工序管理', 'legacy': True},
        {'key': 'styles.price', 'label': '工序上价/改价', 'legacy': True},
        {'key': 'styles.delete', 'label': '删除款式/工序', 'legacy': False},
    ]},
    {'module': 'customers', 'label': '客户管理', 'points': [
        {'key': 'customers.manage', 'label': '客户与分组管理', 'legacy': True},
    ]},
    {'module': 'records', 'label': '扫菲/登记', 'points': [
        # 旧 coarse 'production' 历史上即 governs 扫菲登记（records/views.py check_perm('production')）
        {'key': 'records.create', 'label': '扫菲登记', 'legacy': True, 'legacy_key': 'production'},
        {'key': 'records.revoke', 'label': '撤销他人登记', 'legacy': False},
        {'key': 'scan.assist', 'label': '协助扫菲', 'legacy': False},
    ]},
    {'module': 'production', 'label': '生产/床次', 'points': [
        {'key': 'production.manage', 'label': '编菲/床次管理', 'legacy': True},
        {'key': 'production.delete', 'label': '删除床次', 'legacy': False},
        {'key': 'production.lock', 'label': '锁定/解锁床次', 'legacy': False},
        # 订单管理（2026-10-20 P1）：legacy 覆盖旧 'production'（组 module 默认 legacy_key），
        # 旧裁床岗零回归；非高危（删单/取消均知情操作，不设独立高危点）
        {'key': 'orders.manage', 'label': '订单管理', 'legacy': True, 'legacy_key': 'production'},
    ]},
    {'module': 'working', 'label': '工作中', 'points': [
        {'key': 'working.view', 'label': '在制进度/本人登记', 'legacy': True},
    ]},
    {'module': 'my_salary', 'label': '我的薪酬', 'points': [
        {'key': 'my_salary.view', 'label': '本人薪酬查询', 'legacy': True},
    ]},
    {'module': 'salary', 'label': '薪酬管理', 'points': [
        {'key': 'salary.view', 'label': '薪酬查询（他人/汇总）', 'legacy': False},
        {'key': 'salary.config', 'label': '工资配置/调整', 'legacy': False},
        {'key': 'salary.settle', 'label': '工资结算', 'legacy': False},
        {'key': 'salary.settle_revoke', 'label': '撤销结算单', 'legacy': False},
        {'key': 'salary.review', 'label': '结算申请审核/发放', 'legacy': False},
    ]},
    {'module': 'employees', 'label': '员工管理', 'points': [
        {'key': 'employees.manage', 'label': '员工与岗位管理', 'legacy': False},
    ]},
    {'module': 'company', 'label': '公司', 'points': [
        {'key': 'company.manage', 'label': '公司信息/套餐', 'legacy': False},
        {'key': 'company.qr', 'label': '公司二维码', 'legacy': False},
    ]},
    {'module': 'print', 'label': '打印管理', 'points': [
        {'key': 'print.manage', 'label': '打印与标签计数', 'legacy': False},
    ]},
    {'module': 'trace', 'label': '质量追溯', 'points': [
        # 追溯 API 走 check_perm('production')——旧 'production' 覆盖
        {'key': 'trace.view', 'label': '追溯查询', 'legacy': True, 'legacy_key': 'production'},
    ]},
    {'module': 'warehouse', 'label': '轻量仓库', 'points': [
        # 仓库 API 全走 check_perm('production')——旧 'production' 覆盖
        {'key': 'warehouse.manage', 'label': '采购/入库/领料', 'legacy': True, 'legacy_key': 'production'},
        # P2 采购补全域（SA-P2-1，2026-10-24）：供应商主档独立管控，
        # legacy 默认零授权（决策表「P2 权限点」2 段式）；暂收/需求汇总挂 warehouse.manage
        {'key': 'warehouse.supplier', 'label': '供应商主档', 'legacy': False},
    ]},
    {'module': 'kanban', 'label': '生产看板', 'points': [
        # 看板数据接口当前全员可看（DashboardView 无权限门槛），view 点仅前端显隐用
        {'key': 'kanban.view', 'label': '看板查看', 'legacy': False},
        {'key': 'kanban.manage', 'label': '看板模板管理', 'legacy': False},
    ]},
    {'module': 'logs', 'label': '日志', 'points': [
        {'key': 'logs.view', 'label': '操作日志', 'legacy': False},
        {'key': 'logs.client', 'label': '客户端日志', 'legacy': False},
    ]},
    {'module': 'settings', 'label': '系统设置', 'points': [
        {'key': 'settings.manage', 'label': '系统设置', 'legacy': False},
    ]},
    {'module': 'tech', 'label': '技术管理', 'points': [
        # P1 tech 域（2026-10-24）：legacy 默认零授权（决策表「P1 权限点」），
        # 旧 coarse 岗位零回归；导入并入对应点，不设独立 import 点
        {'key': 'tech.material', 'label': '物料主档', 'legacy': False},
        {'key': 'tech.bom', 'label': '版式BOM', 'legacy': False},
    ]},
    {'module': 'cost', 'label': '订单成本', 'points': [
        # P1 cost 域（2026-10-24）：legacy 默认零授权；岗位映射建议——
        # 老板/厂长全开、跟单+cost.view；recompute/overhead/batch 并入 cost.manage
        {'key': 'cost.view', 'label': '成本查看', 'legacy': False},
        {'key': 'cost.manage', 'label': '成本重算/制费', 'legacy': False},
    ]},
    {'module': 'quotation', 'label': '报价管理', 'points': [
        # P2 报价域（2026-10-24 §4.8）：legacy 默认零授权；岗位映射建议——
        # 老板/厂长全开、跟单+quotation.view；建/改/删/流转并入 quotation.manage
        {'key': 'quotation.view', 'label': '报价查看', 'legacy': False},
        {'key': 'quotation.manage', 'label': '报价维护', 'legacy': False},
    ]},
    {'module': 'finance', 'label': '应收财务', 'points': [
        # P2 finance 应收域（2026-10-24 §4.3）：legacy 默认零授权；岗位映射建议——
        # 老板/财务全开、跟单+finance.view；建应收/核销/作废/移动加权报表并入
        # finance.manage（设计分工原文：view=GET 应收列表，manage=动作+报表）
        {'key': 'finance.view', 'label': '应收查看', 'legacy': False},
        {'key': 'finance.manage', 'label': '应收建立/核销/作废/报表', 'legacy': False},
    ]},
    {'module': 'shipment', 'label': '出货管理', 'points': [
        # P2 出货域（SA-P2-4，2026-10-24 §4.2）：legacy 默认零授权；岗位映射建议——
        # 老板/厂长/跟单全开、跟单查看岗+shipment.view；建单/改单/审核/作废/
        # 装箱行/打印上报并入 shipment.manage（打印上报 print.manage 亦可）
        {'key': 'shipment.view', 'label': '出货查看', 'legacy': False},
        {'key': 'shipment.manage', 'label': '出货建立/审核/作废', 'legacy': False},
    ]},
    {'module': 'quality', 'label': '质检管理', 'points': [
        # 竞品对齐 GAP-1（2026-11-04）：legacy 默认零授权；岗位映射建议——
        # 老板/厂长/质检员全开；view=质检列表/导出，manage=登记返修/收货/完成/删除
        {'key': 'quality.view', 'label': '质检查看', 'legacy': False},
        {'key': 'quality.manage', 'label': '质检登记/收货/完成', 'legacy': False},
    ]},
    {'module': 'preproduction', 'label': '产前管理', 'points': [
        # 查看、资料维护与最终放行分离；最终审批不能由普通资料维护权限代替。
        {'key': 'preproduction.view', 'label': '产前资料与审批查看', 'legacy': False},
        {'key': 'preproduction.manage', 'label': '工艺资料/产前样/工艺问题维护（工艺负责人）', 'legacy': False},
        {'key': 'preproduction.brief', 'label': '班组交底确认（生产负责人）', 'legacy': False},
        {'key': 'preproduction.approve', 'label': '产前大货放行审批（产前审批人）', 'legacy': False},
    ]},
    {'module': 'manufacturing', 'label': '生产节点执行', 'points': [
        {'key': 'manufacturing.view', 'label': '生产节点与数量流水查看', 'legacy': False},
        {'key': 'manufacturing.manage', 'label': '路线配置与节点数量登记', 'legacy': False},
        {'key': 'manufacturing.reverse', 'label': '生产数量反向冲销', 'legacy': False},
    ]},
    {'module': 'cutting', 'label': '裁片交接', 'points': [
        {'key': 'cutting.view', 'label': '裁片交接与签收查看', 'legacy': False},
        {'key': 'cutting.manage', 'label': '裁片下发/签收/取消', 'legacy': False},
    ]},
    {'module': 'handover', 'label': '责任交接', 'points': [
        {'key': 'handover.view', 'label': '交接记录查看', 'legacy': False},
        {'key': 'handover.create', 'label': '批量发起交接', 'legacy': False},
        {'key': 'handover.receive', 'label': '接收本人或本组交接', 'legacy': False},
        {'key': 'handover.manage', 'label': '交接配置/改派/异常管理', 'legacy': False},
    ]},
    {'module': 'finished_goods', 'label': '包装与成品库存', 'points': [
        {'key': 'finished_goods.view', 'label': '包装/入库/成品库存查看', 'legacy': False},
        {'key': 'finished_goods.manage', 'label': '包装与成品入库登记', 'legacy': False},
    ]},
    {'module': 'outsource', 'label': '外发管理', 'points': [
        # 竞品对齐 GAP-5（2026-11-04）：legacy 默认零授权；岗位映射建议——
        # 老板/厂长/跟单全开；view=外发单列表/导出，manage=建单/收回/结算/删除。
        # P3 §4.5 委外全量（发料三本账/按收回合格量计费/入应付）在此域上扩展
        {'key': 'outsource.view', 'label': '外发查看', 'legacy': False},
        {'key': 'outsource.manage', 'label': '外发建单/收回/结算', 'legacy': False},
    ]},
    {'module': 'ai', 'label': 'AI 问报', 'points': [
        {'key': 'ai.full_data', 'label': '全域业务数据查询', 'legacy': False},
        {'key': 'ai.export', 'label': 'AI 动态报表导出', 'legacy': False},
        {'key': 'ai.share', 'label': 'AI 报表公司内分享', 'legacy': False},
    ]},
]

# 全部合法 key（岗位 permissions 白名单用）：旧 coarse key + 全部细粒度点
ALL_PERMISSION_KEYS = tuple(PERMISSION_KEYS) + tuple(
    p['key'] for g in PERMISSION_GROUPS for p in g['points'])

# 旧模块 key → 被 legacy 覆盖的点集合（跨组：records.create 等由旧 'production' 覆盖）
_LEGACY_COVERED = {}
for _g in PERMISSION_GROUPS:
    for _p in _g['points']:
        if _p.get('legacy'):
            _LEGACY_COVERED.setdefault(_p.get('legacy_key', _g['module']), set()).add(_p['key'])


def effective_perms(post) -> list:
    """岗位实际持有的全部细粒度权限点（Plan A 二期：随 post 下发）。

    - is_admin 或持有 '*' → ['*']（前端视为全量）；
    - 否则 = 岗位 permissions 里自带的细粒度点
      ∪ 每个 legacy coarse key 按 _LEGACY_COVERED 展开出的点（去重，按字典顺序稳定输出）。
    前端拿到的即"能做什么"，无需再复制 legacy 展开逻辑。
    """
    if post is None:
        return []
    if post.is_admin:
        return ['*']
    perms = post.permissions or []
    if '*' in perms:
        return ['*']
    keys = set(ALL_PERMISSION_KEYS)
    got = set()
    for k in perms:
        if k in _LEGACY_COVERED:          # 旧 coarse key：展开为其 legacy 点集
            got |= _LEGACY_COVERED[k]
        elif k in keys:
            got.add(k)
    # 按 PERMISSION_GROUPS 声明顺序输出，保证响应稳定可快照
    ordered = [p['key'] for g in PERMISSION_GROUPS for p in g['points'] if p['key'] in got]
    return ordered


def has_perm(user_company, key: str) -> bool:
    """判断某公司成员是否拥有指定权限（key 可为细粒度点或旧 coarse key）。

    管理员岗位（is_admin）与 '*' 拥有全部权限；
    旧 coarse key 覆盖该模块全部 legacy 点（历史行为零回归）。
    """
    if user_company is None:
        return False
    post = user_company.post
    if post is None:
        return False
    if post.is_admin:
        return True
    perms = post.permissions or []
    if '*' in perms or key in perms:
        return True
    # 旧 coarse key 兼容：岗位持有的旧 key（如 'production'）覆盖其 legacy 点集
    # （含跨组：records.create/trace.view/warehouse.manage 均由旧 'production' 覆盖）
    for legacy_key, points in _LEGACY_COVERED.items():
        if legacy_key in perms and key in points:
            return True
    return False
