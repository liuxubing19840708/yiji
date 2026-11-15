"""账号视图：认证、我的信息、公司、员工、岗位。"""
import json
import urllib.request
import urllib.error
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.core import signing
from django.db.models import Count
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.mixins import CompanyContextMixin
from apps.core.permissions import has_perm

from .models import Company, Post, User, UserCompany
from .serializers import (ChangePasswordSerializer, CompanySerializer, EmployeeListSerializer,
                          EmployeeSetGroupSerializer, EmployeeSetPostSerializer,
                          EmployeeSetProcessesSerializer,
                          InviteEmployeeSerializer, JoinCompanySerializer, PasswordLoginSerializer,
                          PostSerializer, SMSLoginSerializer, UserSerializer)
from .services import create_company_with_owner, get_wx_access_token, send_sms_code


def issue_tokens(user):
    from rest_framework_simplejwt.tokens import RefreshToken
    refresh = RefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'user': UserSerializer(user).data,
    }


class SendSMSCodeView(APIView):
    """POST auth/sms-code  {phone}"""
    permission_classes = [AllowAny]

    def post(self, request):
        phone = str(request.data.get('phone', '')).strip()
        if not phone.isdigit() or len(phone) != 11:
            raise ValidationError('请输入正确的手机号')
        send_sms_code(phone)
        return Response({'message': '验证码已发送'})


class SMSLoginView(APIView):
    """POST auth/login-sms  {phone, code}，手机号不存在则自动注册。"""
    permission_classes = [AllowAny]

    def post(self, request):
        ser = SMSLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(ser.validated_data)


class PasswordLoginView(TokenObtainPairView):
    """POST auth/login  {phone, password}"""
    serializer_class = PasswordLoginSerializer


class WechatLoginView(APIView):
    """POST auth/wechat-login  {code}。需要配置 WECHAT_APPID/WECHAT_SECRET。"""
    permission_classes = [AllowAny]

    def post(self, request):
        from django.conf import settings
        appid, secret = getattr(settings, 'WECHAT_APPID', ''), getattr(settings, 'WECHAT_SECRET', '')
        code = str(request.data.get('code', '')).strip()
        if not code:
            raise ValidationError('缺少 code')
        if not appid or not secret:
            raise ValidationError('未配置微信小程序参数（WECHAT_APPID/WECHAT_SECRET）')
        url = (f'https://api.weixin.qq.com/sns/jscode2session?appid={appid}'
               f'&secret={secret}&js_code={code}&grant_type=authorization_code')
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('微信登录响应不是 JSON 对象')
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ValidationError(f'微信登录失败：网络错误 {exc}') from exc
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValidationError('微信登录失败：响应格式异常') from exc
        openid = data.get('openid')
        if not isinstance(openid, str) or not openid:
            raise ValidationError(f"微信登录失败：{data.get('errmsg', 'code 无效')}")
        unionid = data.get('unionid')
        if not isinstance(unionid, str) or not unionid:
            unionid = None
        # unionid 才能跨微信应用关联同一微信主体；不同应用的 openid 绝不互查。
        user = User.objects.filter(unionid=unionid).first() if unionid else None
        if user is None:
            user = User.objects.filter(openid=openid).first()
        if user is None:
            user = User.objects.create_user(
                username=f'wx_{openid[:20]}', openid=openid, unionid=unionid)
        if not user.is_active:
            raise ValidationError('该账号已注销，不能继续登录')
        if user.openid != openid or user.unionid != unionid:
            user.openid = openid
            if unionid:
                user.unionid = unionid
            user.save(update_fields=['openid', 'unionid'])
        return Response(issue_tokens(user))


def _wechat_get_json(url, action):
    """读取微信 JSON；传输、编码和业务错误都转为明确的客户端可见错误。"""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if not isinstance(data, dict):
            raise ValueError('微信响应不是 JSON 对象')
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValidationError(f'{action}：网络错误 {exc}') from exc
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValidationError(f'{action}：响应格式异常') from exc
    if data.get('errcode'):
        raise ValidationError(f"{action}：{data.get('errmsg') or '微信返回未知错误'}")
    return data


class WechatWebAuthView(APIView):
    """GET auth/wechat-web：返回网站应用配置状态及真实微信授权 URL。"""
    permission_classes = [AllowAny]

    def get(self, request):
        if not settings.WECHAT_OPEN_APPID or not settings.WECHAT_OPEN_SECRET:
            return Response({
                'enabled': False,
                'authorization_url': None,
                'message': '部署方需配置微信开放平台网站应用 WECHAT_OPEN_APPID/WECHAT_OPEN_SECRET',
            })
        # 不接受前端 redirect_uri：回调始终是当前后端的同源 /web/。
        redirect_uri = request.build_absolute_uri('/web/')
        state = signing.TimestampSigner(salt='accounts.wechat-web').sign_object({
            'purpose': 'wechat-web-login', 'nonce': uuid.uuid4().hex,
        })
        query = urlencode({
            'appid': settings.WECHAT_OPEN_APPID,
            'redirect_uri': redirect_uri,
            'response_type': 'code', 'scope': 'snsapi_login', 'state': state,
        })
        response = Response({
            'enabled': True,
            'authorization_url': f'https://open.weixin.qq.com/connect/qrconnect?{query}#wechat_redirect',
            'redirect_uri': redirect_uri,
        })
        response.set_cookie(
            'wechat_oauth_state', state, max_age=settings.WECHAT_WEB_STATE_MAX_AGE,
            secure=request.is_secure(), httponly=True, samesite='Lax',
        )
        return response


class WechatWebLoginView(APIView):
    """POST auth/wechat-web/login {code,state}：code 换 token/userinfo 后签发 JWT。"""
    permission_classes = [AllowAny]

    def post(self, request):
        code = str(request.data.get('code', '')).strip()
        state = str(request.data.get('state', '')).strip()
        if not code or not state:
            raise ValidationError('缺少 code 或 state')
        if not settings.WECHAT_OPEN_APPID or not settings.WECHAT_OPEN_SECRET:
            raise ValidationError('未配置微信开放平台网站应用（WECHAT_OPEN_APPID/WECHAT_OPEN_SECRET）')
        try:
            state_data = signing.TimestampSigner(salt='accounts.wechat-web').unsign_object(
                state, max_age=settings.WECHAT_WEB_STATE_MAX_AGE)
        except (signing.BadSignature, signing.SignatureExpired) as exc:
            raise ValidationError('微信登录 state 无效或已过期，请重新发起登录') from exc
        if not isinstance(state_data, dict) or state_data.get('purpose') != 'wechat-web-login':
            raise ValidationError('微信登录 state 无效或已过期，请重新发起登录')
        if request.COOKIES.get('wechat_oauth_state') != state:
            raise ValidationError('微信登录状态与发起浏览器不一致，请重新发起登录')

        token_url = 'https://api.weixin.qq.com/sns/oauth2/access_token?' + urlencode({
            'appid': settings.WECHAT_OPEN_APPID, 'secret': settings.WECHAT_OPEN_SECRET,
            'code': code, 'grant_type': 'authorization_code',
        })
        token = _wechat_get_json(token_url, '微信网页登录换取凭证失败')
        access_token, openid = token.get('access_token'), token.get('openid')
        if not isinstance(access_token, str) or not access_token or not isinstance(openid, str) or not openid:
            raise ValidationError('微信网页登录换取凭证失败：响应缺少 access_token 或 openid')
        userinfo_url = 'https://api.weixin.qq.com/sns/userinfo?' + urlencode({
            'access_token': access_token, 'openid': openid, 'lang': 'zh_CN',
        })
        info = _wechat_get_json(userinfo_url, '微信网页登录获取用户信息失败')
        if info.get('openid') != openid:
            raise ValidationError('微信网页登录获取用户信息失败：openid 与授权凭证不一致')
        unionid = info.get('unionid') or token.get('unionid')
        if not isinstance(unionid, str) or not unionid:
            unionid = None

        user = User.objects.filter(unionid=unionid).first() if unionid else None
        if user is None:
            user = User.objects.filter(web_openid=openid).first()
        if user is None:
            user = User.objects.create_user(
                username=f'wxweb_{openid[:20]}', web_openid=openid, unionid=unionid)
        if not user.is_active:
            raise ValidationError('该账号已注销，不能继续登录')
        if user.web_openid != openid or (unionid and user.unionid != unionid):
            user.web_openid = openid
            if unionid:
                user.unionid = unionid
            user.save(update_fields=['web_openid', 'unionid'])
        response = Response(issue_tokens(user))
        response.delete_cookie('wechat_oauth_state')
        return response


class MeView(APIView):
    """GET/PATCH/PUT auth/me：我的资料（小程序端 wx.request 不支持 PATCH，PUT 等价转发）。"""

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        return self._update(request)

    def put(self, request):
        return self._update(request)

    def _update(self, request):
        ser = UserSerializer(request.user, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class ChangePasswordView(APIView):
    """PUT auth/password  {old_password, new_password}"""

    def put(self, request):
        ser = ChangePasswordSerializer(data=request.data, context={'request': request})
        ser.is_valid(raise_exception=True)
        request.user.set_password(ser.validated_data['new_password'])
        request.user.save(update_fields=['password'])
        update_session_auth_hash(request, request.user)
        return Response({'message': '密码修改成功'})


class DeactivateView(APIView):
    """POST auth/deactivate  {password?}：本人注销账号（§2.2 / 规则⑧）。

    二次校验通过后匿名化（保留生产/工资记录完整性，仅脱敏身份信息）：
    real_name→'已注销用户'，phone/username→'del_'+uuid8，密码置不可用，is_active=False。
    """

    def post(self, request):
        user = request.user
        # 二次校验：设置了可用密码则必须验证（短信/微信注册用户无密码，跳过）
        if user.has_usable_password() and user.password:
            password = str(request.data.get('password') or '')
            if not password or not user.check_password(password):
                raise ValidationError('密码校验失败，无法注销')
        # 规则⑧：任一在职公司的最后一个管理员不可注销
        links = list(UserCompany.objects.filter(
            user=user, status=UserCompany.Status.ACTIVE, is_deleted=False
        ).select_related('company', 'post'))
        for link in links:
            if _is_last_admin(link):
                raise ValidationError(
                    f'你是公司「{link.company.name}」的最后一名管理员，'
                    f'请先转让管理员或办理离职后再注销账号')
        tail = (user.phone or '')[-4:]
        suffix = uuid.uuid4().hex[:8]
        user.real_name = '已注销用户'
        user.phone = f'del_{suffix}'
        user.username = f'del_{suffix}'
        user.openid = None
        user.web_openid = None
        user.unionid = None
        user.set_unusable_password()
        user.is_active = False
        user.save(update_fields=[
            'real_name', 'phone', 'username', 'openid', 'web_openid', 'unionid',
            'password', 'is_active',
        ])
        # 审计（规则⑪）：在各在职公司各落一条，message 已插值
        from apps.ops.models import AuditLog
        from apps.ops.services import audit
        for link in links:
            audit(link.company, AuditLog.Type.OTHER, operator=user, target_user=user,
                  message=f'用户注销账号，个人信息已匿名化（手机号尾号 {tail}）')
        return Response({'message': '账号已注销'})


class CompanyViewSet(viewsets.ViewSet):
    """公司：mine / join / switch。三个动作都基于当前登录用户。"""

    def mine(self, request):
        links = (UserCompany.objects
                 .filter(user=request.user, is_deleted=False)
                 .select_related('company', 'post'))
        companies = [l.company for l in links]
        counts = dict(Company.objects.filter(
            id__in=[c.id for c in companies]
        ).annotate(n=Count('members', filter=models_q_active())).values_list('id', 'n'))
        ser = CompanySerializer(companies, many=True, context={
            'links': {l.company_id: l for l in links},
            'member_counts': counts,
        })
        return Response(ser.data)

    def create_company(self, request):
        """POST companies/create {name, city?} → 创建公司，创建人自动成为在职管理员。"""
        name = (request.data.get('name') or '').strip()
        if not name:
            raise ValidationError({'name': '公司名称不能为空'})
        city = (request.data.get('city') or '').strip()
        company, admin_post = create_company_with_owner(request.user, name, city)
        link = UserCompany.objects.get(user=request.user, company=company, is_deleted=False)
        return Response({'message': '创建成功', 'company': CompanySerializer(
            company, context={'links': {company.id: link},
                              'member_counts': {company.id: 1}}).data}, status=201)

    def join(self, request):
        """POST companies/join {company_no} → 创建待审核成员关系。"""
        ser = JoinCompanySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        company = ser.company
        if UserCompany.objects.filter(user=request.user, company=company, is_deleted=False).exists():
            raise ValidationError('你已在该公司的成员列表中')
        UserCompany.objects.create(user=request.user, company=company)
        from apps.ops.services import notify
        notify(company, [link.user_id for link in UserCompany.objects.filter(
            company=company, status=UserCompany.Status.ACTIVE, is_deleted=False,
            post__is_admin=True)], 'join_request', '新成员申请',
            f'用户 {request.user.real_name or request.user.username} 申请加入公司，请到员工管理中审核')
        return Response(CompanySerializer(
            company, context={'links': {company.id: UserCompany.objects.get(
                user=request.user, company=company)}}).data,
            status=status.HTTP_201_CREATED)

    def switch(self, request, pk):
        """PUT companies/:id/switch：校验在职后更新 last_used_at。"""
        link = UserCompany.objects.filter(
            user=request.user, company_id=pk, status=UserCompany.Status.ACTIVE,
            is_deleted=False).select_related('company', 'post').first()
        if link is None:
            raise NotFound('不是该公司的在职成员')
        link.last_used_at = timezone.now()
        link.save(update_fields=['last_used_at', 'updated_at'])
        return Response({'message': '已切换', 'company': CompanySerializer(
            link.company, context={'links': {link.company_id: link}}).data})


class CompanyWxacodeView(CompanyContextMixin, APIView):
    """GET companies/wxacode：当前公司的小程序码 PNG（scene=c=<company_no>，落地登录页）。

    微信「扫一扫」扫小程序码可直达小程序登录页并预填加入编号——这是公司二维码
    真正可用的链路。需要配置 WECHAT_APPID/WECHAT_SECRET，未配置时 400 明确提示
    （前端回落本地文本二维码 JOIN:<company_no>，仅 app 内扫码可识别）。
    版本由 settings.WECHAT_WXACODE_ENV 控制（release/trial/develop，默认 release）。
    """

    def get(self, request):
        from django.conf import settings
        from django.http import HttpResponse
        if not settings.WECHAT_APPID or not settings.WECHAT_SECRET:
            raise ValidationError('未配置微信小程序参数（WECHAT_APPID/WECHAT_SECRET），无法生成小程序码')
        import urllib.error
        import urllib.request
        token = get_wx_access_token()
        payload = json.dumps({
            'scene': f'c={self.company.company_no}',
            'page': 'pages/login/index',
            'check_path': False,   # 允许页面未发布/体验版
            'env_version': getattr(settings, 'WECHAT_WXACODE_ENV', 'release'),
            'width': 430,
        }).encode('utf-8')
        url = f'https://api.weixin.qq.com/wxa/getwxacodeunlimit?access_token={token}'
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                ctype = resp.headers.get('Content-Type', '')
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ValidationError(f'生成小程序码失败：网络错误 {exc}') from exc
        if 'image' not in ctype:
            try:
                data = json.loads(body.decode('utf-8'))
                detail = data.get('errmsg') or f"errcode={data.get('errcode')}"
            except Exception:
                detail = '响应格式异常'
            raise ValidationError(f'生成小程序码失败：{detail}')
        return HttpResponse(body, content_type='image/png')


def models_q_active():
    """用于 Count('members', filter=...)：Q 需带 members__ 前缀（针对 Company 注解）。"""
    from django.db.models import Q
    return Q(members__is_deleted=False, members__status=UserCompany.Status.ACTIVE)


def _is_last_admin(link: UserCompany) -> bool:
    """该成员是否是公司在职管理员中的最后一个。"""
    if not (link.post and link.post.is_admin):
        return False
    return not UserCompany.objects.filter(
        company=link.company, status=UserCompany.Status.ACTIVE, is_deleted=False,
        post__is_admin=True).exclude(id=link.id).exists()


class EmployeeViewSet(CompanyContextMixin, viewsets.ViewSet):
    """员工管理：tab 列表 + approve/reject/resign/rehire/set-post/pin。"""

    def list(self, request):
        tab = request.query_params.get('tab', 'all')
        qs = UserCompany.objects.filter(company=self.company).select_related('user', 'post')
        if tab == 'pending':
            qs = qs.filter(status=UserCompany.Status.PENDING)
        elif tab == 'resigned':
            qs = qs.filter(status=UserCompany.Status.RESIGNED)
        else:
            qs = qs.filter(status=UserCompany.Status.ACTIVE)
        kw = request.query_params.get('keyword')
        if kw:
            from django.db.models import Q
            qs = qs.filter(Q(user__real_name__icontains=kw) | Q(user__phone__icontains=kw))
        # 岗位筛选（员工管理页岗位 chips）：post=<post_id>，post=0 表示未设置岗位
        post_id = request.query_params.get('post')
        if post_id is not None and post_id != '':
            if post_id == '0':
                qs = qs.filter(post__isnull=True)
            else:
                qs = qs.filter(post_id=post_id)
        return Response(EmployeeListSerializer(qs.order_by('-is_pinned', 'id'), many=True).data)

    def approve(self, request, pk):
        """通过入职申请（规则⑨：容量上限校验）。"""
        # Plan A：补权限门槛（此前全员可审批——安全洞）；走 employees.manage 点
        self.check_perm('employees.manage')
        link = self._get_link(pk)
        if link.status != UserCompany.Status.PENDING:
            raise ValidationError('该成员不处于待审核状态')
        active_count = UserCompany.objects.filter(
            company=self.company, status=UserCompany.Status.ACTIVE, is_deleted=False).count()
        if active_count >= self.company.capacity:
            raise ValidationError(f'员工数量已达上限（{self.company.capacity} 人），无法继续添加')
        # 条件更新（status=PENDING 才落 ACTIVE）：并发双击审批只有一方成功，防 check-then-act 竞态
        hired = link.hired_date or timezone.localdate()
        updated = UserCompany.objects.filter(
            pk=link.pk, status=UserCompany.Status.PENDING).update(
            status=UserCompany.Status.ACTIVE, hired_date=hired, updated_at=timezone.now())
        if not updated:
            raise ValidationError('该成员不处于待审核状态')
        link.refresh_from_db()
        from apps.ops.services import notify
        notify(self.company, [link.user_id], 'join_approved', '申请已通过',
               f'你已加入公司「{self.company.name}」')
        return Response(EmployeeListSerializer(link).data)

    def reject(self, request, pk):
        self.check_perm('employees.manage')
        link = self._get_link(pk)
        if link.status != UserCompany.Status.PENDING:
            raise ValidationError('仅待审核成员可拒绝')
        link.status = UserCompany.Status.RESIGNED
        link.save(update_fields=['status', 'updated_at'])
        return Response(EmployeeListSerializer(link).data)

    def resign(self, request, pk):
        """办理离职（2026-10-18 拍板：未结清一律 400，删除 need_confirm/force 通道）。

        最后一个管理员不允许；存在未结算计件记录时强制先结算再离职。
        离职后的历史薪酬与未结余额仍可查（EmployeeSalaryView /
        CompanyEmployeesView 已放开离职成员），不会出现「离了就查不到钱」。
        """
        self.check_perm('employees.manage')
        link = self._get_link(pk)
        if link.status != UserCompany.Status.ACTIVE:
            raise ValidationError('仅在职成员可办理离职')
        if _is_last_admin(link):
            raise ValidationError('公司至少需要一名在职管理员')
        if self._has_unsettled(link):
            raise ValidationError('该员工存在未结算的计件记录，请先结算后再办理离职')
        link.status = UserCompany.Status.RESIGNED
        link.is_suspended = False
        link.save(update_fields=['status', 'is_suspended', 'updated_at'])
        from apps.ops.services import notify
        notify(self.company, [link.user_id], 'resigned', '离职通知',
               f'你已在公司「{self.company.name}」办理离职')
        return Response(EmployeeListSerializer(link).data)

    def rehire(self, request, pk):
        """离职复职：重新回到在职状态。"""
        self.check_perm('employees.manage')
        link = self._get_link(pk)
        if link.status != UserCompany.Status.RESIGNED:
            raise ValidationError('仅离职成员可复职')
        active_count = UserCompany.objects.filter(
            company=self.company, status=UserCompany.Status.ACTIVE, is_deleted=False).count()
        if active_count >= self.company.capacity:
            raise ValidationError(f'员工数量已达上限（{self.company.capacity} 人），无法复职')
        link.status = UserCompany.Status.ACTIVE
        link.save(update_fields=['status', 'updated_at'])
        return Response(EmployeeListSerializer(link).data)

    def set_post(self, request, pk):
        self.check_perm('employees.manage')
        ser = EmployeeSetPostSerializer(data=request.data, context={'company': self.company})
        ser.is_valid(raise_exception=True)
        link = self._get_link(pk)
        if link.status != UserCompany.Status.ACTIVE:
            raise ValidationError('仅在职成员可设置岗位')
        link.post = ser.post
        link.save(update_fields=['post', 'updated_at'])
        return Response(EmployeeListSerializer(link).data)

    def set_group(self, request, pk):
        """设置组编号（1组/2组…）：group_no 正整数，null 清空。"""
        self.check_perm('employees.manage')
        ser = EmployeeSetGroupSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        link = self._get_link(pk)
        if link.status != UserCompany.Status.ACTIVE:
            raise ValidationError('仅在职成员可设置组编号')
        link.group_no = ser.validated_data.get('group_no')
        link.save(update_fields=['group_no', 'updated_at'])
        return Response(EmployeeListSerializer(link).data)

    def pin(self, request, pk):
        self.check_perm('employees.manage')
        link = self._get_link(pk)
        link.is_pinned = bool(request.data.get('is_pinned'))
        link.save(update_fields=['is_pinned', 'updated_at'])
        return Response({'id': link.id, 'is_pinned': link.is_pinned})

    def set_plan(self, request):
        """PUT companies/plan {plan}：变更版本套餐（2026-10-17）。

        容量随套餐同步写 capacity（体验20/基础50/标准150/旗舰500）；
        在职人数超出目标套餐容量时拒绝降档。所有容量校验仍统一读 capacity。
        """
        self.check_perm('company.manage')  # Plan A：套餐变更走 company.manage 点
        plan = str(request.data.get('plan', ''))
        if plan not in Company.PLAN_CAPACITY:
            raise ValidationError(f'未知版本套餐：{plan or "（空）"}'
                                  f'（可选：{"、".join(Company.PLAN_CAPACITY)}）')
        new_capacity = Company.PLAN_CAPACITY[plan]
        active_count = UserCompany.objects.filter(
            company=self.company, status=UserCompany.Status.ACTIVE, is_deleted=False).count()
        if active_count > new_capacity:
            label = Company.Plan(plan).label
            raise ValidationError(f'当前在职 {active_count} 人，超出{label}容量（{new_capacity} 人），'
                                  f'请先办理部分员工离职或升级更高版本')
        old_plan, old_capacity = self.company.plan, self.company.capacity
        if plan != old_plan or new_capacity != old_capacity:
            from apps.ops.models import AuditLog
            from apps.ops.services import audit
            self.company.plan = plan
            self.company.capacity = new_capacity
            self.company.save(update_fields=['plan', 'capacity', 'updated_at'])
            audit(self.company, AuditLog.Type.OTHER, operator=request.user,
                  before={'plan': old_plan, 'capacity': old_capacity},
                  after={'plan': plan, 'capacity': new_capacity},
                  message=f'{request.user.real_name} 变更版本套餐：'
                          f'{Company.Plan(old_plan).label} → {Company.Plan(plan).label}，'
                          f'员工容量 {old_capacity} → {new_capacity} 人')
        return Response({'message': '已变更', 'plan': plan,
                         'plan_display': Company.Plan(plan).label, 'capacity': new_capacity})

    def invite(self, request):
        """POST employees/invite {phone, real_name?, post_id?}（§11.1：手机号邀请员工）。

        创建 PENDING 成员关系，对方登录后走既有 approve 流程；容量口径与 approve 一致。
        """
        self.check_perm('employees.manage')  # Plan A：邀请员工走 employees.manage 点
        ser = InviteEmployeeSerializer(data=request.data, context={'company': self.company})
        ser.is_valid(raise_exception=True)
        phone = ser.validated_data['phone']
        real_name = ser.validated_data.get('real_name') or ''
        post = getattr(ser, 'post', None)
        # 容量校验（口径同 approve / 规则⑨）
        active_count = UserCompany.objects.filter(
            company=self.company, status=UserCompany.Status.ACTIVE, is_deleted=False).count()
        if active_count >= self.company.capacity:
            raise ValidationError(f'员工数量已达上限（{self.company.capacity} 人），无法继续添加')
        user, created = User.objects.get_or_create(
            phone=phone,
            defaults={'username': phone, 'real_name': real_name or f'用户{phone[-4:]}'})
        if not created and real_name and not user.real_name:
            user.real_name = real_name
            user.save(update_fields=['real_name'])
        # (user, company) 有 DB 唯一约束：软删/离职记录需复活为待审核，其余状态拒绝重复邀请
        link = UserCompany.all_objects.filter(user=user, company=self.company).first()
        if link and not link.is_deleted:
            if link.status == UserCompany.Status.PENDING:
                raise ValidationError('该手机号已在公司的待审核列表中')
            if link.status == UserCompany.Status.ACTIVE:
                raise ValidationError('该手机号已是公司成员')
        if link:
            link.is_deleted = False
            link.status = UserCompany.Status.PENDING
            link.post = post
            link.save(update_fields=['is_deleted', 'status', 'post', 'updated_at'])
        else:
            link = UserCompany.objects.create(user=user, company=self.company, post=post)
        from apps.ops.services import notify
        display_name = user.real_name or phone
        notify(self.company, [user.id], 'invited', '入职邀请',
               f'你已被邀请加入公司「{self.company.name}」，登录后等待管理员审核')
        # 提醒其他管理员及时审核
        admin_ids = list(UserCompany.objects.filter(
            company=self.company, status=UserCompany.Status.ACTIVE, is_deleted=False,
            post__is_admin=True).exclude(user=request.user).values_list('user_id', flat=True))
        notify(self.company, admin_ids, 'join_request', '新成员邀请',
               f'已邀请 {display_name}（{phone}）加入公司，待对方登录后请到员工管理中审核')
        return Response({'message': '已邀请，等待对方登录后管理员审核',
                         'employee': EmployeeListSerializer(link).data},
                        status=status.HTTP_201_CREATED)

    def set_assigned_processes(self, request, pk):
        """PUT employees/:id/assigned-processes {process_ids:[...]}（§9.1 指定工序，管理员）。"""
        self.check_perm('employees.manage')
        link = self._get_link(pk)
        ser = EmployeeSetProcessesSerializer(data=request.data, context={'company': self.company})
        ser.is_valid(raise_exception=True)
        process_ids = ser.validated_data['process_ids']
        old_ids = list(link.assigned_processes or [])
        if old_ids != process_ids:
            link.assigned_processes = process_ids
            link.save(update_fields=['assigned_processes', 'updated_at'])
            # 审计（规则⑪/⑫）：message 服务端插值完成后落库
            from apps.catalog.models import Process
            from apps.ops.models import AuditLog
            from apps.ops.services import audit
            old_names = list(Process.objects.filter(company=self.company, id__in=old_ids)
                             .order_by('id').values_list('name', flat=True))
            new_names = list(Process.objects.filter(company=self.company, id__in=process_ids)
                             .order_by('id').values_list('name', flat=True))
            audit(self.company, AuditLog.Type.PROCESS, operator=request.user,
                  target_user=link.user,
                  before={'assigned_processes': old_ids},
                  after={'assigned_processes': process_ids},
                  message=(f'修改指定工序：【用户：{link.user.real_name or link.user.username}】'
                           f'【{"、".join(old_names) or "无"} → {"、".join(new_names) or "无"}】'
                           f'操作人：{request.user.real_name or request.user.username}'))
        return Response(EmployeeListSerializer(link).data)

    def _get_link(self, pk) -> UserCompany:
        link = UserCompany.objects.filter(
            company=self.company, pk=pk, is_deleted=False).select_related('user', 'post').first()
        if link is None:
            raise NotFound('成员不存在')
        return link

    @staticmethod
    def _has_unsettled(link: UserCompany) -> bool:
        from apps.records.models import Record
        return Record.objects.filter(
            company=link.company, user=link.user, settlement__isnull=True,
            is_deleted=False).exists()


class PostViewSet(CompanyContextMixin, viewsets.ModelViewSet):
    """岗位管理：员工管理-岗位 tab（写操作仅管理员，防普通成员建 '*' 岗位提权）。"""
    serializer_class = PostSerializer

    def perform_create(self, serializer):
        self.check_perm('employees.manage')  # Plan A：岗位写操作走 employees.manage 点
        serializer.save(company=self.company)

    def perform_update(self, serializer):
        self.check_perm('employees.manage')
        serializer.save()

    def perform_destroy(self, instance):
        self.check_perm('employees.manage')
        if instance.is_system:
            raise ValidationError('系统内置岗位不可删除')
        if instance.members.filter(is_deleted=False, status=UserCompany.Status.ACTIVE).exists():
            raise ValidationError('该岗位仍有在职成员，无法删除')
        instance.delete()

    def get_queryset(self):
        return (Post.objects.filter(company=self.company)
                .annotate(member_count=Count('members', filter=models_q_active()))
                .order_by('id'))

    def copy(self, request, pk):
        """POST posts/<pk>/copy：复制岗位（名称+权限点），名称自动加「副本」后缀去重。"""
        self.check_perm('employees.manage')
        src = self.get_object()
        base = f'{src.name}副本'
        name, n = base, 2
        # 同名撞唯一约束（company+name）：自动追加序号直到可用
        while Post.objects.filter(company=self.company, name=name, is_deleted=False).exists():
            name = f'{base}{n}'
            n += 1
        post = Post.objects.create(company=self.company, name=name,
                                   permissions=list(src.permissions or []))
        return Response(PostSerializer(
            Post.objects.annotate(member_count=Count('members', filter=models_q_active()))
            .get(pk=post.pk)).data, status=status.HTTP_201_CREATED)

