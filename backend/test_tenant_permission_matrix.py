# -*- coding: utf-8 -*-
"""全 API 租户/权限矩阵：路由发现驱动，新增接口会自动进入门禁。

覆盖：
1. 所有权限点字符串必须在权限字典登记；
2. 非公司成员伪造 X-Company-Id 时，所有公司接口/方法都必须 404；
3. 匿名请求不能进入公司接口；
4. 无权限成员不能成功执行写操作，所有请求均不得暴露 500。

这里不要求每个无权限读请求都 403：工作台、本人薪酬、扫码选择器等明确允许
普通成员访问。写操作的少量本人动作通过 SELF_WRITE_ALLOWLIST 显式登记并复核。
"""
import ast
import inspect
from pathlib import Path
import re

from django.conf import settings
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.core.mixins import CompanyContextMixin
from apps.core.models import ClientLog
from apps.core.permissions import ALL_PERMISSION_KEYS


SAFE_METHODS = {'get', 'head', 'options', 'trace'}
WRITE_METHODS = {'post', 'put', 'patch', 'delete'}
# 复核后允许普通成员执行的写动作；键为 (路由, METHOD)。只放本人/低危操作，
# 任何“管理员代办”接口都不能用此清单绕过权限点。
SELF_WRITE_ALLOWLIST = {
    ('api/notifications/read-all', 'POST'),  # 只更新当前用户自己的通知
}
# 不使用 CompanyContextMixin 的公开/账号级 API（登录、公司切换等）经复核后登记。
NON_COMPANY_VIEWS = {
    'apps.accounts.views.DeactivateView',          # 本人注销，另有末管理员校验
    'apps.accounts.views.PasswordLoginView',       # 公开登录
    'apps.accounts.views.SMSLoginView',            # 公开短信登录
    'apps.accounts.views.MeView',                  # 本人资料
    'apps.accounts.views.ChangePasswordView',      # 本人改密，校验旧密码
    'apps.accounts.views.SendSMSCodeView',         # 公开验证码入口
    'apps.accounts.views.WechatLoginView',         # 公开小程序微信登录
    'apps.accounts.views.WechatWebAuthView',       # 公开网页 OAuth 配置与签名授权 URL
    'apps.accounts.views.WechatWebLoginView',      # 公开网页 OAuth code 换登录令牌
    'apps.accounts.views.CompanyViewSet',          # 我的公司/创建/申请加入/切换
    'apps.core.views.PermissionDictView',          # 登录成员可读的全局权限字典
    'apps.core.views.UploadView',                  # 登录上传，随机路径且不写业务表
    'apps.core.views.VideoUploadView',             # 同上，另有格式和大小校验
    'apps.core.views.DocumentUploadView',          # 登录上传，随机路径且不写业务表
}


def _routes(patterns=None, prefix=''):
    patterns = get_resolver().url_patterns if patterns is None else patterns
    for pattern in patterns:
        path = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from _routes(pattern.url_patterns, path)
        elif isinstance(pattern, URLPattern):
            callback = pattern.callback
            # APIView 用 view_class；ViewSet 的 as_view({'get': ...}) 用 cls/actions。
            view_class = (getattr(callback, 'view_class', None)
                          or getattr(callback, 'cls', None))
            actions = getattr(callback, 'actions', None)
            is_drf_view = (view_class is not None
                           and (issubclass(view_class, APIView) or actions is not None))
            if is_drf_view and path.startswith('api/'):
                methods = (list(actions) if actions is not None else
                           [m for m in ('get', 'post', 'put', 'patch', 'delete')
                            if callable(getattr(view_class, m, None))])
                yield path, view_class, methods


def _url(path):
    # 大整数避免命中测试夹具里的真实对象；权限与租户判断不应依赖参数是否存在。
    path = re.sub(r'<int:[^>]+>', '987654321', path)
    path = re.sub(r'<uuid:[^>]+>', '00000000-0000-0000-0000-000000000000', path)
    path = re.sub(r'<(?:str|slug|path):[^>]+>', 'matrix-nonexistent', path)
    return '/' + path


def _permission_calls():
    """静态盘点全部 views.py 中写死的 check_perm 权限点。"""
    root = Path(settings.BASE_DIR) / 'apps'
    for source_path in root.glob('*/views.py'):
        tree = ast.parse(source_path.read_text(encoding='utf-8'), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == 'check_perm'
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                yield source_path, node.lineno, node.args[0].value


class TenantPermissionMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner_a = User.objects.create_user(
            username='matrix-owner-a', phone='13900007001', password='test-only')
        cls.company_a, _post = create_company_with_owner(cls.owner_a, '权限矩阵A厂')
        cls.company_b = create_company_with_owner(
            User.objects.create_user(username='matrix-owner-b', phone='13900007002',
                                     password='test-only'), '权限矩阵B厂')[0]
        cls.plain_post = Post.objects.create(
            company=cls.company_a, name='矩阵无权限岗', permissions=[])
        cls.plain = User.objects.create_user(
            username='matrix-plain', phone='13900007003', password='test-only')
        UserCompany.objects.create(
            user=cls.plain, company=cls.company_a, post=cls.plain_post,
            status=UserCompany.Status.ACTIVE)
        cls.company_routes = [
            (path, view, methods) for path, view, methods in _routes()
            if issubclass(view, CompanyContextMixin)
            and not getattr(view, 'skip_company_context', False)
        ]

    def _client(self, user=None, company=None):
        client = APIClient(raise_request_exception=False)
        if user is not None:
            client.force_authenticate(user=user)
        if company is not None:
            client.credentials(HTTP_X_COMPANY_ID=str(company.pk))
        return client

    def test_01_route_inventory_and_permission_dictionary(self):
        routes = list(_routes())
        self.assertGreaterEqual(len(routes), 100, '路由发现异常，矩阵可能未覆盖业务接口')
        self.assertGreaterEqual(sum(len(m) for _p, _v, m in self.company_routes), 200)
        invalid = [f'{path.name}:{line} {key}'
                   for path, line, key in _permission_calls()
                   if key not in ALL_PERMISSION_KEYS]
        self.assertEqual(invalid, [], msg=f'未登记权限点:\n{invalid}')
        non_company = sorted(
            f'{path} -> {view.__module__}.{view.__name__}'
            for path, view, _methods in routes
            if not issubclass(view, CompanyContextMixin)
            and f'{view.__module__}.{view.__name__}' not in NON_COMPANY_VIEWS)
        self.assertEqual(non_company, [], msg=f'未复核的非公司上下文接口:\n' + '\n'.join(non_company))
        print(f'[权限矩阵] 发现 {len(routes)} 个 API 路由，'
              f'{sum(len(m) for _p, _v, m in self.company_routes)} 个公司接口方法')

    def test_02_foreign_company_header_is_rejected_for_every_method(self):
        client = self._client(self.plain)
        failures = []
        for path, view, methods in self.company_routes:
            for method in methods:
                response = getattr(client, method)(_url(path), data={},
                                                   format='json', HTTP_X_COMPANY_ID=str(self.company_b.pk))
                if response.status_code != 404:
                    failures.append(f'{method.upper()} /{path}: {response.status_code}')
        self.assertEqual(failures, [], msg='伪造其他公司上下文未被统一拦截:\n' + '\n'.join(failures))

    def test_03_anonymous_cannot_enter_company_apis(self):
        client = self._client()
        failures = []
        for path, view, methods in self.company_routes:
            for method in methods:
                response = getattr(client, method)(_url(path), data={}, format='json')
                if response.status_code not in (401, 403):
                    failures.append(f'{method.upper()} /{path}: {response.status_code}')
        self.assertEqual(failures, [], msg='匿名请求绕过认证:\n' + '\n'.join(failures))

    def test_04_plain_member_cannot_write_and_no_route_returns_500(self):
        client = self._client(self.plain, self.company_a)
        server_errors, unexpected_writes = [], []
        for path, view, methods in self.company_routes:
            for method in methods:
                response = getattr(client, method)(_url(path), data={}, format='json')
                if response.status_code >= 500:
                    server_errors.append(f'{method.upper()} /{path}: {response.status_code}')
                if (method in WRITE_METHODS and 200 <= response.status_code < 300
                        and (path, method.upper()) not in SELF_WRITE_ALLOWLIST):
                    unexpected_writes.append(f'{method.upper()} /{path}: {response.status_code}')
        self.assertEqual(server_errors, [], msg='无权限请求触发服务器错误:\n' + '\n'.join(server_errors))
        self.assertEqual(unexpected_writes, [],
                         msg='无权限成员成功执行写操作（须修复或复核后显式登记）:\n'
                             + '\n'.join(unexpected_writes))

    def test_05_client_log_cannot_forge_foreign_company_attribution(self):
        client = self._client(self.plain, self.company_a)
        response = client.post('/api/client-logs', [{
            'message': 'matrix-foreign-company', 'company_id': self.company_b.pk,
        }], format='json')
        self.assertEqual(response.status_code, 201)
        row = ClientLog.objects.get(message='matrix-foreign-company')
        self.assertIsNone(row.company_id)
        self.assertEqual(row.user_id, self.plain.pk)

        response = self._client(self.plain).get(
            '/api/client-logs', format='json',
            HTTP_X_COMPANY_ID=str(self.company_b.pk))
        self.assertEqual(response.status_code, 404)
        response = self._client().get('/api/client-logs', format='json')
        self.assertIn(response.status_code, (401, 403))
