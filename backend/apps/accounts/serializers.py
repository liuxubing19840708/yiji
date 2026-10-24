"""账号序列化器：登录、用户、公司、岗位、成员。"""
from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.utils import q2

from .models import Company, Post, User, UserCompany


class PasswordLoginSerializer(TokenObtainPairSerializer):
    """手机号 + 密码登录（USERNAME_FIELD=phone）。"""

    default_error_messages = {
        'no_active_account': '手机号或密码错误',
        'invalid': '手机号或密码错误',
    }

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['name'] = user.real_name or user.username
        token['phone'] = user.phone
        return token

    def validate(self, attrs):
        # 与微信登录路径（views.issue_tokens）对齐：密码登录同样返回 user，
        # 否则小程序端 yisao_user 存空，首页员工分支会拿 uid=0 去查工资（员工不存在）。
        data = super().validate(attrs)
        data['user'] = UserSerializer(self.user).data
        return data


class SMSLoginSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    code = serializers.CharField(max_length=6)

    def validate(self, attrs):
        from .services import verify_sms_code
        phone, code = attrs['phone'].strip(), attrs['code'].strip()
        verify_sms_code(phone, code)
        user = User.objects.filter(phone=phone).first()
        if user is None:
            user = User.objects.create_user(
                username=f'u_{phone}', phone=phone, real_name=f'用户{phone[-4:]}')
        refresh = RefreshToken.for_user(user)
        # user 必须序列化为字典：裸 User 实例会在 JSON 渲染时 500
        return {'refresh': str(refresh), 'access': str(refresh.access_token),
                'user': UserSerializer(user).data}


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'phone', 'username', 'real_name', 'avatar', 'gender', 'birthday']


class PostSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True, default=0)
    # Plan A 二期：下发解析后的细粒度点集（'*' 岗 → ['*']；legacy coarse key 已展开）
    effective_perms = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = ['id', 'name', 'permissions', 'effective_perms', 'is_admin', 'is_system',
                  'member_count']
        read_only_fields = ['is_system']

    def get_effective_perms(self, obj):
        from apps.core.permissions import effective_perms
        return effective_perms(obj)

    def validate_permissions(self, value):
        # Plan A：白名单 = 旧 coarse key + 全部细粒度权限点（core/permissions.py 字典）
        from apps.core.permissions import ALL_PERMISSION_KEYS
        bad = [k for k in (value or []) if k not in ALL_PERMISSION_KEYS]
        if bad:
            raise serializers.ValidationError(f'未知权限点：{bad}（可选：{list(ALL_PERMISSION_KEYS)}）')
        return value


class CompanySerializer(serializers.ModelSerializer):
    """我的公司列表项：带当前用户在该公司的状态与岗位。"""

    status = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()
    post = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    plan_display = serializers.CharField(source='get_plan_display', read_only=True)

    class Meta:
        model = Company
        fields = ['id', 'name', 'city', 'company_no', 'plan', 'plan_display', 'capacity',
                  'status', 'status_display', 'post', 'member_count', 'created_at']

    def _link(self, obj):
        links = self.context.get('links', {})
        return links.get(obj.id)

    def get_status(self, obj):
        link = self._link(obj)
        return link.status if link else None

    def get_status_display(self, obj):
        link = self._link(obj)
        return link.get_status_display() if link else None

    def get_post(self, obj):
        link = self._link(obj)
        return PostSerializer(link.post, context=self.context).data if link and link.post else None

    def get_member_count(self, obj):
        counts = self.context.get('member_counts', {})
        return counts.get(obj.id, 0)


class JoinCompanySerializer(serializers.Serializer):
    company_no = serializers.CharField(max_length=12)

    def validate_company_no(self, value):
        company = Company.objects.filter(company_no=value.strip()).first()
        if company is None:
            raise serializers.ValidationError('公司编号不存在')
        self.company = company
        return value


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField(min_length=6, max_length=64)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('原密码不正确')
        return value


class EmployeeListSerializer(serializers.ModelSerializer):
    """员工列表/详情。"""

    user_id = serializers.IntegerField(source='user.id', read_only=True)
    name = serializers.SerializerMethodField()
    phone = serializers.CharField(source='user.phone', read_only=True)
    avatar = serializers.ImageField(source='user.avatar', read_only=True)
    gender = serializers.IntegerField(source='user.gender', read_only=True)
    post_id = serializers.IntegerField(source='post.id', read_only=True)
    post_name = serializers.CharField(source='post.name', read_only=True)
    is_admin = serializers.BooleanField(source='post.is_admin', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    fixed_salary = serializers.SerializerMethodField()
    salary_type_display = serializers.CharField(
        source='get_salary_type_display', read_only=True)
    # §9.1 指定工序：员工维度展示与回显（读侧 process-selector 已消费该列表）
    assigned_processes = serializers.JSONField(read_only=True)

    class Meta:
        model = UserCompany
        fields = ['id', 'user_id', 'name', 'phone', 'avatar', 'gender', 'post_id', 'post_name',
                  'is_admin', 'status', 'status_display', 'is_pinned', 'is_suspended',
                  'hired_date', 'group_no', 'fixed_salary', 'fixed_type',
                  'salary_type', 'salary_type_display', 'piece_rate',
                  'commission_rate', 'assigned_processes', 'created_at']

    def get_name(self, obj):
        return obj.user.real_name or obj.user.username

    def get_fixed_salary(self, obj):
        return q2(obj.fixed_salary) if obj.fixed_salary is not None else None


class EmployeeSetPostSerializer(serializers.Serializer):
    post_id = serializers.IntegerField()

    def validate_post_id(self, value):
        post = Post.objects.filter(id=value, company=self.context['company']).first()
        if post is None:
            raise serializers.ValidationError('岗位不存在')
        self.post = post
        return value


class EmployeeSetGroupSerializer(serializers.Serializer):
    """组编号（1组/2组…）：必填正整数（非空字段，不支持清空）。"""
    group_no = serializers.IntegerField(required=True)

    def validate_group_no(self, value):
        if value < 1 or value > 999:
            raise serializers.ValidationError('组编号须为 1~999 的整数')
        return value


class EmployeeSetProcessesSerializer(serializers.Serializer):
    """指定工序保存（§9.1）：process_ids 须全部属于本公司，可为空列表（清空指定）。"""

    process_ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=True)

    def validate_process_ids(self, value):
        from apps.catalog.models import Process
        # 去重保序
        ids = list(dict.fromkeys(int(i) for i in value))
        found = set(Process.objects.filter(
            company=self.context['company'], id__in=ids).values_list('id', flat=True))
        missing = [i for i in ids if i not in found]
        if missing:
            raise serializers.ValidationError(f'工序不存在或不属于本公司（id: {missing}）')
        return ids


class InviteEmployeeSerializer(serializers.Serializer):
    """手机号邀请员工（§11.1：手机号邀请 + 录入姓名/岗位，岗位可空）。"""

    phone = serializers.CharField(max_length=20)
    real_name = serializers.CharField(max_length=30, required=False, allow_blank=True, default='')
    post_id = serializers.IntegerField(required=False, allow_null=True, default=None)

    def validate_phone(self, value):
        phone = value.strip()
        if not phone.isdigit() or len(phone) != 11:
            raise serializers.ValidationError('请输入正确的手机号')
        return phone

    def validate_real_name(self, value):
        return (value or '').strip()

    def validate_post_id(self, value):
        if value is None:
            return None
        post = Post.objects.filter(id=value, company=self.context['company']).first()
        if post is None:
            raise serializers.ValidationError('岗位不存在')
        self.post = post
        return value
