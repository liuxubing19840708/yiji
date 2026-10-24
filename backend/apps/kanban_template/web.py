# -*- coding: utf-8 -*-
"""Vue3 配置端静态托管：frontend-admin 构建产物（dist/）由 Django 同源托管。

路径约定：frontend-admin 构建输出到 backend/static/kanban_admin/，
访问 /api/kanban-admin（页面）与 /api/kanban-admin/<asset>（js/css）。
产物缺失时返回构建提示（不影响 API）。
"""
import os

from django.conf import settings
from django.http import FileResponse, HttpResponse

DIST_DIR = os.path.join(settings.BASE_DIR, 'static', 'kanban_admin')
INDEX_HTML = os.path.join(DIST_DIR, 'index.html')


def spa_index(request):
    if not os.path.exists(INDEX_HTML):
        return HttpResponse(
            '<h1>看板配置端未构建</h1><p>请在 frontend-admin 目录执行：'
            '<code>npm install &amp;&amp; npm run build</code>，'
            '产物输出到 backend/static/kanban_admin/。'
            '详见 frontend-admin/README.md</p>', content_type='text/html; charset=utf-8')
    return FileResponse(open(INDEX_HTML, 'rb'), content_type='text/html; charset=utf-8')


def spa_asset(request, asset):
    path = os.path.normpath(os.path.join(DIST_DIR, asset))
    if not path.startswith(os.path.abspath(DIST_DIR)) or not os.path.isfile(path):
        from django.http import Http404
        raise Http404('资源不存在')
    ctype = 'application/octet-stream'
    if path.endswith('.js'):
        ctype = 'text/javascript'
    elif path.endswith('.css'):
        ctype = 'text/css'
    elif path.endswith('.html'):
        ctype = 'text/html; charset=utf-8'
    elif path.endswith('.svg'):
        ctype = 'image/svg+xml'
    return FileResponse(open(path, 'rb'), content_type=ctype)
