from django.contrib import admin

from apps.quotation.models import QuoteLabor, QuoteMaterial, Quotation

admin.site.register(Quotation)
admin.site.register(QuoteMaterial)
admin.site.register(QuoteLabor)
