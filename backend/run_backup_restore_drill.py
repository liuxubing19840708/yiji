# -*- coding: utf-8 -*-
"""真实 pg_dump/pg_restore 隔离演练：备份时点后源库变化不得污染恢复预期。

只创建临时 PostgreSQL 实例、临时数据库和临时 dump，不读取 DB_* 业务配置。
运行：./.venv/Scripts/python.exe run_backup_restore_drill.py
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from run_postgres_regression import _remove_tree_with_retry, _temporary_ascii_root

BASE_DIR = Path(__file__).resolve().parent
SOURCE_DB = 'yisao_drill_source'
TARGET_DB = 'yisao_drill_target'

FIXTURE = r'''
from apps.accounts.models import User
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, Ticket
from apps.finance.models import Receivable
from apps.orders.models import Order, OrderItem
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record
from django.utils import timezone
owner = User.objects.create_user(
    username='restore-drill-owner', phone='13900007501', password='test-only')
company, _ = create_company_with_owner(owner, '备份恢复隔离厂')
style = Style.objects.create(company=company, style_no='DRILL-S1', name='恢复演练款')
order = Order.objects.create(
    company=company, order_no='DRILL-ORDER', style=style, remark='AT_BACKUP')
OrderItem.objects.create(order=order, color='黑', size='M', qty=100, price='8.000')
production = Production.objects.create(
    company=company, style=style, order=order, bed_no='DRILL-BED', total_qty=100)
process = Process.objects.create(
    company=company, name='DRILL-车缝', default_price='2.500', is_fixed=True)
ProductionProcess.objects.create(production=production, process=process, price='2.500')
ticket = Ticket.objects.create(
    company=company, production=production, bed_no=production.bed_no,
    bundle_no='1', color='黑', size='M', qty=100, qr_code='DRILL-QR-1')
Record.objects.create(
    company=company, ticket=ticket, process=process, user=owner,
    qty=60, price='2.500', registered_at=timezone.now())
Receivable.objects.create(
    company=company, receivable_no='DRILL-AR', order=order,
    amount='800.00', received_amount='200.00', status='partial')
obsolete = Process.objects.create(company=company, name='DRILL-软删工序')
obsolete.delete()
print('DRILL_FIXTURE_OK')
'''

SNAPSHOT = r'''
import json
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from apps.accounts.models import Company
from apps.catalog.models import Process
from apps.orders.models import Order, OrderItem
from apps.records.models import Record
with connection.cursor() as cursor:
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
    tables = [row[0] for row in cursor.fetchall()]
    counts = {}
    for table in tables:
        cursor.execute('SELECT count(*) FROM "' + table.replace('"', '""') + '"')
        counts[table] = cursor.fetchone()[0]
order = Order.all_objects.get(order_no='DRILL-ORDER')
executor = MigrationExecutor(connection)
unapplied = executor.migration_plan(executor.loader.graph.leaf_nodes())
result = {
    'table_counts': counts,
    'company_name': Company.all_objects.get(orders__id=order.id).name,
    'order_remark': order.remark,
    'order_qty': OrderItem.all_objects.get(order=order).qty,
    'record_qty': Record.all_objects.get(ticket__production__order=order).qty,
    'receivable_amount': str(order.finance_receivables.get().amount),
    'received_amount': str(order.finance_receivables.get().received_amount),
    'soft_deleted_processes': Process.all_objects.filter(is_deleted=True).count(),
    'unapplied_migrations': len(unapplied),
    'installed_models': apps.get_models().__len__(),
}
print('DRILL_SNAPSHOT=' + json.dumps(result, ensure_ascii=False, sort_keys=True))
'''

MUTATE_AFTER_BACKUP = r'''
from apps.catalog.models import Customer
from apps.orders.models import Order, OrderItem
order = Order.objects.get(order_no='DRILL-ORDER')
order.remark = 'AFTER_BACKUP'
order.save(update_fields=['remark', 'updated_at'])
OrderItem.objects.filter(order=order).update(qty=999)
Customer.objects.create(company=order.company, customer_no='AFTER-BACKUP', name='备份后新增')
print('DRILL_SOURCE_MUTATED')
'''


def _run(command, *, env=None):
    return subprocess.run(command, cwd=BASE_DIR, env=env, check=True,
                          text=True, encoding='utf-8', capture_output=True)


def _django_eval(code, env):
    result = _run([sys.executable, 'manage.py', 'shell', '-c', code], env=env)
    return result.stdout


def _snapshot(env):
    output = _django_eval(SNAPSHOT, env)
    line = next(line for line in output.splitlines() if line.startswith('DRILL_SNAPSHOT='))
    return json.loads(line.removeprefix('DRILL_SNAPSHOT='))


def main():
    try:
        import pgserver
        import pgserver._commands as pg_commands
        import pgserver.postgres_server as pg_server_module
    except ImportError:
        print('缺少测试依赖，请先执行: python -m pip install -r requirements-test.txt')
        return 2

    temp_root = _temporary_ascii_root()
    data_dir = temp_root / 'data'
    data_dir.mkdir()
    server = None
    try:
        bin_path = Path(pg_commands.POSTGRES_BIN_PATH)
        timezone_dir = bin_path.parent / 'share' / 'postgresql' / 'timezone'
        if not str(bin_path).isascii() or not (timezone_dir / 'UTC').is_file():
            runtime = temp_root / 'runtime'
            shutil.copytree(bin_path.parent, runtime)
            bin_path = runtime / 'bin'
            pg_commands.POSTGRES_BIN_PATH = bin_path
            pg_server_module.POSTGRES_BIN_PATH = bin_path
            pgserver.POSTGRES_BIN_PATH = bin_path
            timezone_dir = runtime / 'share' / 'postgresql' / 'timezone'
            if not (timezone_dir / 'UTC').is_file():
                import tzdata
                zoneinfo = Path(next(iter(tzdata.__path__))) / 'zoneinfo'
                shutil.copytree(zoneinfo, timezone_dir)

        pgserver.initdb([
            '--auth=trust', '--auth-local=trust', '--encoding=utf8',
            '--locale=C', '-U', 'postgres',
        ], pgdata=data_dir)
        server = pgserver.get_server(data_dir, cleanup_mode='stop')
        from urllib.parse import urlsplit
        uri = urlsplit(server.get_uri())
        if uri.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise RuntimeError(f'安全保护：临时数据库只能监听本机，实际为 {uri.hostname}')
        common_env = os.environ.copy()
        common_env.update({
            'DB_ENGINE': 'postgres', 'DB_USER': 'postgres', 'DB_PASSWORD': '',
            'DB_HOST': str(uri.hostname), 'DB_PORT': str(uri.port),
            'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1',
        })
        source_env = {**common_env, 'DB_NAME': SOURCE_DB}
        target_env = {**common_env, 'DB_NAME': TARGET_DB}
        server.psql(f'CREATE DATABASE {SOURCE_DB}')
        _run([sys.executable, 'manage.py', 'migrate', '--noinput', '-v', '0'],
             env=source_env)
        fixture_output = _django_eval(FIXTURE, source_env)
        if 'DRILL_FIXTURE_OK' not in fixture_output:
            raise RuntimeError('演练数据创建失败')
        expected = _snapshot(source_env)
        if expected['unapplied_migrations'] != 0:
            raise RuntimeError('源库存在未执行迁移，不能作为恢复基线')

        dump_dir = temp_root / 'backups'
        media_dir = temp_root / 'media'
        (media_dir / 'labels').mkdir(parents=True)
        (media_dir / 'labels' / '菲票标签.txt').write_text('备份时的标签内容', encoding='utf-8')
        (media_dir / 'labels' / 'empty.bin').write_bytes(b'')
        expected_media = {
            path.relative_to(media_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in media_dir.rglob('*') if path.is_file()
        }
        bash = shutil.which('bash')
        if not bash:
            raise RuntimeError('恢复演练需要 Bash；Linux 生产环境默认具备')
        script_env = {
            **source_env,
            'APP_DIR': str(BASE_DIR),
            'BACKUP_DIR': str(dump_dir),
            'MEDIA_DIR': str(media_dir),
            'PYTHON_BIN': sys.executable,
            'PG_DUMP_BIN': str(bin_path / 'pg_dump'),
        }
        for script_name in ('backup.sh', 'restore_drill.sh'):
            _run([bash, '-n', str(BASE_DIR / 'deploy' / script_name)])
        # 直接执行生产备份脚本，验证清单和 dump 来自同一个导出快照。
        _run([bash, str(BASE_DIR / 'deploy' / 'backup.sh')], env=script_env)
        dumps = list(dump_dir.glob('db-*.dump'))
        if len(dumps) != 1 or dumps[0].stat().st_size == 0:
            raise RuntimeError('生产备份脚本未生成唯一有效备份')
        dump_path = dumps[0]
        manifest_path = dump_dir / (dump_path.stem + '.counts.json')
        if not manifest_path.is_file():
            raise RuntimeError('生产备份脚本未生成备份时点清单')
        media_archives = list(dump_dir.glob('media-*.tar.gz'))
        if len(media_archives) != 1:
            raise RuntimeError('生产备份脚本未生成唯一媒体归档')
        media_archive = media_archives[0]
        with tarfile.open(media_archive, 'r:gz') as archive:
            archived_media = {}
            for member in archive.getmembers():
                if member.isfile() and member.name.startswith('media/'):
                    stream = archive.extractfile(member)
                    archived_media[member.name.removeprefix('media/')] = (
                        hashlib.file_digest(stream, 'sha256').hexdigest())
        if archived_media != expected_media:
            raise RuntimeError('媒体归档未完整保存备份时点文件')

        # 备份后的写入只留在源库；恢复验收必须对备份快照，而非当前源库。
        _django_eval(MUTATE_AFTER_BACKUP, source_env)
        (media_dir / 'labels' / '菲票标签.txt').write_text('备份后修改', encoding='utf-8')
        (media_dir / 'labels' / 'after-backup.txt').write_text('不应进入备份', encoding='utf-8')
        changed_source = _snapshot(source_env)
        if changed_source == expected:
            raise RuntimeError('源库变更未生效，演练无法证明备份时点隔离')
        restore_env = {
            **common_env,
            'APP_DIR': str(BASE_DIR),
            'PYTHON_BIN': sys.executable,
            'PG_RESTORE_BIN': str(bin_path / 'pg_restore'),
            'DRILL_DB': TARGET_DB,
            # 仅自动演练保留临时库，供下面 ORM 深度验收；生产脚本默认清理。
            'KEEP_DRILL_DB': '1',
        }
        _run([bash, str(BASE_DIR / 'deploy' / 'restore_drill.sh'), str(dump_path),
              str(manifest_path), str(media_archive)], env=restore_env)
        restored = _snapshot(target_env)

        if restored != expected:
            differing = {
                key: {'expected': expected.get(key), 'restored': restored.get(key)}
                for key in sorted(set(expected) | set(restored))
                if expected.get(key) != restored.get(key)
            }
            raise RuntimeError('恢复快照不一致: ' + json.dumps(differing, ensure_ascii=False))
        _run([sys.executable, 'manage.py', 'check'], env=target_env)

        # dump 与清单不配套时，必须在创建演练库之前失败。
        tampered_dump = dump_dir / 'tampered.dump'
        shutil.copyfile(dump_path, tampered_dump)
        with tampered_dump.open('ab') as stream:
            stream.write(b'not-in-backup')
        tampered_manifest = dump_dir / 'tampered.counts.json'
        shutil.copyfile(manifest_path, tampered_manifest)
        rejected = subprocess.run([
            bash, str(BASE_DIR / 'deploy' / 'restore_drill.sh'), str(tampered_dump),
        ], cwd=BASE_DIR, env={**restore_env, 'DRILL_DB': 'yisao_drill_rejected'},
           check=False, text=True, encoding='utf-8', capture_output=True)
        if rejected.returncode == 0 or '清单不一致' not in rejected.stderr:
            raise RuntimeError('篡改 dump 未被备份清单拒绝: ' + rejected.stdout + rejected.stderr)

        # 恶意媒体路径不得写出临时恢复目录，失败后也必须清理数据库。
        unsafe_archive = dump_dir / 'unsafe-media.tar.gz'
        import io
        with tarfile.open(unsafe_archive, 'w:gz') as archive:
            payload = b'must-not-escape'
            member = tarfile.TarInfo('media/../../escaped.txt')
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        media_rejected = subprocess.run([
            bash, str(BASE_DIR / 'deploy' / 'restore_drill.sh'), str(dump_path),
            str(manifest_path), str(unsafe_archive),
        ], cwd=BASE_DIR, env={**restore_env, 'DRILL_DB': 'yisao_drill_media_rejected',
                              'KEEP_DRILL_DB': '0'},
           check=False, text=True, encoding='utf-8', capture_output=True)
        if (media_rejected.returncode == 0 or '不安全' not in media_rejected.stderr
                or (temp_root / 'escaped.txt').exists()):
            raise RuntimeError('恶意媒体路径未被安全拒绝: '
                               + media_rejected.stdout + media_rejected.stderr)

        # 再跑一次默认模式，确认成功恢复后临时库自动清理。
        cleanup_env = {**restore_env, 'DRILL_DB': 'yisao_drill_cleanup',
                       'KEEP_DRILL_DB': '0'}
        _run([bash, str(BASE_DIR / 'deploy' / 'restore_drill.sh'), str(dump_path)],
             env=cleanup_env)
        remaining = server.psql(
            "SELECT datname FROM pg_database WHERE datname IN "
            "('yisao_drill_rejected', 'yisao_drill_media_rejected', 'yisao_drill_cleanup')")
        if any(name in remaining for name in (
                'yisao_drill_rejected', 'yisao_drill_media_rejected', 'yisao_drill_cleanup')):
            raise RuntimeError('拒绝恢复或默认恢复后残留演练库')
        print(f'RESTORE_DRILL_OK tables={len(expected["table_counts"])} '
              f'rows={sum(expected["table_counts"].values())} '
              f'dump_bytes={dump_path.stat().st_size} '
              f'media_files={len(expected_media)}')
        print('备份后源库及媒体修改未进入恢复结果；数据库业务字段、迁移状态、媒体 SHA-256 全部守恒。')
        return 0
    except (subprocess.CalledProcessError, RuntimeError, StopIteration) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            print(f'命令失败: {exc.cmd}\n{exc.stdout}\n{exc.stderr}', file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return 1
    finally:
        try:
            if server is not None:
                server.cleanup()
        finally:
            _remove_tree_with_retry(temp_root)


if __name__ == '__main__':
    raise SystemExit(main())
