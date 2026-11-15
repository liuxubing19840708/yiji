# -*- coding: utf-8 -*-
"""P0 实付工资隔离回归；用 Django test runner 创建独立测试数据库。"""
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Company, Post, User, UserCompany
from apps.salary.models import (SalaryPayment, SettleApply, Settlement)
from apps.salary.payments import settlement_outstanding


class SalaryPaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username='salary-owner', phone='18800001001', password='pw',
            real_name='工资管理员')
        cls.worker = User.objects.create_user(
            username='salary-worker', phone='18800001002', password='pw',
            real_name='员工甲')
        cls.other_worker = User.objects.create_user(
            username='salary-other', phone='18800001003', password='pw',
            real_name='员工乙')
        cls.viewer = User.objects.create_user(
            username='salary-viewer', phone='18800001004', password='pw',
            real_name='工资查看')
        cls.other_owner = User.objects.create_user(
            username='salary-owner-b', phone='18800001005', password='pw',
            real_name='另一公司')
        cls.company = Company.objects.create(
            name='实付测试A', company_no='990001', creator=cls.owner)
        cls.company_b = Company.objects.create(
            name='实付测试B', company_no='990002', creator=cls.other_owner)
        cls.admin_post = Post.objects.create(
            company=cls.company, name='工资管理', is_admin=True)
        cls.worker_post = Post.objects.create(
            company=cls.company, name='普工', permissions=[])
        cls.viewer_post = Post.objects.create(
            company=cls.company, name='工资查看', permissions=['salary.view'])
        cls.other_admin_post = Post.objects.create(
            company=cls.company_b, name='管理', is_admin=True)
        UserCompany.objects.create(
            company=cls.company, user=cls.owner, post=cls.admin_post,
            status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(
            company=cls.company, user=cls.worker, post=cls.worker_post,
            status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(
            company=cls.company, user=cls.other_worker, post=cls.worker_post,
            status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(
            company=cls.company, user=cls.viewer, post=cls.viewer_post,
            status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(
            company=cls.company_b, user=cls.other_owner,
            post=cls.other_admin_post, status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(
            company=cls.company_b, user=cls.worker,
            post=Post.objects.create(company=cls.company_b, name='普工'),
            status=UserCompany.Status.ACTIVE)
        cls.settlement = Settlement.objects.create(
            company=cls.company, user=cls.worker,
            period_start='2026-01-01', period_end='2026-01-31',
            employee_count=1, amount=Decimal('100.00'),
            wage_detail={str(cls.worker.id): '100.00'})
        cls.group_settlement = Settlement.objects.create(
            company=cls.company, user=None,
            period_start='2026-02-01', period_end='2026-02-28',
            employee_count=2, amount=Decimal('200.00'),
            wage_detail={str(cls.worker.id): '120.00',
                         str(cls.other_worker.id): '80.00'})

    def setUp(self):
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(self.owner)
        self.headers = {'HTTP_X_COMPANY_ID': str(self.company.id)}

    def as_user(self, user, company=None):
        self.client.force_authenticate(user)
        self.headers = {'HTTP_X_COMPANY_ID': str((company or self.company).id)}

    def payload(self, **overrides):
        data = {
            'employee_id': self.worker.id,
            'amount': '40.10',
            'method': 'bank',
            'transaction_ref': 'PAY-001',
            'note': '一月工资',
            'allocations': [{'settlement_id': self.settlement.id,
                             'amount': '40.10'}],
        }
        data.update(overrides)
        return data

    def post_payment(self, data=None):
        return self.client.post('/api/salary/payments', data or self.payload(),
                                format='json', **self.headers)

    def test_amount_precision_allocation_and_unallocated_payment(self):
        original = self.settlement.amount
        response = self.post_payment()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data['amount'], '40.10')
        self.assertEqual(Decimal(response.data['allocations'][0]['outstanding']),
                         Decimal('59.90'))
        self.settlement.refresh_from_db()
        self.assertEqual(self.settlement.amount, original)
        self.assertEqual(settlement_outstanding(self.settlement, self.worker.id),
                         Decimal('59.90'))

        response = self.post_payment(self.payload(
            amount='5.00', transaction_ref='NO-SETTLEMENT', allocations=[]))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data['allocations'], [])
        self.assertEqual(settlement_outstanding(self.settlement, self.worker.id),
                         Decimal('59.90'))

        for amount, allocation in [('0.001', '0.001'), ('1.00', '0')]:
            response = self.post_payment(self.payload(
                amount=amount, transaction_ref=f'BAD-{amount}',
                allocations=[{'settlement_id': self.settlement.id,
                              'amount': allocation}]))
            self.assertEqual(response.status_code, 400)
        response = self.post_payment(self.payload(
            transaction_ref='BAD-SUM',
            allocations=[{'settlement_id': self.settlement.id, 'amount': '1.00'}]))
        self.assertEqual(response.status_code, 400)

    def test_overpayment_rejected_atomically(self):
        self.assertEqual(self.post_payment().status_code, 201)
        before = SalaryPayment.objects.count()
        response = self.post_payment(self.payload(
            amount='59.91', transaction_ref='PAY-OVER',
            allocations=[{'settlement_id': self.settlement.id,
                          'amount': '59.91'}]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SalaryPayment.objects.count(), before)
        self.assertEqual(settlement_outstanding(self.settlement, self.worker.id),
                         Decimal('59.90'))
        revoked = Settlement.objects.create(
            company=self.company, user=self.worker,
            period_start='2026-03-01', period_end='2026-03-31', amount='10.00',
            status=Settlement.Status.REVOKED)
        response = self.post_payment(self.payload(
            amount='1.00', transaction_ref='REVOKED',
            allocations=[{'settlement_id': revoked.id, 'amount': '1.00'}]))
        self.assertEqual(response.status_code, 400)

    def test_company_employee_and_settlement_isolation(self):
        foreign = Settlement.objects.create(
            company=self.company_b, user=self.worker,
            period_start='2026-01-01', period_end='2026-01-31', amount='10.00')
        response = self.post_payment(self.payload(
            amount='1.00', transaction_ref='CROSS-SETTLEMENT',
            allocations=[{'settlement_id': foreign.id, 'amount': '1.00'}]))
        self.assertEqual(response.status_code, 400)

        outsider = User.objects.create_user(
            username='salary-outsider', phone='18800001006', password='pw')
        response = self.post_payment(self.payload(
            employee_id=outsider.id, amount='1.00', transaction_ref='CROSS-EMP',
            allocations=[]))
        self.assertEqual(response.status_code, 400)

        other_settlement = Settlement.objects.create(
            company=self.company, user=self.other_worker,
            period_start='2026-01-01', period_end='2026-01-31', amount='10.00')
        response = self.post_payment(self.payload(
            amount='1.00', transaction_ref='WRONG-EMP',
            allocations=[{'settlement_id': other_settlement.id, 'amount': '1.00'}]))
        self.assertEqual(response.status_code, 400)

    def test_group_settlement_requires_wage_detail_attribution(self):
        no_detail = Settlement.objects.create(
            company=self.company, user=None,
            period_start='2026-04-01', period_end='2026-04-30', amount='100.00')
        response = self.post_payment(self.payload(
            amount='10.00', transaction_ref='NO-DETAIL',
            allocations=[{'settlement_id': no_detail.id, 'amount': '10.00'}]))
        self.assertEqual(response.status_code, 400)

        response = self.post_payment(self.payload(
            employee_id=self.viewer.id, amount='1.00', transaction_ref='NOT-IN-DETAIL',
            allocations=[{'settlement_id': self.group_settlement.id, 'amount': '1.00'}]))
        self.assertEqual(response.status_code, 400)

        response = self.post_payment(self.payload(
            amount='30.00', transaction_ref='GROUP-OK',
            allocations=[{'settlement_id': self.group_settlement.id, 'amount': '30.00'}]))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(settlement_outstanding(
            self.group_settlement, self.worker.id), Decimal('90.00'))
        self.assertEqual(settlement_outstanding(
            self.group_settlement, self.other_worker.id), Decimal('80.00'))

    def test_active_reference_unique_per_company_and_reusable_after_void(self):
        self.assertEqual(self.post_payment().status_code, 201)
        response = self.post_payment(self.payload(amount='1.00', allocations=[]))
        self.assertEqual(response.status_code, 400)

        self.as_user(self.other_owner, self.company_b)
        response = self.post_payment(self.payload(
            amount='1.00', allocations=[]))
        self.assertEqual(response.status_code, 201, response.content)

        self.as_user(self.owner)
        payment = SalaryPayment.objects.get(
            company=self.company, transaction_ref='PAY-001')
        response = self.client.post(
            f'/api/salary/payments/{payment.id}/void', {'reason': '重录'},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        response = self.post_payment(self.payload(
            amount='1.00', allocations=[]))
        self.assertEqual(response.status_code, 201, response.content)

    def test_permissions_employee_scope_and_read_only_detail(self):
        own = self.post_payment(self.payload(
            amount='1.00', transaction_ref='OWN', allocations=[])).data
        other = self.post_payment(self.payload(
            employee_id=self.other_worker.id, amount='2.00',
            transaction_ref='OTHER', allocations=[])).data

        self.as_user(self.worker)
        response = self.client.get('/api/salary/payments', **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.data['results']], [own['id']])
        self.assertEqual(self.client.get(
            f"/api/salary/payments/{own['id']}", **self.headers).status_code, 200)
        self.assertEqual(self.client.get(
            f"/api/salary/payments/{other['id']}", **self.headers).status_code, 403)
        self.assertEqual(self.post_payment(self.payload(
            transaction_ref='DENIED', allocations=[])).status_code, 403)

        self.as_user(self.viewer)
        response = self.client.get('/api/salary/payments', **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(self.client.get(
            f"/api/salary/payments/{other['id']}", **self.headers).status_code, 200)

        self.as_user(self.owner)
        self.assertEqual(self.client.patch(
            f"/api/salary/payments/{own['id']}", {'note': '改'},
            format='json', **self.headers).status_code, 405)
        self.assertEqual(self.client.delete(
            f"/api/salary/payments/{own['id']}", **self.headers).status_code, 405)
        self.as_user(self.other_owner, self.company_b)
        self.assertEqual(self.client.get(
            f"/api/salary/payments/{own['id']}", **self.headers).status_code, 404)

    def test_existing_review_permission_can_manage(self):
        self.admin_post.is_admin = False
        self.admin_post.permissions = ['salary.review']
        self.admin_post.save()
        self.assertEqual(self.post_payment(self.payload(
            amount='1.00', transaction_ref='REVIEW-PERM', allocations=[]
        )).status_code, 201)

    def test_void_restores_balance_and_cannot_repeat(self):
        payment_id = self.post_payment().data['id']
        response = self.client.post(
            f'/api/salary/payments/{payment_id}/void', {'reason': '  '},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            f'/api/salary/payments/{payment_id}/void', {'reason': '银行退汇'},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['status'], SalaryPayment.Status.VOID)
        self.assertEqual(response.data['void_reason'], '银行退汇')
        self.assertEqual(response.data['voided_by'], self.owner.id)
        self.assertIsNotNone(response.data['voided_at'])
        self.assertEqual(settlement_outstanding(self.settlement, self.worker.id),
                         Decimal('100.00'))
        self.assertEqual(SalaryPayment.objects.filter(id=payment_id).count(), 1)
        response = self.client.post(
            f'/api/salary/payments/{payment_id}/void', {'reason': '再次'},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 400)

    def test_revoke_protected_only_by_active_allocations(self):
        payment_id = self.post_payment().data['id']
        response = self.client.post(
            f'/api/salary/settlements/{self.settlement.id}/revoke', {},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 400)
        self.client.post(f'/api/salary/payments/{payment_id}/void',
                         {'reason': '撤销重结'}, format='json', **self.headers)
        response = self.client.post(
            f'/api/salary/settlements/{self.settlement.id}/revoke', {},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.content)

    def test_legacy_apply_pay_creates_payment_and_keeps_response(self):
        apply = SettleApply.objects.create(
            company=self.company, applicant=self.worker, user=self.worker,
            period_start='2026-05-01', period_end='2026-05-31',
            amount='100.00', status=SettleApply.Status.APPROVED)
        member = UserCompany.objects.get(company=self.company, user=self.worker)
        member.salary_type = UserCompany.SalaryType.FIXED
        member.fixed_salary = Decimal('100.00')
        member.save()
        with mock.patch('apps.salary.views._outstanding_wage',
                        return_value=({}, Decimal('100.00'))):
            response = self.client.put(
                f'/api/salary/settle-applies/{apply.id}/pay',
                {'payer': '员工甲', 'method': 'cash'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['status'], SettleApply.Status.PAID)
        self.assertEqual(response.data['amount'], '100.00')
        self.assertNotIn('payment', response.data)
        payment = SalaryPayment.objects.get(transaction_ref=f'settle-apply-{apply.id}')
        self.assertEqual(payment.employee, self.worker)
        self.assertEqual(payment.amount, Decimal('100.00'))
        self.assertEqual(payment.method, 'cash')
        allocation = payment.allocations.get()
        self.assertEqual(allocation.amount, Decimal('100.00'))
        self.assertEqual(allocation.settlement.amount, Decimal('100.00'))
