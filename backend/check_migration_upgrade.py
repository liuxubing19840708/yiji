# -*- coding: utf-8 -*-
"""在独立空库验证真实历史结构升级，不由普通 Django TestCase 执行。

由 run_postgres_regression.py 在完整回归后调用。数据库必须名为
yisao_migration_gate，避免误对开发库或生产库执行迁移。
"""
from datetime import date
from decimal import Decimal
import os

import django


DB_NAME = os.environ.get('DB_NAME', '')
if os.environ.get('DB_ENGINE') != 'postgres' or DB_NAME != 'yisao_migration_gate':
    raise RuntimeError(f'安全保护：迁移升级测试只允许独立 PostgreSQL 空库，实际 {DB_NAME!r}')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.apps import apps  # noqa: E402
from django.db import IntegrityError, connection, transaction  # noqa: E402
from django.db.migrations.executor import MigrationExecutor  # noqa: E402
from django.db.migrations.loader import MigrationLoader  # noqa: E402


# 键是当前叶节点，值是历史演练起点；工资 0003 依赖 0002，因此从 0001
# 升级时必须同时经过支付迁移，不能仍把 0002 当成独立叶节点。
REPLACEMENTS = {
    ('accounts', '0007_user_unionid_user_web_openid'):
        ('accounts', '0006_usercompany_commission_rate_usercompany_piece_rate_and_more'),
    ('salary', '0003_salary_payments'):
        ('salary', '0001_initial'),
    ('tech', '0003_material_safety_stock'):
        ('tech', '0002_material_price_unit'),
    ('production', '0004_remove_production_uq_production_company_bed_and_more'):
        ('production', '0003_production_source_order_no'),
    ('records', '0004_scanidempotency'):
        ('records', '0002_initial'),
    ('kanban_template', '0002_remove_kanbantemplate_uq_kanban_template_company_name_and_more'):
        ('kanban_template', '0001_initial'),
}


def main():
    loader = MigrationLoader(connection)
    latest = set(loader.graph.leaf_nodes())
    baseline = set(latest)
    for current, previous in REPLACEMENTS.items():
        if current not in baseline:
            raise AssertionError(f'迁移基线已变化，请重新评审此测试：{current}')
        baseline.remove(current)
        baseline.add(previous)
    # 被回退节点的所有下游叶节点也不能留在历史基线中，否则 Django 必须同时
    # 前进到依赖结构、又回退被依赖迁移，会直接拒绝混合方向计划。
    replaced = set(REPLACEMENTS)
    baseline = {
        node for node in baseline
        if not replaced.intersection(loader.graph.forwards_plan(node))
    }
    historical_nodes = set()
    expected_upgrade = set()
    for node in baseline:
        historical_nodes.update(loader.graph.forwards_plan(node))
    for node in latest:
        plan = set(loader.graph.forwards_plan(node))
        if replaced.intersection(plan):
            expected_upgrade.update(plan)
    # 历史基线已包含的共同祖先不应计入本次升级；中间迁移（如工资 0002、
    # 质检 0001）即使不是叶节点，也必须进入预期计划。
    expected_upgrade.difference_update(historical_nodes)

    print(f'[迁移] 建立历史结构：{len(baseline)} 个应用叶节点，回退 {len(REPLACEMENTS)} 个最新迁移')
    executor = MigrationExecutor(connection)
    executor.migrate(baseline)
    old_apps = executor.loader.project_state(list(baseline)).apps

    User = old_apps.get_model('accounts', 'User')
    Company = old_apps.get_model('accounts', 'Company')
    Style = old_apps.get_model('catalog', 'Style')
    Process = old_apps.get_model('catalog', 'Process')
    Ticket = old_apps.get_model('catalog', 'Ticket')
    Production = old_apps.get_model('production', 'Production')
    Settlement = old_apps.get_model('salary', 'Settlement')
    Record = old_apps.get_model('records', 'Record')
    Material = old_apps.get_model('tech', 'Material')
    Template = old_apps.get_model('kanban_template', 'KanbanTemplate')

    user = User.objects.create(
        username='migration-owner', phone='13900009999',
        real_name='迁移测试管理员', password='unused', is_active=True)
    company = Company.objects.create(
        name='迁移测试厂', company_no='999901', creator=user)
    assert company.pk != 86
    style = Style.objects.create(
        company=company, style_no='MIG-S1', name='历史测试款')
    process = Process.objects.create(
        company=company, name='历史缝制', default_price=Decimal('1.250'), sort=1)
    production = Production.objects.create(
        company=company, style=style, bed_no='MIG-B1',
        total_qty=120, plan_qty=100, source_order_no='MIG-ORDER-1')
    ticket = Ticket.objects.create(
        company=company, production=production, bed_no='MIG-B1', bundle_no='1',
        color='黑', size='M', qty=120, qr_code='999901|MIG-B1|MIG-S1|1|黑|M|120')
    settlement = Settlement.objects.create(
        company=company, user=user, created_by=user,
        period_start=date(2026, 10, 1), period_end=date(2026, 10, 31),
        employee_count=1, amount=Decimal('31.25'))
    record = Record.objects.create(
        company=company, ticket=ticket, process=process, user=user, operator=user,
        settlement=settlement, qty=25, price=Decimal('1.250'))
    material = Material.objects.create(
        company=company, code='MIG-FABRIC', name='历史面料', unit='m',
        category='fabric', price_reference=Decimal('12.500'))
    template = Template.objects.create(
        company=company, name='历史看板', filters={'color': '黑'}, version=3)

    print('[迁移] 旧数据已写入，升级到最新结构')
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(latest)
    planned = {(migration.app_label, migration.name) for migration, _backwards in plan}
    if planned != expected_upgrade:
        raise AssertionError(f'实际升级计划与预期不一致: {sorted(planned)}')
    executor.migrate(latest)

    # 使用当前模型读取，验证迁移后的真实 ORM 兼容性，而非只查 migration 记录。
    user_now = apps.get_model('accounts', 'User').objects.get(pk=user.pk)
    company_now = apps.get_model('accounts', 'Company').objects.get(pk=company.pk)
    production_now = apps.get_model('production', 'Production').objects.get(pk=production.pk)
    ticket_now = apps.get_model('catalog', 'Ticket').objects.get(pk=ticket.pk)
    settlement_now = apps.get_model('salary', 'Settlement').objects.get(pk=settlement.pk)
    record_now = apps.get_model('records', 'Record').objects.get(pk=record.pk)
    material_now = apps.get_model('tech', 'Material').objects.get(pk=material.pk)
    template_now = apps.get_model('kanban_template', 'KanbanTemplate').objects.get(pk=template.pk)

    assert user_now.web_openid is None and user_now.unionid is None
    assert company_now.name == '迁移测试厂' and company_now.company_no == '999901'
    assert production_now.bed_no == 'MIG-B1' and production_now.total_qty == 120
    assert production_now.plan_qty == 100 and production_now.order_id is None
    assert ticket_now.qr_code == '999901|MIG-B1|MIG-S1|1|黑|M|120'
    assert settlement_now.amount == Decimal('31.25') and settlement_now.wage_detail is None
    assert record_now.qty == 25 and record_now.price == Decimal('1.250')
    assert record_now.qty_defect == 0 and record_now.settlement_id == settlement.pk
    assert material_now.name == '历史面料' and material_now.safety_stock == Decimal('0')
    assert template_now.filters == {'color': '黑'} and template_now.version == 3

    # 直接打数据库约束：软删后允许重建，新的有效行之间仍保持唯一。
    ProductionNow = apps.get_model('production', 'Production')
    TemplateNow = apps.get_model('kanban_template', 'KanbanTemplate')
    RecordNow = apps.get_model('records', 'Record')
    production_now.is_deleted = True
    production_now.save(update_fields=['is_deleted'])
    production_rebuilt = ProductionNow.objects.create(
        company=company_now, style_id=production_now.style_id,
        bed_no='MIG-B1', total_qty=120, plan_qty=100)
    template_now.is_deleted = True
    template_now.save(update_fields=['is_deleted'])
    template_rebuilt = TemplateNow.objects.create(
        company=company_now, name='历史看板', filters={'color': '蓝'}, version=1)
    record_now.is_deleted = True
    record_now.save(update_fields=['is_deleted'])
    record_rebuilt = RecordNow.objects.create(
        company=company_now, ticket_id=record_now.ticket_id,
        process_id=record_now.process_id, user_id=record_now.user_id,
        operator_id=record_now.operator_id, settlement_id=settlement.pk,
        qty=30, price=Decimal('1.250'), qty_defect=2)

    with transaction.atomic():
        try:
            ProductionNow.objects.create(
                company=company_now, style_id=production_now.style_id,
                bed_no='MIG-B1', total_qty=1)
        except IntegrityError:
            pass
        else:
            raise AssertionError('有效床次重复未被 PostgreSQL 唯一约束拦截')
    with transaction.atomic():
        try:
            TemplateNow.objects.create(company=company_now, name='历史看板')
        except IntegrityError:
            pass
        else:
            raise AssertionError('有效看板模板重名未被 PostgreSQL 唯一约束拦截')
    with transaction.atomic():
        try:
            RecordNow.objects.create(
                company=company_now, ticket_id=record_now.ticket_id,
                process_id=record_now.process_id, user_id=record_now.user_id,
                qty=1, price=Decimal('1.250'))
        except IntegrityError:
            pass
        else:
            raise AssertionError('有效重复报工未被 PostgreSQL 唯一约束拦截')

    assert record_rebuilt.qty_defect == 2
    assert production_rebuilt.order_id is None
    assert template_rebuilt.filters == {'color': '蓝'}

    remaining = MigrationExecutor(connection).loader.graph.leaf_nodes()
    applied = MigrationLoader(connection).applied_migrations
    unapplied = [node for node in remaining if node not in applied]
    assert not unapplied, f'仍有未执行迁移: {unapplied}'
    print(f'[迁移] PASS：{len(REPLACEMENTS)} 个历史升级点、7 类旧业务数据、最新 ORM 读取均正确；'
          '3 组软删除条件唯一约束已验证')


if __name__ == '__main__':
    main()
