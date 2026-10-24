"""公司上下文 Mixin：从请求头/参数解析当前公司，并提供权限校验助手。"""
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.core.permissions import has_perm


class CompanyContextMixin:
    """在 initial 阶段解析公司上下文。

    公司 ID 来源（优先级从高到低）：
      1. 请求头 X-Company-Id
      2. 查询参数 company_id
    解析成功后挂到 self.user_company / self.company。
    """

    def initialize_company(self, request):
        cid = request.headers.get('X-Company-Id') or request.query_params.get('company_id')
        if not cid:
            raise ValidationError('缺少公司上下文（X-Company-Id 请求头或 company_id 参数）')
        from apps.accounts.models import UserCompany
        try:
            uc = UserCompany.objects.select_related('company', 'post', 'user').get(
                user=request.user,
                company_id=cid,
                status=UserCompany.Status.ACTIVE,
            )
        except UserCompany.DoesNotExist:
            raise NotFound('不是该公司的在职成员')
        if uc.is_suspended:
            raise PermissionDenied('账号已被停职')
        self.user_company = uc
        self.company = uc.company
        return uc

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not getattr(self, 'skip_company_context', False):
            self.initialize_company(request)

    # ---- 权限助手 ----
    def check_perm(self, key: str):
        """无权限则抛 PermissionDenied。"""
        if not has_perm(self.user_company, key):
            raise PermissionDenied('没有该模块的操作权限')
        return True

    def has_perm(self, key: str) -> bool:
        return has_perm(self.user_company, key)

    def require_admin(self):
        if not (self.user_company and self.user_company.post and self.user_company.post.is_admin):
            raise PermissionDenied('仅管理员可操作')
