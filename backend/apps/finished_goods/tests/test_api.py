from uuid import uuid4

from rest_framework.test import APIClient

from apps.accounts.models import Post, User, UserCompany
from apps.manufacturing.models import QuantityLedger
from apps.finished_goods.models import FinishedInbound, PackingTask
from .test_services import FinishedGoodsTestBase, rid


class FinishedGoodsAPITests(FinishedGoodsTestBase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.headers = {'HTTP_X_COMPANY_ID': str(self.company.pk)}

    def test_complete_api_flow_and_inventory_contract(self):
        self.receive()
        create_response = self.client.post('/api/finished-goods/packing-tasks', {
            'route_id': self.route.pk, 'request_id': rid(), 'boxes': [
                {'box_no': 'FG-API-BOX',
                 'items': [{'ticket_id': self.ticket.pk, 'quantity': 4}]}],
        }, format='json', **self.headers)
        self.assertEqual(create_response.status_code, 201, create_response.content)
        task_id = create_response.data['id']
        self.assertEqual(create_response.data['packing_node'], self.packing.pk)
        self.assertEqual(create_response.data['inbound_node'], self.inbound.pk)
        self.assertEqual(create_response.data['boxes'][0]['lines'][0]['color'], '黑')
        box_id = create_response.data['boxes'][0]['id']

        approve_response = self.client.post(
            f'/api/finished-goods/packing-tasks/{task_id}/approve', {'request_id': rid()},
            format='json', **self.headers)
        self.assertEqual(approve_response.status_code, 200, approve_response.content)
        self.assertEqual(approve_response.data['status'], 'approved')

        inbound_response = self.client.post('/api/finished-goods/inbounds', {
            'route_id': self.route.pk, 'request_id': rid(), 'warehouse_name': 'API测试仓',
            'location_name': 'API-01', 'lines': [
                {'box_id': box_id, 'ticket_id': self.ticket.pk, 'quantity': 3}],
        }, format='json', **self.headers)
        self.assertEqual(inbound_response.status_code, 201, inbound_response.content)
        document_id = inbound_response.data['id']
        self.assertEqual(inbound_response.data['warehouse_name'], 'API测试仓')
        self.assertEqual(inbound_response.data['lines'][0]['box_no'], 'FG-API-BOX')
        inbound_approve = self.client.post(
            f'/api/finished-goods/inbounds/{document_id}/approve', {'request_id': rid()},
            format='json', **self.headers)
        self.assertEqual(inbound_approve.status_code, 200, inbound_approve.content)

        inventory = self.client.get('/api/finished-goods/inventory', **self.headers)
        self.assertEqual(inventory.status_code, 200, inventory.content)
        self.assertEqual(inventory.data['total_available'], 3)
        self.assertEqual(len(inventory.data['results']), 1)
        self.assertEqual(inventory.data['results'][0]['node_id'], self.inbound.pk)
        box_response = self.client.get(f'/api/finished-goods/boxes/{box_id}', **self.headers)
        self.assertEqual(box_response.status_code, 200)
        self.assertEqual(box_response.data['total_quantity'], 4)

    def test_view_permission_cannot_write_and_missing_permission_cannot_read(self):
        viewer = User.objects.create_user(username='fg-viewer', phone='13800000012',
                                          password='password')
        view_post = Post.objects.create(company=self.company, name='成品查看',
                                        permissions=['finished_goods.view'])
        UserCompany.objects.create(user=viewer, company=self.company, post=view_post,
                                   status=UserCompany.Status.ACTIVE)
        self.client.force_authenticate(viewer)
        response = self.client.get('/api/finished-goods/packing-tasks', **self.headers)
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/api/finished-goods/packing-tasks', {
            'route_id': self.route.pk, 'request_id': rid(), 'boxes': []},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 403)
        view_post.permissions = []
        view_post.save(update_fields=['permissions', 'updated_at'])
        response = self.client.get('/api/finished-goods/inventory', **self.headers)
        self.assertEqual(response.status_code, 403)

    def test_api_cross_company_detail_and_over_quantity_fail_cleanly(self):
        self.receive()
        task = self.make_task(quantity=2)
        other_user = User.objects.create_user(username='fg-other-user', phone='13800000013',
                                              password='password')
        other_post = Post.objects.create(company=self.other_company, name='查看',
                                         permissions=['finished_goods.view'])
        UserCompany.objects.create(user=other_user, company=self.other_company, post=other_post,
                                   status=UserCompany.Status.ACTIVE)
        self.client.force_authenticate(other_user)
        response = self.client.get(f'/api/finished-goods/packing-tasks/{task.pk}',
                                   HTTP_X_COMPANY_ID=str(self.other_company.pk))
        self.assertEqual(response.status_code, 404)

        self.client.force_authenticate(self.user)
        before = QuantityLedger.objects.count()
        response = self.client.post('/api/finished-goods/packing-tasks', {
            'route_id': self.route.pk, 'request_id': rid(), 'boxes': [
                {'box_no': 'FG-API-OVER',
                 'items': [{'ticket_id': self.ticket.pk, 'quantity': 99}]}]},
            format='json', **self.headers)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(QuantityLedger.objects.count(), before)

    def test_api_create_idempotency_returns_original_document(self):
        self.receive()
        request_id = uuid4().hex
        payload = {'route_id': self.route.pk, 'request_id': request_id, 'boxes': [
            {'box_no': 'FG-API-IDEMPOTENT',
             'items': [{'ticket_id': self.ticket.pk, 'quantity': 2}]}]}
        first = self.client.post('/api/finished-goods/packing-tasks', payload, format='json',
                                 **self.headers)
        second = self.client.post('/api/finished-goods/packing-tasks', payload, format='json',
                                  **self.headers)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(PackingTask.objects.count(), 1)
    def test_reversed_document_detail_exposes_operator_and_immutable_ledger_links(self):
        self.receive()
        task = self.make_task(quantity=4, box_no='FG-DETAIL-BOX')
        document = self.make_inbound(task, quantity=3)
        inbound_reverse = self.client.post(
            f'/api/finished-goods/inbounds/{document.pk}/reverse',
            {'request_id': rid(), 'reason': '入库明细核对冲销'}, format='json', **self.headers)
        self.assertEqual(inbound_reverse.status_code, 200, inbound_reverse.content)
        packing_reverse = self.client.post(
            f'/api/finished-goods/packing-tasks/{task.pk}/reverse',
            {'request_id': rid(), 'reason': '包装明细核对冲销'}, format='json', **self.headers)
        self.assertEqual(packing_reverse.status_code, 200, packing_reverse.content)

        list_response = self.client.get('/api/finished-goods/inbounds', **self.headers)
        self.assertEqual(list_response.data[0]['ledger_entries'], [])
        self.assertEqual(list_response.data[0]['reverser_name'], str(self.user))
        for kind, document_id, expected_count in (
                ('inbounds', document.pk, 4), ('packing-tasks', task.pk, 2)):
            response = self.client.get(
                f'/api/finished-goods/{kind}/{document_id}', **self.headers)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.data['reverser_name'], str(self.user))
            entries = response.data['ledger_entries']
            self.assertEqual(len(entries), expected_count)
            original_ids = {entry['id'] for entry in entries if entry['reversal_of'] is None}
            reversals = [entry for entry in entries if entry['reversal_of'] is not None]
            self.assertTrue(original_ids)
            self.assertEqual(len(reversals), expected_count // 2)
            self.assertTrue(all(entry['reversal_of'] in original_ids for entry in reversals))
            self.assertTrue(all(entry['operator_name'] for entry in entries))
