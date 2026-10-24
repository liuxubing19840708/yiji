# 工序模板 + 款式工艺说明/图片/视频（2026-10-21 用户 8 项需求 #1/#2，DDL 已批准）
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_remove_usercompany_uniq_user_company_and_more'),
        ('catalog', '0005_remove_customer_uq_customer_company_no_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProcessTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True,
                                                    verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('is_deleted', models.BooleanField(db_index=True, default=False,
                                                   verbose_name='是否已删除')),
                ('name', models.CharField(max_length=50, verbose_name='模板名称')),
                ('items', models.JSONField(blank=True, default=list,
                                           help_text='[{process_id, price}]',
                                           verbose_name='工序配置')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='process_templates',
                                              to='accounts.company',
                                              verbose_name='公司')),
            ],
            options={
                'verbose_name': '工序模板',
                'verbose_name_plural': '工序模板',
                'db_table': 'process_template',
                'ordering': ['-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='processtemplate',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_deleted', False)),
                fields=('company', 'name'),
                name='uq_process_template_company_name'),
        ),
        migrations.AddField(
            model_name='style',
            name='craft_note',
            field=models.TextField(blank=True, default='', verbose_name='工艺说明'),
        ),
        migrations.AddField(
            model_name='style',
            name='craft_images',
            field=models.JSONField(blank=True, default=list, verbose_name='工艺图片'),
        ),
        migrations.AddField(
            model_name='style',
            name='video',
            field=models.CharField(blank=True, default='', max_length=255,
                                   verbose_name='工艺视频地址'),
        ),
    ]
