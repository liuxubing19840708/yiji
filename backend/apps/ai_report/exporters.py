# -*- coding: utf-8 -*-
"""动态报表导出；与预览共用 QueryResult，避免两套统计口径。"""
from apps.core.export import xlsx_response

from .query_engine import export_rows


def result_xlsx(result):
    response = xlsx_response(
        [column['label'] for column in result.columns],
        export_rows(result),
        filename=result.title,
    )
    if result.comparison:
        _append_comparison_sheet(response, result)
    return response


def _append_comparison_sheet(response, result):
    from decimal import Decimal
    from io import BytesIO

    from openpyxl import load_workbook
    from openpyxl.styles import Font

    comparison = result.comparison
    wb = load_workbook(BytesIO(response.content))
    ws = wb.create_sheet('期间对比')
    ws.append(['模式', comparison['mode'], '本期', '~'.join(comparison['current_range']),
               '对比期', '~'.join(comparison['previous_range'])])
    ws.append([comparison['message']])
    if comparison['available']:
        metric_keys = {item for row in comparison['rows'] for item in row['metrics']}
        dimension_count = len(next(iter(comparison['rows']), {'dimensions': []})['dimensions'])
        dimension_headers = [column['label'] for column in result.columns
                             if column['key'] not in metric_keys][:dimension_count]
        headers = list(dimension_headers)
        ordered_metrics = [column for column in result.columns if column['key'] in metric_keys]
        for column in ordered_metrics:
            headers.extend([f"{column['label']}-本期", f"{column['label']}-对比期",
                            f"{column['label']}-差额", f"{column['label']}-变化率"])
        ws.append(headers)
        for cell in ws[3]:
            cell.font = Font(bold=True)
        for row in comparison['rows']:
            values = list(row['dimensions'])
            for column in ordered_metrics:
                for key in ('current', 'previous', 'delta', 'change_rate'):
                    value = row['metrics'][column['key']][key]
                    if value is not None:
                        try:
                            value = Decimal(value)
                        except Exception:
                            if isinstance(value, str) and value.startswith(('=', '+', '-', '@')):
                                value = "'" + value
                    values.append(value)
            ws.append(values)
    buf = BytesIO()
    wb.save(buf)
    response.content = buf.getvalue()


def result_xlsx_bytes(result):
    """异步 worker 与同步接口复用同一工作簿生成逻辑。"""
    return result_xlsx(result).content
