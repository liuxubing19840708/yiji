# -*- coding: utf-8 -*-
"""出货可出货量：末道必过口径、历史单工序过渡与作废回池。"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.accounts.models import Company, User
from apps.catalog.models import Customer, Process, Style, StyleProcess, Ticket
from apps.orders.models import Order, OrderItem
from apps.production.models import Production, ProductionProcess
from apps.records.models import Record
from apps.shipment.models import Shipment, ShipmentItem
from apps.shipment.services import (audit_shipment, produced_qty_map,
                                    shipped_qty_map, void_shipment)


class ProducedQtyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='shipment-p0', phone='13900000001', password='x')
        cls.worker2 = User.objects.create_user(
            username='shipment-p0-w2', phone='13900000002', password='x')
        cls.company = Company.objects.create(
            name='出货P0隔离测试', company_no='990001', creator=cls.user)
        cls.customer = Customer.objects.create(
            company=cls.company, customer_no='C-P0', name='测试客户')
        cls.style = Style.objects.create(
            company=cls.company, style_no='S-P0', name='五道工序款')
        cls.processes = [Process.objects.create(
            company=cls.company, name=f'工序{i}', default_price=1, sort=i)
            for i in range(1, 6)]
        cls.order = Order.objects.create(
            company=cls.company, order_no='SO-P0', customer=cls.customer,
            style=cls.style)
        OrderItem.objects.create(
            order=cls.order, color='红', size='M', qty=100, price=Decimal('10'))

    def setUp(self):
        self.bed = Production.objects.create(
            company=self.company, style=self.style, order=self.order,
            customer=self.customer, bed_no=f'P0-{self._testMethodName}'[:32],
            total_qty=100)
        self.ticket = Ticket.objects.create(
            company=self.company, production=self.bed, bed_no=self.bed.bed_no,
            bundle_no='1', color='红', size='M', qty=100,
            qr_code=f'{self.bed.bed_no}|1')

    def configure(self, allow_skips=(True, True, True, True, False)):
        return [StyleProcess.objects.create(
            style=self.style, process=process, price=1, sort=index,
            allow_skip=allow_skips[index - 1])
            for index, process in enumerate(self.processes, 1)]

    def record(self, process, qty=100, user=None):
        return Record.objects.create(
            company=self.company, ticket=self.ticket, process=process,
            user=user or self.user, qty=qty, price=1)

    def shipment(self, qty=100, no='SH-P0'):
        shipment = Shipment.objects.create(
            company=self.company, order=self.order, customer=self.customer,
            shipment_no=no, total_qty=qty)
        ShipmentItem.objects.create(
            shipment=shipment, color='红', size='M', qty=qty)
        return shipment

    def test_five_processes_are_not_summed_and_workers_accumulate(self):
        self.configure()
        for process in self.processes:
            self.record(process, 100)
        # 同扎末道由两个员工协作；历史总量不得变成 500，也不能只取一个员工。
        Record.objects.filter(ticket=self.ticket, process=self.processes[-1],
                              user=self.user).update(qty=60)
        self.record(self.processes[-1], 40, self.worker2)
        self.assertEqual(Record.objects.filter(ticket=self.ticket).count(), 6)
        self.assertEqual(produced_qty_map(self.company, self.order),
                         {('红', 'M'): 100})

    def test_soft_deleted_record_is_not_effective_quantity(self):
        self.configure()
        self.record(self.processes[-1], 100)
        cancelled = self.record(self.processes[-1], 0, self.worker2)
        cancelled.qty = 40
        cancelled.is_deleted = True
        cancelled.save(update_fields=['qty', 'is_deleted', 'updated_at'])
        self.assertEqual(produced_qty_map(self.company, self.order),
                         {('红', 'M'): 100})

    def test_multi_process_without_mandatory_last_is_diagnostic(self):
        self.configure(allow_skips=(False, False, False, False, True))
        for process in self.processes:
            self.record(process, 100)
        with self.assertRaises(ValidationError) as ctx:
            produced_qty_map(self.company, self.order)
        message = str(ctx.exception.detail)
        self.assertIn(self.bed.bed_no, message)
        self.assertIn('未设置禁止跳序', message)
        self.assertIn('allow_skip=false', message)

    def test_unconfigured_multi_process_records_do_not_guess_latest(self):
        self.record(self.processes[0], 100)
        self.record(self.processes[-1], 100, self.worker2)
        with self.assertRaises(ValidationError) as ctx:
            produced_qty_map(self.company, self.order)
        self.assertIn('已有多道工序报工', str(ctx.exception.detail))

    def test_single_mandatory_style_process_is_backward_compatible(self):
        process = self.processes[0]
        StyleProcess.objects.create(
            style=self.style, process=process, price=1, sort=1, allow_skip=False)
        self.record(process, 60)
        self.record(process, 40, self.worker2)
        self.assertEqual(produced_qty_map(self.company, self.order),
                         {('红', 'M'): 100})

    def test_legacy_unconfigured_single_process_remains_available(self):
        self.record(self.processes[2], 100)
        self.assertEqual(produced_qty_map(self.company, self.order),
                         {('红', 'M'): 100})

    def test_production_process_override_uses_configured_last(self):
        self.configure(allow_skips=(True, True, True, False, False))
        # 床次只保留第 2、4 道；第 4 道在款式中明确必过且为该床次末道。
        for index in (1, 3):
            ProductionProcess.objects.create(
                production=self.bed, process=self.processes[index], price=1)
        self.record(self.processes[1], 100)
        self.record(self.processes[3], 100)
        self.record(self.processes[4], 100)
        self.assertEqual(produced_qty_map(self.company, self.order),
                         {('红', 'M'): 100})

    def test_equal_last_sort_is_not_resolved_by_id(self):
        self.configure()
        last = StyleProcess.objects.get(style=self.style,
                                        process=self.processes[-1])
        last.sort = 4
        last.save(update_fields=['sort', 'updated_at'])
        with self.assertRaises(ValidationError) as ctx:
            produced_qty_map(self.company, self.order)
        self.assertIn('并列工序', str(ctx.exception.detail))

    def test_audit_rollback_void_returns_shipped_pool(self):
        self.configure()
        for process in self.processes:
            self.record(process, 100)
        first = self.shipment(60, 'SH-P0-FIRST')
        audit_shipment(self.company, first, self.user)
        self.assertEqual(shipped_qty_map(self.company, self.order),
                         {('红', 'M'): 60})

        over = self.shipment(41, 'SH-P0-OVER')
        with self.assertRaises(ValidationError):
            audit_shipment(self.company, over, self.user)
        over.refresh_from_db()
        self.assertEqual(over.status, Shipment.Status.DRAFT)

        void_shipment(self.company, first, self.user)
        self.assertEqual(shipped_qty_map(self.company, self.order), {})
        second = self.shipment(100, 'SH-P0-SECOND')
        audited, receivable = audit_shipment(self.company, second, self.user)
        self.assertEqual(audited.status, Shipment.Status.CONFIRMED)
        self.assertEqual(receivable.amount, Decimal('1000.00'))
