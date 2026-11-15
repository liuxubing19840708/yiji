# -*- coding: utf-8 -*-
"""大数据量报表基线：结果守恒 + SQL 次数有界，不用易抖动的毫秒硬门禁。"""
import time

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Process, Style, StyleProcess, Ticket
from apps.orders.models import Order, OrderItem
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record


class LargeDatasetReportPerformanceTests(TestCase):
    """120 款/单/床/扎、240 条登记；分页首屏 100 行不得产生逐行 SQL。"""

    SCALE = 120
    MAX_QUERIES = {
        'orders': 12,
        'productions': 15,
        'styles': 15,
        'dashboard': 30,
    }

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='report-perf-owner', phone='13900007401', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '大数据报表隔离厂')
        cls.processes = Process.objects.bulk_create([
            Process(company=cls.company, name='PERF-车缝', default_price='1.000'),
            Process(company=cls.company, name='PERF-包装', default_price='2.000'),
        ])
        cls.styles = Style.objects.bulk_create([
            Style(company=cls.company, style_no=f'PERF-S{i:03}', name=f'性能款{i}')
            for i in range(cls.SCALE)
        ])
        orders = Order.objects.bulk_create([
            Order(company=cls.company, order_no=f'PERF-O{i:03}', style=style,
                  delivery_date=timezone.localdate())
            for i, style in enumerate(cls.styles)
        ])
        OrderItem.objects.bulk_create([
            OrderItem(order=order, color='黑', size='M', qty=10, price='5.000')
            for order in orders
        ])
        productions = Production.objects.bulk_create([
            Production(company=cls.company, style=style, order=order,
                       bed_no=f'PERF-B{i:03}', total_qty=10, plan_qty=10)
            for i, (style, order) in enumerate(zip(cls.styles, orders))
        ])
        StyleProcess.objects.bulk_create([
            StyleProcess(style=style, process=process, price=process.default_price,
                         sort=process_index)
            for style in cls.styles
            for process_index, process in enumerate(cls.processes)
        ])
        ProductionProcess.objects.bulk_create([
            ProductionProcess(production=production, process=process,
                              price=process.default_price)
            for production in productions
            for process in cls.processes
        ])
        tickets = Ticket.objects.bulk_create([
            Ticket(company=cls.company, production=production,
                   bed_no=production.bed_no, bundle_no='1', color='黑', size='M',
                   qty=10, qr_code=f'PERF-QR-{i:03}')
            for i, production in enumerate(productions)
        ])
        now = timezone.now()
        Record.objects.bulk_create([
            Record(company=cls.company, ticket=ticket, process=process,
                   user=cls.owner, qty=3, price=process.default_price,
                   registered_at=now)
            for ticket in tickets
            for process in cls.processes
        ])

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.client.credentials(HTTP_X_COMPANY_ID=str(self.company.pk))

    def test_large_dataset_reports_are_correct_and_query_bounded(self):
        endpoints = {
            'orders': '/api/orders?page_size=100',
            'productions': '/api/productions?page_size=100',
            'styles': '/api/styles?page_size=100',
            'dashboard': '/api/dashboard?range=month',
        }
        measurements = {}
        for name, path in endpoints.items():
            with self.subTest(endpoint=name):
                started = time.perf_counter()
                with CaptureQueriesContext(connection) as captured:
                    response = self.client.get(path)
                elapsed_ms = (time.perf_counter() - started) * 1000
                measurements[name] = len(captured)
                print(f'[性能基线] {name}: {len(captured)} SQL, {elapsed_ms:.1f} ms, '
                      f'scale={self.SCALE}')
                self.assertEqual(response.status_code, 200, response.content)
                data = response.data
                if name == 'dashboard':
                    self.assertEqual(data['summary']['registered_qty'], self.SCALE * 6)
                    self.assertEqual(data['summary']['done_qty'], self.SCALE * 6)
                    self.assertEqual(data['summary']['working_qty'], 0)
                    self.assertEqual(data['summary']['ticket_count'], self.SCALE)
                    self.assertEqual(data['summary']['order_total'], self.SCALE * 10)
                    self.assertEqual(sum(row['qty'] for row in data['daily']),
                                     self.SCALE * 6)
                else:
                    self.assertEqual(data['count'], self.SCALE)
                    self.assertEqual(len(data['results']), 100)
                    if name == 'orders':
                        self.assertEqual(sum(row['total_qty'] for row in data['results']), 1000)
                        self.assertEqual(sum(row['registered_qty']
                                             for row in data['results']), 600)
                    elif name == 'productions':
                        self.assertEqual(sum(row['ticket_count']
                                             for row in data['results']), 100)
                        self.assertEqual(sum(row['registered_qty']
                                             for row in data['results']), 600)
                        self.assertEqual(sum(row['done_ticket_count']
                                             for row in data['results']), 100)
                    elif name == 'styles':
                        self.assertEqual(sum(row['cut_total'] for row in data['results']), 1000)
                        self.assertEqual(sum(row['process_count']
                                             for row in data['results']), 200)
                        self.assertEqual(sum(row['done_ticket_count']
                                             for row in data['results']), 100)
        for name, query_count in measurements.items():
            self.assertLessEqual(
                query_count, self.MAX_QUERIES[name],
                f'{name} 出现逐行查询或查询回退：{query_count} SQL；'
                f'上限 {self.MAX_QUERIES[name]}')
