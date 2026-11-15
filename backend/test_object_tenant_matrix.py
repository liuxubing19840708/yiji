# -*- coding: utf-8 -*-
"""真实业务对象 ID 的跨公司隔离矩阵。

A 厂管理员携带 A 厂上下文访问 B 厂真实对象。与伪造成员公司头的全路由
矩阵互补：这里所有对象都真实存在，不能以“不存在 ID”代替租户隔离验证。
新增带参数 API 必须登记真实夹具，否则路由完整性测试直接失败。
"""
from datetime import date

from django.apps import apps
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.ai_report.models import AiExportTask, AiReportSchedule, AiScheduledRun, SavedReport
from apps.catalog.models import (
    Customer, CustomerGroup, Process, ProcessTemplate, Style, StyleProcess, Ticket,
)
from apps.cutting.models import CuttingHandover
from apps.finance.models import CashEntry, Payable, Receivable, SupplierPayment
from apps.finished_goods.models import (
    FinishedInbound, FinishedInboundLine, PackingBox as FinishedPackingBox, PackingBoxLine,
    PackingTask,
)
from apps.handover.models import Handover, HandoverItem, HandoverPolicy
from apps.kanban_template.models import KanbanTemplate
from apps.manufacturing.models import (
    ProductionRoute, ProductionStage, QuantityLedger, RouteNode,
)
from apps.ops.models import Notification
from apps.orders.models import Order, OrderItem
from apps.preproduction.models import (
    BriefingTarget, CraftTemplate, ReviewIssue, SampleTask, TechPackVersion,
)
from apps.preproduction.services import content_fingerprint
from apps.production.models import Production, ProductionProcess
from apps.quality.models import QualityInspection, QualityRecord
from apps.quotation.models import QuoteLabor, QuoteMaterial, Quotation
from apps.records.models import Record
from apps.salary.models import SalaryPayment, SettleApply, Settlement
from apps.shipment.models import PackingBox, Shipment, ShipmentItem
from apps.subcontract.models import OutsourceOrder
from apps.tech.models import Bom, BomItem, Material
from apps.warehouse.models import (
    IssueItem, Lot, MaterialIssue, PurchaseItem, PurchaseOrder, Receipt, ReceiptItem,
    StockIn, StockInItem, Supplier,
)
from test_tenant_permission_matrix import _routes


# 没有冗余 company 字段的子表也必须纳入守恒快照，防止主表未动却新增子行。
def _child_querysets(company):
    return {
        'style_process': StyleProcess.all_objects.filter(style__company=company),
        'production_process': ProductionProcess.all_objects.filter(
            production__company=company),
        'order_item': OrderItem.all_objects.filter(order__company=company),
        'purchase_item': PurchaseItem.all_objects.filter(order__company=company),
        'stock_in_item': StockInItem.all_objects.filter(stock_in__company=company),
        'issue_item': IssueItem.all_objects.filter(issue__company=company),
        'receipt_item': ReceiptItem.all_objects.filter(receipt__company=company),
        'shipment_item': ShipmentItem.all_objects.filter(shipment__company=company),
        'packing_box': PackingBox.all_objects.filter(shipment__company=company),
        'bom_item': BomItem.all_objects.filter(bom__company=company),
        'quote_material': QuoteMaterial.all_objects.filter(quotation__company=company),
        'quote_labor': QuoteLabor.all_objects.filter(quotation__company=company),
    }


def _company_fingerprint(company):
    rows = []
    for model in apps.get_models():
        if not any(field.name == 'company' for field in model._meta.fields):
            continue
        manager = getattr(model, 'all_objects', model._meta.default_manager)
        queryset = manager.filter(company=company).order_by('pk')
        rows.append((model._meta.label, repr(list(queryset.values()))))
    rows.extend((name, repr(list(queryset.order_by('pk').values())))
                for name, queryset in _child_querysets(company).items())
    return rows


class ObjectTenantMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner_a = User.objects.create_user(
            username='object-owner-a', phone='13900008001', password='test-only')
        cls.company_a, cls.post_a = create_company_with_owner(cls.owner_a, '对象矩阵A厂')
        cls.owner_b = User.objects.create_user(
            username='object-owner-b', phone='13900008002', password='test-only')
        cls.company_b, cls.post_admin_b = create_company_with_owner(cls.owner_b, '对象矩阵B厂')
        cls.worker_b = User.objects.create_user(
            username='object-worker-b', phone='13900008003', password='test-only')
        cls.post_b = Post.objects.create(
            company=cls.company_b, name='B厂普通岗', permissions=['orders.view'])
        cls.member_b = UserCompany.objects.create(
            user=cls.worker_b, company=cls.company_b, post=cls.post_b,
            status=UserCompany.Status.ACTIVE)
        cls.notification_b = Notification.objects.create(
            company=cls.company_b, user=cls.worker_b, code='matrix',
            title='B厂通知', message='不得跨厂已读')

        cls.group_b = CustomerGroup.objects.create(company=cls.company_b, name='B厂分组')
        cls.customer_b = Customer.objects.create(
            company=cls.company_b, customer_no='OM-CU-B', name='B厂客户', group=cls.group_b)
        cls.style_b = Style.objects.create(
            company=cls.company_b, style_no='OM-S-B', name='B厂款式', customer=cls.customer_b)
        cls.process_b = Process.objects.create(
            company=cls.company_b, name='B厂车缝', default_price='2.000', is_fixed=True)
        StyleProcess.objects.create(style=cls.style_b, process=cls.process_b, price='2.000')
        cls.template_b = ProcessTemplate.objects.create(
            company=cls.company_b, name='B厂模板', items=[])
        cls.order_b = Order.objects.create(
            company=cls.company_b, order_no='OM-ORDER-B', customer=cls.customer_b,
            style=cls.style_b)
        OrderItem.objects.create(
            order=cls.order_b, color='蓝', size='M', qty=20, price='10.000')
        cls.production_b = Production.objects.create(
            company=cls.company_b, style=cls.style_b, customer=cls.customer_b,
            order=cls.order_b, bed_no='OM-BED-B', total_qty=20, plan_qty=20)
        ProductionProcess.objects.create(
            production=cls.production_b, process=cls.process_b, price='2.000')
        cls.ticket_b = Ticket.objects.create(
            company=cls.company_b, production=cls.production_b, bed_no='OM-BED-B',
            bundle_no='1', color='蓝', size='M', qty=20, qr_code='OM-QR-B')
        cls.record_b = Record.objects.create(
            company=cls.company_b, ticket=cls.ticket_b, process=cls.process_b,
            user=cls.worker_b, qty=2, price='2.000')
        cls.settlement_b = Settlement.objects.create(
            company=cls.company_b, user=cls.worker_b,
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            employee_count=1, amount='4.00', created_by=cls.owner_b)
        cls.apply_b = SettleApply.objects.create(
            company=cls.company_b, applicant=cls.worker_b, user=cls.worker_b,
            period_start=date(2026, 2, 1), period_end=date(2026, 2, 28), amount='4.00')

        cls.lot_b = Lot.objects.create(
            company=cls.company_b, lot_no='OM-LOT-B', fabric_name='B厂布料', qty=100)
        cls.supplier_b = Supplier.objects.create(
            company=cls.company_b, code='OM-SUP-B', name='B厂供应商')
        cls.purchase_b = PurchaseOrder.objects.create(
            company=cls.company_b, order_no='OM-PO-B', supplier=cls.supplier_b,
            ordered_at=date(2026, 1, 1))
        cls.purchase_item_b = PurchaseItem.objects.create(
            order=cls.purchase_b, fabric_name='B厂布料', color='蓝', qty=100, price='3.000')
        cls.stock_in_b = StockIn.objects.create(
            company=cls.company_b, in_no='OM-SI-B', purchase=cls.purchase_b,
            in_at=timezone.now(), operator=cls.owner_b)
        StockInItem.objects.create(
            stock_in=cls.stock_in_b, lot=cls.lot_b, fabric_name='B厂布料', lot_no=cls.lot_b.lot_no,
            qty=100, unit_price='3.000', purchase_item=cls.purchase_item_b)
        cls.issue_b = MaterialIssue.objects.create(
            company=cls.company_b, issue_no='OM-MI-B', issue_at=timezone.now(),
            operator=cls.owner_b)
        IssueItem.objects.create(
            issue=cls.issue_b, production=cls.production_b, lot=cls.lot_b, qty=10)
        cls.receipt_b = Receipt.objects.create(
            company=cls.company_b, receipt_no='OM-RC-B', supplier=cls.supplier_b,
            purchase=cls.purchase_b)
        ReceiptItem.objects.create(
            receipt=cls.receipt_b, purchase_item=cls.purchase_item_b,
            fabric_name='B厂布料', lot_no='OM-LOT-B', arrived_qty=10, qualified_qty=10)

        cls.material_b = Material.objects.create(
            company=cls.company_b, code='OM-MAT-B', name='B厂物料', unit='米')
        cls.bom_b = Bom.objects.create(company=cls.company_b, style=cls.style_b, version=1)
        cls.bom_item_b = BomItem.objects.create(
            bom=cls.bom_b, material=cls.material_b, unit='米', unit_usage='1.2000')
        cls.quotation_b = Quotation.objects.create(
            company=cls.company_b, quote_no='OM-Q-B', customer=cls.customer_b,
            style=cls.style_b, qty=20)
        QuoteLabor.objects.create(quotation=cls.quotation_b, labor_base='2.000')
        QuoteMaterial.objects.create(
            quotation=cls.quotation_b, material=cls.material_b, bom_item=cls.bom_item_b,
            quote_unit_usage='1.2000', price='3.000')
        cls.receivable_b = Receivable.objects.create(
            company=cls.company_b, receivable_no='OM-AR-B', customer=cls.customer_b,
            order=cls.order_b, amount='200.00')
        cls.shipment_b = Shipment.objects.create(
            company=cls.company_b, shipment_no='OM-SH-B', order=cls.order_b,
            customer=cls.customer_b, total_qty=1)
        ShipmentItem.objects.create(
            shipment=cls.shipment_b, color='蓝', size='M', qty=1)
        cls.box_b = PackingBox.objects.create(
            shipment=cls.shipment_b, box_no='1', color='蓝', size='M', qty=1)
        cls.quality_b = QualityRecord.objects.create(
            company=cls.company_b, ticket=cls.ticket_b, process=cls.process_b,
            defect_qty=2, reason='B厂返修', inspector=cls.owner_b)
        cls.outsource_b = OutsourceOrder.objects.create(
            company=cls.company_b, no='OM-OS-B', supplier=cls.supplier_b,
            factory_name='B厂外协', order=cls.order_b, style=cls.style_b,
            send_qty=5, unit_price='1.000', amount='5.00', creator=cls.owner_b)
        cls.kanban_b = KanbanTemplate.objects.create(company=cls.company_b, name='B厂看板')

        # 统一生产路线及其真实节点。模板和床次实例分别覆盖路线管理与数量动作。
        cls.stages_b = {}
        for code, name, stage_type in (
                ('OM-CUT', 'B厂裁剪', 'cutting'), ('OM-SEW', 'B厂车缝', 'sewing'),
                ('OM-QC', 'B厂尾查', 'final_inspection'),
                ('OM-PACK', 'B厂包装', 'packing'),
                ('OM-IN', 'B厂成品入库', 'finished_inbound')):
            cls.stages_b[stage_type] = ProductionStage.objects.create(
                company=cls.company_b, code=code, name=name, stage_type=stage_type)
        cls.route_template_b = ProductionRoute.objects.create(
            company=cls.company_b, code='OM-ROUTE-T-B', name='B厂路线模板', is_template=True)
        cls.template_node_b = RouteNode.objects.create(
            company=cls.company_b, route=cls.route_template_b,
            stage=cls.stages_b['sewing'], stage_code='OM-SEW', stage_name='B厂车缝',
            stage_type='sewing', sequence=1)
        cls.route_b = ProductionRoute.objects.create(
            company=cls.company_b, code='OM-ROUTE-B', name='B厂床次路线',
            production=cls.production_b, source_template=cls.route_template_b)
        cls.nodes_b = {}
        for sequence, stage_type in enumerate(
                ('cutting', 'sewing', 'final_inspection', 'packing', 'finished_inbound'), 1):
            stage = cls.stages_b[stage_type]
            cls.nodes_b[stage_type] = RouteNode.objects.create(
                company=cls.company_b, route=cls.route_b, stage=stage,
                stage_code=stage.code, stage_name=stage.name, stage_type=stage_type,
                sequence=sequence)
        cls.ledger_b = QuantityLedger.objects.create(
            company=cls.company_b, route=cls.route_b, node=cls.nodes_b['cutting'],
            production=cls.production_b, ticket=cls.ticket_b, color='蓝', size='M',
            event_type='received', quantity=20, operator=cls.owner_b,
            request_id='OM-MFG-LEDGER-B', request_fingerprint='matrix')

        cls.cutting_handover_b = CuttingHandover.objects.create(
            company=cls.company_b, ticket=cls.ticket_b,
            source_node=cls.nodes_b['cutting'], target_node=cls.nodes_b['sewing'],
            quantity=1, receiver=cls.worker_b, request_id='OM-CUT-HO-B',
            request_fingerprint='matrix', created_by=cls.owner_b)
        cls.handover_b = Handover.objects.create(
            company=cls.company_b, handover_no='OM-HO-B', batch_no='OM-HO-BATCH-B',
            kind='cutting', source_node=cls.nodes_b['cutting'],
            target_node=cls.nodes_b['sewing'], receiver=cls.worker_b,
            created_by=cls.owner_b)
        cls.handover_item_b = HandoverItem.objects.create(
            company=cls.company_b, handover=cls.handover_b, ticket=cls.ticket_b, quantity=1)
        cls.handover_policy_b = HandoverPolicy.objects.create(
            company=cls.company_b, kind='cutting', mode='off', updated_by=cls.owner_b)

        cls.packing_task_b = PackingTask.objects.create(
            company=cls.company_b, task_no='OM-PT-B', route=cls.route_b,
            packing_node=cls.nodes_b['packing'], inbound_node=cls.nodes_b['finished_inbound'],
            request_id='OM-PT-CREATE-B', request_fingerprint='matrix', creator=cls.owner_b)
        cls.finished_box_b = FinishedPackingBox.objects.create(
            company=cls.company_b, task=cls.packing_task_b, box_no='OM-FG-BOX-B')
        PackingBoxLine.objects.create(
            company=cls.company_b, box=cls.finished_box_b, ticket=cls.ticket_b,
            color='蓝', size='M', quantity=1)
        cls.inbound_b = FinishedInbound.objects.create(
            company=cls.company_b, document_no='OM-FI-B', route=cls.route_b,
            packing_node=cls.nodes_b['packing'], inbound_node=cls.nodes_b['finished_inbound'],
            request_id='OM-FI-CREATE-B', request_fingerprint='matrix', creator=cls.owner_b)
        FinishedInboundLine.objects.create(
            company=cls.company_b, document=cls.inbound_b, box=cls.finished_box_b,
            ticket=cls.ticket_b, color='蓝', size='M', quantity=1)
        cls.inspection_b = QualityInspection.objects.create(
            company=cls.company_b, ticket=cls.ticket_b, route=cls.route_b,
            node=cls.nodes_b['final_inspection'], inspection_type='final_inspection',
            inspected=1, qualified=1, inspector=cls.owner_b)

        cls.tech_pack_b = TechPackVersion.objects.create(
            company=cls.company_b, order=cls.order_b, style=cls.style_b, version_no=1,
            pattern_no='OM-PATTERN-B', measurements=[{'part': '衣长', 'value': 60}],
            requirements='B厂工艺要求', content_hash='', created_by=cls.owner_b)
        cls.tech_pack_b.content_hash = content_fingerprint(cls.tech_pack_b)
        cls.tech_pack_b.save(update_fields=['content_hash', 'updated_at'])
        cls.sample_b = SampleTask.objects.create(
            company=cls.company_b, tech_pack=cls.tech_pack_b, title='B厂产前样',
            assignee=cls.worker_b, created_by=cls.owner_b)
        cls.issue_pp_b = ReviewIssue.objects.create(
            company=cls.company_b, tech_pack=cls.tech_pack_b, title='B厂产前问题',
            raised_by=cls.owner_b)
        cls.briefing_b = BriefingTarget.objects.create(
            company=cls.company_b, tech_pack=cls.tech_pack_b, employee=cls.worker_b,
            created_by=cls.owner_b)
        cls.craft_template_b = CraftTemplate.objects.create(
            company=cls.company_b, kind='requirements', name='B厂工艺模板',
            content='B厂模板内容', created_by=cls.owner_b)

        cls.saved_report_b = SavedReport.objects.create(
            company=cls.company_b, user=cls.owner_b, name='B厂订单报表', dataset='orders',
            plan={'dataset': 'orders', 'metrics': ['order_count'], 'limit': 10})
        cls.export_task_b = AiExportTask.objects.create(
            company=cls.company_b, user=cls.owner_b, dataset='orders',
            plan=cls.saved_report_b.plan)
        cls.schedule_b = AiReportSchedule.objects.create(
            company=cls.company_b, user=cls.owner_b, saved_report=cls.saved_report_b,
            name='B厂每日订单报表', frequency='daily', hour=9, minute=0,
            next_run_at=timezone.now())
        cls.schedule_run_b = AiScheduledRun.objects.create(
            schedule=cls.schedule_b, scheduled_at=timezone.now(), status='failed',
            error_code='execution_error')

        cls.payable_b = Payable.objects.create(
            company=cls.company_b, payable_no='OM-AP-B', supplier=cls.supplier_b,
            supplier_name=cls.supplier_b.name, purchase=cls.purchase_b, amount='10.00',
            payable_at=date(2026, 1, 1))
        cls.supplier_payment_b = SupplierPayment.objects.create(
            company=cls.company_b, payable=cls.payable_b, supplier=cls.supplier_b,
            supplier_name=cls.supplier_b.name, amount='1.00', paid_at=timezone.now(),
            method='cash', transaction_ref='OM-SP-B', operator=cls.owner_b)
        cls.cash_entry_b = CashEntry.objects.create(
            company=cls.company_b, entry_no='OM-CE-B', direction='expense',
            category='其他', amount='1.00', occurred_at=timezone.now(), operator=cls.owner_b)
        cls.salary_payment_b = SalaryPayment.objects.create(
            company=cls.company_b, employee=cls.worker_b, paid_at=timezone.now(),
            method='cash', transaction_ref='OM-SALARY-PAY-B', amount='1.00',
            operator=cls.owner_b)

        # 请求体用例的本厂合法夹具：每次只替换一个外键为他厂对象，避免被其他
        # 必填项或关联校验先拒绝而形成假绿灯。
        cls.group_a = CustomerGroup.objects.create(company=cls.company_a, name='A厂分组')
        cls.customer_a = Customer.objects.create(
            company=cls.company_a, customer_no='OM-CU-A', name='A厂客户', group=cls.group_a)
        cls.style_a = Style.objects.create(
            company=cls.company_a, style_no='OM-S-A', name='A厂款式', customer=cls.customer_a)
        cls.process_a = Process.objects.create(
            company=cls.company_a, name='A厂车缝', default_price='1.000', is_fixed=True)
        cls.order_a = Order.objects.create(
            company=cls.company_a, order_no='OM-ORDER-A', customer=cls.customer_a,
            style=cls.style_a)
        OrderItem.objects.create(order=cls.order_a, color='蓝', size='M', qty=20, price='9.000')
        cls.production_a = Production.objects.create(
            company=cls.company_a, style=cls.style_a, customer=cls.customer_a,
            order=cls.order_a, bed_no='OM-BED-A', total_qty=20)
        ProductionProcess.objects.create(
            production=cls.production_a, process=cls.process_a, price='1.000')
        cls.ticket_a = Ticket.objects.create(
            company=cls.company_a, production=cls.production_a, bed_no='OM-BED-A',
            bundle_no='1', color='蓝', size='M', qty=20, qr_code='OM-QR-A')
        cls.material_a = Material.objects.create(
            company=cls.company_a, code='OM-MAT-A', name='A厂物料', unit='米')
        cls.bom_a = Bom.objects.create(company=cls.company_a, style=cls.style_a, version=1)
        cls.bom_item_a = BomItem.objects.create(
            bom=cls.bom_a, material=cls.material_a, unit='米', unit_usage='1.0000')
        cls.lot_a = Lot.objects.create(
            company=cls.company_a, lot_no='OM-LOT-A', fabric_name='A厂布料', qty=100)
        cls.supplier_a = Supplier.objects.create(
            company=cls.company_a, code='OM-SUP-A', name='A厂供应商')
        cls.purchase_a = PurchaseOrder.objects.create(
            company=cls.company_a, order_no='OM-PO-A', supplier=cls.supplier_a,
            ordered_at=date(2026, 1, 1))
        cls.purchase_item_a = PurchaseItem.objects.create(
            order=cls.purchase_a, fabric_name='A厂布料', qty=100, price='2.000')

    def setUp(self):
        self.api = APIClient(raise_request_exception=False)
        self.api.force_authenticate(user=self.owner_a)
        self.api.credentials(HTTP_X_COMPANY_ID=str(self.company_a.id))

    def _route_cases(self):
        targets = {
            'api/companies/<int:pk>/switch': {'pk': self.company_b.pk},
            'api/employees/<int:pk>/approve': {'pk': self.member_b.pk},
            'api/employees/<int:pk>/reject': {'pk': self.member_b.pk},
            'api/employees/<int:pk>/resign': {'pk': self.member_b.pk},
            'api/employees/<int:pk>/rehire': {'pk': self.member_b.pk},
            'api/employees/<int:pk>/set-post': {'pk': self.member_b.pk},
            'api/employees/<int:pk>/set-group': {'pk': self.member_b.pk},
            'api/employees/<int:pk>/pin': {'pk': self.member_b.pk},
            'api/employees/<int:pk>/assigned-processes': {'pk': self.member_b.pk},
            'api/posts/<int:pk>/copy': {'pk': self.post_b.pk},
            'api/posts/<int:pk>': {'pk': self.post_b.pk},
            'api/styles/<int:pk>': {'pk': self.style_b.pk},
            'api/styles/<int:pk>/copy': {'pk': self.style_b.pk},
            'api/styles/<int:pk>/sync-process': {'pk': self.style_b.pk},
            'api/processes/<int:pk>': {'pk': self.process_b.pk},
            'api/process-templates/<int:pk>': {'pk': self.template_b.pk},
            'api/customer-groups/<int:pk>': {'pk': self.group_b.pk},
            'api/customers/<int:pk>': {'pk': self.customer_b.pk},
            'api/tickets/<int:pk>': {'pk': self.ticket_b.pk},
            'api/productions/<int:pk>': {'pk': self.production_b.pk},
            'api/productions/<int:pk>/lock': {'pk': self.production_b.pk},
            'api/productions/<int:pk>/cut-done': {'pk': self.production_b.pk},
            'api/productions/<int:pk>/order-processes': {'pk': self.production_b.pk},
            'api/productions/<int:pk>/bundles': {'pk': self.production_b.pk},
            'api/productions/<int:pk>/cut-detail': {'pk': self.production_b.pk},
            'api/orders/<int:pk>': {'pk': self.order_b.pk},
            'api/orders/<int:pk>/status': {'pk': self.order_b.pk},
            'api/orders/<int:pk>/progress': {'pk': self.order_b.pk},
            'api/orders/<int:pk>/audit-logs': {'pk': self.order_b.pk},
            'api/records/<int:pk>': {'pk': self.record_b.pk},
            'api/salary/employee/<int:uid>': {'uid': self.worker_b.pk},
            'api/salary/employee/<int:uid>/config': {'uid': self.worker_b.pk},
            'api/salary/settlements/<int:pk>/payslip': {'pk': self.settlement_b.pk},
            'api/salary/settlements/<int:pk>/payslip/export': {'pk': self.settlement_b.pk},
            'api/salary/settlements/<int:pk>/revoke': {'pk': self.settlement_b.pk},
            'api/salary/settle-applies/<int:pk>/review': {'pk': self.apply_b.pk},
            'api/salary/settle-applies/<int:pk>/pay': {'pk': self.apply_b.pk},
            'api/salary/settle-applies/<int:pk>/void': {'pk': self.apply_b.pk},
            'api/notifications/<int:pk>/read': {'pk': self.notification_b.pk},
            'api/lots/<int:pk>': {'pk': self.lot_b.pk},
            'api/productions/<int:production_id>/lot-links': {
                'production_id': self.production_b.pk},
            'api/trace/lot/<int:pk>': {'pk': self.lot_b.pk},
            'api/trace/ticket/<int:pk>': {'pk': self.ticket_b.pk},
            'api/trace/production/<int:pk>': {'pk': self.production_b.pk},
            'api/purchase-orders/<int:pk>': {'pk': self.purchase_b.pk},
            'api/purchase-orders/<int:pk>/confirm': {'pk': self.purchase_b.pk},
            'api/stock-ins/<int:pk>': {'pk': self.stock_in_b.pk},
            'api/stock-ins/<int:pk>/confirm': {'pk': self.stock_in_b.pk},
            'api/material-issues/<int:pk>': {'pk': self.issue_b.pk},
            'api/material-issues/<int:pk>/confirm': {'pk': self.issue_b.pk},
            'api/suppliers/<int:pk>': {'pk': self.supplier_b.pk},
            'api/receipts/<int:pk>': {'pk': self.receipt_b.pk},
            'api/receipts/<int:pk>/confirm': {'pk': self.receipt_b.pk},
            'api/kanban/templates/<int:pk>': {'pk': self.kanban_b.pk},
            'api/kanban/templates/<int:pk>/default': {'pk': self.kanban_b.pk},
            'api/tech/materials/<int:pk>': {'pk': self.material_b.pk},
            'api/tech/boms/<int:pk>': {'pk': self.bom_b.pk},
            'api/tech/boms/<int:pk>/activate': {'pk': self.bom_b.pk},
            'api/tech/boms/<int:pk>/fork': {'pk': self.bom_b.pk},
            'api/tech/boms/<int:pk>/references': {'pk': self.bom_b.pk},
            'api/cost/orders/<int:order_id>/recompute': {'order_id': self.order_b.pk},
            'api/cost/orders/<int:order_id>/overhead': {'order_id': self.order_b.pk},
            'api/quotation/quotations/<int:pk>': {'pk': self.quotation_b.pk},
            'api/quotation/quotations/<int:pk>/mark-status': {'pk': self.quotation_b.pk},
            'api/quotation/quotations/<int:pk>/backfill-compare': {'pk': self.quotation_b.pk},
            'api/finance/customers/<int:customer_id>/receipts-batch': {
                'customer_id': self.customer_b.pk},
            'api/finance/receivables/<int:receivable_id>/receipts/revoke': {
                'receivable_id': self.receivable_b.pk},
            'api/finance/receivables/<int:receivable_id>/receipts': {
                'receivable_id': self.receivable_b.pk},
            'api/finance/receivables/<int:receivable_id>/receipt-lines': {
                'receivable_id': self.receivable_b.pk},
            'api/finance/receivables/<int:receivable_id>/void': {
                'receivable_id': self.receivable_b.pk},
            'api/finance/receivables/<int:receivable_id>': {
                'receivable_id': self.receivable_b.pk},
            'api/shipments/<int:pk>/undelivered': {'pk': self.shipment_b.pk},
            'api/shipments/<int:pk>/print-logs': {'pk': self.shipment_b.pk},
            'api/shipments/<int:pk>/audit': {'pk': self.shipment_b.pk},
            'api/shipments/<int:pk>/void': {'pk': self.shipment_b.pk},
            'api/shipments/<int:pk>/boxes': {'pk': self.shipment_b.pk},
            'api/shipments/<int:pk>/boxes/<int:box_id>': {
                'pk': self.shipment_b.pk, 'box_id': self.box_b.pk},
            'api/shipments/<int:pk>': {'pk': self.shipment_b.pk},
            'api/quality/records/<int:pk>': {'pk': self.quality_b.pk},
            'api/outsource/orders/<int:pk>': {'pk': self.outsource_b.pk},
            'api/ai/report/exports/<uuid:pk>': {'pk': self.export_task_b.pk},
            'api/ai/report/exports/<uuid:pk>/download': {'pk': self.export_task_b.pk},
            'api/ai/report/saved/<int:pk>': {'pk': self.saved_report_b.pk},
            'api/ai/report/saved/<int:pk>/execute': {'pk': self.saved_report_b.pk},
            'api/ai/report/saved/<int:pk>/share': {'pk': self.saved_report_b.pk},
            'api/ai/report/schedules/<int:pk>': {'pk': self.schedule_b.pk},
            'api/ai/report/schedules/<int:pk>/run': {'pk': self.schedule_b.pk},
            'api/ai/report/schedules/runs/<int:pk>/retry': {'pk': self.schedule_run_b.pk},
            'api/cutting/handovers/<int:pk>/': {'pk': self.cutting_handover_b.pk},
            'api/cutting/handovers/<int:pk>/cancel/': {'pk': self.cutting_handover_b.pk},
            'api/cutting/handovers/<int:pk>/sign/': {'pk': self.cutting_handover_b.pk},
            'api/finance/cash-entries/<int:pk>/void': {'pk': self.cash_entry_b.pk},
            'api/finance/payables/<int:pk>': {'pk': self.payable_b.pk},
            'api/finance/payables/<int:pk>/void': {'pk': self.payable_b.pk},
            'api/finance/supplier-payments/<int:pk>/void': {
                'pk': self.supplier_payment_b.pk},
            'api/finished-goods/boxes/<int:pk>': {'pk': self.finished_box_b.pk},
            'api/finished-goods/inbounds/<int:pk>': {'pk': self.inbound_b.pk},
            'api/finished-goods/inbounds/<int:pk>/approve': {'pk': self.inbound_b.pk},
            'api/finished-goods/inbounds/<int:pk>/reverse': {'pk': self.inbound_b.pk},
            'api/finished-goods/packing-tasks/<int:pk>': {'pk': self.packing_task_b.pk},
            'api/finished-goods/packing-tasks/<int:pk>/approve': {
                'pk': self.packing_task_b.pk},
            'api/finished-goods/packing-tasks/<int:pk>/reverse': {
                'pk': self.packing_task_b.pk},
            'api/handovers/<int:pk>/': {'pk': self.handover_b.pk},
            'api/handovers/<int:pk>/reassign': {'pk': self.handover_b.pk},
            'api/handovers/<int:pk>/receive': {'pk': self.handover_b.pk},
            'api/handovers/<int:pk>/reject': {'pk': self.handover_b.pk},
            'api/handovers/batches/<str:batch_no>/': {
                'batch_no': self.handover_b.batch_no},
            'api/handovers/policies/<int:pk>': {'pk': self.handover_policy_b.pk},
            'api/manufacturing/actions/<str:action>': {'action': 'receive'},
            'api/manufacturing/ledger/<int:pk>/reverse': {'pk': self.ledger_b.pk},
            'api/manufacturing/routes/<int:pk>': {'pk': self.route_template_b.pk},
            'api/manufacturing/routes/<int:pk>/balances': {'pk': self.route_b.pk},
            'api/manufacturing/routes/<int:pk>/copy': {'pk': self.route_template_b.pk},
            'api/manufacturing/routes/<int:pk>/dashboard': {'pk': self.route_b.pk},
            'api/manufacturing/routes/<int:pk>/instantiate': {'pk': self.route_template_b.pk},
            'api/manufacturing/routes/<int:pk>/new-version': {'pk': self.route_template_b.pk},
            'api/manufacturing/routes/<int:pk>/status': {'pk': self.route_template_b.pk},
            'api/manufacturing/routes/<int:pk>/timeline': {'pk': self.route_b.pk},
            'api/manufacturing/stages/<int:pk>': {'pk': self.stages_b['sewing'].pk},
            'api/manufacturing/stages/<int:pk>/status': {'pk': self.stages_b['sewing'].pk},
            'api/preproduction/briefing-targets/<int:pk>/sign': {'pk': self.briefing_b.pk},
            'api/preproduction/issues/<int:pk>': {'pk': self.issue_pp_b.pk},
            'api/preproduction/orders/<int:order_id>/status': {'order_id': self.order_b.pk},
            'api/preproduction/samples/<int:pk>/records': {'pk': self.sample_b.pk},
            'api/preproduction/tech-packs/<int:pack_id>/audit-events': {
                'pack_id': self.tech_pack_b.pk},
            'api/preproduction/tech-packs/<int:pack_id>/briefing-confirmation': {
                'pack_id': self.tech_pack_b.pk},
            'api/preproduction/tech-packs/<int:pack_id>/briefing-targets': {
                'pack_id': self.tech_pack_b.pk},
            'api/preproduction/tech-packs/<int:pack_id>/issues': {
                'pack_id': self.tech_pack_b.pk},
            'api/preproduction/tech-packs/<int:pack_id>/samples': {
                'pack_id': self.tech_pack_b.pk},
            'api/preproduction/tech-packs/<int:pk>': {'pk': self.tech_pack_b.pk},
            'api/preproduction/tech-packs/<int:pk>/approval': {'pk': self.tech_pack_b.pk},
            'api/preproduction/templates/<int:pk>': {'pk': self.craft_template_b.pk},
            'api/quality/inspections/<int:pk>': {'pk': self.inspection_b.pk},
            'api/quality/inspections/<int:pk>/confirm': {'pk': self.inspection_b.pk},
            'api/salary/payments/<int:pk>': {'pk': self.salary_payment_b.pk},
            'api/salary/payments/<int:pk>/void': {'pk': self.salary_payment_b.pk},
        }
        payloads = {
            'api/employees/<int:pk>/reject': {'reason': '跨厂拒绝'},
            'api/employees/<int:pk>/set-post': {'post_id': self.post_a.pk},
            'api/employees/<int:pk>/set-group': {'group_no': 2},
            'api/employees/<int:pk>/assigned-processes': {'process_ids': []},
            'api/posts/<int:pk>/copy': {'name': '不得复制'},
            'api/styles/<int:pk>/copy': {'style_no': 'OM-NO-COPY'},
            'api/styles/<int:pk>/sync-process': {'process_ids': []},
            'api/productions/<int:pk>/lock': {'is_locked': True},
            'api/productions/<int:pk>/cut-done': {'is_cut_done': True},
            'api/productions/<int:pk>/order-processes': {'process_items': []},
            'api/orders/<int:pk>/status': {'status': 'done'},
            'api/records/<int:pk>': {'qty': 1},
            'api/salary/employee/<int:uid>/config': {'salary_type': 'process'},
            'api/salary/settle-applies/<int:pk>/review': {'action': 'approve'},
            'api/salary/settle-applies/<int:pk>/pay': {'payer': '不得支付'},
            'api/salary/settle-applies/<int:pk>/void': {'void_reason': '不得作废'},
            'api/productions/<int:production_id>/lot-links': {'links': []},
            'api/cost/orders/<int:order_id>/overhead': {'overhead_cost': '1.00'},
            'api/quotation/quotations/<int:pk>/mark-status': {'status': 'sent'},
            'api/finance/customers/<int:customer_id>/receipts-batch': {
                'amount': '1.00', 'method': 'cash'},
            'api/finance/receivables/<int:receivable_id>/receipts': {
                'amount': '1.00', 'method': 'cash', 'received_at': timezone.now().isoformat()},
            'api/shipments/<int:pk>/print-logs': {
                'print_key': 'OM-NO-PRINT', 'template': 'shipment'},
            'api/shipments/<int:pk>/boxes': {'box_no': '2', 'qty': 1},
            'api/quality/records/<int:pk>': {'action': 'finish'},
            'api/outsource/orders/<int:pk>': {'action': 'receive', 'qty': 1},
            'api/ai/report/saved/<int:pk>/share': {'visibility': 'private'},
            'api/ai/report/schedules/<int:pk>/run': {
                'request_id': '00000000-0000-4000-8000-000000000101'},
            'api/ai/report/schedules/runs/<int:pk>/retry': {
                'request_id': '00000000-0000-4000-8000-000000000102'},
            'api/finance/cash-entries/<int:pk>/void': {'reason': '跨厂不得作废'},
            'api/finance/supplier-payments/<int:pk>/void': {'reason': '跨厂不得作废'},
            'api/finished-goods/inbounds/<int:pk>/approve': {'request_id': 'OM-NO-FI-APPROVE'},
            'api/finished-goods/inbounds/<int:pk>/reverse': {
                'request_id': 'OM-NO-FI-REVERSE', 'reason': '跨厂不得冲销'},
            'api/finished-goods/packing-tasks/<int:pk>/approve': {
                'request_id': 'OM-NO-PT-APPROVE'},
            'api/finished-goods/packing-tasks/<int:pk>/reverse': {
                'request_id': 'OM-NO-PT-REVERSE', 'reason': '跨厂不得冲销'},
            'api/handovers/<int:pk>/reassign': {
                'request_id': 'OM-NO-HO-REASSIGN', 'receiver': self.worker_b.pk,
                'reason': '跨厂不得改派'},
            'api/handovers/<int:pk>/receive': {
                'request_id': 'OM-NO-HO-RECEIVE',
                'items': [{'id': self.handover_item_b.pk, 'received_quantity': 1}]},
            'api/handovers/<int:pk>/reject': {
                'request_id': 'OM-NO-HO-REJECT', 'reason': '跨厂不得拒收'},
            'api/manufacturing/actions/<str:action>': {
                'ticket_id': self.ticket_b.pk, 'node_id': self.nodes_b['cutting'].pk,
                'quantity': 1, 'request_id': 'OM-NO-MFG-ACTION'},
            'api/manufacturing/ledger/<int:pk>/reverse': {
                'request_id': 'OM-NO-MFG-REVERSE', 'reason': '跨厂不得冲销'},
            'api/manufacturing/routes/<int:pk>': {
                'name': '不得修改', 'nodes': [{
                    'stage_id': self.stages_b['sewing'].pk, 'sequence': 1}]},
            'api/manufacturing/routes/<int:pk>/copy': {
                'code': 'OM-NO-ROUTE-COPY', 'name': '不得复制'},
            'api/manufacturing/routes/<int:pk>/instantiate': {
                'production_id': self.production_b.pk},
            'api/manufacturing/routes/<int:pk>/new-version': {
                'code': 'OM-NO-ROUTE-VERSION', 'name': '不得新建版本'},
            'api/manufacturing/routes/<int:pk>/status': {'is_active': False},
            'api/manufacturing/stages/<int:pk>': {
                'code': 'OM-SEW', 'name': '不得修改', 'stage_type': 'sewing'},
            'api/manufacturing/stages/<int:pk>/status': {'is_active': False},
            'api/preproduction/issues/<int:pk>': {
                'action': 'rectify', 'rectification': '跨厂不得整改'},
            'api/preproduction/samples/<int:pk>/records': {'result': 'passed'},
            'api/preproduction/tech-packs/<int:pack_id>/samples': {'title': '不得新增'},
            'api/preproduction/tech-packs/<int:pack_id>/issues': {'title': '不得新增'},
            'api/preproduction/tech-packs/<int:pack_id>/briefing-targets': {
                'employee': self.worker_b.pk},
            'api/preproduction/tech-packs/<int:pk>/approval': {'action': 'approve'},
            'api/preproduction/templates/<int:pk>': {
                'kind': 'requirements', 'name': '不得修改', 'content': '不得修改'},
            'api/quality/inspections/<int:pk>/confirm': {'request_id': 'OM-NO-QC-CONFIRM'},
            'api/salary/payments/<int:pk>/void': {'reason': '跨厂不得作废'},
        }
        cases = {}
        for path, ids in targets.items():
            url = '/' + path
            for key, value in ids.items():
                for converter in ('int', 'uuid', 'str', 'slug', 'path'):
                    url = url.replace(f'<{converter}:{key}>', str(value))
            cases[path] = (url, payloads.get(path, {}))
        return cases

    def test_01_every_parameterized_business_route_has_real_fixture(self):
        discovered = {
            path for path, _view, _methods in _routes()
            if '<' in path and path != 'api/kanban-admin/<path:asset>'
        }
        registered = set(self._route_cases())
        self.assertEqual(discovered, registered,
                         f'缺少真实夹具: {sorted(discovered - registered)}；'
                         f'失效登记: {sorted(registered - discovered)}')

    def test_02_foreign_object_path_ids_are_invisible_and_unchanged(self):
        before = _company_fingerprint(self.company_b)
        cases = self._route_cases()
        failures = []
        for path, view, methods in _routes():
            if path not in cases:
                continue
            url, payload = cases[path]
            for method in methods:
                with self.subTest(path=path, method=method):
                    response = getattr(self.api, method)(url, data=payload, format='json')
                    # 应付详情 POST 是业务层明确禁用的方法，在对象查询前固定返回 400；
                    # 其余对象/动作仍必须因真实 B 厂对象不可见而返回 404。
                    expected = (400 if (path, method) == ('api/finance/payables/<int:pk>', 'post')
                                else 404)
                    if response.status_code != expected:
                        failures.append(
                            f'{method.upper()} /{path}: {response.status_code} '
                            f'{getattr(response, "data", None)} ({view.__name__})')
        self.assertEqual(failures, [], '他厂真实对象未被统一隐藏:\n' + '\n'.join(failures))
        self.assertEqual(_company_fingerprint(self.company_b), before,
                         '跨厂请求改变了 B 厂主表或关联子表数据')

    def test_03_foreign_ids_in_request_bodies_are_rejected(self):
        item_a = {'color': '蓝', 'size': 'M', 'qty': 1}
        material_row_a = {'material': self.material_a.pk,
                          'quote_unit_usage': '1.0000', 'price': '2.000'}
        cases = [
            ('客户引用他厂分组', '/api/customers', {
                'customer_no': 'OM-BODY-CU-GROUP', 'name': '非法客户', 'group': self.group_b.pk}),
            ('款式引用他厂客户', '/api/styles', {
                'style_no': 'OM-BODY-S-CU', 'name': '非法款式', 'customer': self.customer_b.pk}),
            ('款式引用他厂工序', '/api/styles', {
                'style_no': 'OM-BODY-S-PROCESS', 'name': '非法款式',
                'process_items': [{'process_id': self.process_b.pk, 'price': '1.000'}]}),
            ('工序模板引用他厂工序', '/api/process-templates', {
                'name': 'OM-BODY-TPL', 'items': [{'process_id': self.process_b.pk}]}),
            ('订单引用他厂客户', '/api/orders', {
                'order_no': 'OM-BODY-O-CU', 'style': self.style_a.pk,
                'customer': self.customer_b.pk, 'items': [dict(item_a, price='1.000')]}),
            ('订单引用他厂款式', '/api/orders', {
                'order_no': 'OM-BODY-O-STYLE', 'style': self.style_b.pk,
                'customer': self.customer_a.pk, 'items': [dict(item_a, price='1.000')]}),
            ('订单引用他厂工序', '/api/orders', {
                'order_no': 'OM-BODY-O-PROCESS', 'style': self.style_a.pk,
                'customer': self.customer_a.pk, 'items': [dict(item_a, price='1.000')],
                'process_items': [{'process_id': self.process_b.pk, 'price': '1.000'}]}),
            ('床次引用他厂款式', '/api/productions', {
                'style': self.style_b.pk, 'bed_no': 'OM-BODY-P-STYLE', 'total_qty': 1,
                'bom_bypass': True, 'bypass_reason': '隔离矩阵'}),
            ('床次引用他厂客户', '/api/productions', {
                'style': self.style_a.pk, 'customer': self.customer_b.pk,
                'bed_no': 'OM-BODY-P-CU', 'total_qty': 1,
                'bom_bypass': True, 'bypass_reason': '隔离矩阵'}),
            ('床次引用他厂订单', '/api/productions', {
                'style': self.style_a.pk, 'order_id': self.order_b.pk,
                'bed_no': 'OM-BODY-P-ORDER', 'total_qty': 1,
                'bom_bypass': True, 'bypass_reason': '隔离矩阵'}),
            ('床次引用他厂工序', '/api/productions', {
                'style': self.style_a.pk, 'bed_no': 'OM-BODY-P-PROCESS', 'total_qty': 1,
                'bom_bypass': True, 'bypass_reason': '隔离矩阵',
                'process_items': [{'process_id': self.process_b.pk, 'price': '1.000'}]}),
            ('报工引用他厂菲票', '/api/records', {
                'ticket_id': self.ticket_b.pk, 'process_id': self.process_a.pk, 'qty': 1}),
            ('报工引用他厂工序', '/api/records', {
                'ticket_id': self.ticket_a.pk, 'process_id': self.process_b.pk, 'qty': 1}),
            ('报工引用他厂员工', '/api/records', {
                'ticket_id': self.ticket_a.pk, 'process_id': self.process_a.pk,
                'user_id': self.worker_b.pk, 'qty': 1}),
            ('编菲引用他厂床次', '/api/tickets/batch', {
                'production_id': self.production_b.pk, 'items': [item_a]}),
            ('质检引用他厂菲票', '/api/quality/records', {
                'ticket': self.ticket_b.pk, 'defect_qty': 1, 'reason': '隔离矩阵'}),
            ('质检引用他厂工序', '/api/quality/records', {
                'ticket': self.ticket_a.pk, 'process': self.process_b.pk,
                'defect_qty': 1, 'reason': '隔离矩阵'}),
            ('质检引用他厂工人', '/api/quality/records', {
                'ticket': self.ticket_a.pk, 'process': self.process_a.pk,
                'producer': self.worker_b.pk, 'defect_qty': 1, 'reason': '隔离矩阵'}),
            ('外发引用他厂供应商', '/api/outsource/orders', {
                'supplier': self.supplier_b.pk, 'send_qty': 1, 'unit_price': '1.000'}),
            ('外发引用他厂款式', '/api/outsource/orders', {
                'factory_name': '非法外发', 'style': self.style_b.pk,
                'send_qty': 1, 'unit_price': '1.000'}),
            ('外发引用他厂订单', '/api/outsource/orders', {
                'factory_name': '非法外发', 'order': self.order_b.pk,
                'send_qty': 1, 'unit_price': '1.000'}),
            ('出货引用他厂订单', '/api/shipments', {
                'order': self.order_b.pk, 'customer': self.customer_a.pk, 'items': [item_a]}),
            ('出货引用他厂客户', '/api/shipments', {
                'order': self.order_a.pk, 'customer': self.customer_b.pk, 'items': [item_a]}),
            ('采购引用他厂物料', '/api/purchase-orders', {
                'order_no': 'OM-BODY-PO-MAT', 'ordered_at': '2026-01-01',
                'items': [{'fabric_name': '布', 'qty': 1, 'material_id': self.material_b.pk}]}),
            ('入库引用他厂采购单', '/api/stock-ins', {
                'in_no': 'OM-BODY-SI-PO', 'purchase': self.purchase_b.pk,
                'items': [{'fabric_name': '布', 'lot_no': 'OM-BODY-LOT-1', 'qty': 1}]}),
            ('入库引用他厂采购行', '/api/stock-ins', {
                'in_no': 'OM-BODY-SI-PI', 'items': [{
                    'fabric_name': '布', 'lot_no': 'OM-BODY-LOT-2', 'qty': 1,
                    'purchase_item': self.purchase_item_b.pk}]}),
            ('入库引用他厂物料', '/api/stock-ins', {
                'in_no': 'OM-BODY-SI-MAT', 'items': [{
                    'fabric_name': '布', 'lot_no': 'OM-BODY-LOT-3', 'qty': 1,
                    'material_id': self.material_b.pk}]}),
            ('领料引用他厂床次', '/api/material-issues', {
                'issue_no': 'OM-BODY-MI-P', 'items': [{
                    'production_id': self.production_b.pk, 'lot_id': self.lot_a.pk, 'qty': 1}]}),
            ('领料引用他厂批次', '/api/material-issues', {
                'issue_no': 'OM-BODY-MI-LOT', 'items': [{
                    'production_id': self.production_a.pk, 'lot_id': self.lot_b.pk, 'qty': 1}]}),
            ('领料引用他厂定额行', '/api/material-issues', {
                'issue_no': 'OM-BODY-MI-BOM', 'items': [{
                    'production_id': self.production_a.pk, 'lot_id': self.lot_a.pk,
                    'bom_item_id': self.bom_item_b.pk, 'qty': 1}]}),
            ('领料引用他厂物料', '/api/material-issues', {
                'issue_no': 'OM-BODY-MI-MAT', 'items': [{
                    'production_id': self.production_a.pk, 'lot_id': self.lot_a.pk,
                    'material_id': self.material_b.pk, 'qty': 1}]}),
            ('本厂床次引用他厂批次', f'/api/productions/{self.production_a.pk}/lot-links', {
                'items': [{'lot_id': self.lot_b.pk, 'qty': 1}]}),
            ('暂收引用他厂供应商', '/api/receipts', {
                'receipt_no': 'OM-BODY-RC-SUP', 'supplier': self.supplier_b.pk,
                'purchase': self.purchase_a.pk, 'items': []}),
            ('暂收引用他厂采购单', '/api/receipts', {
                'receipt_no': 'OM-BODY-RC-PO', 'supplier': self.supplier_a.pk,
                'purchase': self.purchase_b.pk, 'items': []}),
            ('暂收引用他厂采购行', '/api/receipts', {
                'receipt_no': 'OM-BODY-RC-PI', 'items': [{
                    'purchase_item': self.purchase_item_b.pk, 'lot_no': 'OM-BODY-RC-LOT',
                    'arrived_qty': 1}]}),
            ('暂收引用他厂物料', '/api/receipts', {
                'receipt_no': 'OM-BODY-RC-MAT', 'items': [{
                    'material_id': self.material_b.pk, 'fabric_name': '布',
                    'lot_no': 'OM-BODY-RC-MAT-LOT', 'arrived_qty': 1}]}),
            ('BOM引用他厂款式', '/api/tech/boms', {
                'style': self.style_b.pk, 'items': [{
                    'material': self.material_a.pk, 'unit_usage': '1.0000'}]}),
            ('BOM引用他厂物料', '/api/tech/boms', {
                'style': self.style_a.pk, 'items': [{
                    'material': self.material_b.pk, 'unit_usage': '1.0000'}]}),
            ('报价引用他厂款式', '/api/quotation/quotations', {
                'style': self.style_b.pk, 'customer': self.customer_a.pk, 'qty': 1,
                'materials': [material_row_a]}),
            ('报价引用他厂客户', '/api/quotation/quotations', {
                'style': self.style_a.pk, 'customer': self.customer_b.pk, 'qty': 1,
                'materials': [material_row_a]}),
            ('报价引用他厂物料', '/api/quotation/quotations', {
                'style': self.style_a.pk, 'customer': self.customer_a.pk, 'qty': 1,
                'materials': [{'material': self.material_b.pk,
                               'quote_unit_usage': '1.0000', 'price': '2.000'}]}),
            ('报价引用他厂定额行', '/api/quotation/quotations', {
                'style': self.style_a.pk, 'customer': self.customer_a.pk, 'qty': 1,
                'materials': [{'material': self.material_a.pk, 'bom_item': self.bom_item_b.pk,
                               'quote_unit_usage': '1.0000', 'price': '2.000'}]}),
            ('应收引用他厂客户', '/api/finance/receivables', {
                'customer': self.customer_b.pk, 'order': self.order_a.pk, 'amount': '1.00'}),
            ('应收引用他厂订单', '/api/finance/receivables', {
                'customer': self.customer_a.pk, 'order': self.order_b.pk, 'amount': '1.00'}),
            ('应收引用他厂出货', '/api/finance/receivables', {
                'customer': self.customer_a.pk, 'order': self.order_a.pk,
                'shipment_id': self.shipment_b.pk, 'amount': '1.00'}),
            ('工资调整引用他厂员工', '/api/salary/adjust', {
                'user_id': self.worker_b.pk, 'amount': '1.00', 'reason': '隔离矩阵'}),
            ('工资结算引用他厂员工', '/api/salary/settlements', {
                'user_id': self.worker_b.pk,
                'period_start': str(timezone.localdate()),
                'period_end': str(timezone.localdate())}),
        ]
        before_a = _company_fingerprint(self.company_a)
        before_b = _company_fingerprint(self.company_b)
        failures = []
        for name, url, payload in cases:
            method = 'put' if url.endswith('/lot-links') else 'post'
            with self.subTest(case=name):
                response = getattr(self.api, method)(url, data=payload, format='json')
                if response.status_code not in (400, 404):
                    failures.append(f'{name}: {response.status_code} {response.data}')
        self.assertEqual(failures, [], '请求体混入他厂对象未被拒绝:\n' + '\n'.join(failures))
        self.assertEqual(_company_fingerprint(self.company_b), before_b,
                         '请求体跨厂用例改变了 B 厂数据')
        self.assertEqual(_company_fingerprint(self.company_a), before_a,
                         '被拒绝的请求体仍在 A 厂留下主表或子表数据')

    def test_04_updates_cannot_relink_local_objects_to_foreign_company(self):
        cases = [
            ('客户改挂他厂分组', f'/api/customers/{self.customer_a.pk}',
             {'group': self.group_b.pk}),
            ('款式改挂他厂客户', f'/api/styles/{self.style_a.pk}',
             {'customer': self.customer_b.pk}),
            ('订单改挂他厂客户', f'/api/orders/{self.order_a.pk}',
             {'customer': self.customer_b.pk}),
            ('订单改挂他厂款式', f'/api/orders/{self.order_a.pk}',
             {'style': self.style_b.pk}),
            ('床次改挂他厂客户', f'/api/productions/{self.production_a.pk}',
             {'customer': self.customer_b.pk}),
            ('床次改挂他厂款式', f'/api/productions/{self.production_a.pk}',
             {'style': self.style_b.pk}),
            ('床次改挂他厂订单', f'/api/productions/{self.production_a.pk}',
             {'order': self.order_b.pk}),
        ]
        before_a = _company_fingerprint(self.company_a)
        before_b = _company_fingerprint(self.company_b)
        failures = []
        for name, url, payload in cases:
            with self.subTest(case=name):
                response = self.api.patch(url, data=payload, format='json')
                if response.status_code != 400:
                    failures.append(f'{name}: {response.status_code} {response.data}')
        self.assertEqual(failures, [], '\n'.join(failures))
        self.assertEqual(_company_fingerprint(self.company_a), before_a)
        self.assertEqual(_company_fingerprint(self.company_b), before_b)
