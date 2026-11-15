# -*- coding: utf-8 -*-
"""向指定既有厂追加一条可追踪的 ERP 全链路演示数据。

安全边界：
- 默认只做只读预检；--execute 才写入，且当前仅支持 SQLite 在线备份后执行。
- 必须同时指定公司 ID 和完整公司名，防止写错厂。
- 不运行任何历史清库脚本，不改密码，不删除或更新目标厂既有对象。
- 复用目标厂现有管理员和已有 E2E 员工账号，仅新增本次员工成员关系。
- 全部业务对象使用唯一前缀，单事务导入；任一步骤或核对失败即整体回滚。
- 成功后写出备份清单和所有新增对象的模型、主键、业务编号。

真实旭东厂执行示例（先去掉 --execute 可只读预检）：
  .venv/Scripts/python.exe import_company_e2e.py \
    --company-id 86 --company-name 汉川旭东服饰 --execute
"""
from __future__ import annotations

import argparse
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys

BASE_DIR = Path(__file__).resolve().parent
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

from django.apps import apps  # noqa: E402
from django.conf import settings  # noqa: E402
from django.db import connection, transaction  # noqa: E402
from django.db.models import Sum  # noqa: E402
from django.db.models.signals import pre_delete, pre_save  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402
from rest_framework_simplejwt.tokens import RefreshToken  # noqa: E402

from apps.accounts.models import Company, Post, User, UserCompany  # noqa: E402
from apps.catalog.models import (  # noqa: E402
    Customer, Process, Style, StyleProcess, Ticket, TicketPrintLog,
)
from apps.cost.models import OrderCost  # noqa: E402
from apps.finance.models import Receivable, ReceiptLine  # noqa: E402
from apps.ops.models import AuditLog, CompanySetting, Notification  # noqa: E402
from apps.orders.models import Order, OrderItem  # noqa: E402
from apps.production.models import Production, ProductionProcess  # noqa: E402
from apps.quotation.models import Quotation, QuoteMaterial  # noqa: E402
from apps.records.models import Record  # noqa: E402
from apps.salary.models import SalaryAdjust, SettleApply, Settlement  # noqa: E402
from apps.shipment.models import PackingBox, Shipment, ShipmentItem, ShipmentPrintLog  # noqa: E402
from apps.tech.models import Bom, BomItem, Material  # noqa: E402
from apps.warehouse.models import (  # noqa: E402
    IssueItem, Lot, MaterialIssue, ProductionLotLink, PurchaseItem,
    PurchaseOrder, Receipt, ReceiptItem, StockIn, StockInItem, Supplier,
)

WORKER_PHONES = ('13988720902', '13988720903')
TARGET_MODELS = (
    Company, User, Post, UserCompany,
    Customer, Process, Style, StyleProcess, Ticket, TicketPrintLog,
    Material, Bom, BomItem, Quotation, QuoteMaterial,
    Order, OrderItem, Production, ProductionProcess, ProductionLotLink,
    Supplier, Lot, PurchaseOrder, PurchaseItem, StockIn, StockInItem,
    MaterialIssue, IssueItem, Receipt, ReceiptItem,
    Record, OrderCost, Shipment, ShipmentItem, PackingBox, ShipmentPrintLog,
    Receivable, ReceiptLine, Settlement, SettleApply, SalaryAdjust,
    CompanySetting, Notification, AuditLog,
)
NUMBER_FIELDS = (
    'order_no', 'style_no', 'customer_no', 'quote_no', 'purchase_no',
    'stock_in_no', 'issue_no', 'receipt_no', 'shipment_no', 'receivable_no',
    'settlement_no', 'code', 'bed_no', 'lot_no', 'qr_code', 'name', 'phone',
)


class ImportFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ImportFailure(message)


def decimal_eq(value, expected, tolerance=Decimal('0.005')):
    try:
        return abs(Decimal(str(value)) - Decimal(str(expected))) <= tolerance
    except Exception:
        return False


def manager(model):
    return getattr(model, 'all_objects', model._default_manager)


def id_snapshot():
    return {
        model._meta.label: set(manager(model).values_list('pk', flat=True))
        for model in TARGET_MODELS
    }


def protected_row_snapshot(company_id, user_ids):
    """用于提交前复核既有目标厂对象及复用账号完全未被修改。"""
    result = {}
    for model in TARGET_MODELS:
        fields = [field.name for field in model._meta.concrete_fields]
        if model is Company:
            queryset = manager(model).filter(pk=company_id)
        elif model is User:
            queryset = manager(model).filter(pk__in=user_ids)
        elif any(field.name == 'company' for field in model._meta.concrete_fields):
            queryset = manager(model).filter(company_id=company_id)
        else:
            continue
        result[model._meta.label] = list(queryset.values(*fields).order_by('pk'))
    return result


def snapshot_difference_fields(before, after):
    differences = []
    for label, old_rows in before.items():
        new_rows = after.get(label, [])
        old_by_pk = {row['id']: row for row in old_rows}
        new_by_pk = {row['id']: row for row in new_rows}
        for pk in sorted(old_by_pk):
            old = old_by_pk[pk]
            new = new_by_pk.get(pk, {})
            fields = sorted(key for key in set(old) | set(new) if old.get(key) != new.get(key))
            if fields:
                differences.append({'model': label, 'pk': pk, 'fields': fields})
    return differences


def new_object_manifest(before_ids):
    objects = []
    for model in TARGET_MODELS:
        label = model._meta.label
        new_ids = set(manager(model).values_list('pk', flat=True)) - before_ids[label]
        if not new_ids:
            continue
        rows = manager(model).filter(pk__in=new_ids).order_by('pk')
        for row in rows:
            numbers = {
                field: str(getattr(row, field))
                for field in NUMBER_FIELDS
                if hasattr(row, field) and getattr(row, field) not in (None, '')
            }
            objects.append({'model': label, 'pk': row.pk, 'business_numbers': numbers})
    return objects


def sqlite_backup(company_id):
    require(connection.vendor == 'sqlite', '安全保护：自动备份当前仅支持 SQLite')
    source_path = Path(connection.settings_dict['NAME']).resolve()
    require(source_path.is_file(), f'数据库文件不存在: {source_path}')
    stamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')
    backup_path = source_path.with_name(
        f'{source_path.name}.bak_company{company_id}_e2e_{stamp}_{secrets.token_hex(2)}')
    require(not backup_path.exists(), f'备份文件已存在: {backup_path}')
    source = sqlite3.connect(str(source_path), timeout=30)
    target = sqlite3.connect(str(backup_path))
    try:
        source.execute('PRAGMA busy_timeout=30000')
        source.backup(target)
        integrity = target.execute('PRAGMA integrity_check').fetchone()[0]
        require(str(integrity).upper() == 'OK', f'备份完整性校验失败: {integrity}')
        require(backup_path.stat().st_size > 0, '备份文件为空')
    finally:
        target.close()
        source.close()
    return backup_path


class Api:
    def __init__(self, company_id, token):
        self.client = Client()
        self.extra = {
            'HTTP_AUTHORIZATION': f'Bearer {token}',
            'HTTP_X_COMPANY_ID': str(company_id),
        }

    def call(self, method, path, payload=None, expected=(200,), query=None):
        method = method.lower()
        kwargs = dict(self.extra)
        if method == 'get':
            response = self.client.get(f'/api{path}', data=query or {}, **kwargs)
        else:
            response = getattr(self.client, method)(
                f'/api{path}', data=json.dumps(payload or {}, ensure_ascii=False),
                content_type='application/json', **kwargs)
        try:
            body = json.loads(response.content.decode('utf-8'))
        except Exception:
            body = {'raw': response.content.decode('utf-8', errors='replace')[:500]}
        require(response.status_code in expected,
                f'{method.upper()} {path} 返回 {response.status_code}: '
                f'{json.dumps(body, ensure_ascii=False)[:1000]}')
        return body


def preflight(company_id, company_name):
    company = Company.all_objects.filter(pk=company_id).first()
    require(company is not None and not company.is_deleted, f'公司 {company_id} 不存在或已删除')
    require(company.name == company_name,
            f'公司名称不匹配：ID {company_id} 实际为 {company.name!r}')
    membership = (UserCompany.all_objects
                  .filter(company=company, status=UserCompany.Status.ACTIVE,
                          is_deleted=False, post__is_admin=True)
                  .select_related('user').order_by('id').first())
    require(membership is not None and membership.user.is_active,
            '目标厂没有可用在职管理员，已停止')
    workers = []
    for phone in WORKER_PHONES:
        user = User.objects.filter(phone=phone, is_active=True).first()
        require(user is not None and user.real_name.startswith('E2E'),
                f'既有 E2E 员工账号缺失或姓名不符: {phone}')
        require(not UserCompany.all_objects.filter(user=user, company=company).exists(),
                f'E2E 员工 {phone} 已有目标厂成员关系，拒绝覆盖')
        workers.append(user)
    require(UserCompany.all_objects.filter(
        company=company, status=UserCompany.Status.ACTIVE,
        is_deleted=False).count() + len(workers) <= company.capacity,
        '目标厂员工容量不足')
    return company, membership.user, workers


def install_existing_data_guards(company_id, before_ids):
    def is_existing_target(instance):
        if isinstance(instance, Company):
            return instance.pk == company_id
        return (getattr(instance, 'company_id', None) == company_id
                and instance.pk in before_ids.get(instance._meta.label, set()))

    def block_update(sender, instance, **kwargs):
        if is_existing_target(instance) and not instance._state.adding:
            raise ImportFailure(f'安全保护：禁止更新既有对象 {sender._meta.label}#{instance.pk}')

    def block_delete(sender, instance, **kwargs):
        if is_existing_target(instance):
            raise ImportFailure(f'安全保护：禁止删除既有对象 {sender._meta.label}#{instance.pk}')

    pre_save.connect(block_update, dispatch_uid='company-e2e-block-update', weak=False)
    pre_delete.connect(block_delete, dispatch_uid='company-e2e-block-delete', weak=False)
    return block_update, block_delete


def import_chain(company, admin, workers, prefix):
    cid = company.id
    token = str(RefreshToken.for_user(admin).access_token)
    api = Api(cid, token)
    today = timezone.localdate()
    mark = f'{prefix}-E2E'

    post = Post.objects.create(
        company=company, name=f'{mark}-计件岗',
        permissions=['records.create', 'working.view', 'my_salary.view'])
    memberships = [UserCompany.objects.create(
        user=user, company=company, post=post,
        status=UserCompany.Status.ACTIVE, hired_date=today,
        salary_type=UserCompany.SalaryType.PROCESS,
    ) for user in workers]

    process_ids = []
    for index, (name, price) in enumerate((('裁剪', '2.500'), ('车缝', '3.000'), ('锁边', '1.000')), 1):
        row = api.call('post', '/processes', {
            'name': f'{mark}-{name}', 'default_price': price,
            'is_fixed': True, 'is_key': True, 'sort': 900 + index,
        }, expected=(201,))
        process_ids.append(row['id'])
    pa, pb, pc = process_ids

    customer = api.call('post', '/customers', {
        'customer_no': f'{prefix}-C1', 'name': f'{mark}-演示客户',
        'phone': '', 'remark': '仅端到端演示数据',
    }, expected=(201,))
    style = api.call('post', '/styles', {
        'style_no': f'{prefix}-S1', 'name': f'{mark}-针织演示款',
        'customer': customer['id'], 'remark': '仅端到端演示数据',
        'process_items': [
            {'process_id': pa, 'price': '2.500', 'sort': 1},
            {'process_id': pb, 'price': '3.000', 'sort': 2},
            {'process_id': pc, 'price': '1.000', 'sort': 3},
        ],
    }, expected=(201,))
    require(decimal_eq(style.get('price'), '6.500'), '款式工序合计不是 6.500')

    material1 = api.call('post', '/tech/materials', {
        'code': f'{prefix}-M1', 'name': f'{mark}-面料', 'category': 'fabric',
        'unit': '米', 'weight_gsm': 200, 'width_cm': 150,
    }, expected=(201,))
    material2 = api.call('post', '/tech/materials', {
        'code': f'{prefix}-M2', 'name': f'{mark}-辅料', 'category': 'accessory', 'unit': '个',
    }, expected=(201,))
    bom = api.call('post', '/tech/boms', {
        'style': style['id'], 'items': [
            {'material': material1['id'], 'kind': 'main', 'unit_usage': '1.2'},
            {'material': material2['id'], 'kind': 'accessory', 'unit_usage': '1'},
        ],
    }, expected=(201,))
    require(len(bom.get('items', [])) == 2, 'BOM 明细数量错误')
    bmi1, bmi2 = [row['id'] for row in bom['items']]
    api.call('post', f"/tech/boms/{bom['id']}/activate", expected=(200,))

    quotation = api.call('post', '/quotation/quotations', {
        'style': style['id'], 'customer': customer['id'], 'qty': 200,
        'materials': [{'bom_item': bmi1, 'quote_unit_usage': '1.2', 'price': '20'}],
        'remark': '仅端到端演示数据',
    }, expected=(201,))
    require(decimal_eq(quotation.get('material_total'), '4800'), '报价材料合计不是 4800')

    order = api.call('post', '/orders', {
        'order_no': f'{prefix}-SO1', 'style': style['id'], 'customer': customer['id'],
        'delivery_date': (today + timedelta(days=14)).isoformat(),
        'remark': '仅端到端演示数据',
        'process_items': [
            {'process_id': pa, 'price': '2.500'},
            {'process_id': pb, 'price': '3.000'},
            {'process_id': pc, 'price': '1.000'},
        ],
        'items': [
            {'color': '黑', 'size': 'M', 'qty': 100, 'price': '30'},
            {'color': '黑', 'size': 'L', 'qty': 60, 'price': '30'},
            {'color': '白', 'size': 'M', 'qty': 40, 'price': '28'},
        ],
    }, expected=(201,))
    order_detail = api.call('get', f"/orders/{order['id']}")
    require(order_detail.get('total_qty') == 200, '订单总数量不是 200')

    production = api.call('post', '/productions', {
        'bed_no': f'{prefix}-B1', 'order_id': order['id'], 'total_qty': 200,
    }, expected=(201,))
    ticket_body = api.call('post', '/tickets/batch', {
        'production_id': production['id'], 'bundle_limit': 30,
        'items': [
            {'color': '黑', 'size': 'M', 'qty': 100},
            {'color': '黑', 'size': 'L', 'qty': 60},
            {'color': '白', 'size': 'M', 'qty': 40},
        ],
    }, expected=(201,))
    tickets = ticket_body if isinstance(ticket_body, list) else ticket_body.get('results', [])
    require(len(tickets) == 8 and sum(row['qty'] for row in tickets) == 200,
            '编菲结果不是 8 扎 / 200 件')
    require(all(row.get('qr_code') for row in tickets), '存在缺少二维码的菲票')

    opening = api.call('post', '/opening-stock/import', {'rows': [
        {'物料编码': f'{prefix}-M1', '缸号': f'{prefix}-L1', '数量': 500, '批次单价': 20},
        {'物料编码': f'{prefix}-M2', '缸号': f'{prefix}-L2', '数量': 300, '批次单价': 2},
    ]}, expected=(200,))
    require(opening.get('created') == 2, '期初库存没有创建两个批次')

    purchase = api.call('post', '/purchase-orders', {'items': [{
        'fabric_name': f'{mark}-面料', 'qty': 300, 'unit': 'kg', 'price': 88,
        'price_unit': 'kg', 'material_id': material1['id'],
    }]}, expected=(201,))
    purchase_item = purchase['items'][0]
    require(decimal_eq(purchase_item.get('price'), '26.4'), '采购公斤价折算米价不是 26.4')
    stock_in = api.call('post', '/stock-ins', {
        'source_type': 'purchase', 'purchase': purchase['id'],
        'items': [{
            'fabric_name': f'{mark}-面料', 'lot_no': f'{prefix}-L3',
            'qty': 1000, 'unit': '米', 'unit_price': '26.4',
            'purchase_item': purchase_item['id'], 'material_id': material1['id'],
        }],
    }, expected=(201,))
    api.call('post', f"/stock-ins/{stock_in['id']}/confirm")

    lots = {row.lot_no: row for row in Lot.all_objects.filter(
        company=company, lot_no__in=[f'{prefix}-L1', f'{prefix}-L2', f'{prefix}-L3'])}
    require(len(lots) == 3, '采购及期初批次不完整')
    issue1 = api.call('post', '/material-issues', {'items': [{
        'production_id': production['id'], 'lot_id': lots[f'{prefix}-L1'].id,
        'qty': '264.4', 'bom_item_id': bmi1,
    }]}, expected=(201,))
    issue2 = api.call('post', '/material-issues', {'items': [{
        'production_id': production['id'], 'lot_id': lots[f'{prefix}-L2'].id,
        'qty': '200.8', 'bom_item_id': bmi2,
    }]}, expected=(201,))
    require(decimal_eq(issue1['items'][0].get('qty_req'), '254.4', Decimal('0.01'))
            and issue1['items'][0].get('over') is True, '面料定额或超领标识错误')
    api.call('post', f"/material-issues/{issue1['id']}/confirm")
    api.call('post', f"/material-issues/{issue2['id']}/confirm")
    require(ProductionLotLink.all_objects.filter(production_id=production['id']).count() == 2,
            '床次与物料批次血缘不是两条')

    w1, w2 = workers

    def register(ticket, process_id, qty, user):
        return api.call('post', '/records', {
            'ticket_id': ticket['id'], 'process_id': process_id,
            'qty': qty, 'user_id': user.id,
        }, expected=(201,))

    for ticket in tickets[:3]:
        for process_id in process_ids:
            register(ticket, process_id, 30, w1)
    for index, ticket in enumerate(tickets[3:7], start=3):
        selected = process_ids if index < 6 else [pa, pb]
        for process_id in selected:
            register(ticket, process_id, ticket['qty'], w2)

    records = Record.all_objects.filter(company=company, ticket_id__in=[row['id'] for row in tickets])
    require(records.count() == 20, '扫菲登记不是 20 条')
    require(records.aggregate(total=Sum('qty'))['total'] == 540, '扫菲登记总量不是 540')
    for user, expected_qty, expected_amount in ((w1, 270, '585'), (w2, 270, '620')):
        own = records.filter(user=user)
        require(own.aggregate(total=Sum('qty'))['total'] == expected_qty,
                f'{user.real_name} 登记件数错误')
        amount = sum((row.qty * row.price for row in own), Decimal('0'))
        require(decimal_eq(amount, expected_amount), f'{user.real_name} 工资金额不是 {expected_amount}')

    progress = api.call('get', f"/orders/{order['id']}/progress")
    require(progress.get('cut_qty') == 200 and progress.get('registered_qty') == 540,
            '订单裁货或登记进度错误')
    api.call('post', f"/cost/orders/{order['id']}/recompute")
    api.call('put', f"/cost/orders/{order['id']}/overhead", {'overhead_cost': 100})
    cost = api.call('post', f"/cost/orders/{order['id']}/recompute")
    require(decimal_eq(cost.get('material_cost'), '5689.6', Decimal('0.02'))
            and decimal_eq(cost.get('labor_cost'), '1205', Decimal('0.02'))
            and decimal_eq(cost.get('revenue'), '5920')
            and decimal_eq(cost.get('total_cost'), '6994.6', Decimal('0.02')),
            f'订单成本核对失败: {cost}')

    shipment = api.call('post', '/shipments', {
        'order': order['id'], 'customer': customer['id'],
        'items': [
            {'color': '黑', 'size': 'M', 'qty': 50},
            {'color': '黑', 'size': 'L', 'qty': 30},
            {'color': '白', 'size': 'M', 'qty': 40},
        ],
    }, expected=(201,))
    audit = api.call('post', f"/shipments/{shipment['id']}/audit")
    receivable = audit.get('receivable', {})
    require(decimal_eq(receivable.get('amount'), '3520'), '出货应收不是 3520')
    api.call('post', f"/finance/receivables/{receivable['id']}/receipts",
             {'amount': 1000, 'method': 'cash'}, expected=(201,))
    settled_ar = api.call('post', f"/finance/receivables/{receivable['id']}/receipts",
                          {'amount': 2520, 'method': 'other'}, expected=(201,))
    require(settled_ar['receivable'].get('status') == 'settled'
            and decimal_eq(settled_ar['receivable'].get('outstanding'), 0),
            '应收两次核销后未结清')

    settlement = api.call('post', '/salary/settlements', {
        'period_start': today.isoformat(), 'period_end': today.isoformat(), 'user_id': w1.id,
    }, expected=(201,))
    require(decimal_eq(settlement.get('amount'), '585'), '员工一结算金额不是 585')
    api.call('post', '/salary/adjust', {
        'user_id': w2.id, 'amount': '-10', 'reason': f'{mark}-演示扣款',
    }, expected=(201,))
    salary1 = api.call('get', f'/salary/employee/{w1.id}')
    salary2 = api.call('get', f'/salary/employee/{w2.id}')
    require(decimal_eq(salary1.get('settled'), '585') and decimal_eq(salary1.get('unsettled'), 0),
            '员工一已结/未结金额错误')
    require(decimal_eq(salary2.get('unsettled'), '620'), '员工二未结金额不是 620')

    valuation = api.call('get', '/finance/stock-valuation')
    balances = {row.get('material_id'): row for row in valuation.get('rows', [])}
    require(decimal_eq(balances.get(material1['id'], {}).get('balance_qty'), '1235.6', Decimal('0.02'))
            and decimal_eq(balances.get(material2['id'], {}).get('balance_qty'), '99.2', Decimal('0.02')),
            '库存估值结存不是面料 1235.6 / 辅料 99.2')

    return {
        'prefix': prefix,
        'business_date': today.isoformat(),
        'main_ids': {
            'post': post.id, 'memberships': [row.id for row in memberships],
            'customer': customer['id'], 'style': style['id'], 'bom': bom['id'],
            'quotation': quotation['id'], 'order': order['id'], 'production': production['id'],
            'tickets': [row['id'] for row in tickets], 'purchase': purchase['id'],
            'stock_in': stock_in['id'], 'material_issues': [issue1['id'], issue2['id']],
            'shipment': shipment['id'], 'receivable': receivable['id'], 'settlement': settlement['id'],
        },
        'expected': {
            'order_qty': 200, 'ticket_count': 8, 'record_count': 20,
            'registered_process_qty': 540, 'labor_cost': '1205.00',
            'material_cost': '5689.60', 'overhead_cost': '100.00',
            'total_cost': '6994.60', 'revenue': '5920.00',
            'shipment_qty': 120, 'receivable': '3520.00', 'received': '3520.00',
            'receivable_status': 'settled', 'worker1_settled': '585.00',
            'worker2_unsettled': '620.00', 'fabric_balance': '1235.6',
            'accessory_balance': '99.2',
        },
        'reused_users': [
            {'id': admin.id, 'phone': admin.phone, 'role': 'existing_admin'},
            *[{'id': user.id, 'phone': user.phone, 'role': 'existing_e2e_worker'} for user in workers],
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--company-id', type=int, required=True)
    parser.add_argument('--company-name', required=True)
    parser.add_argument('--execute', action='store_true', help='备份通过后实际写入；缺省只读预检')
    args = parser.parse_args(argv)

    company, admin, workers = preflight(args.company_id, args.company_name)
    print(f'PREFLIGHT_OK company={company.id} name={company.name} admin_user={admin.id} '
          f'workers={[user.id for user in workers]} db={connection.vendor}')
    if not args.execute:
        print('DRY_RUN_ONLY：未备份、未写入。确认目标后增加 --execute。')
        return 0

    backup_path = sqlite_backup(company.id)
    print(f'BACKUP_OK path={backup_path} bytes={backup_path.stat().st_size} integrity=OK')
    prefix = 'XD{}-{}-{}'.format(
        company.id, timezone.localtime().strftime('%y%m%d-%H%M%S'), secrets.token_hex(2).upper())
    before_ids = id_snapshot()
    protected_before = protected_row_snapshot(company.id, [admin.id, *[u.id for u in workers]])
    guards = install_existing_data_guards(company.id, before_ids)
    result = None
    try:
        with transaction.atomic():
            result = import_chain(company, admin, workers, prefix)
            protected_after = protected_row_snapshot(company.id, [admin.id, *[u.id for u in workers]])
            differences = snapshot_difference_fields(protected_before, protected_after)
            require(not differences,
                    '安全保护：检测到目标厂既有对象或复用账号发生变化，整体回滚: '
                    + json.dumps(differences, ensure_ascii=False))
            result['created_objects'] = new_object_manifest(before_ids)
            require(result['created_objects'], '没有记录到新增对象，拒绝提交')
            result.update({
                'status': 'committed',
                'company': {'id': company.id, 'name': company.name},
                'database': str(Path(connection.settings_dict['NAME']).resolve()),
                'backup': str(backup_path),
                'backup_bytes': backup_path.stat().st_size,
                'created_at': timezone.now().isoformat(),
                'created_object_count': len(result['created_objects']),
            })
            manifest_path = Path(str(backup_path) + '.manifest.json')
            require(not manifest_path.exists(), f'清单已存在: {manifest_path}')
            # 先写临时清单；只有事务成功提交后才改成正式文件名。
            pending_path = Path(str(manifest_path) + '.pending')
            pending_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        pending_path.replace(manifest_path)
    finally:
        pre_save.disconnect(dispatch_uid='company-e2e-block-update')
        pre_delete.disconnect(dispatch_uid='company-e2e-block-delete')

    print('IMPORT_OK ' + json.dumps({
        'company_id': company.id, 'prefix': prefix,
        'created_objects': result['created_object_count'],
        'main_ids': result['main_ids'], 'expected': result['expected'],
        'backup': str(backup_path), 'manifest': str(manifest_path),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ImportFailure as exc:
        print(f'IMPORT_ABORTED: {exc}', file=sys.stderr)
        raise SystemExit(1)
