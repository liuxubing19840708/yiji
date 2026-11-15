"""技术管理视图：物料主档 / 版式BOM（P1 tech 域，契约 §四 端点）。

权限：全部端点走 2 段式权限点 `tech.material` / `tech.bom`（import 并入对应点，
即 POST /tech/materials/import 挂 tech.material、/tech/boms/import 挂 tech.bom）；
两点半 legacy=False —— 旧 coarse 岗位（production 等）零回归、默认零授权。

口径：
  - 物料编码同公司唯一（软删防撞：同码软删行复活复用，PROTECT 不物理清）；
  - 双单位换算防线：录了副单位（unit_alt）即声明公斤↔米换算，克重/幅宽缺失时
    接口层 400 拦截提示补录（清单 A 组落点）；
  - BOM 单 active：DB 部分唯一约束 + activate 事务内降级旧版双保险；
  - 导入 all-or-nothing：任一行校验失败整批不入库，errors 带行号与中文原因
    （不做部分成功静默）。
"""
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.http import Http404
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.ops.models import AuditLog
from apps.ops.services import audit
from apps.tech.models import Bom, BomItem, Material
from apps.tech.serializers import BomSerializer, MaterialSerializer

# Excel/JSON 导入标准模板列（E3：标准模板+校验）→ 字段名
MATERIAL_IMPORT_COLUMNS = {
    '编码': 'code', '品名': 'name', '分类': 'category', '主单位': 'unit',
    '副单位': 'unit_alt', '克重': 'weight_gsm', '幅宽': 'width_cm',
    '缩率': 'shrink_rate', '旧编码': 'legacy_code',
}
BOM_IMPORT_COLUMNS = {
    '款号': 'style_no', '版本': 'version', '物料编码': 'material_code',
    '类别': 'kind', '颜色': 'color', '尺码': 'size', '单耗': 'unit_usage',
    '损耗率': 'loss_rate', '部位': 'position', '备注': 'remark',
}
CATEGORY_BY_NAME = {label: val for val, label in Material.Category.choices}
KIND_BY_NAME = {'面料': 'main', '里料': 'lining', '辅料': 'accessory'}


def _purge_deleted(model, **filters):
    """软删行物理清理（仅用于无 PROTECT 引用风险的模型，沿用订单域惯例）。"""
    model.all_objects.filter(is_deleted=True, **filters).delete()


def _to_decimal(raw):
    """宽容取数：''/None → None；非法数字抛 InvalidOperation。"""
    if raw is None or raw == '':
        return None
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise InvalidOperation(str(raw))


def _clean_material_payload(company, data, instance=None, allow_existing=False):
    """物料建/改公共校验：必填、分类、数值、编码查重、换算参数拦截。

    allow_existing=True（导入场景）：同码活跃行不算错误——导入语义为"存在即更新"，
    由视图层走 updated 计数；批内重复行仍由导入视图以 seen_codes 拦截。
    """
    errors = {}
    code = str(data.get('code') or '').strip()
    name = str(data.get('name') or '').strip()
    unit = str(data.get('unit') or '').strip()
    if not code:
        errors['code'] = '请填写物料编码'
    if not name:
        errors['name'] = '请填写品名'
    if not unit:
        errors['unit'] = '请填写主单位'

    category = data.get('category') or Material.Category.FABRIC
    if isinstance(category, str):
        category = CATEGORY_BY_NAME.get(category.strip(), category.strip())
    if category not in Material.Category.values:
        errors['category'] = '分类须为 面料/里料/辅料/包装'

    unit_alt = str(data.get('unit_alt') or '').strip()
    kwargs = {}
    for field in ('weight_gsm', 'width_cm', 'shrink_rate', 'price_reference'):
        raw = data.get(field)
        if raw in (None, ''):
            kwargs[field] = None
            continue
        try:
            val = _to_decimal(raw)
        except InvalidOperation:
            errors[field] = '数值不合法'
            continue
        if val < 0:
            errors[field] = '不能为负数'
        kwargs[field] = val
    if unit_alt and (kwargs.get('weight_gsm') is None or kwargs.get('width_cm') is None):
        # 换算参数拦截：公斤↔米=克重×幅宽折算，缺任一参数则双单位不成立
        errors['weight_gsm'] = '已填副单位，须补录克重与幅宽（双单位换算参数）'

    status = data.get('status') or Material.Status.ACTIVE
    if status not in Material.Status.values:
        errors['status'] = '状态须为 active/disabled'

    if code and not errors.get('code') and not allow_existing:
        dup = Material.objects.filter(company=company, code=code)
        if instance is not None:
            dup = dup.exclude(pk=instance.pk)
        if dup.exists():
            errors['code'] = '物料编码已存在'

    if errors:
        raise ValidationError(errors)

    attrs = data.get('attrs')
    if attrs is not None and not isinstance(attrs, dict):
        raise ValidationError({'attrs': '扩展属性须为对象'})

    return {
        'code': code, 'name': name, 'category': category, 'unit': unit,
        'unit_alt': unit_alt, 'status': status,
        'legacy_code': str(data.get('legacy_code') or '').strip(),
        'attrs': attrs if attrs is not None else {},
        **kwargs,
    }


def _revive_deleted_material(company, code):
    """同码软删行复活（PROTECT 模型不物理清，防止 BomItem 历史引用悬空）。"""
    dead = Material.all_objects.filter(company=company, code=code, is_deleted=True).first()
    return dead


def _get_material(company, pk) -> Material:
    obj = Material.objects.filter(company=company, pk=pk).first()
    if obj is None:
        raise Http404('物料不存在')
    return obj


def _get_bom(company, pk) -> Bom:
    obj = Bom.objects.filter(company=company, pk=pk).first()
    if obj is None:
        raise Http404('BOM不存在')
    return obj


def _validated_bom_items(company, bom, raw):
    """BOM 明细校验：物料归属本公司、单耗>0、损耗率≥0、同码行唯一。

    loss_rate 缺省按物料品类默认值补（B5：梭织6/针织8(attrs)/辅料0.4，
    attrs.loss_rate 显式覆盖优先——默认可覆盖）。
    """
    if raw is None:
        return None  # PUT 不传 items 保持原明细
    if not isinstance(raw, list):
        raise ValidationError({'items': '明细须为列表'})
    rows, seen = [], set()
    for idx, it in enumerate(raw):
        label = f'第{idx + 1}项'
        if not isinstance(it, dict):
            raise ValidationError({'items': f'{label}格式不正确'})
        try:
            material_id = int(it.get('material'))
        except (TypeError, ValueError):
            raise ValidationError({'items': f'{label}缺少物料'})
        material = Material.objects.filter(company=company, pk=material_id).first()
        if material is None:
            raise ValidationError({'items': f'{label}物料不存在'})
        try:
            usage = _to_decimal(it.get('unit_usage'))
        except InvalidOperation:
            raise ValidationError({'items': f'{label}单耗不合法'})
        if usage is None or usage <= 0:
            raise ValidationError({'items': f'{label}单耗必须大于0'})
        loss_raw = it.get('loss_rate')
        if loss_raw in (None, ''):
            loss = material.default_loss_rate()
        else:
            try:
                loss = _to_decimal(loss_raw)
            except InvalidOperation:
                raise ValidationError({'items': f'{label}损耗率不合法'})
            if loss < 0:
                raise ValidationError({'items': f'{label}损耗率不能为负数'})
        kind = it.get('kind') or 'main'
        if kind not in ('main', 'lining', 'accessory'):
            kind = 'main'
        color = str(it.get('color') or '').strip()
        size = str(it.get('size') or '').strip()
        key = (material_id, color, size)
        if key in seen:
            raise ValidationError({'items': f'{label}同物料同色码重复'})
        seen.add(key)
        rows.append(BomItem(
            bom=bom, material=material, kind=kind, unit=material.unit,
            color=color, size=size, unit_usage=usage, loss_rate=loss,
            position=str(it.get('position') or '').strip(),
            remark=str(it.get('remark') or '').strip()))
    return rows


class MaterialListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /tech/materials?keyword=&category= + POST /tech/materials。"""
    serializer_class = MaterialSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = Material.objects.filter(company=self.company)
        category = self.request.query_params.get('category')
        if category in Material.Category.values:
            qs = qs.filter(category=category)
        keyword = self.request.query_params.get('keyword')
        if keyword:
            from django.db.models import Q
            qs = qs.filter(Q(code__icontains=keyword) | Q(name__icontains=keyword)
                           | Q(legacy_code__icontains=keyword))
        return qs

    def list(self, request, *args, **kwargs):
        self.check_perm('tech.material')
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def perform_create(self, serializer):
        self.check_perm('tech.material')
        payload = _clean_material_payload(self.company, self.request.data)
        # 同码软删行复活复用（PROTECT 模型不物理清）；serializer.instance 指向
        # 复活行再 save，保证响应体带 id（CreateModelMixin 回包取 serializer.data）
        dead = _revive_deleted_material(self.company, payload['code'])
        if dead is not None:
            for k, v in payload.items():
                setattr(dead, k, v)
            dead.is_deleted = False
            serializer.instance = dead
            serializer.save(**payload)
            instance = dead
        else:
            try:
                instance = serializer.save(company=self.company, **payload)
            except IntegrityError:
                raise ValidationError({'code': '物料编码已存在'})
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'新建物料：【{instance.code}】{instance.name}')


class MaterialDetailView(CompanyContextMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET / PUT / DELETE /tech/materials/<id>（DELETE=软删，编码可复用）。"""
    serializer_class = MaterialSerializer

    def get_object(self):
        return _get_material(self.company, self.kwargs['pk'])

    def retrieve(self, request, *args, **kwargs):
        self.check_perm('tech.material')
        return Response(self.get_serializer(self.get_object()).data)

    def perform_update(self, serializer):
        self.check_perm('tech.material')
        instance = self.get_object()
        payload = _clean_material_payload(self.company, self.request.data,
                                          instance=instance)
        dead = _revive_deleted_material(self.company, payload['code'])
        if dead is not None and dead.pk != instance.pk:
                raise ValidationError({'code': '物料编码已存在'})
        serializer.save(**payload)
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'修改物料：【{instance.code}】')

    def perform_destroy(self, instance):
        self.check_perm('tech.material')
        instance.delete()  # BaseModel.delete = 软删
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'删除物料：【{instance.code}】')


class _ImportMixin:
    """导入公共件：xlsx/JSON 行归一 + all-or-nothing 语义。"""

    def _collect_rows(self, columns):
        """请求归一：multipart file(.xlsx) 优先，否则 JSON rows（键=模板中文列名）。

        返回 [(excel_row_no, {field: value})]；表头行占第 1 行，数据行从 2 起。
        """
        upload = self.request.FILES.get('file')
        if upload is not None:
            if not upload.name.lower().endswith('.xlsx'):
                raise ValidationError({'file': '仅支持 .xlsx 文件'})
            from openpyxl import load_workbook
            try:
                wb = load_workbook(upload, read_only=True, data_only=True)
            except Exception:
                raise ValidationError({'file': '文件解析失败，请使用标准模板'})
            ws = wb.active
            matrix = [list(row) for row in ws.iter_rows(values_only=True)]
            wb.close()
            if not matrix:
                raise ValidationError({'file': '文件内容为空'})
            headers = [str(h).strip() if h is not None else '' for h in matrix[0]]
            unknown = [h for h in headers if h and h not in columns]
            if unknown:
                raise ValidationError({'file': f'表头列不识别：{unknown}，请使用标准模板'})
            rows = []
            for no, raw in enumerate(matrix[1:], start=2):
                if all(v is None or str(v).strip() == '' for v in raw):
                    continue  # 空行跳过（不计错误）
                row = {}
                for h, v in zip(headers, raw):
                    if h in columns:
                        row[columns[h]] = '' if v is None else (
                            str(v).strip() if not isinstance(v, (int, float, Decimal))
                            else v)
                rows.append((no, row))
            return rows
        raw_rows = self.request.data.get('rows')
        if isinstance(raw_rows, list):
            rows = []
            for no, it in enumerate(raw_rows, start=2):
                if not isinstance(it, dict):
                    raise ValidationError({'rows': f'第{no - 1}行格式不正确'})
                row = {}
                for cn, field in columns.items():
                    if cn in it:
                        row[field] = it[cn]
                rows.append((no, row))
            return rows
        raise ValidationError({'file': '请上传 .xlsx 文件（字段 file）或传 rows 数组'})


class MaterialImportView(CompanyContextMixin, _ImportMixin, APIView):
    """POST /tech/materials/import：物料批量导入（all-or-nothing，errors 带行号）。

    模板列：编码/品名/分类/主单位/副单位/克重/幅宽/缩率/旧编码。
    返回 {created, updated, errors:[{row, code, reason}]}；任一行有错整批不入库。
    """

    def post(self, request):
        self.check_perm('tech.material')
        rows = self._collect_rows(MATERIAL_IMPORT_COLUMNS)
        errors, prepared = [], []
        seen_codes = set()
        for no, row in rows:
            row_code = str(row.get('code') or '').strip()
            dup_in_batch = bool(row_code) and row_code in seen_codes
            if row_code:
                seen_codes.add(row_code)  # 失败行也计入：同码后续行均报批内重复
            if dup_in_batch:
                errors.append({'row': no, 'code': row_code,
                               'reason': '物料编码在批内重复'})
                continue
            try:
                payload = _clean_material_payload(self.company, row,
                                                  allow_existing=True)
            except ValidationError as e:
                parts = []
                if isinstance(e.detail, dict):
                    for k, v in e.detail.items():
                        msg = v[0] if isinstance(v, (list, tuple)) and v else v
                        parts.append(f'{k}:{msg}')
                else:
                    parts.append(str(e.detail))
                errors.append({'row': no, 'code': row_code,
                               'reason': '；'.join(parts)})
                continue
            except Exception:
                errors.append({'row': no, 'code': row_code,
                               'reason': '格式不正确，请检查该行'})
                continue
            if row_code:
                seen_codes.add(row_code)
            prepared.append((no, payload))
        if not rows:
            return Response({'created': 0, 'updated': 0,
                             'errors': [{'row': 0, 'code': '', 'reason': '无数据行'}]})
        if errors:
            # 不做部分成功静默：任一行失败 → 整批不入库，明确返回错误行
            return Response({'created': 0, 'updated': 0, 'errors': errors})
        created = updated = 0
        with transaction.atomic():
            for _no, payload in prepared:
                dead = _revive_deleted_material(self.company, payload['code'])
                if dead is not None:
                    for k, v in payload.items():
                        setattr(dead, k, v)
                    dead.is_deleted = False
                    dead.save()
                    updated += 1
                    continue
                exist = Material.objects.filter(
                    company=self.company, code=payload['code']).first()
                if exist is not None:
                    for k, v in payload.items():
                        setattr(exist, k, v)
                    exist.save()
                    updated += 1
                else:
                    Material.objects.create(company=self.company, **payload)
                    created += 1
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              message=f'物料批量导入：新建{created} 更新{updated}')
        return Response({'created': created, 'updated': updated, 'errors': []})


class BomListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """GET /tech/boms?style_id=&version= + POST /tech/boms（可带 items）。"""
    serializer_class = BomSerializer
    pagination_class = DefaultPagination

    def get_queryset(self):
        qs = Bom.objects.filter(company=self.company).select_related('style')
        style_id = self.request.query_params.get('style_id')
        if style_id:
            qs = qs.filter(style_id=style_id)
        version = self.request.query_params.get('version')
        if version and version.isdigit():
            qs = qs.filter(version=int(version))
        status = self.request.query_params.get('status')
        if status in Bom.Status.values:
            qs = qs.filter(status=status)
        return qs

    def list(self, request, *args, **kwargs):
        self.check_perm('tech.bom')
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def perform_create(self, serializer):
        self.check_perm('tech.bom')
        from apps.catalog.models import Style
        raw_style = self.request.data.get('style')
        try:
            style_id = int(raw_style)
        except (TypeError, ValueError):
            raise ValidationError({'style': '款式不存在'})
        style = Style.objects.filter(company=self.company, pk=style_id).first()
        if style is None:
            raise ValidationError({'style': '款式不存在'})
        version_raw = self.request.data.get('version')
        if version_raw in (None, ''):
            max_v = Bom.all_objects.filter(company=self.company, style=style) \
                .order_by().values_list('version', flat=True)
            version = (max(max_v) if max_v else 0) + 1
        else:
            try:
                version = int(version_raw)
            except (TypeError, ValueError):
                raise ValidationError({'version': '版本须为正整数'})
            if version <= 0:
                raise ValidationError({'version': '版本须为正整数'})
            if Bom.objects.filter(company=self.company, style=style,
                                  version=version).exists():
                raise ValidationError({'version': '该款式此版本已存在'})
        remark = str(self.request.data.get('remark') or '').strip()
        try:
            with transaction.atomic():
                bom = serializer.save(company=self.company, style=style,
                                      version=version, status=Bom.Status.DRAFT,
                                      remark=remark)
                rows = _validated_bom_items(self.company, bom,
                                            self.request.data.get('items'))
                if rows:
                    BomItem.objects.bulk_create(rows)
        except IntegrityError:
            raise ValidationError({'version': '该款式此版本已存在'})
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'新建BOM：【{style.style_no}】v{version}')


class BomDetailView(CompanyContextMixin, generics.RetrieveUpdateDestroyAPIView):
    """GET / PUT / DELETE /tech/boms/<id>。PUT items 全量替换；DELETE=软删。"""
    serializer_class = BomSerializer

    def get_object(self):
        return _get_bom(self.company, self.kwargs['pk'])

    def retrieve(self, request, *args, **kwargs):
        self.check_perm('tech.bom')
        return Response(self.get_serializer(self.get_object()).data)

    def perform_update(self, serializer):
        self.check_perm('tech.bom')
        instance = self.get_object()
        remark = self.request.data.get('remark')
        extra = {}
        if remark is not None:
            extra['remark'] = str(remark).strip()
        with transaction.atomic():
            updated = serializer.save(**extra)
            if self.request.data.get('items') is not None:
                rows = _validated_bom_items(self.company, updated,
                                            self.request.data.get('items'))
                BomItem.all_objects.filter(bom=updated).delete()
                if rows:
                    BomItem.objects.bulk_create(rows)
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'修改BOM：【{updated.style.style_no}】v{updated.version}')

    def perform_destroy(self, instance):
        self.check_perm('tech.bom')
        instance.delete()  # 软删（明细行随行保留，PROTECT 无碍）
        audit(self.company, AuditLog.Type.OTHER, operator=self.request.user,
              message=f'删除BOM：【{instance.style.style_no}】v{instance.version}')


class BomActivateView(CompanyContextMixin, APIView):
    """POST /tech/boms/<id>/activate：草稿→生效，旧 active 事务内自动降级。"""

    def post(self, request, pk):
        self.check_perm('tech.bom')
        bom = _get_bom(self.company, pk)
        if bom.status == Bom.Status.ACTIVE:
            raise ValidationError({'status': '该版本已生效'})
        try:
            with transaction.atomic():
                # 旧 active 自动降级（双保险之一；DB 部分唯一约束兜底并发）
                Bom.objects.filter(company=self.company, style_id=bom.style_id,
                                   status=Bom.Status.ACTIVE) \
                    .exclude(pk=bom.pk).update(status=Bom.Status.DEACTIVATED)
                bom.status = Bom.Status.ACTIVE
                bom.save(update_fields=['status', 'updated_at'])
        except IntegrityError:
            raise ValidationError({'status': '激活冲突，请刷新重试'})
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              message=f'激活BOM：【{bom.style.style_no}】v{bom.version}')
        return Response(BomSerializer(bom).data)


class BomForkView(CompanyContextMixin, APIView):
    """POST /tech/boms/<id>/fork：改版另存新版本（B4），旧版自动停用。

    新版本号 = 该款式历史最大版本 + 1（all_objects 扫描防软删段复用混乱）；
    明细整表复制为新草稿；原版 active 则降级 deactivated 留痕。
    """

    def post(self, request, pk):
        self.check_perm('tech.bom')
        source = _get_bom(self.company, pk)
        max_v = Bom.all_objects.filter(company=self.company, style_id=source.style_id) \
            .order_by().values_list('version', flat=True)
        new_version = (max(max_v) if max_v else 0) + 1
        with transaction.atomic():
            new_bom = Bom.objects.create(
                company=self.company, style=source.style, version=new_version,
                status=Bom.Status.DRAFT,
                remark=source.remark or '')
            items = []
            for it in source.items.all():
                items.append(BomItem(
                    bom=new_bom, material=it.material, kind=it.kind, unit=it.unit,
                    color=it.color, size=it.size, unit_usage=it.unit_usage,
                    loss_rate=it.loss_rate, position=it.position, remark=it.remark))
            if items:
                BomItem.objects.bulk_create(items)
            if source.status == Bom.Status.ACTIVE:
                source.status = Bom.Status.DEACTIVATED
                source.save(update_fields=['status', 'updated_at'])
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              message=f'改版BOM：【{source.style.style_no}】v{source.version}→v{new_version}')
        return Response(BomSerializer(new_bom).data)


class BomReferencesView(CompanyContextMixin, APIView):
    """GET /tech/boms/<id>/references：按版本反查在途单据引用（P1 预留）。

    P1 不接在途单据，恒返 {count: 0}；P2 采购/领料接入后在此聚合
    IssueItem.bom_item / PurchaseItem 引用计数（清单 B4 落点）。
    """

    def get(self, request, pk):
        self.check_perm('tech.bom')
        bom = _get_bom(self.company, pk)
        return Response({'count': 0, 'bom_id': bom.id,
                               'note': 'P1 预留：在途单据反查于 P2 接入'})


class BomImportView(CompanyContextMixin, _ImportMixin, APIView):
    """POST /tech/boms/import：BOM 批量导入（all-or-nothing，errors 带行号）。

    模板列：款号/版本/物料编码/类别/颜色/尺码/单耗/损耗率/部位/备注。
    同（款号,版本）多行归并为一个草稿 BOM；款号/物料须本公司已存在。
    """

    def post(self, request):
        self.check_perm('tech.bom')
        from apps.catalog.models import Style
        rows = self._collect_rows(BOM_IMPORT_COLUMNS)
        if not rows:
            return Response({'created': 0, 'updated': 0,
                             'errors': [{'row': 0, 'reason': '无数据行'}]})
        errors = []
        groups = {}   # (style_no, version) -> {'bom': None, 'items': [(no, payload)]}
        style_cache, material_cache = {}, {}
        for no, row in rows:
            style_no = str(row.get('style_no') or '').strip()
            if not style_no:
                errors.append({'row': no, 'reason': '缺少款号'})
                continue
            if style_no not in style_cache:
                style_cache[style_no] = Style.objects.filter(
                    company=self.company, style_no=style_no).first()
            style = style_cache[style_no]
            if style is None:
                errors.append({'row': no, 'reason': f'款号 {style_no} 不存在'})
                continue
            v_raw = row.get('version')
            if v_raw in (None, ''):
                version = 1
            else:
                try:
                    version = int(v_raw)
                except (TypeError, ValueError):
                    errors.append({'row': no, 'reason': '版本须为正整数'})
                    continue
                if version <= 0:
                    errors.append({'row': no, 'reason': '版本须为正整数'})
                    continue
            code = str(row.get('material_code') or '').strip()
            if not code:
                errors.append({'row': no, 'reason': '缺少物料编码'})
                continue
            if code not in material_cache:
                material_cache[code] = Material.objects.filter(
                    company=self.company, code=code).first()
            material = material_cache[code]
            if material is None:
                errors.append({'row': no, 'reason': f'物料 {code} 不存在'})
                continue
            try:
                usage = _to_decimal(row.get('unit_usage'))
            except InvalidOperation:
                errors.append({'row': no, 'reason': '单耗不合法'})
                continue
            if usage is None or usage <= 0:
                errors.append({'row': no, 'reason': '单耗必须大于0'})
                continue
            loss_raw = row.get('loss_rate')
            if loss_raw in (None, ''):
                loss = material.default_loss_rate()
            else:
                try:
                    loss = _to_decimal(loss_raw)
                except InvalidOperation:
                    errors.append({'row': no, 'reason': '损耗率不合法'})
                    continue
                if loss < 0:
                    errors.append({'row': no, 'reason': '损耗率不能为负数'})
                    continue
            kind = KIND_BY_NAME.get(str(row.get('kind') or '').strip(), 'main')
            key = (style_no, version)
            groups.setdefault(key, {'style': style, 'items': []})
            groups[key]['items'].append(BomItem(
                material=material, kind=kind, unit=material.unit,
                color=str(row.get('color') or '').strip(),
                size=str(row.get('size') or '').strip(),
                unit_usage=usage, loss_rate=loss,
                position=str(row.get('position') or '').strip(),
                remark=str(row.get('remark') or '').strip()))
        if errors:
            return Response({'created': 0, 'updated': 0, 'errors': errors})
        # 二段校验（全部在写库前完成，保证 all-or-nothing）：
        #   1) 同组内 同物料同色码 重复行；2) (款号,版本) 已有活跃 BOM
        for (style_no, version), grp in groups.items():
            seen = set()
            for it in grp['items']:
                k = (it.material_id, it.color, it.size)
                if k in seen:
                    errors.append({'row': 0, 'reason':
                                   f'{style_no} v{version} 明细重复：'
                                   f'{it.material.code}/{it.color}/{it.size}'})
                seen.add(k)
            if not errors and Bom.objects.filter(
                    company=self.company, style=grp['style'],
                    version=version).exists():
                errors.append({'row': 0, 'reason':
                               f'{style_no} v{version} 已存在，不可重复导入'})
        if errors:
            return Response({'created': 0, 'updated': 0, 'errors': errors})
        created = updated = 0
        with transaction.atomic():
            for (style_no, version), grp in groups.items():
                items = grp['items']
                dead = Bom.all_objects.filter(company=self.company,
                                              style=grp['style'],
                                              version=version, is_deleted=True).first()
                if dead is not None:
                    # 软删行复活并整表替换明细
                    dead.is_deleted = False
                    dead.status = Bom.Status.DRAFT
                    dead.save(update_fields=['is_deleted', 'status', 'updated_at'])
                    BomItem.all_objects.filter(bom=dead).delete()
                    for it in items:
                        it.bom = dead
                        it.pk = None
                    BomItem.objects.bulk_create(items)
                    updated += 1
                    continue
                bom = Bom.objects.create(company=self.company, style=grp['style'],
                                         version=version, status=Bom.Status.DRAFT)
                for it in items:
                    it.bom = bom
                BomItem.objects.bulk_create(items)
                created += 1
        audit(self.company, AuditLog.Type.OTHER, operator=request.user,
              message=f'BOM批量导入：新建{created} 更新{updated}')
        return Response({'created': created, 'updated': updated, 'errors': []})
