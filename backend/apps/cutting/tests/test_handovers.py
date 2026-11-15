"""运行：DJANGO_SETTINGS_MODULE=apps.cutting.tests.test_settings .venv/Scripts/python manage.py test apps.cutting"""
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Style, Ticket
from apps.cutting.models import CuttingHandover
from apps.cutting.services import cancel_handover, create_handover, sign_handover
from apps.manufacturing.exceptions import IdempotencyError, NotFound, QuantityError, ValidationError
from apps.manufacturing.models import (ProductionRoute, ProductionStage, QuantityLedger, RouteNode)
from apps.manufacturing.services import complete_stage, get_balance, receive_stage, transfer_stage
from apps.production.models import Production


def rid():
    return uuid4().hex


class CuttingHandoverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = cls.make_user('owner')
        cls.receiver = cls.make_user('receiver')
        cls.outsider = cls.make_user('outsider')
        cls.company = Company.objects.create(name='虚拟裁剪一厂', company_no='910001', creator=cls.owner)
        cls.other_company = Company.objects.create(name='虚拟裁剪二厂', company_no='910002', creator=cls.owner)
        cls.manager_post = Post.objects.create(company=cls.company, name='交接管理',
                                                permissions=['cutting.view', 'cutting.manage'])
        cls.worker_post = Post.objects.create(company=cls.company, name='车间工人',
                                               permissions=['cutting.view'])
        for user, post in [(cls.owner, cls.manager_post), (cls.receiver, cls.worker_post),
                           (cls.outsider, cls.worker_post)]:
            UserCompany.objects.create(company=cls.company, user=user, post=post,
                                       status=UserCompany.Status.ACTIVE)
        UserCompany.objects.create(company=cls.other_company, user=cls.owner,
                                   post=cls.manager_post, status=UserCompany.Status.ACTIVE)
        # manager_post 属于另一公司，不能用于授权；另建二厂管理岗。
        other_post = Post.objects.create(company=cls.other_company, name='二厂管理',
                                         permissions=['cutting.view', 'cutting.manage'])
        link = UserCompany.objects.get(company=cls.other_company, user=cls.owner)
        link.post = other_post
        link.save(update_fields=['post', 'updated_at'])
        cls.style = Style.objects.create(company=cls.company, style_no='CUT-TEST', name='虚拟款')
        cls.bed = Production.objects.create(company=cls.company, style=cls.style,
                                            bed_no='CUT-BED', total_qty=10)
        cls.ticket = Ticket.objects.create(company=cls.company, production=cls.bed,
                                           bed_no=cls.bed.bed_no, bundle_no='1', qty=10,
                                           qr_code='CUT-BED-1')
        cls.route = ProductionRoute.objects.create(company=cls.company, code='CUT-ROUTE',
                                                   name='虚拟路线', production=cls.bed)
        cut_stage = ProductionStage.objects.create(company=cls.company, code='cut', name='裁剪',
                                                   stage_type=ProductionStage.StageType.CUTTING)
        sew_stage = ProductionStage.objects.create(company=cls.company, code='sew', name='车缝',
                                                   stage_type=ProductionStage.StageType.SEWING)
        pack_stage = ProductionStage.objects.create(company=cls.company, code='pack', name='包装',
                                                    stage_type=ProductionStage.StageType.PACKING)
        cls.cut = RouteNode.objects.create(company=cls.company, route=cls.route, stage=cut_stage,
                                           sequence=1)
        cls.sew = RouteNode.objects.create(company=cls.company, route=cls.route, stage=sew_stage,
                                           sequence=2)
        cls.pack = RouteNode.objects.create(company=cls.company, route=cls.route, stage=pack_stage,
                                            sequence=3, allow_skip=True)
        receive_stage(cls.company, cls.cut, cls.ticket, 10, operator=cls.owner, request_id=rid())
        complete_stage(cls.company, cls.cut, cls.ticket, 10, operator=cls.owner, request_id=rid())

    @classmethod
    def make_user(cls, name):
        return User.objects.create_user(username=f'cut-{name}', phone=None, password='password')

    def create(self, quantity=4, **overrides):
        data = dict(ticket_id=self.ticket.id, source_node_id=self.cut.id,
                    target_node_id=self.sew.id, receiver_id=self.receiver.id,
                    quantity=quantity, request_id=rid(), created_by=self.owner)
        data.update(overrides)
        return create_handover(self.company, **data)

    def test_create_only_reserves_and_repeated_idempotency(self):
        before = QuantityLedger.objects.count()
        handover = self.create(remark='虚拟交接')
        again = self.create(request_id=handover.request_id, remark='虚拟交接')
        self.assertEqual(handover, again)
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(handover.status, CuttingHandover.Status.PENDING)
        with self.assertRaises(IdempotencyError):
            self.create(quantity=5, request_id=handover.request_id, remark='虚拟交接')

    def test_pending_reservations_cannot_overlap_and_cancel_releases(self):
        self.create(4)
        self.create(6)
        with self.assertRaises(QuantityError):
            self.create(1)
        cancelled = CuttingHandover.objects.filter(status='pending').first()
        cancel_handover(self.company, cancelled.id, actor=self.owner)
        replacement = self.create(cancelled.quantity)
        self.assertEqual(replacement.status, CuttingHandover.Status.PENDING)
        repeated = cancel_handover(self.company, cancelled.id, actor=self.owner)
        self.assertEqual(repeated.status, CuttingHandover.Status.CANCELLED)
        self.assertIsNotNone(repeated.cancelled_at)
        self.assertIsNone(repeated.outgoing_ledger_id)

    def test_invalid_nodes_direction_quantity_and_company(self):
        with self.assertRaises(ValidationError):
            self.create(source_node_id=self.sew.id, target_node_id=self.pack.id)
        with self.assertRaises(ValidationError):
            self.create(target_node_id=self.cut.id)
        with self.assertRaises(ValidationError):
            self.create(quantity=0)
        with self.assertRaises(NotFound):
            create_handover(self.other_company, ticket_id=self.ticket.id,
                            source_node_id=self.cut.id, target_node_id=self.sew.id,
                            receiver_id=self.receiver.id, quantity=1, request_id=rid(),
                            created_by=self.owner)
        handover = self.create()
        with self.assertRaises(NotFound):
            sign_handover(self.other_company, handover.id, actor=self.owner)

    def test_self_sign_partial_is_atomic_and_idempotent(self):
        handover = self.create(4)
        before = QuantityLedger.objects.count()
        signed = sign_handover(self.company, handover.id, actor=self.receiver)
        self.assertEqual(signed.status, CuttingHandover.Status.SIGNED)
        self.assertEqual(signed.signed_by, self.receiver)
        self.assertEqual(QuantityLedger.objects.count(), before + 2)
        self.assertEqual(get_balance(self.company, self.cut, self.ticket)['available'], 6)
        self.assertEqual(get_balance(self.company, self.sew, self.ticket)['received'], 4)
        repeated = sign_handover(self.company, handover.id, actor=self.receiver)
        self.assertEqual(repeated.outgoing_ledger_id, signed.outgoing_ledger_id)
        self.assertEqual(QuantityLedger.objects.count(), before + 2)
        with self.assertRaises(ValidationError):
            cancel_handover(self.company, handover.id, actor=self.owner)

    def test_delegate_requires_explicit_manage_permission(self):
        handover = self.create()
        with self.assertRaises(PermissionDenied):
            sign_handover(self.company, handover.id, actor=self.outsider)
        self.assertEqual(CuttingHandover.objects.get(pk=handover.pk).status, 'pending')
        signed = sign_handover(self.company, handover.id, actor=self.owner)
        self.assertEqual(signed.receiver, self.receiver)
        self.assertEqual(signed.signed_by, self.owner)

    def test_failed_transfer_rolls_back_status_and_ledgers(self):
        handover = self.create()
        before = QuantityLedger.objects.count()
        def fail_after_pair(*args, **kwargs):
            transfer_stage(*args, **kwargs)
            raise RuntimeError('虚拟故障')

        with patch('apps.cutting.services.transfer_stage', side_effect=fail_after_pair):
            with self.assertRaises(RuntimeError):
                sign_handover(self.company, handover.id, actor=self.receiver)
        handover.refresh_from_db()
        self.assertEqual(handover.status, CuttingHandover.Status.PENDING)
        self.assertIsNone(handover.signed_at)
        self.assertIsNone(handover.outgoing_ledger_id)
        self.assertEqual(QuantityLedger.objects.count(), before)

    def test_api_permissions_detail_filters_and_actions(self):
        handover = self.create()
        client = APIClient()
        client.force_authenticate(self.receiver)
        headers = {'HTTP_X_COMPANY_ID': str(self.company.id)}
        self.assertEqual(client.get('/api/cutting/handovers/', **headers).status_code, 200)
        denied = client.post('/api/cutting/handovers/', {
            'ticket_id': self.ticket.id, 'source_node_id': self.cut.id,
            'target_node_id': self.sew.id, 'receiver_id': self.receiver.id,
            'quantity': 1, 'request_id': rid(),
        }, format='json', **headers)
        self.assertEqual(denied.status_code, 403)
        signed = client.post(f'/api/cutting/handovers/{handover.id}/sign/', {}, **headers)
        self.assertEqual(signed.status_code, 200, signed.data)
        self.assertEqual(signed.data['status'], 'signed')
        detail = client.get(f'/api/cutting/handovers/{handover.id}/', **headers)
        self.assertEqual(detail.data['outgoing_ledger_id'], signed.data['outgoing_ledger_id'])
        other_headers = {'HTTP_X_COMPANY_ID': str(self.other_company.id)}
        client.force_authenticate(self.owner)
        self.assertEqual(client.get(f'/api/cutting/handovers/{handover.id}/',
                                    **other_headers).status_code, 404)
        pending = self.create()
        cancelled = client.post(f'/api/cutting/handovers/{pending.id}/cancel/', {}, **headers)
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data['status'], 'cancelled')
