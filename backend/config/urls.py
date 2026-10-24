"""全局路由：所有业务接口挂在 /api/ 下，由各应用维护自己的子路径。"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
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
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
