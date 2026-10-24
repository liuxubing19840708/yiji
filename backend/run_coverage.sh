#!/bin/sh
# 全量 L1 套件 × 分支覆盖率测量（2026-10-23 门禁复测 + 拟真数据补缺套件）
# 用法: sh run_coverage.sh  （产物: coverage_suite.json + 每套件 PASS/FAIL 尾行）
cd "D:/project/扫菲/backend" || exit 1
PY=.venv/Scripts/python.exe
export PYTHONIOENCODING=utf-8
rm -f .coverage coverage_suite.json

# 脚本式套件（manage.py shell exec）
SCRIPT_SUITES="test_orders.py test_orders_gap.py test_newcode_gap.py test_scan_enhance.py \
test_kanban_suite.py test_ops_accounts.py test_prod_gap.py \
test_branch_backfill.py test_order_features.py test_salary_edge.py \
test_deadline_notify.py test_e2e_full.py"

for S in $SCRIPT_SUITES; do
  [ -f "$S" ] || { echo "===== SKIP $S (不存在) ====="; continue; }
  echo "===== RUN $S ====="
  $PY -m coverage run --append manage.py shell -c \
    "exec(compile(open('$S',encoding='utf-8').read(),'$S','exec'))" 2>&1 |
    grep -E "pass / |FAIL|Error|Traceback" | tail -4
done

# TestCase 式套件（manage.py test，事务回滚）
echo "===== RUN manage.py test (stats_refactor + kanban_link + bundle_progress) ====="
$PY -m coverage run --append manage.py test test_stats_refactor test_kanban_link test_bundle_progress test_print_manage 2>&1 | tail -3

$PY -m coverage json -o coverage_suite.json
$PY - <<'EOF'
import json
d = json.load(open('coverage_suite.json', encoding='utf-8'))
t = d['totals']
stmt_pct = t['covered_lines'] / max(t['num_statements'], 1) * 100
br_pct = t['covered_branches'] / max(t['num_branches'], 1) * 100
print('=== COVERAGE 总览 ===')
print(f"语句: {t['covered_lines']}/{t['num_statements']} = {stmt_pct:.1f}%")
print(f"分支: {t['covered_branches']}/{t['num_branches']} = {br_pct:.1f}%  (门禁 >=80%)")
print(f"综合(语句+分支): {t['percent_covered_display']}%")
EOF
