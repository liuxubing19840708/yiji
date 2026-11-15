"""账号模块路由：认证、我的信息、公司、员工、岗位。"""
from django.urls import path

from . import views

urlpatterns = [
    # 认证（匿名可访问，见各视图 permission_classes）
    path('auth/login', views.PasswordLoginView.as_view()),
    path('auth/sms-code', views.SendSMSCodeView.as_view()),
    path('auth/login-sms', views.SMSLoginView.as_view()),
    path('auth/wechat-login', views.WechatLoginView.as_view()),
    path('auth/wechat-web', views.WechatWebAuthView.as_view()),
    path('auth/wechat-web/login', views.WechatWebLoginView.as_view()),

    # 我的信息
    path('auth/me', views.MeView.as_view()),
    path('auth/password', views.ChangePasswordView.as_view()),
    path('auth/deactivate', views.DeactivateView.as_view()),

    # 公司
    path('companies/mine', views.CompanyViewSet.as_view({'get': 'mine'})),
    path('companies/create', views.CompanyViewSet.as_view({'post': 'create_company'})),
    path('companies/join', views.CompanyViewSet.as_view({'post': 'join'})),
    path('companies/plan', views.EmployeeViewSet.as_view({'put': 'set_plan', 'post': 'set_plan'})),
    path('companies/<int:pk>/switch', views.CompanyViewSet.as_view({'post': 'switch', 'put': 'switch'})),
    path('companies/wxacode', views.CompanyWxacodeView.as_view()),

    # 员工管理
    path('employees', views.EmployeeViewSet.as_view({'get': 'list'})),
    path('employees/invite', views.EmployeeViewSet.as_view({'post': 'invite'})),
    path('employees/<int:pk>/approve', views.EmployeeViewSet.as_view({'post': 'approve'})),
    path('employees/<int:pk>/reject', views.EmployeeViewSet.as_view({'post': 'reject'})),
    path('employees/<int:pk>/resign', views.EmployeeViewSet.as_view({'post': 'resign'})),
    path('employees/<int:pk>/rehire', views.EmployeeViewSet.as_view({'post': 'rehire'})),
    path('employees/<int:pk>/set-post', views.EmployeeViewSet.as_view({'post': 'set_post'})),
    path('employees/<int:pk>/set-group', views.EmployeeViewSet.as_view({'post': 'set_group'})),
    path('employees/<int:pk>/pin', views.EmployeeViewSet.as_view({'post': 'pin', 'put': 'pin'})),
    path('employees/<int:pk>/assigned-processes', views.EmployeeViewSet.as_view(
        {'put': 'set_assigned_processes', 'post': 'set_assigned_processes'})),

    # 岗位 CRUD
    path('posts', views.PostViewSet.as_view({'get': 'list', 'post': 'create'})),
    path('posts/<int:pk>/copy', views.PostViewSet.as_view({'post': 'copy'})),
    path('posts/<int:pk>', views.PostViewSet.as_view({
        'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'})),
]
