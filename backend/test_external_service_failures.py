# -*- coding: utf-8 -*-
"""外部服务异常契约：微信/GLM 不可信响应不得变成 500 或业务脏数据。"""
import urllib.error
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.services import (_wx_token_cache, create_company_with_owner,
                                    get_wx_access_token)
from apps.ai_report.models import AiQueryLog
from apps.ai_report import services as ai_services


class FakeResponse:
    def __init__(self, body, content_type='application/json'):
        self.body = body
        self.headers = {'Content-Type': content_type}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@override_settings(WECHAT_APPID='external-test-appid',
                   WECHAT_SECRET='external-test-secret')
class WechatFailureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='external-owner', phone='13900007301', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '外部服务异常隔离厂')

    def setUp(self):
        self.client = APIClient()
        self.client.raise_request_exception = False
        self.old_cache = dict(_wx_token_cache)
        _wx_token_cache.update(token='', expires_at=0.0)
        self.addCleanup(self._restore_cache)

    def _restore_cache(self):
        _wx_token_cache.clear()
        _wx_token_cache.update(self.old_cache)

    def test_login_transport_failures_return_400_without_creating_user(self):
        failures = {
            'timeout': TimeoutError('timed out'),
            'dns': urllib.error.URLError('dns failed'),
            'http_502': urllib.error.HTTPError(
                'https://test.invalid/jscode2session', 502, 'Bad Gateway', {}, None),
        }
        for name, error in failures.items():
            with self.subTest(name=name):
                before = User.objects.count()
                with mock.patch('urllib.request.urlopen', side_effect=error):
                    response = self.client.post(
                        '/api/auth/wechat-login', {'code': 'external-code'}, format='json')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('微信登录失败', str(response.data))
                self.assertEqual(User.objects.count(), before)

    def test_login_malformed_success_response_is_controlled_400(self):
        responses = {
            'bad_json': b'not-json',
            'bad_utf8': b'\xff\xfe',
            'json_array': b'[]',
            'business_error': b'{"errcode":40029,"errmsg":"invalid code"}',
        }
        for name, body in responses.items():
            with self.subTest(name=name):
                before = User.objects.count()
                with mock.patch('urllib.request.urlopen', return_value=FakeResponse(body)):
                    response = self.client.post(
                        '/api/auth/wechat-login', {'code': 'external-code'}, format='json')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('微信登录失败', str(response.data))
                self.assertEqual(User.objects.count(), before)

    def test_access_token_malformed_response_does_not_poison_cache(self):
        responses = {
            'bad_json': b'not-json',
            'bad_utf8': b'\xff\xfe',
            'json_array': b'[]',
            'invalid_expiry': b'{"access_token":"token-x","expires_in":"invalid"}',
        }
        for name, body in responses.items():
            with self.subTest(name=name):
                with mock.patch('urllib.request.urlopen', return_value=FakeResponse(body)):
                    with self.assertRaises(ValidationError) as caught:
                        get_wx_access_token()
                self.assertIn('获取小程序凭证失败', str(caught.exception.detail))
                self.assertEqual(_wx_token_cache, {'token': '', 'expires_at': 0.0})


@override_settings(GLM_API_KEY='external-test-key', AI_FREE_MODE=False)
class AiFailureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='external-ai-owner', phone='13900007302', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, 'AI外部异常隔离厂')

    def setUp(self):
        self.client = APIClient()
        self.client.raise_request_exception = False
        self.client.force_authenticate(self.owner)
        self.client.credentials(HTTP_X_COMPANY_ID=str(self.company.pk))
        ai_services._FREE_CACHE.clear()
        self.addCleanup(ai_services._FREE_CACHE.clear)

    def _assert_fallback(self):
        response = self.client.post(
            '/api/ai/report/query', {'question': '这个问题怎么看'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.data['fallback'])
        self.assertEqual(response.data['quick_questions'], ai_services.QUICK_QUESTIONS)
        log = AiQueryLog.objects.get(company=self.company, user=self.owner)
        self.assertFalse(log.ok)

    def test_intent_transport_and_payload_failures_fall_back(self):
        failures = {
            'timeout': TimeoutError('timed out'),
            'http_503': urllib.error.HTTPError(
                'https://test.invalid/chat/completions', 503, 'Unavailable', {}, None),
            'bad_json': FakeResponse(b'not-json'),
            'bad_utf8': FakeResponse(b'\xff\xfe'),
            'json_array': FakeResponse(b'[]'),
            'empty_choices': FakeResponse(b'{"choices":[]}'),
        }
        for name, failure in failures.items():
            with self.subTest(name=name):
                AiQueryLog.objects.all().delete()
                patch = (mock.patch('apps.ai_report.services.urlrequest.urlopen',
                                    side_effect=failure)
                         if isinstance(failure, Exception)
                         else mock.patch('apps.ai_report.services.urlrequest.urlopen',
                                         return_value=failure))
                with patch:
                    self._assert_fallback()

    @override_settings(AI_FREE_MODE=True)
    def test_free_answer_failure_also_falls_back_without_cached_answer(self):
        with mock.patch.object(ai_services, '_glm_chat', side_effect=TimeoutError('timed out')):
            self._assert_fallback()
        self.assertFalse(ai_services._FREE_CACHE)
