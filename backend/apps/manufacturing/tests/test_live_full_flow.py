"""真实 HTTP 全流程 E2E；测试数据库由 Django 创建并销毁，不使用开发业务库。"""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from django.test import LiveServerTestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Customer, Style, Ticket
from apps.finished_goods.models import PackingTask
from apps.manufacturing.models import QuantityLedger
from apps.orders.models import Order, OrderItem
from apps.production.models import Production


def rid():
    return uuid4().hex


class LiveFullProductionFlowTests(LiveServerTestCase):
    """通过独立 HTTP 服务验证业务接口，而不是 APIClient 或服务函数直调。"""

    def setUp(self):
        # 本机 LiveServer 必须直连，不能被开发环境的 HTTP 代理截获。
        self.opener = build_opener(ProxyHandler({}))
        self.owner = User.objects.create_user(username='live-flow-owner', password='pw')
        self.company = Company.objects.create(
            name='真实HTTP全流程隔离厂', company_no='980001', creator=self.owner)
        self.other_company = Company.objects.create(
            name='真实HTTP跨厂隔离厂', company_no='980002', creator=self.owner)
        self.admin = self.member('live-flow-admin', self.company, is_admin=True)
        self.admin.phone = '13900009801'
        self.admin.save(update_fields=['phone'])
        self.worker = self.member('live-flow-worker', self.company)
        self.viewer_post = Post.objects.create(
            company=self.company, name='只读岗', permissions=['manufacturing.view'])
        self.viewer = self.member(
            'live-flow-viewer', self.company, post=self.viewer_post)
        self.other = self.member('live-flow-other', self.other_company, is_admin=True)
        self.customer = Customer.objects.create(
            company=self.company, customer_no='LIVE-C1', name='隔离测试客户')
        self.style = Style.objects.create(
            company=self.company, style_no='LIVE-S1', name='隔离测试款')
        self.order = Order.objects.create(
            company=self.company, order_no='LIVE-SO1', customer=self.customer,
            style=self.style)
        OrderItem.objects.create(
            order=self.order, color='红', size='M', qty=10, price='12.50')
        self.bed = Production.objects.create(
            company=self.company, style=self.style, order=self.order,
            customer=self.customer, bed_no='LIVE-B1', total_qty=10)
        self.ticket = Ticket.objects.create(
            company=self.company, production=self.bed, bed_no=self.bed.bed_no,
            bundle_no='1', color='红', size='M', qty=10, qr_code='LIVE-B1|1')

    def member(self, username, company, *, is_admin=False, post=None):
        user = User.objects.create_user(username=username, password='pw')
        if post is None:
            post = Post.objects.create(
                company=company, name=f'{username}-岗', is_admin=is_admin)
        UserCompany.objects.create(
            user=user, company=company, post=post,
            status=UserCompany.Status.ACTIVE)
        return user

    def call(self, method, path, actor=None, company=None, payload=None,
             expected=(200,)):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if actor is not None:
            headers['Authorization'] = f'Bearer {AccessToken.for_user(actor)}'
            headers['X-Company-Id'] = str((company or self.company).pk)
        request = Request(
            self.live_server_url + '/api' + path, data=body,
            headers=headers, method=method)
        try:
            with self.opener.open(request) as response:
                status = response.status
                raw = response.read()
        except HTTPError as error:
            status = error.code
            raw = error.read()
        data = json.loads(raw) if raw else None
        self.assertIn(status, expected, f'{method} {path}: {status} {data}')
        return data

    def post(self, path, payload, **kwargs):
        return self.call('POST', path, payload=payload, **kwargs)

    def action(self, action, node, quantity, *, target=None, expected=(201,)):
        payload = {
            'ticket_id': self.ticket.pk, 'node_id': node['id'],
            'quantity': quantity, 'request_id': rid(),
        }
        if target is not None:
            payload['to_node_id'] = target['id']
        return self.post(
            f'/manufacturing/actions/{action}', payload,
            actor=self.admin, expected=expected)

    def transfer(self, source, target, quantity):
        return self.action('transfer', source, quantity, target=target)

    def inspect(self, node, inspection_type, inspected, qualified, *,
                rework=0, scrapped=0, rework_node=None, parent=None):
        request_id = rid()
        create_request_id = rid()
        payload = {
            'ticket': self.ticket.pk, 'node': node['id'],
            'inspection_type': inspection_type, 'inspected': inspected,
            'qualified': qualified, 'rework': rework, 'scrapped': scrapped,
            'rework_node': rework_node['id'] if rework_node else None,
            'reinspection_of': parent, 'reason': '隔离HTTP检验',
            'create_request_id': create_request_id,
        }
        inspection = self.post('/quality/inspections', payload, actor=self.admin, expected=(201,))
        repeated_create = self.post(
            '/quality/inspections', payload, actor=self.admin, expected=(201,))
        self.assertEqual(repeated_create['id'], inspection['id'])
        confirmed = self.post(
            f"/quality/inspections/{inspection['id']}/confirm",
            {'request_id': request_id}, actor=self.admin)
        before = QuantityLedger.objects.count()
        repeated = self.post(
            f"/quality/inspections/{inspection['id']}/confirm",
            {'request_id': request_id}, actor=self.admin)
        self.assertEqual(repeated['id'], confirmed['id'])
        self.assertEqual(QuantityLedger.objects.count(), before)
        return confirmed

    def inventory(self):
        return self.call(
            'GET', f'/finished-goods/inventory?order_id={self.order.pk}',
            actor=self.admin)['total_available']

    def test_http_preproduction_to_shipment_void_and_security_boundaries(self):
        pack = self.post('/preproduction/tech-packs', {
            'order': self.order.pk, 'pattern_no': 'LIVE-P1',
            'pattern_files': ['patterns/live.pdf'],
            'measurements': [{'size': 'M', 'point': '衣长', 'value': 70,
                              'tolerance_plus': 1, 'tolerance_minus': 1}],
            'tolerances': {'default': 1},
            'attachments': [{'name': '工艺单', 'path': 'tech/live.pdf'}],
            'requirements': '隔离HTTP全流程工艺要求',
        }, actor=self.admin, expected=(201,))
        sample = self.post(
            f"/preproduction/tech-packs/{pack['id']}/samples",
            {'title': '产前确认样'}, actor=self.admin, expected=(201,))
        self.post(f"/preproduction/samples/{sample['id']}/records",
                  {'result': 'passed', 'note': '尺寸工艺合格'},
                  actor=self.admin, expected=(201,))
        target = self.post(
            f"/preproduction/tech-packs/{pack['id']}/briefing-targets",
            {'employee': self.worker.pk}, actor=self.admin, expected=(201,))
        self.post(f"/preproduction/briefing-targets/{target['id']}/sign", {},
                  actor=self.worker, expected=(201,))
        self.post(f"/preproduction/tech-packs/{pack['id']}/briefing-confirmation",
                  {'note': '生产负责人确认班组交底完成'}, actor=self.admin, expected=(201,))
        self.post(f"/preproduction/tech-packs/{pack['id']}/approval",
                  {'action': 'approve'}, actor=self.admin, expected=(201,))
        status = self.call(
            'GET', f'/preproduction/orders/{self.order.pk}/status', actor=self.admin)
        self.assertEqual(status['approval']['status'], 'approved')

        stage_ids = {}
        specs = [
            ('cut', '裁剪', 'cutting'), ('sew', '车缝', 'sewing'),
            ('semi', '半成品质检', 'semi_inspection'), ('special', '专机', 'special_machine'),
            ('iron', '整烫', 'ironing'), ('final', '尾查', 'final_inspection'),
            ('pack', '包装', 'packing'), ('in', '成品入库', 'finished_inbound'),
        ]
        for code, name, stage_type in specs:
            stage = self.post('/manufacturing/stages', {
                'code': f'LIVE-{code}', 'name': name, 'stage_type': stage_type,
            }, actor=self.admin, expected=(201,))
            stage_ids[code] = stage['id']
        template = self.post('/manufacturing/routes', {
            'code': 'LIVE-TEMPLATE', 'name': '隔离全流程路线',
            'nodes': [{'stage_id': stage_ids[code], 'sequence': index, 'required': True}
                      for index, (code, _, _) in enumerate(specs, 1)],
        }, actor=self.admin, expected=(201,))
        route = self.post(
            f"/manufacturing/routes/{template['id']}/instantiate",
            {'production_id': self.bed.pk, 'code': 'LIVE-ROUTE'},
            actor=self.admin, expected=(201,))
        nodes = {code: route['nodes'][index]
                 for index, (code, _, _) in enumerate(specs)}

        self.action('receive', nodes['cut'], 10)
        self.action('complete', nodes['cut'], 10)
        operations = self.call(
            'GET', '/manufacturing/operations?stalled_hours=1', actor=self.admin)
        self.assertEqual(operations['total'], 1)
        self.assertEqual(operations['summary']['wip'], 10)
        self.assertEqual(operations['results'][0]['route'], route['id'])
        self.assertEqual(operations['results'][0]['delivery']['remaining'], 10)
        self.assertEqual(operations['results'][0]['stalled_nodes'], [])
        QuantityLedger.objects.filter(node_id=nodes['cut']['id']).update(
            created_at=timezone.now() - timedelta(hours=2))
        stalled = self.call(
            'GET', '/manufacturing/operations?stalled_hours=1', actor=self.admin)
        self.assertEqual(stalled['summary']['stalled_routes'], 1)
        self.assertEqual(stalled['results'][0]['stalled_nodes'][0]['available'], 10)
        QuantityLedger.objects.filter(node_id=nodes['cut']['id']).update(
            created_at=timezone.now())
        other_operations = self.call(
            'GET', '/manufacturing/operations', actor=self.other,
            company=self.other_company)
        self.assertEqual(other_operations['total'], 0)
        self.assertEqual(other_operations['summary']['wip'], 0)
        for query in (
                f'order_id={self.order.pk}', f'production_id={self.bed.pk}',
                f'customer_id={self.customer.pk}', 'style_no=LIVE-S1', 'q=LIVE-B1'):
            filtered = self.call(
                'GET', f'/manufacturing/operations?{query}', actor=self.admin)
            self.assertEqual(filtered['total'], 1, query)
            self.assertEqual(filtered['summary']['wip'], 10, query)
        self.assertEqual(self.call(
            'GET', '/manufacturing/operations?style_no=NO-SUCH-STYLE',
            actor=self.admin)['total'], 0)
        handover = self.post('/cutting/handovers/', {
            'ticket_id': self.ticket.pk, 'source_node_id': nodes['cut']['id'],
            'target_node_id': nodes['sew']['id'], 'receiver_id': self.worker.pk,
            'quantity': 10, 'request_id': rid(),
        }, actor=self.admin, expected=(201,))
        self.assertEqual(QuantityLedger.objects.filter(
            source=f"cutting_handover:{handover['id']}").count(), 0)
        signed = self.post(
            f"/cutting/handovers/{handover['id']}/sign/", {},
            actor=self.worker)
        self.assertEqual(signed['status'], 'signed')
        self.action('complete', nodes['sew'], 10)
        self.transfer(nodes['sew'], nodes['semi'], 10)

        semi = self.inspect(nodes['semi'], 'semi_inspection', 10, 9,
                            rework=1, rework_node=nodes['sew'])
        self.action('complete', nodes['sew'], 1)
        self.transfer(nodes['sew'], nodes['semi'], 1)
        self.inspect(nodes['semi'], 'reinspection', 1, 1, parent=semi['id'])
        self.transfer(nodes['semi'], nodes['special'], 10)
        self.action('complete', nodes['special'], 10)
        self.transfer(nodes['special'], nodes['iron'], 10)
        self.action('complete', nodes['iron'], 10)
        self.transfer(nodes['iron'], nodes['final'], 10)
        self.inspect(nodes['final'], 'final_inspection', 10, 10)
        self.transfer(nodes['final'], nodes['pack'], 10)

        packing_payload = {
            'route_id': route['id'], 'request_id': rid(),
            'boxes': [{'box_no': 'LIVE-BOX-1',
                       'items': [{'ticket_id': self.ticket.pk, 'quantity': 10}]}],
        }
        task = self.post('/finished-goods/packing-tasks', packing_payload,
                         actor=self.admin, expected=(201,))
        repeated = self.post('/finished-goods/packing-tasks', packing_payload,
                             actor=self.admin, expected=(201,))
        self.assertEqual(repeated['id'], task['id'])
        self.assertEqual(PackingTask.objects.count(), 1)
        approval_id = rid()
        self.post(f"/finished-goods/packing-tasks/{task['id']}/approve",
                  {'request_id': approval_id}, actor=self.admin)
        ledger_count = QuantityLedger.objects.count()
        self.post(f"/finished-goods/packing-tasks/{task['id']}/approve",
                  {'request_id': approval_id}, actor=self.admin)
        self.assertEqual(QuantityLedger.objects.count(), ledger_count)
        self.post('/finished-goods/packing-tasks', {
            'route_id': route['id'], 'request_id': rid(),
            'boxes': [{'box_no': 'LIVE-BOX-OVER',
                       'items': [{'ticket_id': self.ticket.pk, 'quantity': 1}]}],
        }, actor=self.admin, expected=(400,))
        box_id = task['boxes'][0]['id']
        inbound = self.post('/finished-goods/inbounds', {
            'route_id': route['id'], 'request_id': rid(),
            'warehouse_name': '隔离成品仓', 'location_name': 'LIVE-A1',
            'lines': [{'box_id': box_id, 'ticket_id': self.ticket.pk, 'quantity': 10}],
        }, actor=self.admin, expected=(201,))
        self.post(f"/finished-goods/inbounds/{inbound['id']}/approve",
                  {'request_id': rid()}, actor=self.admin)
        self.assertEqual(self.inventory(), 10)

        shipment = self.post('/shipments', {
            'order': self.order.pk, 'customer': self.customer.pk,
            'shipment_no': 'LIVE-SHIP-1',
            'items': [{'color': '红', 'size': 'M', 'qty': 6}],
        }, actor=self.admin, expected=(201,))
        self.post(f"/shipments/{shipment['id']}/boxes", {
            'box_no': 'LIVE-BOX-1', 'color': '红', 'size': 'M', 'qty': 6,
        }, actor=self.admin, expected=(201,))
        audited = self.post(f"/shipments/{shipment['id']}/audit", {}, actor=self.admin)
        self.assertEqual(audited['receivable']['amount'], '75.00')
        self.assertEqual(self.inventory(), 4)
        self.post(f"/finished-goods/inbounds/{inbound['id']}/reverse", {
            'request_id': rid(), 'reason': '出货后禁止直接冲销',
        }, actor=self.admin, expected=(400,))
        over = self.post('/shipments', {
            'order': self.order.pk, 'customer': self.customer.pk,
            'shipment_no': 'LIVE-SHIP-OVER',
            'items': [{'color': '红', 'size': 'M', 'qty': 5}],
        }, actor=self.admin, expected=(201,))
        self.post(f"/shipments/{over['id']}/boxes", {
            'box_no': 'LIVE-BOX-1', 'color': '红', 'size': 'M', 'qty': 5,
        }, actor=self.admin, expected=(201,))
        self.post(f"/shipments/{over['id']}/audit", {}, actor=self.admin,
                  expected=(400,))
        self.assertEqual(self.inventory(), 4)
        self.call('PUT', f"/shipments/{shipment['id']}", actor=self.admin,
                  payload={'items': [{'color': '红', 'size': 'M', 'qty': 1}]},
                  expected=(400,))
        self.post(f"/shipments/{shipment['id']}/void", {}, actor=self.admin)
        self.assertEqual(self.inventory(), 10)

        self.action('receive', nodes['cut'], 1, expected=(400,))
        self.post('/manufacturing/actions/receive', {
            'ticket_id': self.ticket.pk, 'node_id': nodes['cut']['id'],
            'quantity': 1, 'request_id': rid(),
        }, actor=self.viewer, expected=(403,))
        self.call('GET', f"/manufacturing/routes/{route['id']}",
                  actor=self.other, company=self.other_company, expected=(404,))
        timeline = self.call(
            'GET', f"/manufacturing/routes/{route['id']}/timeline?ticket_id={self.ticket.pk}",
            actor=self.admin)
        self.assertEqual(timeline['total'], 30)
        self.assertFalse(timeline['truncated'])
        dashboard = self.call(
            'GET', f"/manufacturing/routes/{route['id']}/dashboard", actor=self.admin)
        self.assertEqual(dashboard['summary']['wip'], 0)
        self.assertEqual(dashboard['summary']['finished_available'], 10)
        self.assertEqual(dashboard['delivery'], {
            'ordered': 10, 'shipped': 0, 'remaining': 10})
        operations_final = self.call(
            'GET', '/manufacturing/operations', actor=self.admin)['results'][0]
        self.assertEqual(operations_final['summary'], dashboard['summary'])
        self.assertEqual(operations_final['pending'], dashboard['pending'])
        self.assertEqual(operations_final['delivery'], dashboard['delivery'])

        self.post(f"/finished-goods/packing-tasks/{task['id']}/reverse", {
            'request_id': rid(), 'reason': '后续入库尚未冲销',
        }, actor=self.admin, expected=(400,))
        reversal_id = rid()
        reversed_inbound = self.post(f"/finished-goods/inbounds/{inbound['id']}/reverse", {
            'request_id': reversal_id, 'reason': '真实HTTP入库冲销',
        }, actor=self.admin)
        self.assertEqual(reversed_inbound['status'], 'reversed')
        self.assertEqual(self.inventory(), 0)
        ledger_count = QuantityLedger.objects.count()
        self.post(f"/finished-goods/inbounds/{inbound['id']}/reverse", {
            'request_id': reversal_id, 'reason': '真实HTTP入库冲销',
        }, actor=self.admin)
        self.assertEqual(QuantityLedger.objects.count(), ledger_count)
        reversed_task = self.post(f"/finished-goods/packing-tasks/{task['id']}/reverse", {
            'request_id': rid(), 'reason': '真实HTTP包装冲销',
        }, actor=self.admin)
        self.assertEqual(reversed_task['status'], 'reversed')
        self.post(f"/finished-goods/packing-tasks/{task['id']}/approve", {
            'request_id': rid(),
        }, actor=self.admin, expected=(400,))
        self.run_optional_web_ui()

    def run_optional_web_ui(self):
        """显式启用时，让真实浏览器访问同一隔离 LiveServer，而非 mock API。"""
        if os.environ.get('RUN_MANUFACTURING_WEB_UI') != '1':
            return
        frontend = Path(__file__).resolve().parents[4] / 'frontend-web'
        environment = os.environ.copy()
        environment.update({
            'WEB_BASE_URL': self.live_server_url,
            'WEB_PHONE': self.admin.phone,
            'WEB_PASSWORD': 'pw',
            'NO_PROXY': '127.0.0.1,localhost',
            'no_proxy': '127.0.0.1,localhost',
        })
        command = ['npx.cmd' if os.name == 'nt' else 'npx', 'playwright', 'test',
                   'e2e/manufacturing-flow-live.spec.js']
        result = subprocess.run(command, cwd=frontend, env=environment, text=True,
                                encoding='utf-8', errors='replace',
                                capture_output=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 0,
                         f'真实 Web UI 联调失败：\n{result.stdout}\n{result.stderr}')
