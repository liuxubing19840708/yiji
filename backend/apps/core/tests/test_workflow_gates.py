from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Style, Ticket
from apps.core.workflow_gates import enforce_manufacturing_gate
from apps.manufacturing.models import ProductionRoute
from apps.ops.models import AuditLog, CompanySetting
from apps.ops.serializers import SettingSerializer
from apps.orders.models import Order
from apps.production.models import Production


class WorkflowGateTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='workflow-gate-user', phone='13800000111', password='password')
        cls.company = Company.objects.create(
            name='门禁隔离测试厂', company_no='910111', creator=cls.user)
        post = Post.objects.create(company=cls.company, name='生产测试岗',
                                   permissions=['production.manage', 'records.create'])
        UserCompany.objects.create(user=cls.user, company=cls.company, post=post,
                                   status=UserCompany.Status.ACTIVE)
        cls.style = Style.objects.create(company=cls.company, style_no='GATE-S', name='门禁款')
        cls.order = Order.objects.create(company=cls.company, order_no='GATE-O', style=cls.style)
        cls.bed = Production.objects.create(company=cls.company, style=cls.style, order=cls.order,
                                           bed_no='GATE-B', total_qty=0)
        cls.ticket = Ticket.objects.create(
            company=cls.company, production=cls.bed, bed_no=cls.bed.bed_no,
            bundle_no='1', color='黑', size='M', qty=5, qr_code='GATE-B-1')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.headers = {'HTTP_X_COMPANY_ID': str(self.company.pk)}

    def set_mode(self, key, value):
        CompanySetting.objects.update_or_create(company=self.company, key=key,
                                                defaults={'value': value})

    def batch(self):
        return self.client.post('/api/tickets/batch', {
            'production_id': self.bed.pk,
            'items': [{'color': '黑', 'size': 'M', 'qty': 2}],
        }, format='json', **self.headers)

    def test_preproduction_gate_off_and_strict(self):
        self.set_mode('preproduction_gate_mode', 'off')
        self.assertEqual(self.batch().status_code, 201)
        self.set_mode('preproduction_gate_mode', 'strict')
        response = self.batch()
        self.assertEqual(response.status_code, 400)
        self.assertIn('产前', str(response.data))

    def test_preproduction_warn_allows_and_audits(self):
        self.set_mode('preproduction_gate_mode', 'warn')
        self.assertEqual(self.batch().status_code, 201)
        self.assertTrue(AuditLog.objects.filter(
            company=self.company, message__contains='产前门禁警告').exists())

    def test_manufacturing_gate_only_applies_to_tickets_with_route(self):
        self.set_mode('manufacturing_gate_mode', 'strict')
        enforce_manufacturing_gate(self.company, self.ticket, self.user)
        ProductionRoute.objects.create(company=self.company, code='GATE-R', name='门禁路线',
                                       order=self.order)
        with self.assertRaises(ValidationError):
            enforce_manufacturing_gate(self.company, self.ticket, self.user)
        self.set_mode('manufacturing_gate_mode', 'warn')
        enforce_manufacturing_gate(self.company, self.ticket, self.user)
        self.assertTrue(AuditLog.objects.filter(
            company=self.company, message__contains='生产节点门禁警告').exists())

    def test_orderless_bed_is_not_blocked_by_unrelated_orderless_route(self):
        self.set_mode('manufacturing_gate_mode', 'strict')
        other_bed = Production.objects.create(
            company=self.company, style=self.style, bed_no='GATE-B-OTHER', total_qty=0)
        ProductionRoute.objects.create(
            company=self.company, code='GATE-OTHER-R', name='其他无订单床次路线',
            production=other_bed)
        enforce_manufacturing_gate(self.company, self.ticket, self.user)
        ProductionRoute.objects.create(
            company=self.company, code='GATE-CURRENT-R', name='当前床次路线',
            production=self.bed)
        with self.assertRaises(ValidationError):
            enforce_manufacturing_gate(self.company, self.ticket, self.user)

    def test_invalid_gate_setting_is_rejected(self):
        serializer = SettingSerializer(data={'preproduction_gate_mode': 'enabled'})
        self.assertFalse(serializer.is_valid())
        serializer = SettingSerializer(data={'manufacturing_gate_mode': 'strict'})
        self.assertTrue(serializer.is_valid(), serializer.errors)
