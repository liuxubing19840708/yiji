from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('salary', '0002_settlement_wage_detail'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SalaryPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('is_deleted', models.BooleanField(db_index=True, default=False, verbose_name='是否已删除')),
                ('paid_at', models.DateTimeField(verbose_name='实付时间')),
                ('method', models.CharField(max_length=32, verbose_name='支付方式')),
                ('transaction_ref', models.CharField(max_length=100, verbose_name='外部流水号')),
                ('note', models.CharField(blank=True, default='', max_length=500, verbose_name='备注')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))], verbose_name='实付金额')),
                ('status', models.CharField(choices=[('active', '有效'), ('void', '已作废')], default='active', max_length=10, verbose_name='状态')),
                ('void_reason', models.CharField(blank=True, default='', max_length=500, verbose_name='作废原因')),
                ('voided_at', models.DateTimeField(blank=True, null=True, verbose_name='作废时间')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='salary_payments', to='accounts.company', verbose_name='公司')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='salary_payments', to=settings.AUTH_USER_MODEL, verbose_name='员工')),
                ('operator', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='operated_salary_payments', to=settings.AUTH_USER_MODEL, verbose_name='操作人')),
                ('voided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='voided_salary_payments', to=settings.AUTH_USER_MODEL, verbose_name='作废人')),
            ],
            options={
                'verbose_name': '实付工资',
                'verbose_name_plural': '实付工资',
                'db_table': 'salary_payment',
                'ordering': ['-paid_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='SalaryPaymentAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('is_deleted', models.BooleanField(db_index=True, default=False, verbose_name='是否已删除')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))], verbose_name='分摊金额')),
                ('payment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='allocations', to='salary.salarypayment', verbose_name='实付工资')),
                ('settlement', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payment_allocations', to='salary.settlement', verbose_name='结算单')),
            ],
            options={
                'verbose_name': '实付工资分摊',
                'verbose_name_plural': '实付工资分摊',
                'db_table': 'salary_payment_allocation',
                'ordering': ['id'],
            },
        ),
        migrations.AddIndex(
            model_name='salarypayment',
            index=models.Index(fields=['company', 'employee', 'paid_at'], name='idx_salarypay_co_emp_time'),
        ),
        migrations.AddIndex(
            model_name='salarypayment',
            index=models.Index(fields=['company', 'status'], name='idx_salarypay_co_status'),
        ),
        migrations.AddConstraint(
            model_name='salarypayment',
            constraint=models.UniqueConstraint(condition=models.Q(('is_deleted', False), ('status', 'active')), fields=('company', 'transaction_ref'), name='uniq_salarypay_active_ref'),
        ),
        migrations.AddIndex(
            model_name='salarypaymentallocation',
            index=models.Index(fields=['settlement', 'payment'], name='idx_salaryalloc_settle_pay'),
        ),
        migrations.AddConstraint(
            model_name='salarypaymentallocation',
            constraint=models.UniqueConstraint(fields=('payment', 'settlement'), name='uniq_salaryalloc_pay_settle'),
        ),
    ]
