"""仅供 cutting 隔离测试挂接，不修改全局 urls。"""
from django.urls import include, path

urlpatterns = [path('api/cutting/', include('apps.cutting.urls'))]
