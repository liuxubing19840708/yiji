# -*- coding: utf-8 -*-
"""出货数据集：已审核口径、主单汇总、防连接放大、权限与公司隔离。"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer, Style
from apps.orders.models import Order
from apps.shipment.models import PackingBox, Shipment, ShipmentItem


class ShipmentDatasetApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.yesterday = cls.today - timedelta(days=1)
        cls.owner = User.objects.create_user(
            username='ship-report-owner', phone='13900008301', password='test-only')
        cls.company, _ = create_company_with_owner(cls.owner, '出货报表A厂')
        viewer_post = Post.objects.create(
            company=cls.company, name='出货查看岗', permissions=['shipment.view'])
        plain_post = Post.objects.create(company=cls.company, name='出货普工', permissions=[])
        cls.viewer = User.objects.create_user(
            username='ship-report-viewer', phone='13900008302', password='test-only')
        cls.worker = User.objects.create_user(
            username='ship-report-worker', phone='13900008303', password='test-only')
        UserCompany.objects.create(
            user=cls.viewer, company=cls.company, post=viewer_post,
            status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(
            user=cls.worker, company=cls.company, post=plain_post,
            status=UserCompany.Status.ACTIVE)
        customer = Customer.objects.create(
            company=cls.company, customer_no='SH-C1', name='出货客户')
        style = Style.objects.create(
            company=cls.company, style_no='SH-S1', name='出货款', customer=customer)
        order = Order.objects.create(
            company=cls.company, order_no='SH-O1', style=style, customer=customer)

        def shipment(number, status, qty, boxes, ship_date, deleted=False):
            return Shipment.objects.create(
                company=cls.company, order=order, customer=customer,
                shipment_no=number, status=status, total_qty=qty, boxes=boxes,
                ship_date=ship_date, is_deleted=deleted)

        confirmed = shipment('SH-1', Shipment.Status.CONFIRMED, 120, 2, cls.today)
        partial = shipment('SH-2', Shipment.Status.PARTIAL, 80, 1, cls.today)
        draft = shipment('SH-3', Shipment.Status.DRAFT, 50, 4, cls.today)
        old = shipment('SH-4', Shipment.Status.CONFIRMED, 30, 1, cls.yesterday)
        deleted = shipment('SH-DEL', Shipment.Status.CONFIRMED, 500, 9, cls.today, True)
        ShipmentItem.objects.bulk_create([
            ShipmentItem(shipment=confirmed, color='黑色', size='M', qty=70),
            ShipmentItem(shipment=confirmed, color='黑色', size='L', qty=50),
            ShipmentItem(shipment=partial, color='黑色', size='M', qty=80),
            ShipmentItem(shipment=draft, color='黑色', size='M', qty=50),
            ShipmentItem(shipment=old, color='黑色', size='M', qty=30),
            ShipmentItem(shipment=deleted, color='黑色', size='M', qty=500),
        ])
        PackingBox.objects.bulk_create([
            PackingBox(shipment=confirmed, box_no='1', color='黑色', size='M', qty=70),
            PackingBox(shipment=confirmed, box_no='2', color='黑色', size='L', qty=50),
            PackingBox(shipment=partial, box_no='1', color='黑色', size='M', qty=80),
        ])

        other_owner = User.objects.create_user(
            username='ship-report-other', phone='13900008304', password='test-only')
        other_company, _ = create_company_with_owner(other_owner, '出货报表B厂')
        other_style = Style.objects.create(company=other_company, style_no='SH-B-S1')
        other_order = Order.objects.create(
            company=other_company, order_no='SH-B-O1', style=other_style)
        Shipment.objects.create(
            company=other_company, order=other_order, shipment_no='SH-B-1',
            status=Shipment.Status.CONFIRMED, total_qty=999, boxes=9,
            ship_date=cls.today)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        return client

    @staticmethod
    def plan(**patch):
        value = {
            'dataset': 'shipments',
            'metrics': [
                {'key': 'shipment_count', 'aggregation': 'count'},
                {'key': 'audited_shipment_count', 'aggregation': 'count'},
                {'key': 'shipped_qty', 'aggregation': 'sum'},
                {'key': 'document_qty', 'aggregation': 'sum'},
                {'key': 'boxes', 'aggregation': 'sum'},
            ],
            'dimensions': [],
            'filters': [],
            'order_by': [],
            'limit': 100,
            'include_deleted': False,
            'presentation': 'table',
        }
        value.update(patch)
        return value

    @staticmethod
    def rows(response):
        keys = [column['key'] for column in response.data['report']['columns']]
        return [dict(zip(keys, row)) for row in response.data['report']['rows']]

    def execute(self, user, plan, expected=200):
        response = self.client_for(user).post(
            '/api/ai/report/execute', {'plan': plan}, format='json')
        self.assertEqual(response.status_code, expected, response.content)
        return response

    def test_actual_shipments_exclude_drafts_and_related_rows_do_not_fanout(self):
        response = self.execute(self.viewer, self.plan())
        self.assertEqual(self.rows(response), [{
            'shipment_count': 4, 'audited_shipment_count': 3,
            'shipped_qty': 230, 'document_qty': 280, 'boxes': 8,
        }])
        response = self.execute(self.viewer, self.plan(filters=[{
            'field': 'ship_date', 'operator': 'between',
            'value': [self.today.isoformat(), self.today.isoformat()],
        }]))
        self.assertEqual(self.rows(response), [{
            'shipment_count': 3, 'audited_shipment_count': 2,
            'shipped_qty': 200, 'document_qty': 250, 'boxes': 7,
        }])

    def test_status_groups_make_draft_quantity_explicit(self):
        response = self.execute(self.viewer, self.plan(
            dimensions=['status'],
            filters=[{'field': 'ship_date', 'operator': 'between',
                      'value': [self.today.isoformat(), self.today.isoformat()]}],
            order_by=[{'field': 'status', 'direction': 'asc'}]))
        self.assertEqual(self.rows(response), [
            {'status': 'confirmed', 'shipment_count': 1, 'audited_shipment_count': 1,
             'shipped_qty': 120, 'document_qty': 120, 'boxes': 2},
            {'status': 'draft', 'shipment_count': 1, 'audited_shipment_count': 0,
             'shipped_qty': 0, 'document_qty': 50, 'boxes': 4},
            {'status': 'partial', 'shipment_count': 1, 'audited_shipment_count': 1,
             'shipped_qty': 80, 'document_qty': 80, 'boxes': 1},
        ])
        choices = response.data['report']['columns'][0]['choices']
        self.assertIn({'value': 'draft', 'label': '草稿'}, choices)

    def test_soft_deleted_shipments_require_full_data_permission(self):
        plan = self.plan(
            include_deleted=True,
            filters=[{'field': 'ship_date', 'operator': 'between',
                      'value': [self.today.isoformat(), self.today.isoformat()]}])
        response = self.execute(self.owner, plan)
        self.assertEqual(self.rows(response), [{
            'shipment_count': 4, 'audited_shipment_count': 3,
            'shipped_qty': 700, 'document_qty': 750, 'boxes': 16,
        }])
        self.execute(self.viewer, plan, expected=403)

    def test_natural_actual_shipment_question_uses_audited_metric(self):
        response = self.client_for(self.viewer).post('/api/ai/report/query', {
            'question': '今天实际出货多少件？', 'history': [], 'mode': 'query_plan',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['planner']['source'], 'local')
        self.assertEqual(response.data['plan']['metrics'], [{
            'key': 'shipped_qty', 'aggregation': 'sum',
        }])
        self.assertEqual(self.rows(response), [{'shipped_qty': 200}])

    def test_shipment_view_permission_and_company_scope_are_enforced(self):
        viewer_catalog = self.client_for(self.viewer).get('/api/ai/report/catalog')
        worker_catalog = self.client_for(self.worker).get('/api/ai/report/catalog')
        self.assertIn('shipments', {item['key'] for item in viewer_catalog.data['datasets']})
        self.assertNotIn('shipments', {item['key'] for item in worker_catalog.data['datasets']})
        self.execute(self.worker, self.plan(), expected=403)
        self.execute(self.viewer, self.plan(filters=[{
            'field': 'status', 'operator': 'eq', 'value': 'not-a-status',
        }]), expected=400)
