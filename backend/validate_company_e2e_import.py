# -*- coding: utf-8 -*-
"""在一次性 SQLite 空库验证真实厂导入器的回滚、备份、提交和清单。

只创建临时目录和临时数据库，不读取或写入默认开发库。
运行：PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe validate_company_e2e_import.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

BASE_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable

FIXTURE = r'''
from apps.accounts.models import Company, Post, User, UserCompany
admin = User.objects.create_user(
    username='13800000001', phone='13800000001', password='isolated-only',
    real_name='隔离管理员')
company = Company.objects.create(
    id=86, name='汉川旭东服饰', company_no='TEST86', creator=admin)
post = Post.objects.create(
    company=company, name='管理员', permissions=['*'], is_admin=True, is_system=True)
UserCompany.objects.create(
    user=admin, company=company, post=post, status=UserCompany.Status.ACTIVE)
for phone, name in [('13988720902', 'E2E员工一'), ('13988720903', 'E2E员工二')]:
    User.objects.create_user(username=phone, phone=phone, password='isolated-only', real_name=name)
print('FIXTURE_OK')
'''

ROLLBACK = r'''
import import_company_e2e as importer
from apps.accounts.models import UserCompany
from apps.catalog.models import Ticket
from apps.orders.models import Order
from apps.records.models import Record
original = importer.import_chain
def fail_after_chain(*args, **kwargs):
    original(*args, **kwargs)
    raise importer.ImportFailure('隔离演练：故意在全链路完成后失败')
importer.import_chain = fail_after_chain
try:
    importer.main(['--company-id', '86', '--company-name', '汉川旭东服饰', '--execute'])
except importer.ImportFailure as exc:
    assert '故意' in str(exc), exc
else:
    raise AssertionError('故意失败没有触发')
assert Order.all_objects.filter(company_id=86).count() == 0
assert Ticket.all_objects.filter(company_id=86).count() == 0
assert Record.all_objects.filter(company_id=86).count() == 0
assert UserCompany.all_objects.filter(company_id=86, post__is_admin=False).count() == 0
print('ROLLBACK_OK orders=0 tickets=0 records=0 worker_memberships=0')
'''

VERIFY = r'''
import json
from pathlib import Path
from django.conf import settings
from apps.finance.models import Receivable
from apps.orders.models import Order
from apps.production.models import Production
from apps.records.models import Record
from apps.salary.models import Settlement
order = Order.all_objects.get(company_id=86, order_no__startswith='XD86-')
production = Production.all_objects.get(company_id=86, order=order)
assert order.items.count() == 3
assert production.tickets.count() == 8
assert Record.all_objects.filter(company_id=86, ticket__production=production).count() == 20
ar = Receivable.all_objects.get(company_id=86, order=order)
assert ar.status == 'settled' and str(ar.received_amount) == '3520.00'
assert Settlement.all_objects.filter(company_id=86).count() == 1
manifests = list(Path(settings.DATABASES['default']['NAME']).parent.glob('*.manifest.json'))
assert len(manifests) == 1, manifests
manifest = json.loads(manifests[0].read_text(encoding='utf-8'))
assert manifest['status'] == 'committed'
assert manifest['company']['id'] == 86
assert manifest['created_object_count'] == len(manifest['created_objects'])
assert manifest['main_ids']['order'] == order.id
assert len(manifest['main_ids']['tickets']) == 8
assert Path(manifest['backup']).is_file()
print('VERIFY_OK created_objects={} order_id={} receivable={} backup={}'.format(
    manifest['created_object_count'], order.id, ar.status, Path(manifest['backup']).name))
'''


def run(command, env, *, allow_failure=False):
    result = subprocess.run(
        command, cwd=BASE_DIR, env=env, check=False,
        text=True, encoding='utf-8', capture_output=True)
    if result.stdout:
        print(result.stdout, end='')
    if result.stderr:
        print(result.stderr, end='', file=sys.stderr)
    if result.returncode and not allow_failure:
        raise RuntimeError(f'命令失败({result.returncode}): {command}')
    return result


def main():
    with tempfile.TemporaryDirectory(prefix='yisao-company-e2e-import-') as temp:
        env = os.environ.copy()
        env.update({
            'DB_ENGINE': 'sqlite', 'DB_NAME': str(Path(temp) / 'test.sqlite3'),
            'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1',
        })
        run([PYTHON, 'manage.py', 'migrate', '--noinput', '-v', '0'], env)
        run([PYTHON, 'manage.py', 'shell', '-c', FIXTURE], env)
        run([PYTHON, 'manage.py', 'shell', '-c', ROLLBACK], env)
        run([PYTHON, 'import_company_e2e.py', '--company-id', '86',
             '--company-name', '汉川旭东服饰', '--execute'], env)
        run([PYTHON, 'manage.py', 'shell', '-c', VERIFY], env)
        run([PYTHON, 'manage.py', 'check'], env)
    print('ISOLATED_IMPORT_VALIDATION_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
