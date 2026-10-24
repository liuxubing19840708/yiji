# -*- coding: utf-8 -*-
"""看板模板视图：字段字典 / 模板 CRUD（仅管理员）/ Excel 表头导入 / 试跑 / 渲染。

权限（用户拍板第 7 条）：模板增删改查 = 仅本公司管理员（require_admin）；
fields 与 render 为登录成员可用（render 是小程序看板数据源）。
"""
import json
import logging

from django.core.exceptions import ValidationError
from django.http import Http404
from rest_framework import parsers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.kanban_template.excel import parse_header
from apps.kanban_template.fields import fields_payload
from apps.kanban_template.formula import FormulaError, validate_expr
from apps.kanban_template.models import KanbanColumn, KanbanTemplate
from apps.kanban_template.services import render_payload, render_with_fallback

logger = logging.getLogger(__name__)

MAX_COLUMNS = 60


def _validate_columns(columns):
    """校验列定义数组，返回清洗后的列表（不落库）。"""
    if not isinstance(columns, list) or not columns:
        raise ValidationError({'columns': '至少需要一列'})
    if len(columns) > MAX_COLUMNS:
        raise ValidationError({'columns': f'列数过多（>{MAX_COLUMNS}）'})
    cleaned = []
    seen_seq = set()
    for idx, c in enumerate(columns):
        if not isinstance(c, dict):
            raise ValidationError({'columns': f'第{idx + 1}列格式不正确'})
        title = str(c.get('title') or '').strip()
        if not title:
            raise ValidationError({'columns': f'第{idx + 1}列缺少标题'})
        bind = c.get('bind_type') or 'field'
        if bind not in ('field', 'formula', 'static'):
            raise ValidationError({'columns': f'第{idx + 1}列绑定类型不合法'})
        item = {'title': title[:64], 'bind_type': bind,
                'field_key': '', 'expr': '', 'static_text': '',
                'extra': c.get('extra') if isinstance(c.get('extra'), dict) else {}}
        seq = c.get('seq')
        seq = idx if seq is None else int(seq)
        if seq < 0 or seq in seen_seq:
            raise ValidationError({'columns': f'第{idx + 1}列 seq 重复或非法'})
        seen_seq.add(seq)
        item['seq'] = seq
        if bind == 'field':
            key = str(c.get('field_key') or '').strip()
            from apps.kanban_template.fields import KANBAN_FIELDS
            if key not in KANBAN_FIELDS:
                raise ValidationError({'columns': f'第{idx + 1}列未知字段：{key}'})
            item['field_key'] = key
        elif bind == 'formula':
            try:
                item['expr'] = validate_expr(c.get('expr'))
            except FormulaError as e:
                raise ValidationError({'columns': f'第{idx + 1}列公式非法：{e}'})
        else:
            item['static_text'] = str(c.get('static_text') or '')[:64]
        cleaned.append(item)
    return cleaned


def _save_columns(template, columns):
    """全量替换模板列（软删旧列 + 重建），版本号 +1。"""
    KanbanColumn.all_objects.filter(template=template).delete()
    rows = [KanbanColumn(template=template, **c) for c in columns]
    KanbanColumn.objects.bulk_create(rows)
    template.version = (template.version or 0) + 1
    template.save(update_fields=['version', 'updated_at'])


def _template_dict(t):
    return {'id': t.id, 'name': t.name, 'dataset': t.dataset,
            'filters': t.filters or {}, 'is_default': t.is_default,
            'version': t.version,
            'columns': [{'id': c.id, 'seq': c.seq, 'title': c.title,
                         'bind_type': c.bind_type, 'field_key': c.field_key,
                         'expr': c.expr, 'static_text': c.static_text,
                         'extra': c.extra or {}} for c in t.columns.all()]}


class KanbanFieldListView(CompanyContextMixin, APIView):
    """GET /api/kanban/fields：可绑定字段字典（配置端拖拽面板数据源）。"""

    def get(self, request):
        return Response({'results': fields_payload()})


class KanbanTemplateListCreateView(CompanyContextMixin, APIView):
    """GET /api/kanban/templates：模板列表（含列定义）；POST 新建。均仅管理员。"""

    def get(self, request):
        self.check_perm('kanban.manage')  # Plan A：看板模板管理点
        ts = (KanbanTemplate.objects.filter(company=self.company)
              .prefetch_related('columns'))
        return Response({'results': [_template_dict(t) for t in ts]})

    def post(self, request):
        self.check_perm('kanban.manage')  # Plan A：看板模板管理点
        name = str(request.data.get('name') or '').strip()
        if not name:
            raise ValidationError({'name': '模板名称不能为空'})
        if len(name) > 64:
            raise ValidationError({'name': '模板名称过长'})
        columns = _validate_columns(request.data.get('columns'))
        dataset = request.data.get('dataset') or KanbanTemplate.Dataset.PLAN_ROWS
        if dataset not in KanbanTemplate.Dataset.values:
            raise ValidationError({'dataset': '行数据源不合法'})
        filters = request.data.get('filters')
        if filters is not None and not isinstance(filters, dict):
            raise ValidationError({'filters': '筛选条件集必须是对象'})
        is_default = bool(request.data.get('is_default'))
        # 同业务键软删行先物理清理（撞唯一约束）
        KanbanTemplate.all_objects.filter(company=self.company, name=name,
                                          is_deleted=True).delete()
        with __import__('django').db.transaction.atomic():
            t = KanbanTemplate.objects.create(
                company=self.company, name=name, dataset=dataset,
                filters=filters or {}, is_default=False)
            _save_columns(t, columns)
            if is_default:
                _set_default(self.company, t)
        return Response(_template_dict(KanbanTemplate.objects.get(pk=t.pk)), status=201)


class KanbanTemplateDetailView(CompanyContextMixin, APIView):
    """GET/PUT/DELETE /api/kanban/templates/{id}：详情 / 全量更新 / 删除。仅管理员。"""

    def _get(self, pk):
        t = KanbanTemplate.objects.filter(company=self.company, pk=pk) \
            .prefetch_related('columns').first()
        if not t:
            raise Http404('模板不存在')
        return t

    def get(self, request, pk):
        self.check_perm('kanban.manage')  # Plan A：看板模板管理点
        return Response(_template_dict(self._get(pk)))

    def put(self, request, pk):
        self.check_perm('kanban.manage')  # Plan A：看板模板管理点
        t = self._get(pk)
        name = str(request.data.get('name') or t.name).strip()
        if not name:
            raise ValidationError({'name': '模板名称不能为空'})
        if name != t.name:
            KanbanTemplate.all_objects.filter(company=self.company, name=name,
                                              is_deleted=True).delete()
            if KanbanTemplate.objects.filter(company=self.company, name=name) \
                    .exclude(pk=t.pk).exists():
                raise ValidationError({'name': '同名模板已存在'})
        columns = _validate_columns(request.data.get('columns'))
        is_default = request.data.get('is_default')
        from django.db import transaction
        with transaction.atomic():
            t.name = name
            t.save(update_fields=['name', 'updated_at'])
            _save_columns(t, columns)
            if is_default:
                _set_default(self.company, t)
        return Response(_template_dict(self._get(pk)))

    def delete(self, request, pk):
        self.check_perm('kanban.manage')  # Plan A：看板模板管理点
        t = self._get(pk)
        if t.is_default:
            other = (KanbanTemplate.objects.filter(company=self.company)
                     .exclude(pk=t.pk).order_by('id').first())
            if other:
                _set_default(self.company, other)
            # 删到只剩 0 套时渲染自动回落内置默认模板，允许删
        t.delete()  # 软删（BaseModel）
        return Response({'id': t.id, 'deleted': True})


def _set_default(company, template):
    """设默认：清掉其它默认再置位。"""
    KanbanTemplate.objects.filter(company=company, is_default=True) \
        .exclude(pk=template.pk).update(is_default=False)
    template.is_default = True
    template.save(update_fields=['is_default', 'updated_at'])


class KanbanTemplateDefaultView(CompanyContextMixin, APIView):
    """PUT /api/kanban/templates/{id}/default：设为默认模板。仅管理员。"""

    def put(self, request, pk):
        self.check_perm('kanban.manage')  # Plan A：看板模板管理点
        t = KanbanTemplate.objects.filter(company=self.company, pk=pk).first()
        if not t:
            raise Http404('模板不存在')
        _set_default(self.company, t)
        return Response({'id': t.id, 'is_default': True})


class KanbanImportHeaderView(CompanyContextMixin, APIView):
    """POST /api/kanban/import-header：上传客户 Excel，解析表头（只读不落库）。

    multipart：file=xlsx；header_row=表头行号（1-based，缺省自动探测）。
    支持两级/合并表头：返回 columns[{col_index, col_letter, title, parent, col_span}]。
    """
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def post(self, request):
        self.check_perm('kanban.manage')  # Plan A：看板模板管理点
        f = request.FILES.get('file')
        if not f:
            raise ValidationError({'file': '请上传 xlsx 文件'})
        if f.size > 5 * 1024 * 1024:
            raise ValidationError({'file': '文件超过 5MB'})
        if not f.name.lower().endswith(('.xlsx', '.xlsm')):
            raise ValidationError({'file': '仅支持 .xlsx/.xlsm'})
        header_row = request.data.get('header_row')
        if header_row in (None, ''):
            header_row = None
        else:
            try:
                header_row = int(header_row)
            except (TypeError, ValueError):
                raise ValidationError({'header_row': '表头行必须是数字'})
        try:
            result = parse_header(f, header_row=header_row)
        except ValueError as e:
            raise ValidationError({'file': str(e)})
        except Exception:
            logger.exception('kanban import-header parse failed')
            raise ValidationError({'file': 'Excel 解析失败，请确认是有效的 xlsx 文件'})
        return Response(result)


class KanbanPreviewView(CompanyContextMixin, APIView):
    """POST /api/kanban/preview：试跑（配置端保存前看效果）。

    body: {template_id} 或内联 {columns: [...]}，可选 range/start_date/end_date。
    返回与渲染接口同构数据。
    """

    def post(self, request):
        self.check_perm('kanban.manage')  # Plan A：看板模板管理点
        tid = request.data.get('template_id')
        range_key = request.data.get('range') or 'month'
        start_date = request.data.get('start_date') or ''
        end_date = request.data.get('end_date') or ''
        if tid:
            template = (KanbanTemplate.objects.filter(company=self.company, pk=tid)
                        .prefetch_related('columns').first())
            if not template:
                raise Http404('模板不存在')
            payload = render_with_fallback(self.company, range_key, start_date,
                                           end_date, template=template)
        else:
            columns = _validate_columns(request.data.get('columns'))
            payload = render_payload(
                self.company,
                {'id': None, 'name': '预览', 'version': 0, 'is_default': False},
                [dict(c, id=None) for c in columns],
                range_key, start_date, end_date)
        return Response(payload)


class KanbanRenderView(CompanyContextMixin, APIView):
    """GET /api/kanban/render?range=&start_date=&end_date=&template_id=

    小程序看板数据源：template_id 缺省用公司默认模板；无模板或渲染失败
    一律回落内置默认模板（绝不允许白屏）。
    """

    def get(self, request):
        tid = request.query_params.get('template_id')
        template = None
        if tid:
            template = (KanbanTemplate.objects.filter(company=self.company, pk=tid)
                        .prefetch_related('columns').first())
            if not template:
                raise Http404('模板不存在')
        payload = render_with_fallback(
            self.company,
            request.query_params.get('range') or 'month',
            request.query_params.get('start_date') or '',
            request.query_params.get('end_date') or '',
            template=template)
        return Response(payload)
