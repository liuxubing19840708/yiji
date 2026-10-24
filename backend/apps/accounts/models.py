"""用户、公司、岗位、公司成员、短信验证码。"""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class User(AbstractUser):
    """用户：以手机号登录；微信用户手机号可为空、以 openid 关联。"""

    GENDERS = ((0, '未知'), (1, '男'), (2, '女'))

    username = models.CharField('用户名', max_length=64, unique=True)
    phone = models.CharField('手机号', max_length=20, unique=True, null=True, blank=True)
    real_name = models.CharField('姓名', max_length=30, blank=True, default='')
    avatar = models.ImageField('头像', upload_to='avatars/', blank=True, null=True)
    gender = models.SmallIntegerField('性别', choices=GENDERS, default=0)
    birthday = models.DateField('生日', null=True, blank=True)
    openid = models.CharField('微信openid', max_length=64, unique=True, null=True, blank=True)

    USERNAME_FIELD = 'phone'
    REQUIRED_FIELDS = ['username']

    class Meta:
        db_table = 'user'
        verbose_name = '用户'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.real_name or self.phone or self.username


class Company(BaseModel):
    """公司。company_no 从 100001 起自增（按当前最大值 +1 生成）。"""

    class Plan(models.TextChoices):
        """版本套餐（2026-10-17：员工容量按套餐限制，容量随套餐开通/变更同步写 capacity）。"""

        TRIAL = 'trial', '体验版'
        BASIC = 'basic', '基础版'
        STANDARD = 'standard', '标准版'
        FLAGSHIP = 'flagship', '旗舰版'

    # 各版本员工容量上限（审批/邀请/复职校验统一读 capacity，不直接读套餐）
    PLAN_CAPACITY = {Plan.TRIAL: 20, Plan.BASIC: 50, Plan.STANDARD: 150, Plan.FLAGSHIP: 500}

    name = models.CharField('公司名称', max_length=60)
    city = models.CharField('城市', max_length=30, blank=True, default='')
    company_no = models.CharField('公司编号', max_length=12, unique=True)
    plan = models.CharField('版本套餐', max_length=16, choices=Plan.choices,
                            default=Plan.BASIC, db_index=True)
    capacity = models.IntegerField('员工容量上限', default=50)
    creator = models.ForeignKey(User, verbose_name='创建人', on_delete=models.PROTECT,
                                related_name='created_companies')

    class Meta:
        db_table = 'company'
        verbose_name = '公司'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f'{self.name}({self.company_no})'

    @classmethod
    def next_company_no(cls) -> str:
        max_no = cls.all_objects.aggregate(m=models.Max('company_no'))['m']
        base = int(max_no) if max_no and max_no.isdigit() else 100000
        return str(base + 1)


class Post(BaseModel):
    """岗位：permissions 为模块 key 列表，'*' 表示全部。"""

    company = models.ForeignKey(Company, verbose_name='公司', on_delete=models.CASCADE,
                                related_name='posts')
    name = models.CharField('岗位名称', max_length=30)
    permissions = models.JSONField('权限列表', default=list, blank=True)
    is_admin = models.BooleanField('是否管理员岗位', default=False)
    is_system = models.BooleanField('是否系统内置岗位', default=False)

    class Meta:
        db_table = 'post'
        verbose_name = '岗位'
        verbose_name_plural = verbose_name
        constraints = [
            # 仅约束在职岗位：软删（is_deleted）后允许重建同名岗位，否则撞库报 500
            models.UniqueConstraint(fields=['company', 'name'], condition=Q(is_deleted=False),
                                    name='uniq_post_company_name'),
        ]

    def __str__(self):
        return f'{self.company_id}:{self.name}'


class UserCompany(BaseModel):
    """公司成员关系：一个用户在一家公司一条记录、一个岗位。"""

    class Status(models.IntegerChoices):
        PENDING = 0, '待审核'
        ACTIVE = 1, '在职'
        RESIGNED = 2, '离职'

    class FixedType(models.TextChoices):
        DAY = 'day', '按天'
        MONTH = 'month', '按月'

    class SalaryType(models.TextChoices):
        """工资计算方式（2026-10-23 拍板：每人四选一，互斥）。"""
        FIXED = 'fixed', '固定工资'
        WHOLE = 'whole', '整件计件'
        PROCESS = 'process', '工序计件'
        COMMISSION = 'commission', '计件提成'

    user = models.ForeignKey(User, verbose_name='用户', on_delete=models.CASCADE,
                             related_name='company_links')
    company = models.ForeignKey(Company, verbose_name='公司', on_delete=models.CASCADE,
                                related_name='members')
    post = models.ForeignKey(Post, verbose_name='岗位', on_delete=models.PROTECT,
                             related_name='members', null=True, blank=True)
    status = models.SmallIntegerField('状态', choices=Status.choices, default=Status.PENDING)
    is_pinned = models.BooleanField('是否置顶', default=False)
    hired_date = models.DateField('入职日期', null=True, blank=True)
    is_suspended = models.BooleanField('是否停职', default=False)
    fixed_salary = models.DecimalField('固定工资', max_digits=10, decimal_places=2,
                                       null=True, blank=True)
    fixed_type = models.CharField('固定工资类型', max_length=10, choices=FixedType.choices,
                                  default=FixedType.MONTH)
    salary_type = models.CharField(
        '工资计算方式', max_length=12, choices=SalaryType.choices,
        default=SalaryType.PROCESS,
        help_text='四选一互斥：fixed 固定 / whole 整件计件 / process 工序计件 / '
                  'commission 计件提成（口径详见 apps/salary/wage.py 模块头）')
    piece_rate = models.DecimalField(
        '整件单价', max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='salary_type=whole 时生效：完成整件数（合作均摊）×该单价')
    commission_rate = models.DecimalField(
        '计件提成比例%', max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='salary_type=commission 时生效：本组期间计件总额×比例%')
    assigned_processes = models.JSONField('指定工序ID列表', default=list, blank=True,
                                          help_text='扫菲工序选择器 = 固定工序 ∪ 本列表工序')
    group_no = models.PositiveIntegerField('组编号', default=1,
                                           help_text='生产组编号（1组/2组…），非空，排单展示用')
    last_used_at = models.DateTimeField('最近使用时间', null=True, blank=True)

    class Meta:
        db_table = 'user_company'
        verbose_name = '公司成员'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=['user', 'company'], condition=Q(is_deleted=False),
                                    name='uniq_user_company'),
        ]

    def __str__(self):
        return f'{self.user_id}@{self.company_id}:{self.get_status_display()}'


class SMSCode(BaseModel):
    """短信验证码：60 秒内不可重发，每天每号最多 20 条。"""

    phone = models.CharField('手机号', max_length=20, db_index=True)
    code = models.CharField('验证码', max_length=6)
    expires_at = models.DateTimeField('过期时间')
    used_at = models.DateTimeField('使用时间', null=True, blank=True)

    class Meta:
        db_table = 'sms_code'
        verbose_name = '短信验证码'
        verbose_name_plural = verbose_name
