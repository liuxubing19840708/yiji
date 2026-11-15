from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
        ('orders', '0003_orderitem_price'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderCost',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True,
                                                    verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True,
                                                    verbose_name='更新时间')),
                ('is_deleted', models.BooleanField(db_index=True, default=False,
                                                   verbose_name='是否已删除')),
                ('material_cost', models.DecimalField(decimal_places=2, default=0,
                                                      max_digits=12,
                                                      verbose_name='材料成本')),
                ('labor_cost', models.DecimalField(
                    decimal_places=2, default=0, max_digits=12,
                    help_text='process=Σqty×price（全量含已结算）+ whole=完成菲票票面均摊额合计',
                    verbose_name='人工成本')),
                ('outsourcing_cost', models.DecimalField(
                    decimal_places=2, default=0, max_digits=12,
                    help_text='P1 恒 0，P3 委外接入后自动纳入', verbose_name='委外成本')),
                ('overhead_cost', models.DecimalField(
                    decimal_places=2, default=0, max_digits=12,
                    help_text='手工录入（MVP 不做自动分摊）', verbose_name='制费/管理费')),
                ('total_cost', models.DecimalField(decimal_places=2, default=0,
                                                   max_digits=12, verbose_name='总成本')),
                ('output_qty', models.PositiveIntegerField(
                    default=0, help_text='P2 前口径=全部登记完成扎数×扎量（近似）',
                    verbose_name='产出件数')),
                ('unit_cost', models.DecimalField(
                    blank=True, decimal_places=3, help_text='=total_cost/output_qty，产出 0 时为 null',
                    max_digits=12, null=True, verbose_name='单件成本')),
                ('revenue', models.DecimalField(
                    decimal_places=2, default=0, max_digits=12,
                    help_text='Σ OrderItem.qty×price；与实际收款差异属预期',
                    verbose_name='理论收入')),
                ('no_order_cnt', models.PositiveIntegerField(
                    default=0,
                    help_text='公司级口径：有成本活动但未挂单的床次数（其成本不计入任何订单，防毛利虚高静默漏算）',
                    verbose_name='未归属床次数')),
                ('missing_price_cnt', models.PositiveIntegerField(
                    default=0, help_text='已确认领料中 lot 无价/0价/换算缺参的行数（不进材料成本）',
                    verbose_name='缺价行数')),
                ('gross_profit', models.DecimalField(decimal_places=2, default=0,
                                                     max_digits=12, verbose_name='毛利')),
                ('gross_margin', models.DecimalField(
                    blank=True, decimal_places=2, help_text='=毛利/理论收入×100，收入 0 时为 null',
                    max_digits=6, null=True, verbose_name='毛利率%')),
                ('status', models.CharField(
                    choices=[('draft', '草稿'), ('computed', '已计算')], default='draft',
                    max_length=10, verbose_name='状态')),
                ('computed_at', models.DateTimeField(blank=True, null=True,
                                                     verbose_name='计算时间')),
                ('computed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='computed_order_costs', to='accounts.user',
                    verbose_name='计算人')),
                ('is_stale', models.BooleanField(
                    default=False, help_text='上游单据变动后为 True，待重算',
                    verbose_name='数据已过期')),
                ('company', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='order_costs', to='accounts.company',
                    verbose_name='公司')),
                ('order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='cost_record', to='orders.order',
                    verbose_name='客户订单')),
            ],
            options={
                'verbose_name': '订单成本',
                'verbose_name_plural': '订单成本',
                'db_table': 'cost_order_cost',
                'ordering': ['-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='ordercost',
            constraint=models.UniqueConstraint(
                condition=models.Q(is_deleted=False),
                fields=['company', 'order'], name='uq_cost_company_order_active'),
        ),
        migrations.AddIndex(
            model_name='ordercost',
            index=models.Index(fields=['company', 'status'],
                               name='idx_cost_company_status'),
        ),
    ]
