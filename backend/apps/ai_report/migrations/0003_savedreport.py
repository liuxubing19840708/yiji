# -*- coding: utf-8 -*-
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ai_report', '0002_aiqueryexecution'),
    ]

    operations = [
        migrations.CreateModel(
            name='SavedReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=80, verbose_name='报表名称')),
                ('dataset', models.CharField(max_length=64, verbose_name='数据集')),
                ('plan', models.JSONField(default=dict, verbose_name='规范化查询计划')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='保存时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_saved_reports', to='accounts.company', verbose_name='公司')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_saved_reports', to=settings.AUTH_USER_MODEL, verbose_name='保存人')),
            ],
            options={
                'verbose_name': 'AI私有保存报表',
                'verbose_name_plural': 'AI私有保存报表',
                'ordering': ['-updated_at', '-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='savedreport',
            constraint=models.UniqueConstraint(fields=('company', 'user', 'name'), name='uniq_ai_saved_owner_name'),
        ),
        migrations.AddIndex(
            model_name='savedreport',
            index=models.Index(fields=['company', 'user', 'updated_at'], name='idx_ai_saved_owner_time'),
        ),
    ]
