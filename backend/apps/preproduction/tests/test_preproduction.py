"""产前域服务/API 隔离测试。运行时仅需把 apps.preproduction 加入 INSTALLED_APPS。"""
from django.test import TestCase, override_settings
from django.urls import resolve
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import Company, Post, User, UserCompany
from apps.catalog.models import Style
from apps.orders.models import Order

from .. import services as svc
from ..models import PreproductionApproval, PreproductionAuditEvent, ReviewIssue, SampleTask


class PreproductionTestCase(TestCase):
    def setUp(self):
        self.company = self.make_company('A厂', '900001')
        self.other_company = self.make_company('B厂', '900002')
        self.post = Post.objects.create(company=self.company, name='技术主管', permissions=[
            'preproduction.view', 'preproduction.manage', 'preproduction.brief', 'preproduction.approve'])
        self.manager = self.make_member(self.company, 'manager', self.post)
        self.employee = self.make_member(self.company, 'employee')
        other_post = Post.objects.create(company=self.other_company, name='技术主管', permissions=[
            'preproduction.view', 'preproduction.manage', 'preproduction.brief', 'preproduction.approve'])
        self.other_manager = self.make_member(self.other_company, 'other_manager', other_post)
        self.style = Style.objects.create(company=self.company, style_no='ST-1', name='夹克')
        self.order = Order.objects.create(company=self.company, order_no='SO-1', style=self.style)
        self.other_order = Order.objects.create(company=self.other_company, order_no='SO-2')

    @staticmethod
    def make_company(name, no):
        creator = User.objects.create_user(username=f'creator_{no}', password='pw')
        return Company.objects.create(name=name, company_no=no, creator=creator)

    @staticmethod
    def make_member(company, username, post=None):
        user = User.objects.create_user(username=username, password='pw', real_name=username)
        uc = UserCompany.objects.create(user=user, company=company, post=post,
                                        status=UserCompany.Status.ACTIVE)
        return uc

    def pack_data(self, **changes):
        data = {
            'pattern_no': 'P-001', 'pattern_files': ['patterns/p1.pdf'],
            'measurements': [{'size': 'M', 'point': '衣长', 'value': 70,
                              'tolerance_plus': 1, 'tolerance_minus': 1}],
            'tolerances': {'default': 1},
            'attachments': [{'name': '工艺单', 'path': 'tech/process.pdf'}],
            'requirements': '针距每厘米4针，领口平服',
            'challenges': '领口弧形对位', 'common_mistakes': '左右领不对称',
            'quality_standards': '尺寸在公差内，外观平服',
        }
        data.update(changes)
        return data

    def make_pack(self, **changes):
        return svc.create_tech_pack(self.manager, self.order, self.pack_data(**changes))

    def qualify_and_sign(self, pack):
        task = svc.create_sample_task(self.manager, pack, {'title': '确认样'})
        svc.record_sample_result(self.manager, task, 'passed', '尺寸和工艺合格')
        target = svc.add_briefing_target(self.manager, pack, self.employee.user_id)
        svc.sign_briefing(self.employee, target)
        svc.confirm_briefing(self.manager, pack, '已向生产班组完成交底')
        return task, target

    def approved_pack(self):
        pack = self.make_pack()
        self.qualify_and_sign(pack)
        approval = svc.decide_approval(self.manager, pack, True)
        return pack, approval

    @override_settings(ROOT_URLCONF='config.urls')
    def test_global_api_route_is_registered(self):
        match = resolve('/api/preproduction/tech-packs')
        self.assertEqual(match.func.view_class.__name__, 'TechPackListCreateView')

    def test_version_number_validation_and_duplicate_are_client_errors(self):
        for value in ('abc', '1.5', 1.5, 0, -1, True):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.make_pack(version_no=value)
        self.make_pack(version_no=1)
        with self.assertRaises(ValidationError):
            self.make_pack(version_no=1)

    def test_full_approval_and_gate_and_audit(self):
        pack, approval = self.approved_pack()
        self.assertEqual(approval.tech_pack, pack)
        self.assertEqual(svc.get_active_approval(self.order), approval)
        self.assertEqual(svc.assert_preproduction_approved(self.order), approval)
        actions = set(PreproductionAuditEvent.objects.filter(tech_pack=pack)
                      .values_list('action', flat=True))
        self.assertTrue({'tech_pack.created', 'sample_task.created', 'sample.passed',
                         'briefing_target.created', 'briefing.signed', 'briefing.confirmed',
                         'approval.approved'} <= actions)

    def test_reject_rework_review_and_resubmit(self):
        pack = self.make_pack()
        task = svc.create_sample_task(self.manager, pack, {'title': '产前样'})
        svc.record_sample_result(self.manager, task, 'failed', '衣长超差')
        task.refresh_from_db()
        self.assertEqual(task.status, SampleTask.Status.FAILED)
        svc.record_sample_result(self.manager, task, 'rework', '重新打样')
        issue = svc.create_review_issue(self.manager, pack, {'title': '领口起皱'})
        svc.rectify_review_issue(self.manager, issue, '调整领口纸样')
        svc.review_issue(self.manager, issue, False, '仍有起皱')
        self.assertEqual(issue.status, ReviewIssue.Status.OPEN)
        svc.rectify_review_issue(self.manager, issue, '复改并整烫')
        svc.review_issue(self.manager, issue, True, '复审通过')
        svc.record_sample_result(self.manager, task, 'passed', '重做样合格')
        target = svc.add_briefing_target(self.manager, pack, self.employee.user_id)
        svc.sign_briefing(self.employee, target)
        svc.confirm_briefing(self.manager, pack)
        rejected = svc.decide_approval(self.manager, pack, False, '历史遗留需主管复核')
        self.assertEqual(rejected.status, PreproductionApproval.Status.REJECTED)
        self.assertIsNone(svc.get_active_approval(self.order))
        approved = svc.decide_approval(self.manager, pack, True, '复核通过')
        self.assertEqual(svc.get_active_approval(self.order), approved)
        self.assertEqual(task.records.count(), 3)

    def test_size_change_invalidates_existing_approval(self):
        pack, _ = self.approved_pack()
        data = self.pack_data(measurements=[{'size': 'M', 'point': '衣长', 'value': 71}])
        svc.update_tech_pack(self.manager, pack, data)
        self.assertIsNone(svc.get_active_approval(self.order))
        with self.assertRaises(svc.PreproductionNotApproved):
            svc.assert_preproduction_approved(self.order)
        svc.confirm_briefing(self.manager, pack, '尺寸变更后重新交底')
        approval = svc.decide_approval(self.manager, pack, True, '尺寸变更后重新审批')
        self.assertNotEqual(approval.content_hash, '')
        self.assertEqual(svc.get_active_approval(self.order), approval)

    def test_new_version_invalidates_old_approval(self):
        old, _ = self.approved_pack()
        new = self.make_pack(pattern_no='P-002')
        old.refresh_from_db()
        self.assertFalse(old.is_current)
        self.assertTrue(new.is_current)
        self.assertIsNone(svc.get_active_approval(self.order))
        with self.assertRaises(ValidationError):
            svc.decide_approval(self.manager, old, True)

    def test_legacy_unsigned_target_does_not_replace_lightweight_confirmation(self):
        pack, _ = self.approved_pack()
        second = self.make_member(self.company, 'second_employee')
        svc.add_briefing_target(self.manager, pack, second.user_id)
        # 历史多人签收继续可查，但不是轻量审批条件，也不伪造为负责人确认。
        self.assertIsNotNone(svc.get_active_approval(self.order))
        self.assertEqual(pack.briefing_targets.get(employee=second.user).employee, second.user)

    def test_latest_sample_result_controls_approval(self):
        pack, _ = self.approved_pack()
        task = svc.create_sample_task(self.manager, pack, {'title': '追加样'})
        self.assertIsNone(svc.get_active_approval(self.order))
        svc.record_sample_result(self.manager, task, 'failed', '追加样不合格')
        self.assertIsNone(svc.get_active_approval(self.order))
        svc.record_sample_result(self.manager, task, 'passed', '重做后合格')
        self.assertIsNone(svc.get_active_approval(self.order))
        svc.decide_approval(self.manager, pack, True, '新样衣结果重新审批')
        self.assertIsNotNone(svc.get_active_approval(self.order))

    def test_briefing_requires_designated_employee_and_is_idempotent(self):
        pack = self.make_pack()
        target = svc.add_briefing_target(self.manager, pack, self.employee.user_id)
        with self.assertRaises(PermissionDenied):
            svc.sign_briefing(self.manager, target)
        svc.sign_briefing(self.employee, target, '已理解工艺要求')
        with self.assertRaises(ValidationError):
            svc.sign_briefing(self.employee, target)
        with self.assertRaises(ValidationError):
            svc.add_briefing_target(self.manager, pack, self.employee.user_id)

    def test_cross_company_cannot_read_mutate_or_approve(self):
        pack = self.make_pack()
        with self.assertRaises(ValidationError):
            svc.create_tech_pack(self.other_manager, self.order, self.pack_data())
        with self.assertRaises(ValidationError):
            svc.create_sample_task(self.other_manager, pack, {'title': '越权打样'})
        with self.assertRaises(ValidationError):
            svc.decide_approval(self.other_manager, pack, True)
        target = svc.add_briefing_target(self.manager, pack, self.employee.user_id)
        with self.assertRaises(ValidationError):
            svc.sign_briefing(self.other_manager, target)

    def test_permissions_manage_approve_and_active_membership(self):
        viewer_post = Post.objects.create(company=self.company, name='查看岗',
                                          permissions=['preproduction.view'])
        viewer = self.make_member(self.company, 'viewer', viewer_post)
        with self.assertRaises(PermissionDenied):
            svc.create_tech_pack(viewer, self.order, self.pack_data())
        pack = self.make_pack()
        self.qualify_and_sign(pack)
        manage_only_post = Post.objects.create(company=self.company, name='技术岗',
                                               permissions=['preproduction.manage'])
        manage_only = self.make_member(self.company, 'manage_only', manage_only_post)
        with self.assertRaises(PermissionDenied):
            svc.decide_approval(manage_only, pack, True)
        self.manager.status = UserCompany.Status.RESIGNED
        self.manager.save()
        with self.assertRaises(PermissionDenied):
            svc.create_review_issue(self.manager, pack, {'title': '离职账号操作'})

    def test_incomplete_readiness_and_duplicate_approval(self):
        pack = self.make_pack()
        with self.assertRaises(ValidationError) as ctx:
            svc.decide_approval(self.manager, pack, True)
        self.assertIn('problems', ctx.exception.detail)
        self.qualify_and_sign(pack)
        svc.decide_approval(self.manager, pack, True)
        with self.assertRaises(ValidationError):
            svc.decide_approval(self.manager, pack, True)
        self.assertEqual(PreproductionApproval.objects.filter(tech_pack=pack).count(), 1)

    def test_rejecting_approved_version_closes_gate(self):
        pack, old = self.approved_pack()
        rejected = svc.decide_approval(self.manager, pack, False, '审批后发现工艺风险')
        old.refresh_from_db()
        self.assertEqual(old.status, PreproductionApproval.Status.INVALIDATED)
        self.assertEqual(rejected.status, PreproductionApproval.Status.REJECTED)
        self.assertIsNone(svc.get_active_approval(self.order))

    def test_order_style_change_requires_new_pack(self):
        pack, _ = self.approved_pack()
        other_style = Style.objects.create(company=self.company, style_no='ST-2', name='长裤')
        self.order.style = other_style
        self.order.save(update_fields=['style', 'updated_at'])
        pack.order = self.order
        self.assertIsNone(svc.get_active_approval(self.order))

    def test_lightweight_issue_resolution_and_reopen_invalidate_approval(self):
        pack, _ = self.approved_pack()
        issue = svc.create_review_issue(self.manager, pack, {'title': '袖口', 'detail': '袖口起皱'})
        self.assertIsNone(svc.get_active_approval(self.order))
        with self.assertRaises(ValidationError):
            svc.resolve_review_issue(self.manager, issue, '')
        svc.resolve_review_issue(self.manager, issue, '调整纸样并复样确认')
        self.assertIsNone(svc.get_active_approval(self.order))
        svc.decide_approval(self.manager, pack, True, '问题处理后重新审批')
        self.assertIsNotNone(svc.get_active_approval(self.order))
        self.assertEqual(issue.reviewed_by, self.manager.user)
        self.assertIsNotNone(issue.reviewed_at)
        svc.reopen_review_issue(self.manager, issue, '大货复核仍有问题')
        self.assertIsNone(svc.get_active_approval(self.order))

    def test_briefing_confirmation_requires_production_permission_and_current_content(self):
        pack = self.make_pack()
        manage_only_post = Post.objects.create(company=self.company, name='工艺岗',
                                               permissions=['preproduction.manage'])
        manage_only = self.make_member(self.company, 'craft_only', manage_only_post)
        with self.assertRaises(PermissionDenied):
            svc.confirm_briefing(manage_only, pack)
        svc.confirm_briefing(self.manager, pack)
        with self.assertRaises(ValidationError):
            svc.confirm_briefing(self.manager, pack)
        svc.update_tech_pack(self.manager, pack, self.pack_data(challenges='新难点'))
        pack.refresh_from_db()
        self.assertIsNone(svc.current_briefing_confirmation(pack))
        self.assertEqual(pack.briefing_confirmations.count(), 1)

    def test_reverting_craft_or_deleting_new_issue_does_not_restore_old_approval(self):
        pack, _ = self.approved_pack()
        original = self.pack_data()
        svc.update_tech_pack(self.manager, pack, self.pack_data(requirements='临时工艺变更'))
        svc.update_tech_pack(self.manager, pack, original)
        pack.refresh_from_db()
        self.assertIsNone(svc.get_active_approval(self.order))
        self.assertIsNotNone(svc.current_briefing_confirmation(pack))
        svc.decide_approval(self.manager, pack, True, '恢复工艺后重新审批')
        issue = svc.create_review_issue(self.manager, pack, {'title': '临时问题'})
        svc.delete_review_issue(self.manager, issue)
        self.assertIsNone(svc.get_active_approval(self.order))

    def test_pattern_number_is_generated_when_blank(self):
        pack = self.make_pack(pattern_no='')
        self.assertEqual(pack.pattern_no, f'P{self.order.id:06d}-V1')
        new = self.make_pack(pattern_no='')
        self.assertEqual(new.pattern_no, f'P{self.order.id:06d}-V2')

    def test_company_craft_template_create_update_and_cross_company_isolation(self):
        created = svc.save_craft_template(self.manager, {
            'kind': 'requirements', 'name': '本厂针织要求', 'content': '使用适合针织面料的针迹'})
        self.assertFalse(created.is_system)
        changed = svc.save_craft_template(self.manager, {
            'kind': 'requirements', 'name': '本厂针织要求V2', 'content': '首件确认后生产'}, created)
        self.assertEqual(changed.pk, created.pk)
        self.assertEqual(changed.content, '首件确认后生产')
        with self.assertRaises(ValidationError):
            svc.save_craft_template(self.other_manager, {
                'kind': 'requirements', 'name': '越权修改', 'content': '不允许'}, created)
        self.assertEqual(svc.list_craft_templates(self.other_manager, 'requirements')
                         .filter(pk=created.pk).count(), 0)

    def test_measurement_template_validation_and_system_text_templates(self):
        with self.assertRaises(ValidationError):
            svc.save_craft_template(self.manager, {
                'kind': 'measurements', 'name': '空尺寸', 'content': []})
        template = svc.save_craft_template(self.manager, {
            'kind': 'measurements', 'name': '本厂上装尺寸', 'content': self.pack_data()['measurements']})
        self.assertEqual(template.content[0]['size'], 'M')
        system = svc.list_craft_templates(self.manager, 'quality_standards')
        self.assertGreaterEqual(system.filter(company__isnull=True).count(), 2)
        with self.assertRaises(ValidationError):
            svc.delete_craft_template(self.manager, system.filter(company__isnull=True).first())

    def test_soft_delete_current_pack_closes_gate(self):
        pack, _ = self.approved_pack()
        svc.delete_tech_pack(self.manager, pack)
        self.assertIsNone(svc.get_active_approval(self.order))
        self.assertFalse(TechPackVisible(self.order))


def TechPackVisible(order):
    from ..models import TechPackVersion
    return TechPackVersion.objects.filter(order=order, is_current=True).exists()


@override_settings(ROOT_URLCONF='apps.preproduction.urls')
class PreproductionAPITest(PreproductionTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.manager.user)
        self.headers = {'HTTP_X_COMPANY_ID': str(self.company.id)}

    def test_api_complete_flow_uses_service_contract(self):
        response = self.client.post('/preproduction/tech-packs',
                                    {'order': self.order.id, **self.pack_data()},
                                    format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        pack_id = response.data['id']
        response = self.client.post(f'/preproduction/tech-packs/{pack_id}/samples',
                                    {'title': '确认样'}, format='json', **self.headers)
        task_id = response.data['id']
        response = self.client.post(f'/preproduction/samples/{task_id}/records',
                                    {'result': 'passed', 'note': '合格'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(f'/preproduction/tech-packs/{pack_id}/briefing-targets',
                                    {'employee': self.employee.user_id}, format='json', **self.headers)
        target_id = response.data['id']
        employee_client = APIClient()
        employee_client.force_authenticate(self.employee.user)
        response = employee_client.post(f'/preproduction/briefing-targets/{target_id}/sign', {},
                                        format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(f'/preproduction/tech-packs/{pack_id}/briefing-confirmation',
                                    {'note': '班组已交底'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(f'/preproduction/tech-packs/{pack_id}/approval',
                                    {'action': 'approve'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.get(f'/preproduction/orders/{self.order.id}/status', **self.headers)
        self.assertTrue(response.data['approved'])
        self.assertEqual(response.data['status'], 'approved')
        self.assertEqual(response.data['status_display'], '已通过')
        self.assertEqual(response.data['missing_items'], [])
        self.assertEqual(response.data['unresolved_issue_count'], 0)
        self.assertEqual(response.data['current_sample']['result'], 'passed')
        self.assertEqual(response.data['briefing_confirmation']['confirmed_by_name'], 'manager')

    def test_api_permission_and_cross_company_order_hidden(self):
        no_perm_post = Post.objects.create(company=self.company, name='无权限', permissions=[])
        no_perm = self.make_member(self.company, 'no_perm', no_perm_post)
        client = APIClient()
        client.force_authenticate(no_perm.user)
        response = client.get('/preproduction/tech-packs', **self.headers)
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/preproduction/tech-packs',
                                    {'order': self.other_order.id, **self.pack_data()},
                                    format='json', **self.headers)
        self.assertEqual(response.status_code, 404)
