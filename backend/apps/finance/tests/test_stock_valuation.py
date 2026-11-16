from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Style
from apps.finance.services import stock_valuation
from apps.production.models import Production
from apps.warehouse.models import Lot, MaterialIssue, IssueItem, StockIn, StockInItem


class StockValuationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = User.objects.create_user(
            username='stock-valuation-owner', phone='13900007301', password='test-only')
        cls.company, _ = create_company_with_owner(owner, '库存估值测试厂')
        cls.style = Style.objects.create(
            company=cls.company, style_no='SV-S1', name='库存估值款')
        cls.production = Production.objects.create(
            company=cls.company, style=cls.style, bed_no='SV-BED',
            total_qty=100, plan_qty=100)
        cls.lot = Lot.objects.create(
            company=cls.company, lot_no='SV-LOT-1', fabric_name='估值面料', qty=0)
        cls.yesterday = timezone.localtime().replace(
            hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)

    def stock_in(self, number, at, qty, price, status=StockIn.Status.DONE):
        doc = StockIn.objects.create(
            company=self.company, in_no=number, in_at=at, status=status)
        item = StockInItem.objects.create(
            stock_in=doc, lot=self.lot, fabric_name='估值面料',
            lot_no=self.lot.lot_no, qty=qty, unit_price=price)
        StockIn.objects.filter(pk=doc.pk).update(created_at=at)
        return doc, item

    def issue(self, number, qty, at=None, status=MaterialIssue.Status.CONFIRMED):
        doc = MaterialIssue.objects.create(
            company=self.company, issue_no=number, issue_at=at or timezone.now(),
            status=status)
        IssueItem.objects.create(
            issue=doc, production=self.production, lot=self.lot, qty=qty)
        if at is not None:
            MaterialIssue.objects.filter(pk=doc.pk).update(created_at=at)
        return doc

    def test_weighted_average_issue_return_and_missing_price_conserve_value(self):
        self.stock_in('SV-IN-1', self.yesterday, 10, '2.000')
        self.stock_in('SV-IN-2', self.yesterday + timedelta(hours=1), 10, '4.000')
        self.issue('SV-ISSUE-1', 5, at=self.yesterday + timedelta(hours=2))
        return_issue = MaterialIssue.objects.create(
            company=self.company, issue_no='SV-RET-1', issue_at=self.yesterday + timedelta(hours=3),
            status=MaterialIssue.Status.CONFIRMED, type=MaterialIssue.Type.RETURN)
        IssueItem.objects.create(
            issue=return_issue, production=self.production, lot=self.lot, qty=2)
        MaterialIssue.objects.filter(pk=return_issue.pk).update(
            created_at=self.yesterday + timedelta(hours=3))
        self.stock_in('SV-IN-MISSING', timezone.now(), 100, None)
        self.stock_in('SV-IN-DRAFT', timezone.now(), 1000, '100.000', StockIn.Status.DRAFT)
        self.issue('SV-ISSUE-DRAFT', 1000, status=MaterialIssue.Status.DRAFT)

        report = stock_valuation(self.company)
        self.assertEqual(len(report['rows']), 1)
        row = report['rows'][0]['lots'][0]
        self.assertEqual(row['in_qty'], '20.00')
        self.assertEqual(row['in_amt'], '60.00')
        self.assertEqual(row['issued_qty'], '3.00')
        self.assertEqual(row['balance_qty'], '17.00')
        self.assertEqual(row['balance_amt'], '51.00')
        self.assertEqual(row['wavg_price'], '3.000')
        self.assertEqual(row['missing_price_rows'], 1)

    def test_valuation_date_cutoff_excludes_later_flows(self):
        self.stock_in('SV-CUTOFF-IN', self.yesterday, 10, '2.000')
        self.issue('SV-CUTOFF-ISSUE', 5)

        report = stock_valuation(self.company, date_to=self.yesterday.date())
        row = report['rows'][0]['lots'][0]
        self.assertEqual(row['in_qty'], '10.00')
        self.assertEqual(row['in_amt'], '20.00')
        self.assertEqual(row['issued_qty'], '0.00')
        self.assertEqual(row['balance_qty'], '10.00')
        self.assertEqual(row['balance_amt'], '20.00')
        self.assertEqual(row['wavg_price'], '2.000')
