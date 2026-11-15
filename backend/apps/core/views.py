"""通用上传：POST uploads（multipart，字段 file）→ 存入 MEDIA，返回可访问 URL。

款式图（catalog Style.image 存 URL 串）与头像（accounts User.avatar）共用此入口；
前端封装见 utils/upload.ts（wx.uploadFile + Bearer 头）。
客户端日志：POST /client-logs 批量上报（未登录可报），GET 按厂→用户隔离读取。
"""
import uuid

from django.conf import settings
from django.core.files.storage import default_storage
from django.utils.dateparse import parse_date
from rest_framework.exceptions import NotAuthenticated, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .mixins import CompanyContextMixin
from .models import ClientLog
from .pagination import DefaultPagination
from .serializers import ClientLogSerializer

ALLOWED_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
MAX_SIZE = 5 * 1024 * 1024

# 工艺视频上传（2026-10-21 用户 8 项需求 #2）：仅 mp4，上限 100MB
# 小程序 <video> 可解码的格式=ISO-BMFF ftyp 家族（mp4/m4v/mov/3gp，魔数同源）；
# webm/mkv/avi/flv 组件解不了，放行只会「传得上、放不出」，故不收
VIDEO_ALLOWED_EXT = {'mp4', 'm4v', 'mov', '3gp'}
VIDEO_MAX_SIZE = 100 * 1024 * 1024
DOCUMENT_ALLOWED_EXT = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'jpg', 'jpeg', 'png', 'gif', 'webp'}
DOCUMENT_MAX_SIZE = 20 * 1024 * 1024


class UploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        f = request.FILES.get('file')
        if f is None:
            raise ValidationError({'file': '缺少文件字段 file'})
        ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
        if ext not in ALLOWED_EXT:
            raise ValidationError({'file': '仅支持图片格式：' + '/'.join(sorted(ALLOWED_EXT))})
        if f.size > MAX_SIZE:
            raise ValidationError({'file': '图片不能超过 5MB'})
        name = f'uploads/{uuid.uuid4().hex}.{ext}'
        default_storage.save(name, f)
        # 存相对路径（前端 mediaUrl 补域名）：绝对 URL 把请求时的域名焊死在库里，
        # 换 IP/从开发者工具传（127.0.0.1）后其它端全部裂图
        return Response({'url': f'{settings.MEDIA_URL}{name}'})


class DocumentUploadView(APIView):
    """产前纸样、工艺单和样衣附件；只允许常见文档和图片，不接收可执行文件。"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        f = request.FILES.get('file')
        if f is None:
            raise ValidationError({'file': '缺少文件字段 file'})
        ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
        if ext not in DOCUMENT_ALLOWED_EXT:
            raise ValidationError({'file': '仅支持 pdf/doc/docx/xls/xlsx 或图片附件'})
        if f.size > DOCUMENT_MAX_SIZE:
            raise ValidationError({'file': '附件不能超过 20MB'})
        head = f.read(8)
        f.seek(0)
        if ext == 'pdf' and not head.startswith(b'%PDF-'):
            raise ValidationError({'file': 'PDF 文件格式不正确'})
        if ext in {'docx', 'xlsx'} and not head.startswith(b'PK'):
            raise ValidationError({'file': 'Office 文件格式不正确'})
        if ext in {'doc', 'xls'} and head != bytes.fromhex('D0CF11E0A1B11AE1'):
            raise ValidationError({'file': '旧版 Office 文件格式不正确'})
        name = f'uploads/{uuid.uuid4().hex}.{ext}'
        default_storage.save(name, f)
        return Response({'url': f'{settings.MEDIA_URL}{name}', 'name': f.name})


class VideoUploadView(APIView):
    """POST /uploads/video 工艺视频上传（multipart，字段 file）→ 返回相对路径。

    仅 ftyp 家族视频（mp4/m4v/mov/3gp）：扩展名 + 魔数（第 5~8 字节 'ftyp'）双校验；上限 100MB。
    FILE_UPLOAD_MAX_MEMORY_SIZE 不动（大文件自动落临时文件，不占内存）；
    DATA_UPLOAD_MAX_MEMORY_SIZE 默认只限非文件字段， multipart 大文件不受其约束，
    settings 未配置该项故无需放宽。权限沿用图片上传（仅登录）。
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        f = request.FILES.get('file')
        if f is None:
            raise ValidationError({'file': '缺少文件字段 file'})
        ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
        if ext not in VIDEO_ALLOWED_EXT:
            raise ValidationError({'file': '仅支持视频格式：mp4/m4v/mov/3gp'})
        if f.size > VIDEO_MAX_SIZE:
            raise ValidationError({'file': '视频不能超过 100MB'})
        head = f.read(12)
        if len(head) < 12 or head[4:8] != b'ftyp':
            raise ValidationError({'file': '视频文件格式不正确'})
        f.seek(0)
        name = f'uploads/{uuid.uuid4().hex}.{ext}'
        default_storage.save(name, f)
        # 与图片上传一致：存相对路径（前端 mediaUrl 补域名）
        return Response({'url': f'{settings.MEDIA_URL}{name}'})


class PermissionDictView(APIView):
    """GET /permissions 权限点字典（Plan A）：按模块分组，岗位编辑弹窗渲染用。

    仅登录即可读（字典全局一致，与公司无关，无需公司上下文）；
    legacy=True 的点会被旧 coarse key 覆盖（见 core/permissions.py）。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .permissions import PERMISSION_GROUPS
        return Response({'groups': PERMISSION_GROUPS})


class ClientLogView(CompanyContextMixin, APIView):
    """客户端日志。

    POST /client-logs  批量上报 [{level,page,message,detail,company_id}]（≤20 条/批）。
      每条日志带记录时的 company_id（前端 logger 在产生现场打标，批量补传不串厂）；
      后端校验上报人是该厂在职成员（ACTIVE）才落款，未登录/非成员落空。
      user 恒取认证身份（token），不信任请求体。
      上报失败由前端本地队列补传，这里永远静默成功，不反向打断业务。
    GET /client-logs   仅本公司管理员可读（厂隔离），筛选 user_id/level/date_from/date_to。
    """
    permission_classes = [AllowAny]
    skip_company_context = True  # POST 允许未登录；GET 内手动初始化公司上下文

    MAX_BATCH = 20
    MAX_DETAIL = 4000

    def _resolve_company(self, request, company_id):
        """厂落款校验：仅当上报人是该厂在职成员（ACTIVE）才认，防跨厂伪造/旧队列错标。"""
        if not company_id or not request.user.is_authenticated:
            return None
        from apps.accounts.models import UserCompany
        uc = UserCompany.objects.select_related('company').filter(
            user=request.user, company_id=company_id, status=UserCompany.Status.ACTIVE).first()
        return uc.company if uc else None

    def post(self, request):
        items = request.data if isinstance(request.data, list) else [request.data]
        if not items or len(items) > self.MAX_BATCH:
            raise ValidationError({'detail': f'每批 1~{self.MAX_BATCH} 条'})
        user = request.user if request.user.is_authenticated else None
        rows = []
        for it in items[:self.MAX_BATCH]:
            if not isinstance(it, dict) or not str(it.get('message', '')).strip():
                continue
            level = str(it.get('level', 'info'))
            if level not in ClientLog.Level.values:
                level = ClientLog.Level.INFO
            rows.append(ClientLog(
                company=self._resolve_company(request, it.get('company_id')), user=user, level=level,
                page=str(it.get('page', ''))[:64],
                message=str(it.get('message', ''))[:255],
                detail=str(it.get('detail', ''))[:self.MAX_DETAIL],
            ))
        if not rows:
            raise ValidationError({'message': '缺少有效日志条目'})
        ClientLog.objects.bulk_create(rows)
        return Response({'count': len(rows)}, status=201)

    def get(self, request):
        if not request.user.is_authenticated:
            raise NotAuthenticated()
        self.initialize_company(request)
        self.check_perm('logs.client')
        qs = ClientLog.objects.filter(company=self.company)
        uid = request.query_params.get('user_id', '')
        if uid.isdigit():
            qs = qs.filter(user_id=int(uid))
        level = request.query_params.get('level')
        if level in ClientLog.Level.values:
            qs = qs.filter(level=level)
        date_from = parse_date(request.query_params.get('date_from', ''))
        date_to = parse_date(request.query_params.get('date_to', ''))
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(ClientLogSerializer(page, many=True).data)
