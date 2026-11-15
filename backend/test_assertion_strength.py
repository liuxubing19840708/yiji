# -*- coding: utf-8 -*-
"""发布脚本断言强度门禁：禁止恒真、跳过即通过和模糊成功状态码。"""
import ast
from pathlib import Path

from django.test import SimpleTestCase

from test_full_functional import BASE_DIR, SCRIPT_SUITES


class AssertionStrengthTests(SimpleTestCase):
    def test_gated_scripts_have_no_known_false_green_patterns(self):
        findings = []
        for filename in sorted({name for group in SCRIPT_SUITES.values() for name in group}):
            path = Path(BASE_DIR) / filename
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            parents = {
                child: parent
                for parent in ast.walk(tree)
                for child in ast.iter_child_nodes(parent)
            }
            for node in ast.walk(tree):
                if (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
                        and any(isinstance(value, ast.Constant) and value.value is True
                                for value in node.values)):
                    findings.append(f'{filename}:{node.lineno} 使用 or True，断言恒真')
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'check' and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value is True):
                    ancestor = parents.get(node)
                    in_expected_exception = False
                    while ancestor is not None:
                        if isinstance(ancestor, ast.ExceptHandler):
                            in_expected_exception = True
                            break
                        ancestor = parents.get(ancestor)
                    if not in_expected_exception:
                        findings.append(f'{filename}:{node.lineno} check(..., True) 无条件通过')
                if not (isinstance(node, ast.Compare)
                        and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
                        and len(node.comparators) == 1
                        and isinstance(node.comparators[0], (ast.Tuple, ast.Set, ast.List))):
                    continue
                left = node.left
                is_status = ((isinstance(left, ast.Name) and left.id in {'st', 'code'})
                             or (isinstance(left, ast.Attribute) and left.attr == 'status_code'))
                statuses = [element.value for element in node.comparators[0].elts
                            if isinstance(element, ast.Constant)
                            and isinstance(element.value, int)]
                if is_status and 200 in statuses and len(statuses) > 1:
                    findings.append(
                        f'{filename}:{node.lineno} 成功状态码使用多值 {statuses}，应固定接口契约')
        self.assertEqual(findings, [], '\n'.join(findings))
