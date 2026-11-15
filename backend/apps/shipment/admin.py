from django.contrib import admin

from apps.shipment.models import PackingBox, Shipment, ShipmentItem, ShipmentPrintLog

admin.site.register(Shipment)
admin.site.register(ShipmentItem)
admin.site.register(PackingBox)
admin.site.register(ShipmentPrintLog)
