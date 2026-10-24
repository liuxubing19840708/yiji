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
