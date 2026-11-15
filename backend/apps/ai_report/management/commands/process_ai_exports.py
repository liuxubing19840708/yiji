# -*- coding: utf-8 -*-
"""持续处理 AI 异步导出：生产环境由服务管理器托管本命令。"""
import time

from django.core.management.base import BaseCommand

from apps.ai_report.export_services import expire_old_exports, process_next_export


class Command(BaseCommand):
    help = '处理数据库中的 AI 异步导出任务'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='最多处理一条任务后退出')
        parser.add_argument('--poll', type=float, default=2.0, help='空闲轮询秒数')

    def handle(self, *args, **options):
        expire_old_exports()
        if options['once']:
            task_id = process_next_export()
            self.stdout.write(f'processed={task_id}' if task_id else 'idle')
            return
        self.stdout.write('AI export worker started')
        while True:
            task_id = process_next_export()
            if task_id:
                self.stdout.write(f'processed={task_id}')
            else:
                expire_old_exports()
                time.sleep(max(0.2, options['poll']))
