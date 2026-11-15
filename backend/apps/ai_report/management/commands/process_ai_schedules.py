# -*- coding: utf-8 -*-
"""持续处理 AI 定时报表：生产环境由服务管理器托管，不创建裸线程。"""
import time

from django.core.management.base import BaseCommand

from apps.ai_report.schedule_services import process_next_schedule


class Command(BaseCommand):
    help = '处理数据库中的 AI 定时报表任务'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='最多处理一条到期任务后退出')
        parser.add_argument('--poll', type=float, default=2.0, help='空闲轮询秒数')

    def handle(self, *args, **options):
        if options['once']:
            run_id = process_next_schedule()
            self.stdout.write(f'processed={run_id}' if run_id else 'idle')
            return
        self.stdout.write('AI schedule worker started')
        while True:
            run_id = process_next_schedule()
            if run_id:
                self.stdout.write(f'processed={run_id}')
            else:
                time.sleep(max(0.2, options['poll']))
