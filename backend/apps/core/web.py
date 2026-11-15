# -*- coding: utf-8 -*-
"""同源托管 frontend-web 构建产物；只暴露 static/web 中的文件。"""
import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse

DIST_DIR = (Path(settings.BASE_DIR) / 'static' / 'web').resolve()
INDEX_HTML = DIST_DIR / 'index.html'


def _file_response(path, *, cache=False):
    response = FileResponse(path.open('rb'), content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'public, max-age=86400' if cache else 'no-cache'
    return response


def index(request):
    if not INDEX_HTML.is_file():
        return HttpResponse(
            '<h1>Web 测试端尚未构建</h1><p>请在 frontend-web 目录执行 '
            '<code>npm install &amp;&amp; npm run build</code>。</p>',
            content_type='text/html; charset=utf-8', status=503)
    return _file_response(INDEX_HTML)


def asset(request, asset_path):
    candidate = (DIST_DIR / asset_path).resolve()
    try:
        candidate.relative_to(DIST_DIR)
    except ValueError:
        raise Http404('资源不存在')
    if candidate == INDEX_HTML or not candidate.is_file():
        raise Http404('资源不存在')
    return _file_response(candidate, cache=True)
