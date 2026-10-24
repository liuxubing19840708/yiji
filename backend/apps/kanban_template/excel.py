# -*- coding: utf-8 -*-
"""Excel 表头解析（openpyxl，只读不落库）：支持选择表头行 + 两级/合并表头。

返回列定义（供配置端拖拽映射）：
  [{col_index: 1, title: '成品', parent: '1组', col_span: 1}, ...]
  - 单级表头：parent=''
  - 两级表头：parent = 上一行合并单元格的左上值（如「1组」跨 3 列，
    下一行「成品/半成品/退货」各成一列）；被合并覆盖的列只在其
    左上角位置输出一次，col_span = 该合并块宽度。
"""
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

MAX_SCAN_ROWS = 5     # 自动探测表头行最多扫前 5 行
MAX_COLS = 60         # 看板列数上限，防御异常大表


def _merge_map(ws):
    """{单元格坐标: 其所属合并块的 (min_col, max_col, min_row, max_row, 左上值)}"""
    out = {}
    for rng in ws.merged_cells.ranges:
        top_left = ws.cell(row=rng.min_row, column=rng.min_col).value
        info = (rng.min_col, rng.max_col, rng.min_row, rng.max_row,
                str(top_left).strip() if top_left is not None else '')
        for r in range(rng.min_row, rng.max_row + 1):
            for cidx in range(rng.min_col, rng.max_col + 1):
                out[(r, cidx)] = info
    return out


def _cell_text(v):
    if v is None:
        return ''
    return str(v).strip()


def detect_header_row(ws, hint=None):
    """探测表头行：优先用户指定；否则取前 MAX_SCAN_ROWS 行中
    第一个「非空格数 ≥ 2」的行（1-based 返回）。"""
    if hint is not None:
        return int(hint)
    best_r, best_n = 1, -1
    for r in range(1, min(ws.max_row, MAX_SCAN_ROWS) + 1):
        n = sum(1 for cidx in range(1, min(ws.max_column, MAX_COLS) + 1)
                if _cell_text(ws.cell(row=r, column=cidx).value))
        if n > best_n:
            best_r, best_n = r, n
    return best_r


def parse_header(file_obj, header_row=None):
    """解析表头。file_obj = 上传的 xlsx 文件句柄。

    返回 {'header_row': int, 'two_level': bool, 'columns': [...]}。
    两级判定：表头行上一行存在「横向跨 >1 列的合并块」。
    """
    wb = load_workbook(file_obj, read_only=False, data_only=True)
    ws = wb.worksheets[0]
    hr = detect_header_row(ws, header_row)
    if hr < 1 or hr > ws.max_row:
        raise ValueError(f'表头行 {hr} 超出表格范围（共 {ws.max_row} 行）')
    merges = _merge_map(ws)
    max_col = min(ws.max_column, MAX_COLS)

    # 两级表头：上一行（hr-1）有横向合并块，且本行在合并块下方有内容列
    parent_row = hr - 1 if hr > 1 else None
    two_level = False
    if parent_row:
        for (r, _c), (c1, c2, mr1, _mr2, _v) in merges.items():
            if r == parent_row and mr1 == parent_row and c2 > c1:
                two_level = True
                break

    columns = []
    seen = set()
    for cidx in range(1, max_col + 1):
        if (hr, cidx) in seen:
            continue
        title = _cell_text(ws.cell(row=hr, column=cidx).value)
        parent = ''
        col_span = 1
        m = merges.get((hr, cidx))
        if m:
            c1, c2, mr1, mr2, v = m
            for cc in range(c1, c2 + 1):
                seen.add((hr, cc))
            col_span = c2 - c1 + 1
            if parent_row and mr1 == parent_row and mr2 >= hr:
                # 纵向跨两行的合并 = 单级列（父即标题）
                title = title or v
            elif not title:
                continue
        elif not title:
            continue

        # 两级：本列在上一行落在某个横向合并块内 → parent = 合并块左上值
        if two_level and parent_row:
            pm = merges.get((parent_row, cidx))
            if pm and pm[2] == parent_row:
                parent = pm[4]
                if not title:
                    # 本行该格为空但父块覆盖 → 取父值作单列（防子行留空）
                    title = parent
        if not title:
            continue
        columns.append({
            'col_index': cidx,
            'col_letter': get_column_letter(cidx),
            'title': title,
            'parent': parent,
            'col_span': col_span,
        })
    return {'header_row': hr, 'two_level': two_level, 'columns': columns}
