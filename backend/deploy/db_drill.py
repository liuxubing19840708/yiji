#!/usr/bin/env python3
"""备份时点清单、备份文件校验和恢复库行数验收。

连接参数只取 libpq 标准环境变量（PGHOST/PGPORT/PGDATABASE/PGUSER/
PGPASSWORD），不读取业务 .env，也不打印凭据。backup 在同一个导出的
PostgreSQL REPEATABLE READ 快照内统计行数并运行 pg_dump，避免活跃库
后续写入污染恢复预期。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

import psycopg
from psycopg import sql

FORMAT_VERSION = 1


def _connection(*, read_only=False):
    connection = psycopg.connect(connect_timeout=30)
    if read_only:
        # 这是 psycopg Connection 属性，不是 libpq 连接选项。
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.read_only = True
    return connection


def _table_counts(connection) -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
        tables = [row[0] for row in cursor.fetchall()]
        counts = {}
        for table in tables:
            cursor.execute(sql.SQL('SELECT count(*) FROM {}.{}').format(
                sql.Identifier('public'), sql.Identifier(table)))
            counts[table] = cursor.fetchone()[0]
        return counts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict:
    with path.open(encoding='utf-8') as stream:
        manifest = json.load(stream)
    if manifest.get('format') != 'saofei-backup-counts' or manifest.get('version') != FORMAT_VERSION:
        raise RuntimeError('备份清单格式或版本不支持')
    if not isinstance(manifest.get('table_counts'), dict):
        raise RuntimeError('备份清单缺少有效 table_counts')
    return manifest


def backup(args) -> None:
    dump = args.dump.resolve()
    manifest_path = args.manifest.resolve()
    if dump.exists() or manifest_path.exists():
        raise RuntimeError('拒绝覆盖已存在的备份或清单')
    dump.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    completed = False
    try:
        with _connection(read_only=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_export_snapshot()')
                snapshot = cursor.fetchone()[0]
            counts = _table_counts(connection)
            subprocess.run([
                args.pg_dump, '-Fc', f'--snapshot={snapshot}', '-f', str(dump),
            ], check=True, env=os.environ.copy())
        if not dump.is_file() or dump.stat().st_size == 0:
            raise RuntimeError('pg_dump 未生成有效备份')
        manifest = {
            'format': 'saofei-backup-counts',
            'version': FORMAT_VERSION,
            'snapshot': snapshot,
            'table_counts': counts,
            'dump_bytes': dump.stat().st_size,
            'dump_sha256': _sha256(dump),
        }
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=manifest_path.parent,
                prefix=manifest_path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write('\n')
        os.replace(temporary, manifest_path)
        completed = True
    finally:
        if not completed and dump.exists():
            dump.unlink()
    print(f'备份时点清单已生成：tables={len(counts)} rows={sum(counts.values())}')


def check_dump(args) -> None:
    manifest = _load_manifest(args.manifest)
    dump = args.dump.resolve()
    if not dump.is_file():
        raise RuntimeError('备份文件不存在')
    if dump.stat().st_size != manifest['dump_bytes']:
        raise RuntimeError('备份文件大小与清单不一致')
    if _sha256(dump) != manifest['dump_sha256']:
        raise RuntimeError('备份文件 SHA-256 与清单不一致')
    print('备份文件与清单一一匹配')


def verify(args) -> None:
    expected = _load_manifest(args.manifest)['table_counts']
    with _connection() as connection:
        restored = _table_counts(connection)
    missing = sorted(set(expected) - set(restored))
    unexpected = sorted(set(restored) - set(expected))
    differing = {
        table: {'expected': expected[table], 'restored': restored[table]}
        for table in sorted(set(expected) & set(restored))
        if expected[table] != restored[table]
    }
    if missing or unexpected or differing:
        raise RuntimeError('恢复库与备份时点不一致：' + json.dumps({
            'missing_tables': missing,
            'unexpected_tables': unexpected,
            'row_differences': differing,
        }, ensure_ascii=False))
    print(f'恢复对账通过：tables={len(expected)} rows={sum(expected.values())}')


def media_drill(args) -> None:
    """在临时目录真实解压媒体包，并逐文件比对解压前后 SHA-256。"""
    expected = {}
    with tarfile.open(args.archive, 'r:gz') as archive:
        members = archive.getmembers()
        for member in members:
            path = Path(member.name)
            if (path.is_absolute() or '..' in path.parts or '\\' in member.name
                    or not (member.isfile() or member.isdir())):
                raise RuntimeError(f'媒体包包含不安全或不支持的路径: {member.name}')
            if member.name in expected:
                raise RuntimeError(f'媒体包包含重复文件: {member.name}')
            if member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise RuntimeError(f'无法读取媒体包文件: {member.name}')
                expected[member.name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        with tempfile.TemporaryDirectory(prefix='saofei-media-restore-',
                                         dir=args.work_dir) as temporary:
            destination = Path(temporary)
            archive.extractall(destination, members=members, filter='data')
            restored = {}
            for name in expected:
                path = destination / name
                if not path.is_file():
                    raise RuntimeError(f'媒体恢复后缺少文件: {name}')
                with path.open('rb') as stream:
                    restored[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
            if restored != expected:
                raise RuntimeError('媒体恢复后的文件 SHA-256 与归档不一致')
    print(f'媒体恢复演练通过：files={len(expected)}')


def create_database(args) -> None:
    with _connection() as connection:
        connection.autocommit = True
        connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(
            sql.Identifier(args.name), sql.Identifier(args.owner)))


def drop_database(args) -> None:
    with _connection() as connection:
        connection.autocommit = True
        connection.execute(sql.SQL('DROP DATABASE IF EXISTS {}').format(
            sql.Identifier(args.name)))


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)
    for name in ('backup', 'check-dump'):
        sub = subparsers.add_parser(name)
        sub.add_argument('--dump', type=Path, required=True)
        sub.add_argument('--manifest', type=Path, required=True)
        if name == 'backup':
            # 可用绝对路径覆盖，便于与临时 PostgreSQL 版本配套演练。
            sub.add_argument('--pg-dump', default='pg_dump')
    sub = subparsers.add_parser('verify')
    sub.add_argument('--manifest', type=Path, required=True)
    sub = subparsers.add_parser('create-db')
    sub.add_argument('--name', required=True)
    sub.add_argument('--owner', required=True)
    sub = subparsers.add_parser('drop-db')
    sub.add_argument('--name', required=True)
    sub = subparsers.add_parser('media-drill')
    sub.add_argument('--archive', type=Path, required=True)
    sub.add_argument('--work-dir', type=Path)
    args = parser.parse_args()
    {'backup': backup, 'check-dump': check_dump, 'verify': verify,
     'create-db': create_database, 'drop-db': drop_database,
     'media-drill': media_drill}[args.command](args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
