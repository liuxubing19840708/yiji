#!/bin/sh
# 全功能 + 全链路发布回归。任何测试失败均由 Django runner 返回非零。
set -eu
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8

if [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=${PYTHON:-python}
fi

"$PY" manage.py test \
  test_full_functional \
  test_release_regression \
  test_stats_refactor \
  test_kanban_link \
  test_bundle_progress \
  test_print_manage \
  -v 2
