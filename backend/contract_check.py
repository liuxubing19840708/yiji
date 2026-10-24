# -*- coding: utf-8 -*-
"""前后端契约比对：前端 services 里所有 http.<m>('<path>') vs 后端路由表。"""
import re
import io
import os
import glob

FRONT_GLOBS = [
    r'D:/project/扫菲/frontend/services/*.ts',
    r'D:/project/扫菲/frontend/packages/*/services/*.ts',
    r'D:/project/扫菲/frontend/utils/*.ts',
]

# ---- 前端：提取 http.<m>('<path>') ----
fe = set()
for pattern in FRONT_GLOBS:
    for f in glob.glob(pattern):
        s = io.open(f, encoding='utf-8').read()
        rx = r"http\.(get|post|put|del|delete|patch)(?:<[^>]*>)?\(\s*(['\"`])(/[^'\"`]+)\2"
        for m, _q, path in re.findall(rx, s):
            p = re.sub(r'\$\{[^}]+\}', '<p>', path).rstrip('/')
            p = p.lstrip('/')  # 前端路径相对 /api 基址
            fe.add((m.lower(), p))

# ---- 后端：收集全部路由 ----
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.urls import get_resolver

be = set()


def walk(res, prefix=''):
    for p in res.url_patterns:
        if hasattr(p, 'url_patterns'):
            walk(p, prefix + str(p.pattern))
        else:
            route = (prefix + str(p.pattern)).rstrip('/')
            if route.startswith('api/'):
                be.add(re.sub(r'<(?:int|str|slug):[^>]+>', '<p>', route[4:]))


walk(get_resolver())

print('=== 前端调用的接口 (%d 个路径) ===' % len(fe))
missing = []
for m, p in sorted(fe):
    ok = p in be
    print('%-5s /%s%s' % (m.upper(), p, '' if ok else '   <-- 后端无此路由!'))
    if not ok:
        missing.append((m, p))

print()
print('后端路由总数:', len(be))
print('未匹配:', len(missing))
for m, p in missing:
    print('  MISSING', m.upper(), '/' + p)

# ---- 后端先行注册、前端尚未消费的契约路由（SA3 扫菲增强）----
# 出现在下表的路由必须已存在于后端（方向相反的单向核对）。
BACKEND_FIRST = [
    ('get', 'tickets/lookup'),              # SA3 手输码兜底：GET /tickets/lookup
    # B1 工序模板 + 视频上传（2026-10-21 用户 8 项需求 #1/#2，后端先行）
    ('get', 'process-templates'),           # GET  /process-templates
    ('post', 'process-templates'),          # POST /process-templates
    ('get', 'process-templates/<p>'),       # GET  /process-templates/{id}
    ('put', 'process-templates/<p>'),       # PUT  /process-templates/{id}
    ('del', 'process-templates/<p>'),       # DELETE /process-templates/{id}
    ('post', 'uploads/video'),              # POST /uploads/video
]
extra_missing = [(m, p) for m, p in BACKEND_FIRST if p not in be]
print('后端先行路由:', len(BACKEND_FIRST), '个，缺失:', len(extra_missing))
for m, p in extra_missing:
    print('  BACKEND-MISSING', m.upper(), '/' + p)
if extra_missing:
    missing.extend(extra_missing)
