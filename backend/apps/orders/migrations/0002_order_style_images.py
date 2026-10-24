# 订单增强（2026-10-21 用户 8 项需求 #3，DDL 已批准）：
#   style 关联款式（存量兼容 null）、images 备注图片、process_items 订单关联工序。
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0006_process_template_style_craft'),
        ('orders', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='style',
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='orders', to='catalog.style',
                                    verbose_name='款式'),
        ),
        migrations.AddField(
            model_name='order',
            name='images',
            field=models.JSONField(blank=True, default=list, verbose_name='备注图片'),
        ),
        migrations.AddField(
            model_name='order',
            name='process_items',
            field=models.JSONField(blank=True, default=list,
                                   help_text='[{process_id, price}]',
                                   verbose_name='订单工序'),
        ),
    ]
