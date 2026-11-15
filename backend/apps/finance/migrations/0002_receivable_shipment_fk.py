# P2 出货域接入（SA-P2-4，2026-10-24）：Receivable.shipment 整列占位 → 真 FK。
#
# 交接口径（SA-P2-2 备注原文）：按 RemoveField(shipment_id) →
# AddField(shipment=FK('shipment.Shipment', null=True, SET_NULL, db_index=True))
# 顺序平移。「先回填 SELECT 再删」在此为空操作：占位列只可能存悬空整数
# （shipment 表彼时尚不存在，手工端也从未有真行可指），实测无可回填数据，
# 删列即零丢失；真 FK 上线后出货审核自动建应收回填 shipment_id。
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('finance', '0001_initial'),
        ('shipment', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='receivable',
            name='shipment_id',
        ),
        migrations.AddField(
            model_name='receivable',
            name='shipment',
            field=models.ForeignKey(blank=True,
                                    help_text='出货审核自动生成应收时回填（SA-P2-4）',
                                    null=True, db_index=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='finance_receivables',
                                    to='shipment.shipment', verbose_name='出货单'),
        ),
    ]
