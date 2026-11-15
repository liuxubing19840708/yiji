"""运行：DJANGO_SETTINGS_MODULE=apps.quality.tests.test_settings python manage.py test apps.quality"""
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Style, Ticket
from apps.manufacturing.exceptions import IdempotencyError, NotFound, QuantityError, ValidationError
from apps.manufacturing.models import (
    ProductionRoute, ProductionStage, QuantityLedger, RouteNode,
)
from apps.manufacturing.services import (
    complete_stage, get_balance, receive_stage, transfer_stage,
)
from apps.production.models import Production
from apps.quality.models import QualityInspection, QualityRecord
from apps.quality.services import confirm_inspection, create_inspection


def rid():
    return uuid4().hex


class InspectionTestMixin:
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='quality-inspector', phone='13800000091', password='password',
            real_name='测试检验员')
        cls.company = Company.objects.create(
            name='质检测试一厂', company_no='910091', creator=cls.user)
        cls.other_company = Company.objects.create(
            name='质检测试二厂', company_no='910092', creator=cls.user)
        cls.style = Style.objects.create(company=cls.company, style_no='Q-SYNTH-1', name='测试款')
        cls.bed = Production.objects.create(
            company=cls.company, style=cls.style, bed_no='Q-BED-1', total_qty=10)
        cls.ticket = Ticket.objects.create(
            company=cls.company, production=cls.bed, bed_no=cls.bed.bed_no, bundle_no='1',
            color='测试色', size='M', qty=10, qr_code='Q-BED-1-1')
        cls.route = ProductionRoute.objects.create(
            company=cls.company, code='Q-ROUTE-1', name='测试路线', production=cls.bed)
        cls.nodes = {}
        specs = [
            ('cut', '裁剪', ProductionStage.StageType.CUTTING, 1),
            ('semi', '半成品检验', ProductionStage.StageType.SEMI_INSPECTION, 2),
            ('final', '成品尾查', ProductionStage.StageType.FINAL_INSPECTION, 3),
        ]
        for code, name, stage_type, sequence in specs:
            stage = ProductionStage.objects.create(
                company=cls.company, code=code, name=name, stage_type=stage_type)
            cls.nodes[code] = RouteNode.objects.create(
                company=cls.company, route=cls.route, stage=stage, sequence=sequence)

    def to_semi(self):
        receive_stage(self.company, self.nodes['cut'], self.ticket, 10,
                      operator=self.user, request_id=rid())
        complete_stage(self.company, self.nodes['cut'], self.ticket, 10,
                       operator=self.user, request_id=rid())
        transfer_stage(self.company, self.nodes['cut'], self.nodes['semi'], self.ticket, 10,
                       operator=self.user, request_id=rid())

    def make_inspection(self, **overrides):
        data = {
            'ticket': self.ticket, 'node': self.nodes['semi'],
            'inspection_type': QualityInspection.InspectionType.SEMI,
            'inspected': 10, 'qualified': 6, 'rework': 3, 'scrapped': 1,
            'rework_node': self.nodes['cut'], 'reason': '合成测试疵点', 'inspector': self.user,
        }
        data.update(overrides)
        return create_inspection(self.company, **data)


class QualityInspectionServiceTests(InspectionTestMixin, TestCase):
    def test_quantity_sum_positive_and_integer_boundaries(self):
        self.to_semi()
        valid = self.make_inspection(inspected=1, qualified=1, rework=0, scrapped=0,
                                     rework_node=None, reason='')
        self.assertEqual(valid.inspected, 1)
        invalid_values = [
            {'inspected': 0, 'qualified': 0},
            {'inspected': 2, 'qualified': 1},
            {'inspected': -1, 'qualified': -1},
            {'inspected': True, 'qualified': True},
            {'inspected': 1.5, 'qualified': 1.5},
            {'inspected': 1, 'qualified': -1, 'rework': 2, 'rework_node': self.nodes['cut']},
        ]
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.make_inspection(**values)

    def test_creation_request_id_is_idempotent_and_separate_from_confirmation(self):
        self.to_semi()
        create_id = rid()
        first = self.make_inspection(create_request_id=create_id)
        second = self.make_inspection(create_request_id=create_id)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(QualityInspection.objects.count(), 1)
        with self.assertRaises(IdempotencyError):
            self.make_inspection(create_request_id=create_id, reason='不同载荷')
        confirm_inspection(self.company, first, request_id=rid(), operator=self.user)
        first.refresh_from_db()
        self.assertEqual(first.create_request_id, create_id)
        self.assertNotEqual(first.request_id, first.create_request_id)

    def test_stage_type_must_match_and_both_inspection_stages_are_supported(self):
        self.to_semi()
        with self.assertRaises(ValidationError):
            self.make_inspection(inspection_type=QualityInspection.InspectionType.FINAL)
        final = self.make_inspection(
            node=self.nodes['final'], inspection_type=QualityInspection.InspectionType.FINAL,
            inspected=1, qualified=1, rework=0, scrapped=0, rework_node=None, reason='')
        self.assertEqual(final.node, self.nodes['final'])

    def test_rework_reason_and_explicit_previous_node_are_required(self):
        self.to_semi()
        cases = [
            {'rework_node': None},
            {'rework_node': self.nodes['semi']},
            {'rework_node': self.nodes['final']},
            {'reason': '   '},
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.make_inspection(**values)
        with self.assertRaises(ValidationError):
            self.make_inspection(inspected=1, qualified=1, rework=0, scrapped=0, reason='')

    def test_cross_company_and_ticket_route_mismatch_are_rejected(self):
        self.to_semi()
        with self.assertRaises(NotFound):
            create_inspection(
                self.other_company, ticket=self.ticket, node=self.nodes['semi'],
                inspection_type=QualityInspection.InspectionType.SEMI, inspected=1, qualified=1,
                inspector=self.user)
        other_bed = Production.objects.create(
            company=self.company, style=self.style, bed_no='Q-BED-2', total_qty=1)
        other_ticket = Ticket.objects.create(
            company=self.company, production=other_bed, bed_no=other_bed.bed_no,
            bundle_no='1', qty=1, qr_code='Q-BED-2-1')
        with self.assertRaises(ValidationError):
            self.make_inspection(ticket=other_ticket, inspected=1, qualified=1, rework=0,
                                 scrapped=0, rework_node=None, reason='')

    def test_confirm_writes_all_events_with_trace_and_no_inventory_copy(self):
        self.to_semi()
        inspection = self.make_inspection()
        before = QuantityLedger.objects.count()
        confirmed = confirm_inspection(
            self.company, inspection, request_id='x' * 60, operator=self.user)
        self.assertEqual(confirmed.status, QualityInspection.Status.CONFIRMED)
        self.assertEqual(confirmed.inspector, self.user)
        self.assertEqual(confirmed.confirmed_by, self.user)
        self.assertIsNotNone(confirmed.confirmed_at)
        entries = QuantityLedger.objects.filter(source=f'quality_inspection:{inspection.pk}')
        self.assertEqual(entries.count(), 4)
        self.assertEqual(entries.filter(event_type=QuantityLedger.Event.QUALIFIED).count(), 1)
        self.assertEqual(entries.filter(event_type=QuantityLedger.Event.REWORK).count(), 1)
        self.assertEqual(entries.filter(event_type=QuantityLedger.Event.TRANSFERRED).count(), 1)
        self.assertEqual(entries.filter(event_type=QuantityLedger.Event.SCRAPPED).count(), 1)
        self.assertTrue(all(row.reason == '合成测试疵点' for row in entries))
        self.assertEqual(get_balance(self.company, self.nodes['semi'], self.ticket)['available'], 6)
        self.assertEqual(get_balance(self.company, self.nodes['cut'], self.ticket)['rework'], 3)
        self.assertEqual(QuantityLedger.objects.count(), before + 4)

    def test_zero_categories_do_not_create_empty_ledger_entries(self):
        self.to_semi()
        inspection = self.make_inspection(
            inspected=10, qualified=10, rework=0, scrapped=0, rework_node=None, reason='')
        confirm_inspection(self.company, inspection, request_id=rid(), operator=self.user)
        entries = QuantityLedger.objects.filter(source=f'quality_inspection:{inspection.pk}')
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.get().event_type, QuantityLedger.Event.QUALIFIED)

    def test_repeated_confirmation_is_idempotent_and_payload_key_cannot_change(self):
        self.to_semi()
        inspection = self.make_inspection()
        request_id = rid()
        first = confirm_inspection(
            self.company, inspection, request_id=request_id, operator=self.user)
        count = QuantityLedger.objects.count()
        second = confirm_inspection(
            self.company, inspection, request_id=request_id, operator=self.user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(QuantityLedger.objects.count(), count)
        with self.assertRaises(IdempotencyError):
            confirm_inspection(self.company, inspection, request_id=rid(), operator=self.user)
        self.assertEqual(QuantityLedger.objects.count(), count)

    def test_over_quantity_rolls_back_confirmation(self):
        self.to_semi()
        inspection = self.make_inspection(
            inspected=11, qualified=11, rework=0, scrapped=0, rework_node=None, reason='')
        before = QuantityLedger.objects.count()
        with self.assertRaises(QuantityError):
            confirm_inspection(self.company, inspection, request_id=rid(), operator=self.user)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QualityInspection.Status.PENDING)
        self.assertIsNone(inspection.request_id)
        self.assertEqual(QuantityLedger.objects.count(), before)

    def test_partial_service_failure_rolls_back_previous_events_and_status(self):
        self.to_semi()
        inspection = self.make_inspection()
        before = QuantityLedger.objects.count()
        with patch('apps.quality.services.manufacturing.scrap_stage',
                   side_effect=QuantityError('合成报废失败')):
            with self.assertRaises(QuantityError):
                confirm_inspection(self.company, inspection, request_id=rid(), operator=self.user)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QualityInspection.Status.PENDING)
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertEqual(get_balance(self.company, self.nodes['semi'], self.ticket)['available'], 10)
        self.assertEqual(get_balance(self.company, self.nodes['cut'], self.ticket)['rework'], 0)

    def test_reinspection_requires_confirmed_rework_parent_and_cannot_be_normal_final(self):
        self.to_semi()
        pending = self.make_inspection()
        with self.assertRaises(ValidationError):
            self.make_inspection(inspection_type=QualityInspection.InspectionType.REINSPECTION,
                                 reinspection_of=pending)
        confirm_inspection(self.company, pending, request_id=rid(), operator=self.user)
        with self.assertRaises(ValidationError):
            self.make_inspection(reinspection_of=pending)
        with self.assertRaises(ValidationError):
            self.make_inspection(inspection_type=QualityInspection.InspectionType.FINAL,
                                 reinspection_of=pending)
        qualified_parent = self.make_inspection(
            ticket=Ticket.objects.create(
                company=self.company, production=self.bed, bed_no=self.bed.bed_no,
                bundle_no='2', qty=1, qr_code='Q-BED-1-2'),
            inspected=1, qualified=1, rework=0, scrapped=0, rework_node=None, reason='')
        # 只构造已确认原单用于规则测试；真实余额不是本用例关注点。
        qualified_parent.status = QualityInspection.Status.CONFIRMED
        qualified_parent.request_id = rid()
        qualified_parent.save(update_fields=['status', 'request_id', 'updated_at'])
        with self.assertRaises(ValidationError):
            self.make_inspection(inspection_type=QualityInspection.InspectionType.REINSPECTION,
                                 reinspection_of=qualified_parent)

    def test_successful_reinspection_after_repair_returns_to_same_node(self):
        self.to_semi()
        first = self.make_inspection(
            inspected=10, qualified=0, rework=10, scrapped=0, reason='全部返修')
        confirm_inspection(self.company, first, request_id=rid(), operator=self.user)
        complete_stage(self.company, self.nodes['cut'], self.ticket, 10, request_id=rid())
        transfer_stage(self.company, self.nodes['cut'], self.nodes['semi'], self.ticket, 10,
                       request_id=rid())
        second = self.make_inspection(
            inspection_type=QualityInspection.InspectionType.REINSPECTION,
            reinspection_of=first, inspected=10, qualified=9, rework=0, scrapped=1,
            rework_node=None, reason='复检报废一件')
        confirm_inspection(self.company, second, request_id=rid(), operator=self.user)
        self.assertEqual(second.reinspection_of, first)
        self.assertEqual(second.inspection_type, QualityInspection.InspectionType.REINSPECTION)
        balance = get_balance(self.company, self.nodes['semi'], self.ticket)
        self.assertEqual((balance['qualified'], balance['scrapped']), (9, 1))

    def test_cross_company_confirmation_and_request_id_uniqueness(self):
        self.to_semi()
        inspection = self.make_inspection()
        with self.assertRaises(NotFound):
            confirm_inspection(self.other_company, inspection, request_id=rid(), operator=self.user)
        request_id = rid()
        confirm_inspection(self.company, inspection, request_id=request_id, operator=self.user)
        self.assertEqual(QualityInspection.objects.get(pk=inspection.pk).request_id, request_id)

    def test_request_id_cannot_confirm_another_inspection_or_leave_its_events(self):
        self.to_semi()
        first = self.make_inspection(
            inspected=10, qualified=10, rework=0, scrapped=0, rework_node=None, reason='')
        request_id = rid()
        confirm_inspection(self.company, first, request_id=request_id, operator=self.user)
        second_ticket = Ticket.objects.create(
            company=self.company, production=self.bed, bed_no=self.bed.bed_no,
            bundle_no='2', qty=1, qr_code='Q-BED-1-2')
        receive_stage(self.company, self.nodes['cut'], second_ticket, 1, request_id=rid())
        complete_stage(self.company, self.nodes['cut'], second_ticket, 1, request_id=rid())
        transfer_stage(self.company, self.nodes['cut'], self.nodes['semi'], second_ticket, 1,
                       request_id=rid())
        second = self.make_inspection(
            ticket=second_ticket, inspected=1, qualified=1, rework=0, scrapped=0,
            rework_node=None, reason='')
        before = QuantityLedger.objects.count()
        with self.assertRaises(IdempotencyError):
            confirm_inspection(self.company, second, request_id=request_id, operator=self.user)
        second.refresh_from_db()
        self.assertEqual(second.status, QualityInspection.Status.PENDING)
        self.assertEqual(QuantityLedger.objects.count(), before)
        self.assertFalse(QuantityLedger.objects.filter(
            source=f'quality_inspection:{second.pk}').exists())

    def test_database_quantity_constraints(self):
        self.to_semi()
        with self.assertRaises(IntegrityError), transaction.atomic():
            QualityInspection.objects.create(
                company=self.company, ticket=self.ticket, route=self.route,
                node=self.nodes['semi'], inspection_type=QualityInspection.InspectionType.SEMI,
                inspected=2, qualified=1, rework=0, scrapped=0, inspector=self.user)


class QualityInspectionAPITests(InspectionTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.manage_post = Post.objects.create(
            company=cls.company, name='质检管理测试岗', permissions=['quality.view', 'quality.manage'])
        cls.view_post = Post.objects.create(
            company=cls.company, name='质检查看测试岗', permissions=['quality.view'])
        cls.manage_uc = UserCompany.objects.create(
            user=cls.user, company=cls.company, post=cls.manage_post,
            status=UserCompany.Status.ACTIVE)
        cls.viewer = User.objects.create_user(
            username='quality-viewer', phone='13800000092', password='password')
        UserCompany.objects.create(
            user=cls.viewer, company=cls.company, post=cls.view_post,
            status=UserCompany.Status.ACTIVE)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.headers = {'HTTP_X_COMPANY_ID': str(self.company.pk)}

    def payload(self, **overrides):
        data = {
            'ticket': self.ticket.pk, 'node': self.nodes['semi'].pk,
            'inspection_type': 'semi_inspection', 'inspected': 10, 'qualified': 6,
            'rework': 3, 'scrapped': 1, 'rework_node': self.nodes['cut'].pk,
            'reason': 'API合成测试',
        }
        data.update(overrides)
        return data

    def test_view_manage_permissions_and_read_endpoints(self):
        response = self.client.get('/api/quality/inspections', **self.headers)
        self.assertEqual(response.status_code, 200)
        self.client.force_authenticate(self.viewer)
        response = self.client.post('/api/quality/inspections', self.payload(), format='json',
                                    **self.headers)
        self.assertEqual(response.status_code, 403)
        self.manage_post.permissions = ['quality.manage']
        self.manage_post.save(update_fields=['permissions', 'updated_at'])
        self.client.force_authenticate(self.user)
        response = self.client.get('/api/quality/inspections', **self.headers)
        self.assertEqual(response.status_code, 403)

    def test_create_confirm_detail_and_cross_company_detail(self):
        self.to_semi()
        response = self.client.post('/api/quality/inspections', self.payload(), format='json',
                                    **self.headers)
        self.assertEqual(response.status_code, 201, response.content)
        inspection_id = response.data['id']
        self.assertEqual(response.data['inspector'], self.user.pk)
        response = self.client.post(
            f'/api/quality/inspections/{inspection_id}/confirm', {'request_id': rid()},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['status'], 'confirmed')
        response = self.client.get(f'/api/quality/inspections/{inspection_id}', **self.headers)
        self.assertEqual(response.status_code, 200)
        response = self.client.get(
            f'/api/quality/inspections/{inspection_id}',
            HTTP_X_COMPANY_ID=str(self.other_company.pk))
        self.assertEqual(response.status_code, 404)

    def test_api_over_quantity_and_bad_confirm_request_return_400_without_writes(self):
        self.to_semi()
        response = self.client.post(
            '/api/quality/inspections',
            self.payload(inspected=11, qualified=11, rework=0, scrapped=0, rework_node=None),
            format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.content)
        inspection_id = response.data['id']
        before = QuantityLedger.objects.count()
        response = self.client.post(
            f'/api/quality/inspections/{inspection_id}/confirm', {'request_id': rid()},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            f'/api/quality/inspections/{inspection_id}/confirm', {'request_id': '   '},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(QuantityLedger.objects.count(), before)

    def test_legacy_rework_receive_finish_flow_remains_compatible(self):
        response = self.client.post(
            '/api/quality/records',
            {'ticket': self.ticket.pk, 'defect_qty': 2, 'reason': '旧流程兼容测试'},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.content)
        record_id = response.data['id']
        response = self.client.put(
            f'/api/quality/records/{record_id}', {'action': 'receive', 'received_qty': 2},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        response = self.client.put(
            f'/api/quality/records/{record_id}', {'action': 'finish', 'remark': '完成'},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(QualityRecord.objects.get(pk=record_id).status, QualityRecord.Status.DONE)
