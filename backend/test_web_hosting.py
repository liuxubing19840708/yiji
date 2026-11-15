# -*- coding: utf-8 -*-
"""Web 静态托管安全与可用性回归；使用 Django 测试库，不接触开发库。"""
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from apps.core import web


class WebHostingTests(SimpleTestCase):
    def test_routes_are_registered(self):
        self.assertEqual(reverse('web-index'), '/web/')
        self.assertEqual(reverse('web-index-short'), '/web')

    def test_missing_build_returns_actionable_503(self):
        with patch.object(web, 'INDEX_HTML', Path(web.DIST_DIR) / '__missing__.html'):
            response = self.client.get('/web/')
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, 'npm install', status_code=503)

    def test_index_is_served_without_api_authentication(self):
        if not web.INDEX_HTML.is_file():
            self.skipTest('先运行 frontend-web/npm run build')
        response = self.client.get('/web/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-cache')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_asset_cannot_escape_distribution_directory(self):
        response = self.client.get('/web/%2e%2e/config/settings.py')
        self.assertEqual(response.status_code, 404)

    def test_missing_asset_is_404(self):
        response = self.client.get('/web/assets/not-present.js')
        self.assertEqual(response.status_code, 404)

    def test_api_is_not_shadowed_by_web_route(self):
        response = self.client.get('/api/companies/mine')
        self.assertEqual(response.status_code, 401)
