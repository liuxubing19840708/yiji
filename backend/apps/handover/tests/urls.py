"""仅供 handover 隔离测试挂接，不修改全局 urls。"""
from django.urls import include, path

urlpatterns = [path('api/', include('apps.handover.urls'))]
