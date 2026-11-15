"""P1 成本域：OrderItem 加 price DECIMAL(10,3) 可空列（P1 唯一订单结构改动）。

依据 docs/服装ERP全模块设计方案.md §3.4 + P1 设计决策表 D1-D5（2026-10-24 批准）。
只加可空列，存量行 NULL=未定价，零破坏（迁移纪律 §六.5）。
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0002_order_style_images'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='price',
            field=models.DecimalField(blank=True, help_text='理论收入=Σqty×price，与实际收款差异属预期',
                                      max_digits=10, decimal_places=3, null=True,
                                      verbose_name='色码单价'),
        ),
    ]
