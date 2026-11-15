# -*- coding: utf-8 -*-
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('ai_report', '0007_aireportschedule_aischeduledrun_and_more')]

    operations = [
        migrations.AddField(
            model_name='aischeduledrun', name='kind',
            field=models.CharField(
                choices=[('natural', '自然调度'), ('manual', '手动执行'), ('retry', '失败重试')],
                default='natural', max_length=16, verbose_name='运行类型'),
        ),
        migrations.AddField(
            model_name='aischeduledrun', name='request_id',
            field=models.UUIDField(blank=True, null=True, verbose_name='请求幂等键'),
        ),
        migrations.AddField(
            model_name='aischeduledrun', name='retry_of',
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='retry_run', to='ai_report.aischeduledrun', verbose_name='重试来源'),
        ),
        migrations.AddIndex(
            model_name='aischeduledrun',
            index=models.Index(fields=['schedule', 'created_at'], name='idx_ai_sched_run_owner'),
        ),
        migrations.AddConstraint(
            model_name='aischeduledrun',
            constraint=models.UniqueConstraint(
                condition=models.Q(('request_id__isnull', False)),
                fields=('schedule', 'request_id'), name='uniq_ai_sched_run_request'),
        ),
    ]
