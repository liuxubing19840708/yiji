# -*- coding: utf-8 -*-
"""微信网页 OAuth 独立测试：所有微信响应均为 mock，不访问真实微信。"""
import json
import time
import urllib.error
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.core import signing
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User


class FakeResponse:
    def __init__(self, data):
        self.body = data if isinstance(data, bytes) else json.dumps(data).encode('utf-8')

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@override_settings(WECHAT_OPEN_APPID='web-test-appid', WECHAT_OPEN_SECRET='web-test-secret')
class WechatWebOAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.raise_request_exception = False

    def authorize(self):
        response = self.client.get('/api/auth/wechat-web', secure=True,
                                   HTTP_HOST='erp.test')
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def state(self):
        config = self.authorize()
        return parse_qs(urlparse(config['authorization_url']).query)['state'][0]

    def login(self, code='web-code', state=None, responses=None):
        state = state or self.state()
        responses = responses if responses is not None else [
            FakeResponse({'access_token': 'web-token', 'openid': 'web-openid'}),
            FakeResponse({'openid': 'web-openid', 'nickname': '网页用户'}),
        ]
        with mock.patch('apps.accounts.views.urllib.request.urlopen',
                        side_effect=responses) as urlopen:
            response = self.client.post('/api/auth/wechat-web/login', {
                'code': code, 'state': state,
            }, format='json')
        return response, urlopen

    def test_authorization_url_uses_fixed_same_origin_callback_and_signed_state(self):
        config = self.authorize()
        self.assertTrue(config['enabled'])
        self.assertEqual(config['redirect_uri'], 'https://erp.test/web/')
        parsed = urlparse(config['authorization_url'])
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path),
                         ('https', 'open.weixin.qq.com', '/connect/qrconnect'))
        query = parse_qs(parsed.query)
        self.assertEqual(query['redirect_uri'], ['https://erp.test/web/'])
        self.assertEqual(query['scope'], ['snsapi_login'])
        self.assertEqual(parsed.fragment, 'wechat_redirect')
        payload = signing.TimestampSigner(salt='accounts.wechat-web').unsign_object(
            query['state'][0], max_age=600)
        self.assertEqual(payload['purpose'], 'wechat-web-login')
        self.assertTrue(payload['nonce'])
        ignored_redirect = self.client.get(
            '/api/auth/wechat-web?redirect_uri=https%3A%2F%2Fevil.test%2F', secure=True,
            HTTP_HOST='erp.test')
        self.assertEqual(ignored_redirect.data['redirect_uri'], 'https://erp.test/web/')
        self.assertNotIn('evil.test', ignored_redirect.data['authorization_url'])

    def test_state_must_match_the_browser_that_started_oauth(self):
        config = self.authorize()
        cookie = self.client.cookies.get('wechat_oauth_state')
        self.assertIsNotNone(cookie)
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        state = parse_qs(urlparse(config['authorization_url']).query)['state'][0]
        other_browser = APIClient()
        with mock.patch('apps.accounts.views.urllib.request.urlopen') as urlopen:
            response = other_browser.post('/api/auth/wechat-web/login', {
                'code': 'web-code', 'state': state,
            }, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('浏览器', str(response.data))
        urlopen.assert_not_called()

    def test_invalid_and_expired_state_are_rejected_without_wechat_requests(self):
        response, urlopen = self.login(state='forged-state')
        self.assertEqual(response.status_code, 400)
        self.assertIn('state', str(response.data))
        urlopen.assert_not_called()

        state = self.state()
        with mock.patch('django.core.signing.time.time', return_value=time.time() + 601):
            response, urlopen = self.login(state=state)
        self.assertEqual(response.status_code, 400)
        self.assertIn('过期', str(response.data))
        urlopen.assert_not_called()

    def test_unionid_links_existing_mini_program_account_without_mixing_openids(self):
        mini = User.objects.create_user(
            username='mini-user', openid='same-looking-openid', unionid='shared-unionid')
        response, _ = self.login(responses=[
            FakeResponse({'access_token': 'web-token', 'openid': 'web-distinct-openid'}),
            FakeResponse({'openid': 'web-distinct-openid', 'unionid': 'shared-unionid'}),
        ])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['user']['id'], mini.id)
        self.assertEqual(User.objects.count(), 1)
        mini.refresh_from_db()
        self.assertEqual(mini.openid, 'same-looking-openid')
        self.assertEqual(mini.web_openid, 'web-distinct-openid')
        self.assertEqual(mini.unionid, 'shared-unionid')

    def test_same_web_openid_relogin_reuses_user(self):
        first, _ = self.login()
        second, _ = self.login()
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(first.data['user']['id'], second.data['user']['id'])
        self.assertEqual(User.objects.filter(web_openid='web-openid').count(), 1)

    def test_mini_program_login_saves_unionid(self):
        with override_settings(WECHAT_APPID='mini-appid', WECHAT_SECRET='mini-secret'):
            with mock.patch('apps.accounts.views.urllib.request.urlopen', return_value=FakeResponse({
                    'openid': 'mini-openid', 'session_key': 'not-returned', 'unionid': 'mini-unionid'})):
                response = self.client.post('/api/auth/wechat-login', {'code': 'mini-code'}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        user = User.objects.get(openid='mini-openid')
        self.assertEqual(user.unionid, 'mini-unionid')
        self.assertIsNone(user.web_openid)
        self.assertNotIn('session_key', response.data)

    def test_inactive_wechat_accounts_cannot_obtain_new_tokens(self):
        User.objects.create_user(
            username='inactive-web', web_openid='web-openid', is_active=False)
        response, _ = self.login()
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('已注销', str(response.data))

        User.objects.create_user(
            username='inactive-mini', openid='mini-inactive', is_active=False)
        with override_settings(WECHAT_APPID='mini-appid', WECHAT_SECRET='mini-secret'):
            with mock.patch('apps.accounts.views.urllib.request.urlopen', return_value=FakeResponse({
                    'openid': 'mini-inactive'})):
                response = self.client.post('/api/auth/wechat-login', {'code': 'mini-code'}, format='json')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('已注销', str(response.data))

    def test_deactivate_clears_all_wechat_identifiers(self):
        user = User.objects.create_user(
            username='deactivate-wechat', openid='mini-clear', web_openid='web-clear',
            unionid='union-clear')
        user.set_unusable_password()
        user.save(update_fields=['password'])
        self.client.force_authenticate(user)
        response = self.client.post('/api/auth/deactivate', {}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNone(user.openid)
        self.assertIsNone(user.web_openid)
        self.assertIsNone(user.unionid)

    def test_wechat_business_network_and_malformed_errors_do_not_create_users(self):
        cases = {
            'token_business_error': [FakeResponse({'errcode': 40029, 'errmsg': 'invalid code'})],
            'userinfo_business_error': [
                FakeResponse({'access_token': 'web-token', 'openid': 'web-openid'}),
                FakeResponse({'errcode': 40003, 'errmsg': 'invalid openid'}),
            ],
            'network': urllib.error.URLError('offline'),
            'bad_json': [FakeResponse(b'not-json')],
            'mismatched_openid': [
                FakeResponse({'access_token': 'web-token', 'openid': 'web-openid'}),
                FakeResponse({'openid': 'another-openid'}),
            ],
        }
        for name, responses in cases.items():
            with self.subTest(name=name):
                response, _ = self.login(responses=responses)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('微信网页登录', str(response.data))
                self.assertEqual(User.objects.count(), 0)

    @override_settings(WECHAT_OPEN_APPID='', WECHAT_OPEN_SECRET='')
    def test_unconfigured_auth_is_disabled_and_login_fails_clearly(self):
        config = self.client.get('/api/auth/wechat-web')
        self.assertEqual(config.status_code, 200)
        self.assertFalse(config.data['enabled'])
        self.assertIsNone(config.data['authorization_url'])
        self.assertIn('WECHAT_OPEN_APPID/WECHAT_OPEN_SECRET', config.data['message'])
        response, urlopen = self.login(state='unused')
        self.assertEqual(response.status_code, 400)
        self.assertIn('WECHAT_OPEN_APPID/WECHAT_OPEN_SECRET', str(response.data))
        urlopen.assert_not_called()
