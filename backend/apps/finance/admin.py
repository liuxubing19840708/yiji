from django.contrib import admin

from apps.finance.models import Receivable, ReceiptLine

admin.site.register(Receivable)
admin.site.register(ReceiptLine)
