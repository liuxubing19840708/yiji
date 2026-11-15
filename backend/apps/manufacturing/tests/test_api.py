"""生产路线与受控节点执行的全局 API 测试。"""
from uuid import uuid4

from rest_framework.test import APIClient
from django.test import TestCase

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Style, Ticket
from apps.manufacturing.models import ProductionRoute, ProductionStage, QuantityLedger
from apps.production.models import Production


def rid():
    return uuid4().hex


class ManufacturingAPITests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='mfg-api-owner', password='pw')
        self.company = Company.objects.create(name='制造API隔离厂', company_no='910001',
                                              creator=self.owner)
        self.admin_post = Post.objects.create(company=self.company, name='管理员', is_admin=True)
        self.admin = User.objects.create_user(username='mfg-api-admin', password='pw')
        self.membership = UserCompany.objects.create(
            company=self.company, user=self.admin, post=self.admin_post,
            status=UserCompany.Status.ACTIVE)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.headers = {'HTTP_X_COMPANY_ID': str(self.company.id)}
        self.style = Style.objects.create(company=self.company, style_no='API-S1', name='测试款')
        self.bed = Production.objects.create(company=self.company, style=self.style,
                                             bed_no='API-B1', total_qty=10)
        self.ticket = Ticket.objects.create(
            company=self.company, production=self.bed, bed_no=self.bed.bed_no,
            bundle_no='1', color='黑', size='M', qty=10, qr_code='API-B1-1')

    def create_stage(self, code, stage_type):
        response = self.client.post('/api/manufacturing/stages', {
            'code': code, 'name': code, 'stage_type': stage_type,
        }, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def create_route(self):
        cut = self.create_stage('cut', 'cutting')
        sew = self.create_stage('sew', 'sewing')
        response = self.client.post('/api/manufacturing/routes', {
            'code': 'TEMPLATE-1', 'name': '测试路线',
            'nodes': [
                {'stage_id': cut['id'], 'sequence': 1, 'required': True},
                {'stage_id': sew['id'], 'sequence': 2, 'required': True},
            ],
        }, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(
            f"/api/manufacturing/routes/{response.data['id']}/instantiate",
            {'production_id': self.bed.id, 'code': 'ROUTE-1'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def action(self, action, **data):
        return self.client.post(f'/api/manufacturing/actions/{action}', data,
                                format='json', **self.headers)

    def test_full_route_flow_balance_and_reversal(self):
        route = self.create_route()
        cut, sew = route['nodes']
        response = self.action('receive', ticket_id=self.ticket.id, node_id=cut['id'],
                               quantity=10, request_id=rid())
        self.assertEqual(response.status_code, 201, response.data)
        response = self.action('complete', ticket_id=self.ticket.id, node_id=cut['id'],
                               quantity=10, request_id=rid())
        self.assertEqual(response.status_code, 201, response.data)
        pair_id = rid()
        response = self.action('transfer', ticket_id=self.ticket.id, node_id=cut['id'],
                               to_node_id=sew['id'], quantity=10, request_id=pair_id)
        self.assertEqual(response.status_code, 201, response.data)
        pair = response.data
        retry = self.action('transfer', ticket_id=self.ticket.id, node_id=cut['id'],
                            to_node_id=sew['id'], quantity=10, request_id=pair_id)
        self.assertEqual([row['id'] for row in retry.data], [row['id'] for row in pair])
        self.assertEqual(QuantityLedger.objects.count(), 4)
        response = self.client.get(f"/api/manufacturing/routes/{route['id']}/balances",
                                   {'ticket_id': self.ticket.id}, **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[1]['balance']['available'], 10)
        for entry in reversed(pair):
            response = self.client.post(f"/api/manufacturing/ledger/{entry['id']}/reverse",
                                        {'request_id': rid(), 'reason': '测试冲销'},
                                        format='json', **self.headers)
            self.assertEqual(response.status_code, 201, response.data)
        response = self.client.get(f"/api/manufacturing/routes/{route['id']}/balances",
                                   {'ticket_id': self.ticket.id}, **self.headers)
        self.assertEqual(response.data[0]['balance']['available'], 10)
        self.assertEqual(response.data[1]['balance']['available'], 0)
        response = self.client.get(f"/api/manufacturing/routes/{route['id']}/timeline",
                                   {'ticket_id': self.ticket.id}, **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], 6)
        self.assertFalse(response.data['truncated'])
        self.assertEqual(response.data['events'][0]['bed_no'], 'API-B1')
        self.assertEqual(response.data['events'][0]['bundle_no'], '1')

    def test_route_dashboard_uses_ledger_balances_and_document_counts(self):
        route = self.create_route()
        cut, sew = route['nodes']
        self.action('receive', ticket_id=self.ticket.id, node_id=cut['id'],
                    quantity=10, request_id=rid())
        self.action('complete', ticket_id=self.ticket.id, node_id=cut['id'],
                    quantity=10, request_id=rid())
        self.action('transfer', ticket_id=self.ticket.id, node_id=cut['id'],
                    to_node_id=sew['id'], quantity=6, request_id=rid())
        response = self.client.get(
            f"/api/manufacturing/routes/{route['id']}/dashboard", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['summary'], {
            'tickets': 1, 'ticket_qty': 10, 'wip': 10,
            'scrapped': 0, 'finished_available': 0})
        self.assertEqual(response.data['pending'], {
            'quality': 0, 'cutting': 0, 'packing': 0, 'inbound': 0})
        row = response.data['rows'][0]
        self.assertEqual(row['nodes'][0]['available'], 4)
        self.assertEqual(row['nodes'][1]['available'], 6)
        self.assertIsNone(response.data['delivery'])

    def test_cannot_skip_required_or_receive_at_downstream_node(self):
        route = self.create_route()
        cut, sew = route['nodes']
        response = self.action('receive', ticket_id=self.ticket.id, node_id=sew['id'],
                               quantity=1, request_id=rid())
        self.assertEqual(response.status_code, 400, response.data)
        self.action('receive', ticket_id=self.ticket.id, node_id=cut['id'],
                    quantity=10, request_id=rid())
        self.action('complete', ticket_id=self.ticket.id, node_id=cut['id'],
                    quantity=10, request_id=rid())
        other = self.create_stage('final', 'final_inspection')
        route_obj = ProductionRoute.objects.get(pk=route['id'])
        from apps.manufacturing.models import RouteNode
        target = RouteNode.objects.create(company=self.company, route=route_obj,
                                          stage_id=other['id'], sequence=3)
        response = self.action('transfer', ticket_id=self.ticket.id, node_id=cut['id'],
                               to_node_id=target.id, quantity=10, request_id=rid())
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('不能跳过必过节点', str(response.data))

    def test_view_only_cannot_write(self):
        route = self.create_route()
        post = Post.objects.create(company=self.company, name='只看',
                                   permissions=['manufacturing.view'])
        self.membership.post = post
        self.membership.save(update_fields=['post'])
        response = self.client.get('/api/manufacturing/routes', **self.headers)
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/api/manufacturing/stages', {
            'code': 'other', 'name': 'other', 'stage_type': 'other',
        }, format='json', **self.headers)
        self.assertEqual(response.status_code, 403)
        response = self.action('receive', ticket_id=self.ticket.id,
                               node_id=route['nodes'][0]['id'], quantity=1, request_id=rid())
        self.assertEqual(response.status_code, 403)
        entry = QuantityLedger.objects.create(
            company=self.company, route_id=route['id'], node_id=route['nodes'][0]['id'],
            production=self.bed, ticket=self.ticket, event_type='received', quantity=1,
            request_id=rid(), request_fingerprint='0' * 64)
        response = self.client.post(f'/api/manufacturing/ledger/{entry.id}/reverse',
                                    {'request_id': rid()}, format='json', **self.headers)
        self.assertEqual(response.status_code, 403)

    def test_cross_company_objects_are_hidden(self):
        route = self.create_route()
        other = Company.objects.create(name='制造API其他厂', company_no='910002', creator=self.owner)
        other_post = Post.objects.create(company=other, name='管理员', is_admin=True)
        UserCompany.objects.create(company=other, user=self.admin, post=other_post,
                                   status=UserCompany.Status.ACTIVE)
        headers = {'HTTP_X_COMPANY_ID': str(other.id)}
        response = self.client.get(f"/api/manufacturing/routes/{route['id']}", **headers)
        self.assertEqual(response.status_code, 404)
        response = self.action('receive', ticket_id=self.ticket.id,
                               node_id=route['nodes'][0]['id'], quantity=1, request_id=rid())
        self.assertEqual(response.status_code, 201)  # 原公司上下文仍可用
        response = self.client.post('/api/manufacturing/actions/receive', {
            'ticket_id': self.ticket.id, 'node_id': route['nodes'][0]['id'],
            'quantity': 1, 'request_id': rid(),
        }, format='json', **headers)
        self.assertEqual(response.status_code, 404)

    def test_instance_route_is_immutable_and_duplicate_codes_are_400(self):
        route = self.create_route()
        response = self.client.put(f"/api/manufacturing/routes/{route['id']}", {
            'name': '不允许',
            'nodes': [{'stage_id': route['nodes'][0]['stage'], 'sequence': 1}],
        }, format='json', **self.headers)
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/manufacturing/routes', {
            'code': 'TEMPLATE-1', 'name': '重复编码',
            'nodes': [{'stage_id': route['nodes'][0]['stage'], 'sequence': 1}],
        }, format='json', **self.headers)
        self.assertEqual(response.status_code, 400)

    def test_document_managed_nodes_cannot_bypass_business_documents(self):
        cut = self.create_stage('cut-doc', 'cutting')
        quality = self.create_stage('quality-doc', 'final_inspection')
        packing = self.create_stage('packing-doc', 'packing')
        inbound = self.create_stage('inbound-doc', 'finished_inbound')
        response = self.client.post('/api/manufacturing/routes', {
            'code': 'TEMPLATE-DOC', 'name': '单证节点路线',
            'nodes': [
                {'stage_id': cut['id'], 'sequence': 1},
                {'stage_id': quality['id'], 'sequence': 2},
                {'stage_id': packing['id'], 'sequence': 3},
                {'stage_id': inbound['id'], 'sequence': 4},
            ],
        }, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(
            f"/api/manufacturing/routes/{response.data['id']}/instantiate",
            {'production_id': self.bed.id, 'code': 'ROUTE-DOC'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        cut_node, quality_node, packing_node, inbound_node = response.data['nodes']
        cases = [
            ('complete', quality_node['id'], None, '质量检验单'),
            ('scrap', quality_node['id'], None, '质量检验单'),
            ('complete', packing_node['id'], None, '包装任务'),
            ('transfer', packing_node['id'], inbound_node['id'], '成品入库单'),
            ('transfer', cut_node['id'], inbound_node['id'], '成品入库单'),
            ('receive', inbound_node['id'], None, '成品现货'),
            ('transfer', inbound_node['id'], packing_node['id'], '成品现货'),
        ]
        for action, node_id, target_id, message in cases:
            with self.subTest(action=action, node_id=node_id, target_id=target_id):
                payload = {'ticket_id': self.ticket.id, 'node_id': node_id,
                           'quantity': 1, 'request_id': rid()}
                if target_id:
                    payload['to_node_id'] = target_id
                result = self.action(action, **payload)
                self.assertEqual(result.status_code, 400, result.data)
                self.assertIn(message, str(result.data))
    def test_stage_snapshots_and_configuration_warnings_protect_instances(self):
        route = self.create_route()
        template_id = route['source_template']
        template = self.client.get(
            f'/api/manufacturing/routes/{template_id}', **self.headers).data
        self.assertEqual(template['instance_count'], 1)
        self.assertEqual(len(template['configuration_warnings']), 3)
        self.assertTrue(all('缺少' in warning for warning in template['configuration_warnings']))

        cut = route['nodes'][0]
        response = self.client.put(f"/api/manufacturing/stages/{cut['stage']}", {
            'code': cut['stage_code'], 'name': '新的标准阶段名称', 'stage_type': cut['stage_type'],
        }, format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['in_use'])
        historical = self.client.get(
            f"/api/manufacturing/routes/{route['id']}", **self.headers).data['nodes'][0]
        self.assertEqual(historical['stage_name'], cut['stage_name'])
        response = self.client.put(f"/api/manufacturing/stages/{cut['stage']}", {
            'code': 'changed-code', 'name': '不允许改编码', 'stage_type': cut['stage_type'],
        }, format='json', **self.headers)
        self.assertEqual(response.status_code, 400)

        response = self.client.post(f"/api/manufacturing/stages/{cut['stage']}/status", {
            'is_active': False}, format='json', **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['is_active'])
        template = self.client.get(
            f'/api/manufacturing/routes/{template_id}', **self.headers).data
        self.assertTrue(any('已停用阶段' in warning
                            for warning in template['configuration_warnings']))
        response = self.action('receive', ticket_id=self.ticket.id, node_id=cut['id'],
                               quantity=1, request_id=rid())
        self.assertEqual(response.status_code, 201, response.data)

    def test_template_copy_new_version_deactivation_and_history_retention(self):
        instance = self.create_route()
        template_id = instance['source_template']
        copy_response = self.client.post(f'/api/manufacturing/routes/{template_id}/copy', {
            'code': 'TEMPLATE-COPY', 'name': '独立副本'}, format='json', **self.headers)
        self.assertEqual(copy_response.status_code, 201, copy_response.data)
        self.assertEqual(copy_response.data['version'], 1)
        self.assertIsNone(copy_response.data['previous_version'])
        self.assertEqual([node['stage_type'] for node in copy_response.data['nodes']],
                         [node['stage_type'] for node in instance['nodes']])

        version_response = self.client.post(
            f'/api/manufacturing/routes/{template_id}/new-version',
            {'code': 'TEMPLATE-V2', 'name': '第二版', 'nodes': [{
                'stage_id': instance['nodes'][1]['stage'], 'sequence': 1,
                'required': True, 'allow_parallel': False, 'allow_skip': False,
            }]}, format='json', **self.headers)
        self.assertEqual(version_response.status_code, 201, version_response.data)
        self.assertEqual(version_response.data['version'], 2)
        self.assertEqual(version_response.data['previous_version'], template_id)
        self.assertEqual(len(version_response.data['nodes']), 1)
        self.assertEqual(version_response.data['nodes'][0]['stage'], instance['nodes'][1]['stage'])
        response = self.client.post(f'/api/manufacturing/routes/{template_id}/status', {
            'is_active': False}, format='json', **self.headers)
        self.assertEqual(response.status_code, 200)
        other_bed = Production.objects.create(
            company=self.company, style=self.style, bed_no='API-B2', total_qty=10)
        response = self.client.post(
            f'/api/manufacturing/routes/{template_id}/instantiate',
            {'production_id': other_bed.id, 'code': 'ROUTE-2'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 400)
        response = self.client.delete(
            f'/api/manufacturing/routes/{template_id}', **self.headers)
        self.assertEqual(response.status_code, 400)
        historical = self.client.get(
            f"/api/manufacturing/routes/{instance['id']}", **self.headers)
        self.assertEqual(historical.status_code, 200)
        self.assertEqual(historical.data['code'], instance['code'])

    def test_operations_pagination_keeps_global_ledger_summary(self):
        first = self.create_route()
        other_bed = Production.objects.create(
            company=self.company, style=self.style, bed_no='API-B2', total_qty=6)
        other_ticket = Ticket.objects.create(
            company=self.company, production=other_bed, bed_no=other_bed.bed_no,
            bundle_no='1', color='黑', size='M', qty=6, qr_code='API-B2-1')
        response = self.client.post(
            f"/api/manufacturing/routes/{first['source_template']}/instantiate",
            {'production_id': other_bed.id, 'code': 'ROUTE-2'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        second = response.data
        for route, bed, ticket, quantity in (
                (first, self.bed, self.ticket, 4), (second, other_bed, other_ticket, 6)):
            QuantityLedger.objects.create(
                company=self.company, route_id=route['id'], node_id=route['nodes'][0]['id'],
                production=bed, ticket=ticket, event_type='received', quantity=quantity,
                request_id=rid(), request_fingerprint='0' * 64)
        original = QuantityLedger.objects.get(route_id=first['id'])
        QuantityLedger.objects.create(
            company=self.company, route_id=first['id'], node_id=first['nodes'][0]['id'],
            production=self.bed, ticket=self.ticket, event_type='reversal', quantity=1,
            reversal_of=original, request_id=rid(), request_fingerprint='1' * 64)
        response = self.client.get('/api/manufacturing/operations', {
            'page': 2, 'page_size': 1, 'stalled_hours': 8760,
        }, **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['total'], 2)
        self.assertEqual(response.data['pages'], 2)
        self.assertFalse(response.data['has_next'])
        self.assertTrue(response.data['has_previous'])
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['route'], second['id'])
        self.assertEqual(response.data['summary']['wip'], 9)
        first_page = self.client.get('/api/manufacturing/operations', {
            'page': 1, 'page_size': 1, 'stalled_hours': 8760,
        }, **self.headers).data
        self.assertTrue(first_page['has_next'])
        self.assertEqual(first_page['summary'], response.data['summary'])
