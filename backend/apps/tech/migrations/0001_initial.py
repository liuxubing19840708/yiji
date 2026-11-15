import decimal

from django.db import migrations, models
from django.db.models import Q
from django.db.models.deletion import CASCADE, PROTECT


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
        ('catalog', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Material',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True,
                                                    verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('is_deleted', models.BooleanField(db_index=True, default=False,
                                                   verbose_name='是否已删除')),
                ('code', models.CharField(db_index=True, max_length=64,
                                          verbose_name='物料编码')),
                ('name', models.CharField(max_length=128, verbose_name='品名')),
                ('category', models.CharField(choices=[
                    ('fabric', '面料'), ('lining', '里料'),
                    ('accessory', '辅料'), ('packing', '包装')],
                    default='fabric', max_length=16, verbose_name='分类')),
                ('unit', models.CharField(max_length=16, verbose_name='主单位')),
                ('unit_alt', models.CharField(blank=True, default='',
                                              help_text='录了副单位即声明双单位换算，克重+幅宽必填',
                                              max_length=16, verbose_name='副单位')),
                ('weight_gsm', models.DecimalField(blank=True, decimal_places=2,
                                                   max_digits=10, null=True,
                                                   verbose_name='克重(g/m²)')),
                ('width_cm', models.DecimalField(blank=True, decimal_places=2,
                                                 max_digits=10, null=True,
                                                 verbose_name='幅宽(cm)')),
                ('shrink_rate', models.DecimalField(blank=True, decimal_places=2,
                                                    help_text='A4：独立维护计入定额，'
                                                              '定额=净用量×(1+缩率)×(1+损耗)',
                                                    max_digits=5, null=True,
                                                    verbose_name='缩率%')),
                ('price_reference', models.DecimalField(blank=True, decimal_places=3,
                                                        max_digits=12, null=True,
                                                        verbose_name='参考价')),
                ('status', models.CharField(choices=[
                    ('active', '启用'), ('disabled', '停用')],
                    default='active', max_length=16, verbose_name='状态')),
                ('legacy_code', models.CharField(blank=True, default='',
                                                 help_text='旧编码对照（E1），兼容过渡期检索',
                                                 max_length=64, verbose_name='旧编码')),
                ('attrs', models.JSONField(blank=True, default=dict,
                                           help_text='成分/色号/fabric_type(woven/knit) 等扩展',
                                           verbose_name='扩展属性')),
                ('company', models.ForeignKey(on_delete=CASCADE,
                                              related_name='tech_materials',
                                              to='accounts.company',
                                              verbose_name='公司')),
            ],
            options={
                'verbose_name': '物料主档',
                'verbose_name_plural': '物料主档',
                'db_table': 'tech_material',
                'ordering': ['-id'],
            },
        ),
        migrations.CreateModel(
            name='Bom',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True,
                                                    verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('is_deleted', models.BooleanField(db_index=True, default=False,
                                                   verbose_name='是否已删除')),
                ('version', models.PositiveIntegerField(default=1, verbose_name='版本')),
                ('status', models.CharField(choices=[
                    ('draft', '草稿'), ('active', '生效'), ('deactivated', '已停用')],
                    default='draft', max_length=16, verbose_name='状态')),
                ('remark', models.CharField(blank=True, default='', max_length=255,
                                            verbose_name='备注')),
                ('company', models.ForeignKey(on_delete=CASCADE,
                                              related_name='tech_boms',
                                              to='accounts.company',
                                              verbose_name='公司')),
                ('style', models.ForeignKey(on_delete=PROTECT,
                                            related_name='tech_boms',
                                            to='catalog.style', verbose_name='款式')),
            ],
            options={
                'verbose_name': '版式BOM',
                'verbose_name_plural': '版式BOM',
                'db_table': 'tech_bom',
                'ordering': ['-id'],
            },
        ),
        migrations.CreateModel(
            name='BomItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True,
                                                    verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('is_deleted', models.BooleanField(db_index=True, default=False,
                                                   verbose_name='是否已删除')),
                ('kind', models.CharField(choices=[
                    ('main', '面料'), ('lining', '里料'), ('accessory', '辅料')],
                    default='main', max_length=16, verbose_name='类别')),
                ('unit', models.CharField(max_length=16, verbose_name='单位(主单位快照)')),
                ('color', models.CharField(blank=True, default='',
                                           help_text='空串=通码', max_length=64,
                                           verbose_name='颜色')),
                ('size', models.CharField(blank=True, default='',
                                          help_text='空串=通码', max_length=32,
                                          verbose_name='尺码')),
                ('unit_usage', models.DecimalField(decimal_places=4, max_digits=12,
                                                   verbose_name='单耗(净用量)')),
                ('loss_rate', models.DecimalField(decimal_places=2,
                                                  default=decimal.Decimal('0'),
                                                  max_digits=5, verbose_name='损耗率%')),
                ('position', models.CharField(blank=True, default='', max_length=64,
                                              verbose_name='部位')),
                ('remark', models.CharField(blank=True, default='', max_length=255,
                                            verbose_name='备注')),
                ('bom', models.ForeignKey(on_delete=PROTECT,
                                          related_name='items', to='tech.bom',
                                          verbose_name='BOM')),
                ('material', models.ForeignKey(on_delete=PROTECT,
                                               related_name='bom_items',
                                               to='tech.material', verbose_name='物料')),
            ],
            options={
                'verbose_name': 'BOM明细',
                'verbose_name_plural': 'BOM明细',
                'db_table': 'tech_bom_item',
                'ordering': ['id'],
            },
        ),
        migrations.AddConstraint(
            model_name='material',
            constraint=models.UniqueConstraint(
                condition=Q(is_deleted=False), fields=('company', 'code'),
                name='uq_tech_material_company_code'),
        ),
        migrations.AddIndex(
            model_name='material',
            index=models.Index(fields=('company', 'category'),
                               name='idx_tmat_co_category'),
        ),
        migrations.AddConstraint(
            model_name='bom',
            constraint=models.UniqueConstraint(
                condition=Q(is_deleted=False), fields=('company', 'style', 'version'),
                name='uq_tech_bom_company_style_version'),
        ),
        migrations.AddConstraint(
            model_name='bom',
            constraint=models.UniqueConstraint(
                condition=Q(is_deleted=False, status='active'),
                fields=('company', 'style'),
                name='uq_tech_bom_company_style_active'),
        ),
        migrations.AddIndex(
            model_name='bom',
            index=models.Index(fields=('company', 'status'), name='idx_tbom_co_status'),
        ),
        migrations.AddConstraint(
            model_name='bomitem',
            constraint=models.UniqueConstraint(
                condition=Q(is_deleted=False),
                fields=('bom', 'material', 'color', 'size'),
                name='uq_tech_bom_item_bom_mat_color_size'),
        ),
    ]
