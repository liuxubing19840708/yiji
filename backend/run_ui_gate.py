# -*- coding: utf-8 -*-
"""一次性隔离后端 + 微信开发者工具 automator 发布门禁。

用法（backend 目录、项目 venv）：
    ./.venv/Scripts/python.exe run_ui_gate.py --start-devtools
    ./.venv/Scripts/python.exe run_ui_gate.py ui_scan2.js probe_print_preview.js

安全边界：只监听本机指定端口（默认 8000）；数据库为临时目录中的独立 SQLite；每次新建名称带
``UI自动化隔离厂-`` 的公司和测试账号。严禁复用开发库、共享库或真实厂 86。
结束后关闭后端并删除临时数据库。开发者工具默认由调用者管理，不会强制退出。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
UITEST_DIR = ROOT_DIR / 'uitest'
FRONTEND_DIR = ROOT_DIR / 'frontend'
PHONE = '13900009991'
PASSWORD = 'ui-gate-only'
COMPANY_PREFIX = 'UI自动化隔离厂-'
DEFAULT_SCRIPTS = ['ui_scan2.js', 'probe_print_preview.js']


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def _wait_port(port: int, timeout: float, must_open: bool = True) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open(port) == must_open:
            return
        time.sleep(0.25)
    state = '开放' if must_open else '关闭'
    raise RuntimeError(f'等待本机端口 {port} {state}超时')


def _run(command: list[str], env: dict[str, str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=BASE_DIR, env=env, check=False, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scripts', nargs='*', default=DEFAULT_SCRIPTS,
                        help='uitest 下串行执行的 JS，禁止路径穿越')
    parser.add_argument('--start-devtools', action='store_true',
                        help='通过本机微信开发者工具 CLI 开启 9420 自动化端口')
    parser.add_argument('--port', type=int, default=8000,
                        help='一次性隔离后端监听的本机端口，默认 8000')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise RuntimeError('隔离后端端口必须在 1024~65535 之间')
    if not args.scripts:
        args.scripts = DEFAULT_SCRIPTS
    for name in args.scripts:
        script = (UITEST_DIR / name).resolve()
        if script.parent != UITEST_DIR.resolve() or not script.is_file():
            raise RuntimeError(f'只允许执行 uitest 根目录中已存在的脚本: {name}')

    if _port_open(args.port):
        raise RuntimeError(f'本机 {args.port} 已被占用；隔离门禁不会停止或复用现有后端')
    if not (UITEST_DIR / 'node_modules' / 'miniprogram-automator').is_dir():
        raise RuntimeError('缺少 miniprogram-automator，请先在 uitest 执行 npm ci')

    if args.start_devtools and not _port_open(9420):
        cli = Path(os.environ.get(
            'WECHAT_DEVTOOLS_CLI', r'D:\Program Files\微信web开发者工具\cli.bat'))
        if not cli.is_file():
            raise RuntimeError(f'微信开发者工具 CLI 不存在: {cli}')
        print('启动微信开发者工具自动化（9420）...')
        result = subprocess.run(
            ['cmd.exe', '/c', str(cli), 'auto', '--project', str(FRONTEND_DIR),
             '--auto-port', '9420'],
            cwd=ROOT_DIR, timeout=180, check=False)
        if result.returncode:
            return result.returncode
    _wait_port(9420, 90)

    temp_root = Path(tempfile.mkdtemp(prefix='yisao-ui-gate-'))
    db_path = temp_root / 'isolated.sqlite3'
    env = os.environ.copy()
    env.update({
        'DB_ENGINE': 'sqlite',
        'DB_NAME': str(db_path),
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUNBUFFERED': '1',
    })
    server = None
    try:
        print(f'建立一次性隔离数据库：{db_path}')
        result = _run([sys.executable, 'manage.py', 'migrate', '--noinput', '-v', '0'], env)
        if result.returncode:
            return result.returncode
        bootstrap = (
            'import json;'
            'from apps.accounts.models import User;'
            'from apps.accounts.services import create_company_with_owner;'
            f'u=User.objects.create_user(username="ui-gate-owner",phone="{PHONE}",password="{PASSWORD}");'
            f'c,_=create_company_with_owner(u,"{COMPANY_PREFIX}自动化门禁");'
            'print("UI_GATE_CONFIG="+json.dumps({"company_id":c.id,"company_no":c.company_no}))'
        )
        result = _run([sys.executable, 'manage.py', 'shell', '-c', bootstrap],
                      env, capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            return result.returncode
        lines = [line for line in result.stdout.splitlines()
                 if line.startswith('UI_GATE_CONFIG=')]
        if len(lines) != 1:
            print(result.stdout)
            raise RuntimeError('隔离公司创建结果无法识别，停止启动门禁')
        config = json.loads(lines[0].split('=', 1)[1])
        if config['company_id'] == 86:
            raise RuntimeError('安全保护：隔离公司 ID 异常，禁止继续')

        server_log = (temp_root / 'server.log').open('w', encoding='utf-8')
        server = subprocess.Popen(
            [sys.executable, 'manage.py', 'runserver', f'127.0.0.1:{args.port}', '--noreload'],
            cwd=BASE_DIR, env=env, stdout=server_log, stderr=subprocess.STDOUT)
        _wait_port(args.port, 30)
        if server.poll() is not None:
            raise RuntimeError(f'隔离后端启动失败，日志见 {temp_root / "server.log"}')
        print(f'隔离后端已启动，测试公司 company_id={config["company_id"]}')

        ui_env = env.copy()
        ui_env.update({
            'YISAO_E2E_API_BASE': f'http://127.0.0.1:{args.port}/api',
            'YISAO_E2E_WS_ENDPOINT': 'ws://127.0.0.1:9420',
            'YISAO_E2E_COMPANY_ID': str(config['company_id']),
            'YISAO_E2E_PHONE': PHONE,
            'YISAO_E2E_PASSWORD': PASSWORD,
        })
        failures = []
        for name in args.scripts:
            print(f'\n===== UI 门禁：{name} =====', flush=True)
            result = subprocess.run(['node', name], cwd=UITEST_DIR, env=ui_env,
                                    check=False, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=600)
            print(result.stdout, end='')
            if result.stderr:
                print(result.stderr, end='', file=sys.stderr)
            # SKIP 不是通过：连接失败/页面不可达时绝不能给出绿色发布门禁。
            if result.returncode or 'FAIL ' in result.stdout or 'SKIP=0' not in result.stdout:
                failures.append(name)
        if failures:
            print(f'UI 门禁未通过（含 SKIP 或环境失败）: {failures}', file=sys.stderr)
            return 1
        print('\nUI automator 门禁通过；真机摄像头、蓝牙与真实出纸仍须现场走查。')
        return 0
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
