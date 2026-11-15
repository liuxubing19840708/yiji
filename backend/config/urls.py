"""全局路由：所有业务接口挂在 /api/ 下，由各应用维护自己的子路径。"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path, re_path

from apps.core import web


def healthz(request):
    """GET /healthz：进程+数据库探活（无鉴权；供 nginx/升级脚本/外部拨测）。"""
    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
        return JsonResponse({'status': 'ok'})
    except Exception:
        return JsonResponse({'status': 'db-error'}, status=503)


urlpatterns = [
    path('web', web.index, name='web-index-short'),
    path('web/', web.index, name='web-index'),
    re_path(r'^web/(?P<asset_path>.+)$', web.asset, name='web-asset'),
    path('healthz', healthz),
    path('admin/', admin.site.urls),
    path('api/', include('apps.core.urls')),
    path('api/', include('apps.accounts.urls')),
    path('api/', include('apps.catalog.urls')),
    path('api/', include('apps.production.urls')),
    path('api/', include('apps.orders.urls')),
    path('api/', include('apps.records.urls')),
    path('api/', include('apps.salary.urls')),
    path('api/', include('apps.ops.urls')),
    path('api/', include('apps.warehouse.urls')),
    path('api/', include('apps.kanban_template.urls')),
    path('api/', include('apps.tech.urls')),
    path('api/', include('apps.cost.urls')),
    path('api/', include('apps.quotation.urls')),
    path('api/', include('apps.finance.urls')),
    path('api/', include('apps.shipment.urls')),
    path('api/', include('apps.ai_report.urls')),
    path('api/', include('apps.quality.urls')),
    path('api/', include('apps.preproduction.urls')),
    path('api/', include('apps.manufacturing.urls')),
    path('api/cutting/', include('apps.cutting.urls')),
    path('api/', include('apps.handover.urls')),
    path('api/', include('apps.finished_goods.urls')),
    path('api/', include('apps.subcontract.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
