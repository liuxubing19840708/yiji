# P1 领料定额化 + 库存价链（docs/服装ERP全模块设计方案.md §3.2/§3.3，2026-10-24）
# 只加可空列/默认值列，不改既有列语义；存量行 NULL=不启用定额（零破坏）。
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0002_stockinitem_lot'),
        ('tech', '0001_initial'),
    ]

    operations = [
        # Lot：批次单价（库存价链 D4）
        migrations.AddField(
            model_name='lot',
            name='unit_cost',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='入库价折算或补录；缺价（NULL/0）不进材料成本', max_digits=10, null=True, verbose_name='批次单价'),
        ),
        # StockInItem：行级单价 + 行级采购行 FK（P0 仅单头级 FK）
        migrations.AddField(
            model_name='stockinitem',
            name='unit_price',
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True, verbose_name='入库单价'),
        ),
        migrations.AddField(
            model_name='stockinitem',
            name='purchase_item',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='stock_in_items', to='warehouse.purchaseitem', verbose_name='关联采购单行'),
        ),
        # MaterialIssue：退料载体
        migrations.AddField(
            model_name='materialissue',
            name='type',
            field=models.CharField(choices=[('issue', '领料'), ('return', '退料')], default='issue', max_length=8, verbose_name='单据类型'),
        ),
        # IssueItem：定额化五列
        migrations.AddField(
            model_name='issueitem',
            name='bom_item',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='issue_items', to='tech.bomitem', verbose_name='定额来源行'),
        ),
        migrations.AddField(
            model_name='issueitem',
            name='material',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='issue_items', to='tech.material', verbose_name='物料'),
        ),
        migrations.AddField(
            model_name='issueitem',
            name='qty_req',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='单位=Material 主单位；NULL=无定额', max_digits=12, null=True, verbose_name='定额数量'),
        ),
        migrations.AddField(
            model_name='issueitem',
            name='over',
            field=models.BooleanField(default=False, verbose_name='超额标记'),
        ),
        migrations.AddField(
            model_name='issueitem',
            name='over_reason',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='超额原因'),
        ),
    ]
