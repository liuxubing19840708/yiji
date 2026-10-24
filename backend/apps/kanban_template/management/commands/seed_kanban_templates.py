# -*- coding: utf-8 -*-
"""为没有看板模板的公司灌入「经典十列」默认模板（幂等：已有模板的公司跳过）。

用法：python manage.py seed_kanban_templates
用户拍板第 8 条：旧写死看板无感切换——现有 10 列成为每公司默认模板。
"""
from django.core.management.base import BaseCommand

from apps.kanban_template.models import KanbanColumn, KanbanTemplate
from apps.kanban_template.services import builtin_default_columns


class Command(BaseCommand):
    help = '为无模板公司创建默认看板模板（经典十列，幂等）'

    def handle(self, *args, **options):
        from apps.accounts.models import Company
        created, skipped = 0, 0
        for company in Company.objects.all():
            if KanbanTemplate.objects.filter(company=company).exists():
                skipped += 1
                continue
            # 同业务键软删行先物理清理（防撞唯一约束）
            KanbanTemplate.all_objects.filter(company=company, is_deleted=True).delete()
            t = KanbanTemplate.objects.create(
                company=company, name='默认看板',
                dataset=KanbanTemplate.Dataset.PLAN_ROWS, is_default=True, version=1)
            KanbanColumn.objects.bulk_create(
                KanbanColumn(template=t,
                             **{k: v for k, v in c.items() if k != 'id'})
                for c in builtin_default_columns())
            created += 1
            self.stdout.write(f'created template for company {company.pk} ({company.name})')
        self.stdout.write(self.style.SUCCESS(
            f'done: created={created}, skipped(已有模板)={skipped}'))
