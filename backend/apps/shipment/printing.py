"""出货单 / 装箱单真实 PDF 渲染。

ReportLab 的 STSong-Light CID 字体随 PDF 阅读器提供中文映射，不依赖服务器
安装系统中文字体。每次请求都根据当前数据库明细重新生成，不使用静态样板。
"""
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def render_shipment_pdf(shipment, template: str) -> bytes:
    """返回包含当前出货数据的完整 PDF 字节串。"""
    if template not in ('shipment', 'packing'):
        raise ValueError('模板不合法')
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    output = BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f'{shipment.shipment_no}-{"装箱单" if template == "packing" else "出货单"}',
        author=shipment.company.name,
    )
    base = ParagraphStyle(
        'ChineseBody', fontName='STSong-Light', fontSize=9, leading=14,
        wordWrap='CJK', textColor=colors.HexColor('#222222'),
    )
    title = ParagraphStyle(
        'ChineseTitle', parent=base, fontSize=20, leading=26, alignment=TA_CENTER,
        spaceAfter=8 * mm,
    )

    def p(value):
        # Paragraph 把用户输入当标记语言；转义后中文与特殊字符均可安全输出。
        from xml.sax.saxutils import escape
        return Paragraph(escape(str(value if value not in (None, '') else '—')), base)

    story = [Paragraph('装箱单' if template == 'packing' else '出货单', title)]
    metadata = [
        [p('出货单号'), p(shipment.shipment_no), p('客户'), p(shipment.customer.name if shipment.customer else '')],
        [p('订单号'), p(shipment.order.order_no if shipment.order else ''), p('状态'), p(shipment.get_status_display())],
        [p('交货日期'), p(shipment.delivery_date), p('发货日期'), p(shipment.ship_date)],
        [p('物流单号'), p(shipment.logistics_no), p('合计'), p(f'{shipment.total_qty} 件 / {shipment.boxes} 箱')],
    ]
    table = Table(metadata, colWidths=[25 * mm, 58 * mm, 23 * mm, 72 * mm])
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'STSong-Light'),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#B8BDC5')),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F2F4F7')),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#F2F4F7')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.extend([table, Spacer(1, 7 * mm)])

    if template == 'packing':
        rows = [[p('箱号'), p('颜色'), p('尺码'), p('数量'), p('毛重(kg)'), p('净重(kg)'), p('箱规')]]
        rows.extend([
            [p(box.box_no), p(box.color), p(box.size), p(box.qty),
             p(box.gross_wt), p(box.net_wt), p(box.box_spec)]
            for box in shipment.packing_boxes.order_by('box_no', 'id')
        ])
        widths = [26 * mm, 24 * mm, 19 * mm, 17 * mm, 23 * mm, 23 * mm, 46 * mm]
    else:
        rows = [[p('序号'), p('颜色'), p('尺码'), p('数量(件)')]]
        rows.extend([
            [p(index), p(item.color), p(item.size), p(item.qty)]
            for index, item in enumerate(shipment.items.order_by('id'), 1)
        ])
        widths = [22 * mm, 58 * mm, 48 * mm, 50 * mm]
    detail = Table(rows, colWidths=widths, repeatRows=1)
    detail.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'STSong-Light'),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#B8BDC5')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E9EDF2')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story.append(detail)
    if len(rows) == 1:
        story.extend([Spacer(1, 5 * mm), p('暂无明细')])
    story.extend([
        Spacer(1, 8 * mm), p(f'备注：{shipment.remark or "无"}'),
        Spacer(1, 4 * mm),
        p(f'打印时间：{timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")}'),
        Spacer(1, 12 * mm), p('制单：________________    客户签收：________________'),
    ])
    document.build(story)
    return output.getvalue()
