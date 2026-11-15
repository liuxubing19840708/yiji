from django.test import SimpleTestCase

from apps.core.permissions import ALL_PERMISSION_KEYS
from apps.ops.models import CompanySetting


class WorkflowFoundationConfigTests(SimpleTestCase):
    def test_new_workflow_permissions_are_registered_once(self):
        expected = {
            'preproduction.view',
            'preproduction.manage',
            'preproduction.approve',
            'manufacturing.view',
            'manufacturing.manage',
            'manufacturing.reverse',
            'cutting.view',
            'cutting.manage',
            'handover.view',
            'handover.create',
            'handover.receive',
            'handover.manage',
            'finished_goods.view',
            'finished_goods.manage',
        }
        self.assertTrue(expected.issubset(ALL_PERMISSION_KEYS))
        self.assertEqual(len(ALL_PERMISSION_KEYS), len(set(ALL_PERMISSION_KEYS)))

    def test_new_quantity_gates_default_to_compatibility_mode(self):
        self.assertEqual(CompanySetting.DEFAULTS['preproduction_gate_mode'], 'off')
        self.assertEqual(CompanySetting.DEFAULTS['manufacturing_gate_mode'], 'off')
