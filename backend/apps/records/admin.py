"""扫菲登记后台。"""
from django.contrib import admin

from apps.records.models import Record


@admin.register(Record)
class RecordAdmin(admin.ModelAdmin):
    list_display = ('id', 'company', 'ticket', 'process', 'user', 'operator',
                    'qty', 'price', 'registered_at', 'settlement')
    search_fields = ('ticket__bundle_no', 'ticket__production__bed_no',
                     'ticket__production__style__style_no', 'user__real_name')
    list_filter = ('company', 'process', 'settlement')
