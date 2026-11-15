# -*- coding: utf-8 -*-
"""全功能、全链路隔离回归入口。

把项目已有的 shell-exec 业务套件放入 Django TransactionTestCase：
- 由 Django 创建空白测试库，绝不读取、清理或写入 db.sqlite3/生产库；
- 每个套件独立 flush，支持套件内的线程 API 请求；
- 同时检查脚本 FAIL 列表和 SystemExit，避免“打印 FAIL 但进程成功”；
- 依赖同一测试厂的订单脚本按声明顺序在同一用例中执行。

这里纳入的是自建测试厂、可从空库启动的套件。依赖演示公司/固定 company_id 的
旧脚本不再作为发布门禁；其功能由下方新入口及 test_release_regression 覆盖。
"""
import io
import traceback
from contextlib import redirect_stdout
from pathlib import Path

from django.db import connection
from django.test import TransactionTestCase

from apps.accounts.models import Company


BASE_DIR = Path(__file__).resolve().parent

# name -> 按顺序执行的脚本。订单域的新代码补缺依赖 test_orders 创建的测试厂。
SCRIPT_SUITES = {
    'accounts_and_ops': ['test_ops_accounts.py'],
    'ai_report': ['test_ai_report.py'],
    'branch_backfill': ['test_branch_backfill.py'],
    'cost': ['test_cost.py'],
    'deadline_notify': ['test_deadline_notify.py'],
    'e2e_full_dataset': ['test_e2e_full.py'],
    'finance': ['test_finance.py'],
    'finance_batch': ['test_finance_batch.py'],
    'gap_alignment': ['test_gap_align.py'],
    'kanban': ['test_kanban_suite.py'],
    'opening_stock': ['test_opening_stock.py'],
    'order_domain': [
        'test_orders.py', 'test_orders_gap.py', 'test_newcode_gap.py',
        'test_order_features.py', 'test_order_audit.py',
    ],
    'outsource': ['test_outsource.py'],
    'payslip': ['test_payslip.py'],
    'production': ['test_prod_gap.py'],
    'quality': ['test_quality.py'],
    'quotation': ['test_quotation.py'],
    'salary': ['test_salary_edge.py'],
    'scan_and_records': ['test_scan_enhance.py'],
    'shipment': ['test_shipment.py'],
    'tech_and_bom': ['test_tech.py'],
    'warehouse_quota_and_issue': ['test_warehouse_p1.py'],
    'warehouse_purchase_iqc_receipt': ['test_warehouse_p2.py'],
    # 一条共享数据的报价→订单→BOM→采购库存→生产→工资成本→出货应收全链。
    'full_erp_chain': ['test_e2e_consistency.py'],
}


class FullFunctionalTests(TransactionTestCase):
    """每个脚本组在独立、空白、可回滚的 Django 测试数据库中运行。"""

    def setUp(self):
        db_name = str(connection.settings_dict['NAME'])
        if 'test' not in db_name.lower() and 'memory' not in db_name.lower():
            raise RuntimeError(f'安全保护：拒绝在非测试数据库执行全功能套件: {db_name}')
        if Company.all_objects.filter(pk=86).exists():
            raise RuntimeError('安全保护：测试库不得预置真实厂 company id=86')

    def run_scripts(self, filenames):
        total_assertions = 0
        for filename in filenames:
            path = BASE_DIR / filename
            if not path.is_file():
                self.fail(f'测试套件缺失: {filename}')
            namespace = {
                '__name__': '__main__', '__file__': str(path),
                'ISOLATED_TEST_RUN': True,
            }
            output = io.StringIO()
            try:
                with redirect_stdout(output):
                    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), namespace)
            except SystemExit as exc:
                failures = namespace.get('FAIL', [])
                if exc.code not in (None, 0) or failures:
                    self.fail(
                        f'{filename} 退出码={exc.code!r}, FAIL={failures}\n'
                        f'{output.getvalue()[-16000:]}')
            except Exception as exc:
                self.fail(
                    f'{filename} 执行异常: {exc}\n{traceback.format_exc()}\n'
                    f'{output.getvalue()[-16000:]}')

            failures = namespace.get('FAIL')
            passed = namespace.get('PASS')
            if failures is None or passed is None:
                self.fail(f'{filename} 未暴露 PASS/FAIL 结果，不能作为统一门禁\n'
                          f'{output.getvalue()[-8000:]}')
            if failures:
                self.fail(f'{filename}: {len(failures)} 个断言失败: {failures}\n'
                          f'{output.getvalue()[-16000:]}')
            total_assertions += len(passed)
            print(f'  [隔离套件] {filename}: {len(passed)} assertions PASS')
        print(f'  [套件组通过] {", ".join(filenames)}: {total_assertions} assertions')


def _make_script_test(suite_name, filenames):
    def test(self):
        self.run_scripts(filenames)
    test.__name__ = f'test_{suite_name}'
    test.__doc__ = f'隔离执行: {", ".join(filenames)}'
    return test


for _suite_name, _filenames in SCRIPT_SUITES.items():
    setattr(FullFunctionalTests, f'test_{_suite_name}',
            _make_script_test(_suite_name, _filenames))
