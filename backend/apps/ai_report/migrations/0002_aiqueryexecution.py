# -*- coding: utf-8 -*-
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ai_report', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AiQueryExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(choices=[('preview', '预览'), ('export', '导出')], max_length=16, verbose_name='执行方式')),
                ('dataset', models.CharField(blank=True, max_length=64, verbose_name='数据集')),
                ('plan', models.JSONField(default=dict, verbose_name='规范化查询计划')),
                ('status', models.CharField(choices=[('success', '成功'), ('invalid', '计划无效'), ('denied', '权限拒绝'), ('error', '执行错误')], max_length=16, verbose_name='执行状态')),
                ('row_count', models.PositiveIntegerField(default=0, verbose_name='返回行数')),
                ('total_count', models.PositiveIntegerField(default=0, verbose_name='结果总行数')),
                ('truncated', models.BooleanField(default=False, verbose_name='是否截断')),
                ('duration_ms', models.PositiveIntegerField(default=0, verbose_name='执行耗时毫秒')),
                ('error_code', models.CharField(blank=True, max_length=64, verbose_name='错误代码')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='执行时间')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_query_executions', to='accounts.company', verbose_name='公司')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ai_query_executions', to=settings.AUTH_USER_MODEL, verbose_name='执行人')),
            ],
            options={
                'verbose_name': 'AI动态报表执行记录',
                'verbose_name_plural': 'AI动态报表执行记录',
                'ordering': ['-id'],
                'indexes': [models.Index(fields=['company', 'user', 'created_at'], name='idx_ai_exec_co_user_time')],
            },
        ),
    ]
