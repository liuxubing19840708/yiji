# -*- coding: utf-8 -*-
"""在一次性 PostgreSQL 16 实例中运行完整发布回归。

用法：
    python -m pip install -r requirements-test.txt
    python run_postgres_regression.py

安全边界：
- 只使用 pgserver 创建的临时实例和随机本地端口，不读取 DB_* 业务配置；
- Django 只创建/销毁 test_yisao_release_gate；迁移升级只使用
  yisao_migration_gate 独立空库，绝不连接业务数据库；
- 数据目录、PostgreSQL 进程在运行结束后清理；
- Windows 中文路径下复制一份临时 ASCII 运行时，规避 initdb 的编码问题。
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent
TEST_TARGETS = [
    'apps',
    'test_full_functional',
    'test_cross_domain_finance_e2e',
    'test_handover_contract',
    'test_handover_live_http',
    'test_salary_payments',
    'test_scheduled_reports',
    'test_schedule_run_center',
    'test_web_hosting',
    'test_wechat_web_oauth',
    'test_release_regression',
    'test_postgres_concurrency',
    'apps.handover.tests.test_postgres_concurrency',
    'test_tenant_permission_matrix',
    'test_object_tenant_matrix',
    'test_order_change_guard',
    'test_financial_invariants',
    'test_external_service_failures',
    'test_report_performance',
    'test_assertion_strength',
    'test_stats_refactor',
    'test_kanban_link',
    'test_bundle_progress',
    'test_print_manage',
]


def _temporary_ascii_root() -> Path:
    """选择纯 ASCII 的临时父目录；中文 Windows 用户名时退回当前盘根目录。"""
    candidates = [os.environ.get('YISAO_PG_TEST_ROOT'), tempfile.gettempdir(),
                  str(Path(BASE_DIR.anchor))]
    for candidate in candidates:
        if not candidate:
            continue
        parent = Path(candidate)
        if parent.is_dir() and str(parent.resolve()).isascii():
            return Path(tempfile.mkdtemp(prefix='yisao-pg-gate-', dir=parent))
    raise RuntimeError(
        '找不到纯 ASCII 临时目录。请设置 YISAO_PG_TEST_ROOT，例如 D:\\pg-test')


def _remove_tree_with_retry(path: Path) -> None:
    """Windows 的 postgres 日志句柄可能稍晚释放，有限重试后仍失败则如实报错。"""
    last_error = None
    for _ in range(20):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f'临时 PostgreSQL 目录清理失败: {path}') from last_error


def main() -> int:
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
            # initdb 在部分中文 Windows 环境会把安装路径按本地代码页传给 UTF-8
            # bootstrap 进程。pgserver 的 Windows wheel 还可能不带 timezone
            # 目录。只复制并修补临时运行时，不修改虚拟环境中的第三方包。
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
                if not (zoneinfo / 'UTC').is_file():
                    raise RuntimeError('tzdata 缺少 UTC 时区文件，无法启动 Django 测试库')
                shutil.copytree(zoneinfo, timezone_dir)

        pgserver.initdb([
            '--auth=trust', '--auth-local=trust', '--encoding=utf8',
            '--locale=C', '-U', 'postgres',
        ], pgdata=data_dir)
        server = pgserver.get_server(data_dir, cleanup_mode='stop')
        uri = urlsplit(server.get_uri())
        if uri.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise RuntimeError(f'安全保护：临时数据库只能监听本机，实际为 {uri.hostname}')

        env = os.environ.copy()
        # 显式覆盖 .env 中可能存在的生产配置，不读取也不沿用业务数据库名。
        env.update({
            'DB_ENGINE': 'postgres',
            'DB_NAME': 'yisao_release_gate',
            'DB_USER': 'postgres',
            'DB_PASSWORD': '',
            'DB_HOST': str(uri.hostname),
            'DB_PORT': str(uri.port),
            'PYTHONIOENCODING': 'utf-8',
            'PYTHONUNBUFFERED': '1',
        })
        print(f'临时 PostgreSQL 已启动：{uri.hostname}:{uri.port}')
        print('Django 测试库：test_yisao_release_gate（独立空库，结束后销毁）')
        targets = sys.argv[1:] or TEST_TARGETS
        command = [sys.executable, 'manage.py', 'test', *targets, '-v', '2']
        result = subprocess.run(command, cwd=BASE_DIR, env=env, check=False)
        if result.returncode:
            return result.returncode

        # 新建另一个专用空库，避免迁移演练修改全量测试的数据结构。
        server.psql('CREATE DATABASE yisao_migration_gate')
        migration_env = env.copy()
        migration_env['DB_NAME'] = 'yisao_migration_gate'

        print('\n模型迁移完整性检查：makemigrations --check --dry-run')
        result = subprocess.run([
            sys.executable, 'manage.py', 'makemigrations',
            '--check', '--dry-run', '--verbosity', '1',
        ], cwd=BASE_DIR, env=migration_env, check=False)
        if result.returncode:
            return result.returncode

        print('\n历史结构带数据升级检查：yisao_migration_gate（独立空库）')
        result = subprocess.run([
            sys.executable, 'check_migration_upgrade.py',
        ], cwd=BASE_DIR, env=migration_env, check=False)
        return result.returncode
    finally:
        try:
            if server is not None:
                server.cleanup()
        finally:
            _remove_tree_with_retry(temp_root)


if __name__ == '__main__':
    raise SystemExit(main())
