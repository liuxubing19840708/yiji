"""发送裁片交接超时站内通知；建议每 5~15 分钟由计划任务执行。"""
from django.core.management.base import BaseCommand

from apps.handover.timeout_services import notify_handover_timeouts


class Command(BaseCommand):
    help = '扫描 warn/strict 待接收交接单，按交接单+接收人+自然日发送去重超时通知'

    def add_arguments(self, parser):
        parser.add_argument('--noinput', action='store_true',
                            help='无交互运行（供计划任务使用）')

    def handle(self, *args, **options):
        result = notify_handover_timeouts()
        self.stdout.write(
            f'==== 交接超时提醒完成：扫描 {result["scanned"]} 单，'
            f'新通知 {result["sent"]} 人，跳过 {result["skipped"]} 单 ====')
