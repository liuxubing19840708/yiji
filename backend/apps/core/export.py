"""xlsx 导出工具（竞品对齐 GAP-7，2026-11-04）。

openpyxl 生成单表工作簿，直接以 HttpResponse 下发；文件名 RFC 5987
UTF-8 编码（中文文件名）。小程序端 wx.downloadFile 拉取后 openDocument。
"""
from urllib.parse import quote

from django.http import HttpResponse

XLSX_CONTENT_TYPE = (
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def xlsx_response(headers, rows, filename='导出'):
    """headers: [str]；rows: 二维数组（值自动 str 化由 openpyxl 处理）。"""
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.append(headers)
    for r in rows:
        ws.append(list(r))
    # 表头加粗 + 列宽自适应（简单按表头/首行长度，够用即可）
    from openpyxl.styles import Font
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for i, h in enumerate(headers, start=1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:50]] or [10])
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(
            max(width * 2 + 2, 10), 40)
    buf = BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type=XLSX_CONTENT_TYPE)
    resp['Content-Disposition'] = (
        f"attachment; filename*=UTF-8''{quote(filename)}.xlsx")
    return resp
