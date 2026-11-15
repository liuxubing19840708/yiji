# -*- coding: utf-8 -*-
"""出货打印只使用隔离测试数据，并验证生成的是完整动态 PDF。"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Customer
from apps.orders.models import Order
from apps.shipment.models import PackingBox, Shipment, ShipmentItem
from apps.shipment.printing import render_shipment_pdf


class ShipmentPrintingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user(
            username='shipment-print-test', phone='13800009991', password='test-only')
        cls.company = Company.objects.create(
            name='打印隔离测试公司', company_no='989901', creator=user)
        customer = Customer.objects.create(
            company=cls.company, customer_no='PRINT-C', name='中文测试客户')
        order = Order.objects.create(
            company=cls.company, order_no='PRINT-ORDER', customer=customer)
        cls.shipment = Shipment.objects.create(
            company=cls.company, order=order, customer=customer,
            shipment_no='PRINT-SHIPMENT', total_qty=12, boxes=1,
            remark='中文备注与特殊字符 <>&')
        ShipmentItem.objects.create(
            shipment=cls.shipment, color='中国红', size='加大码', qty=12)
        PackingBox.objects.create(
            shipment=cls.shipment, box_no='箱-001', color='中国红', size='加大码',
            qty=12, gross_wt='8.50', net_wt='7.80', box_spec='60×40×40厘米')

    def test_shipment_and_packing_are_real_distinct_chinese_pdfs(self):
        shipment_pdf = render_shipment_pdf(self.shipment, 'shipment')
        packing_pdf = render_shipment_pdf(self.shipment, 'packing')
        for content in (shipment_pdf, packing_pdf):
            self.assertTrue(content.startswith(b'%PDF-'))
            self.assertTrue(content.rstrip().endswith(b'%%EOF'))
            self.assertGreater(len(content), 2000)
            # STSong-Light 是实际中文 CID 字体，不是静态图片或空白占位 PDF。
            self.assertIn(b'STSong-Light', content)
        self.assertNotEqual(shipment_pdf, packing_pdf)

    def test_invalid_template_is_rejected(self):
        with self.assertRaises(ValueError):
            render_shipment_pdf(self.shipment, 'static-preview')


class ShipmentPrintFileApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='shipment-print-api', phone='13800009992', password='test-only')
        cls.company = Company.objects.create(
            name='打印端点隔离公司', company_no='989902', creator=cls.owner)
        cls.post = Post.objects.create(
            company=cls.company, name='出货打印岗',
            permissions=['shipment.view', 'shipment.manage'])
        UserCompany.objects.create(
            user=cls.owner, company=cls.company, post=cls.post,
            status=UserCompany.Status.ACTIVE)
        cls.plain = User.objects.create_user(
            username='shipment-print-plain', phone='13800009993', password='test-only')
        cls.plain_post = Post.objects.create(
            company=cls.company, name='无权限岗', permissions=[])
        UserCompany.objects.create(
            user=cls.plain, company=cls.company, post=cls.plain_post,
            status=UserCompany.Status.ACTIVE)
        customer = Customer.objects.create(
            company=cls.company, customer_no='PRINT-C2', name='端点客户')
        order = Order.objects.create(
            company=cls.company, order_no='PRINT-ORDER-2', customer=customer)
        cls.shipment = Shipment.objects.create(
            company=cls.company, order=order, customer=customer,
            shipment_no='PRINT-SHIPMENT-2', total_qty=2)
        ShipmentItem.objects.create(shipment=cls.shipment, color='黑', size='M', qty=2)

    def test_authorized_user_downloads_real_shipment_pdf(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        client.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        response = client.get(f'/api/shipments/{self.shipment.id}/print-file?template=shipment')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn(b'%PDF-', response.getvalue())
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('PRINT-SHIPMENT-2-shipment.pdf', response['Content-Disposition'])

    def test_anonymous_and_unauthorized_users_cannot_download(self):
        public = APIClient()
        response = public.get(f'/api/shipments/{self.shipment.id}/print-file')
        self.assertEqual(response.status_code, 401)

        client = APIClient()
        client.force_authenticate(self.plain)
        client.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        response = client.get(f'/api/shipments/{self.shipment.id}/print-file')
        self.assertEqual(response.status_code, 403)

    def test_invalid_template_returns_400(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        client.credentials(HTTP_X_COMPANY_ID=str(self.company.id))
        response = client.get(f'/api/shipments/{self.shipment.id}/print-file?template=bogus')
        self.assertEqual(response.status_code, 400)
