"""扫菲登记视图：登记/撤销、工序选择器、扫菲工作台。"""
import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, F, Sum
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Process, StyleProcess, Ticket
from apps.core.mixins import CompanyContextMixin
from apps.core.pagination import DefaultPagination
from apps.core.permissions import has_perm
from apps.core.utils import q2, q3
from apps.ops.models import AuditLog, CompanySetting
from apps.ops.services import audit
from apps.records.models import Record, ScanIdempotency
from apps.records.serializers import RecordCreateSerializer, RecordSerializer


def _can_see_price(user_company) -> bool:
    """工价可见性（竞品对齐 GAP-6，2026-11-04）：工序 hide_price=True 时，
    仅持 styles.price（上价/改价）或 salary.view（薪酬查询）者可见单价，
    管理员岗位恒可见。登记/计件金额不受影响（走 _resolve_price 现价快照）。"""
    return (has_perm(user_company, 'styles.price')
            or has_perm(user_company, 'salary.view'))


def _purge_deleted(model, **filters):
    """同业务键存在软删行时会撞唯一约束，先物理清理。"""
    model.all_objects.filter(is_deleted=True, **filters).delete()


def _ticket_reg_map(company, ticket):
    """该菲票各工序累计登记数 {process_id: qty}。"""
    rows = (Record.objects.filter(company=company, ticket=ticket)
            .order_by().values('process_id').annotate(s=Sum('qty')))
    return {r['process_id']: r['s'] or 0 for r in rows}


def _derive_current_process(company, ticket, allowed, style_map):
    """工序流转推导（2026-10-21 需求；2026-10-22 兜底扩展）：返回第一个
    「登记数 < 扎数」的工序 (process_id, remaining)；无（全部完成/未配置）→ None。

    顺序口径：优先款式工序序（allow_skip=False 必过工序，sort 升序）；
    款式未配工序序（只配订单工序/全跳序）时回退工序档案 sort 在允许集内推导，
    让「扫码自动带出当前工序」对所有配置形态都生效。
    """
    seq = sorted((srt, pid) for pid, (srt, skip) in style_map.items()
                 if pid in allowed and not skip)
    if not seq:
        seq = [(p.sort, p.id) for p in Process.objects.filter(
            company=company, pk__in=allowed).order_by('sort', 'id')]
    if not seq:
        return None
    reg_map = _ticket_reg_map(company, ticket)
    for _srt, pid in seq:
        cur = reg_map.get(pid, 0)
        if cur < ticket.qty:
            return pid, ticket.qty - cur
    return None


def _resolve_price(ticket, process):
    """登记单价：床次工序价 > 款式工序价 > 工序默认价。"""
    from apps.production.models import ProductionProcess
    if ticket is not None:
        pp = ProductionProcess.objects.filter(
            production=ticket.production, process=process).first()
        if pp and pp.price:
            return q3(pp.price)
        sp = StyleProcess.objects.filter(
            style=ticket.production.style, process=process).first()
        if sp and sp.price:
            return q3(sp.price)
    return q3(process.default_price)


def _process_config(production):
    """床次的工序配置：(允许登记的工序id集合, 款式工序映射{pid:(sort, allow_skip)})。

    口径与完成判定一致：订单工序优先；床次未配置订单工序时回退款式工序；
    两者皆空（厂里未做任何配置）则不设限（保持旧行为，由 0 价拦截兜底）。
    """
    from apps.production.models import ProductionProcess
    allowed = set(ProductionProcess.objects.filter(
        production=production).values_list('process_id', flat=True))
    if not allowed:
        allowed = set(StyleProcess.objects.filter(
            style=production.style).values_list('process_id', flat=True))
    style_map = {sp.process_id: (sp.sort, sp.allow_skip)
                 for sp in StyleProcess.objects.filter(style=production.style)}
    return allowed, style_map


def _resolve_target_user(user_id):
    """user_id 参数解析目标员工；缺省返回 None（表示登记本人）。"""
    from apps.accounts.models import User
    if not user_id:
        return None
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        raise ValidationError({'user_id': '员工不存在'})


class IdempotencyConflict(APIException):
    status_code = 409
    default_detail = '同一幂等键已用于不同登记载荷，请保留原载荷重试或更换新键'
    default_code = 'idempotency_payload_conflict'


class RecordListCreateView(CompanyContextMixin, generics.ListCreateAPIView):
    """登记记录列表 + 新增登记。

    GET 过滤：user_id / ticket_id / process_id / date_from / date_to /
    settlement=unsettled（仅未结算）。
    """
    pagination_class = DefaultPagination
    serializer_class = RecordSerializer

    def get_queryset(self):
        qs = (Record.objects.filter(company=self.company)
              .select_related('ticket', 'ticket__production__style',
                              'process', 'user', 'operator'))
        params = self.request.query_params
        if params.get('user_id'):
            qs = qs.filter(user_id=params['user_id'])
        if params.get('ticket_id'):
            qs = qs.filter(ticket_id=params['ticket_id'])
        if params.get('process_id'):
            qs = qs.filter(process_id=params['process_id'])
        date_from = parse_date(params.get('date_from') or '')
        if date_from:
            qs = qs.filter(registered_at__date__gte=date_from)
        date_to = parse_date(params.get('date_to') or '')
        if date_to:
            qs = qs.filter(registered_at__date__lte=date_to)
        if params.get('settlement') == 'unsettled':
            qs = qs.filter(settlement__isnull=True)
        return qs

    def post(self, request, *args, **kwargs):
        serializer = RecordCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = (request.headers.get('Idempotency-Key') or '').strip()
        if not key:
            # 兼容未升级的客户端：没有幂等键时保留原有聚合登记行为。
            record = self._create_record(serializer.validated_data)
            return Response(RecordSerializer(record).data, status=201)
        if len(key) > 128:
            raise ValidationError({'idempotency_key': '幂等键长度不能超过 128 个字符'})
        # 重放也是当前操作人发起的登记请求，必须重新校验当前权限；
        # 不能因为曾经持有幂等键就绕过撤权后的访问控制。
        self.check_perm('records.create')
        target_user_id = serializer.validated_data.get('user_id')
        if target_user_id and target_user_id != request.user.id:
            self.check_perm('scan.assist')
        payload = json.dumps(serializer.validated_data, sort_keys=True,
                             separators=(',', ':'), ensure_ascii=False)
        payload_hash = hashlib.sha256(payload.encode('utf-8')).hexdigest()

        # 凭据、登记、成本 stale 与协助审计在同一事务中提交。并发撞唯一键时，
        # PostgreSQL 会等待首个事务结束；内层 savepoint 保证可继续读取并重放。
        with transaction.atomic():
            try:
                with transaction.atomic():
                    receipt = ScanIdempotency.objects.create(
                        company=self.company, key=key, payload_hash=payload_hash,
                        operator=request.user)
            except IntegrityError:
                receipt = ScanIdempotency.objects.select_for_update().get(
                    company=self.company, key=key)
                if receipt.operator_id != request.user.id:
                    raise PermissionDenied('不能使用其他操作人的登记幂等键')
                if receipt.payload_hash != payload_hash:
                    raise IdempotencyConflict()
                return Response(receipt.response_data, status=201)

            record = self._create_record(serializer.validated_data)
            # JSONRenderer 把 Decimal 等 DRF 类型转成可持久化的 JSON 原生类型。
            receipt.response_data = json.loads(
                JSONRenderer().render(RecordSerializer(record).data))
            receipt.record = record
            receipt.save(update_fields=['response_data', 'record'])
        return Response(receipt.response_data, status=201)

    def _create_record(self, data):
        from apps.accounts.models import UserCompany

        self.check_perm('records.create')  # Plan A：登记走 records.create 点
        try:
            ticket = (Ticket.objects.select_related('production', 'production__style')
                      .get(company=self.company, pk=data['ticket_id']))
        except Ticket.DoesNotExist:
            raise ValidationError({'ticket_id': '菲票不存在'})
        production = ticket.production
        if production.is_locked:
            raise ValidationError({'ticket_id': '床次已锁定，禁止登记'})
        # 2026-10-18 拍板：裁完=裁剪完成，裁剪之后的车缝/尾部等工序照常扫码，
        # is_cut_done 不再拦截登记（旧拦截已删）。

        try:
            process = Process.objects.get(company=self.company, pk=data['process_id'])
        except Process.DoesNotExist:
            raise ValidationError({'process_id': '工序不存在'})

        user = _resolve_target_user(data.get('user_id')) or self.request.user
        # 未审核成员禁止登记（设置开关下的二次保险）
        if CompanySetting.get_bool(self.company, 'forbid_unapproved_register'):
            if not UserCompany.objects.filter(
                    user=user, company=self.company,
                    status=UserCompany.Status.ACTIVE).exists():
                raise ValidationError({'user_id': '该员工未审核，禁止登记'})

        assist = user.id != self.request.user.id
        if assist:
            # Plan A：协助扫菲由「仅管理员」放开为权限点 scan.assist（管理员岗位恒通过）
            self.check_perm('scan.assist')
            # 协助登记硬拦截（2026-10-18 拍板）：目标员工必须是本公司在职成员，
            # 不再依赖 forbid_unapproved_register 开关
            if not UserCompany.objects.filter(
                    user=user, company=self.company,
                    status=UserCompany.Status.ACTIVE).exists():
                raise ValidationError(
                    {'user_id': '该员工不是本公司在职成员，禁止协助登记'})

        # 校验①：登记工序必须在床次工序集内（订单工序优先，回退款式工序）
        allowed, style_map = _process_config(production)
        if allowed and process.id not in allowed:
            raise ValidationError(
                {'process_id': f'工序【{process.name}】不在床次'
                               f'【{production.bed_no}】的工序配置中，禁止登记'})

        # 校验②：工序流转拦截（2026-10-21 需求强化）——款式必过工序（allow_skip=False）
        # 按 sort 顺序流转，前面的工序「登记满扎数」之前，后面工序一律拒绝（提示失败）。
        # 旧口径=「有登记记录即算完成」→「未满扎数即未完成」。
        if allowed and process.id in style_map:
            my_sort, allow_skip = style_map[process.id]
            if not allow_skip:
                reg_map = _ticket_reg_map(self.company, ticket)
                blockers = [pid for pid, (srt, skip) in style_map.items()
                            if pid in allowed and not skip and srt < my_sort
                            and reg_map.get(pid, 0) < ticket.qty]
                if blockers:
                    first = Process.objects.get(pk=sorted(blockers)[0])
                    done = reg_map.get(first.pk, 0)
                    raise ValidationError(
                        {'process_id': f'前道工序【{first.name}】未完成'
                                       f'（已登记 {done}/{ticket.qty} 件），'
                                       f'暂不能登记【{process.name}】'})

        qty = data.get('qty') or ticket.qty
        price = _resolve_price(ticket, process)
        if not price:
            # 痛点 #2（调研 §8.3）：0 元流水是员工差评根源——0 价工序合法存在
            # （质检等不计件），但不可被报工，杜绝「扫了也没钱」的静默登记
            raise ValidationError(
                {'price': f'工序【{process.name}】尚未配置工价，请先在工价管理中设置'})

        # 锁扎行串行化同扎并发登记，保证「累计封顶」与「增量累加」原子生效
        with transaction.atomic():
            locked = Ticket.objects.select_for_update().get(pk=ticket.pk)
            from apps.core.workflow_gates import enforce_manufacturing_gate
            enforce_manufacturing_gate(self.company, locked, self.request.user)
            # 校验③：同一扎同一工序的全部员工登记数量合计 ≤ 扎数（2026-10-18 拍板）
            cur = (Record.objects.filter(company=self.company, ticket=locked,
                                         process=process)
                   .aggregate(s=Sum('qty'))['s'] or 0)
            if cur + qty > locked.qty:
                raise ValidationError(
                    {'qty': f'登记数量超限：该扎该工序已登记 {cur} 件，'
                            f'本次 {qty} 件，超过扎数 {locked.qty} 件'})
            _purge_deleted(Record, company=self.company,
                           ticket=locked, process=process, user=user)
            # 增量补登（2026-10-18 拍板）：同扎同工序同人再次登记 = 累加进同一条
            # 记录（qty 累加、registered_at 更新为本次、单价沿用首次快照），
            # 「同票同工序同人唯一」约束保留，无需 DDL。
            existing = (Record.objects.select_for_update()
                        .filter(company=self.company, ticket=locked,
                                process=process, user=user).first())
            if existing:
                existing.qty = existing.qty + qty
                existing.registered_at = timezone.now()
                existing.operator = self.request.user if assist else None
                existing.save(update_fields=['qty', 'registered_at',
                                             'operator', 'updated_at'])
                record = existing
            else:
                try:
                    record = Record.objects.create(
                        company=self.company, ticket=locked, process=process, user=user,
                        operator=self.request.user if assist else None,
                        qty=qty, price=price, registered_at=timezone.now())
                except IntegrityError:
                    # 并发创建撞唯一约束：另一请求先建了行，转为增量累加
                    record = Record.objects.select_for_update().get(
                        company=self.company, ticket=locked,
                        process=process, user=user)
                    record.qty += qty
                    record.registered_at = timezone.now()
                    record.operator = self.request.user if assist else None
                    record.save(update_fields=['qty', 'registered_at',
                                               'operator', 'updated_at'])

        # P1 成本域 stale 埋点②（登记）：菲票所属床次的挂单订单成本翻 stale
        from apps.cost.services import mark_ticket_stale
        mark_ticket_stale(self.company, locked)

        if assist:
            style = ticket.production.style
            audit(self.company, AuditLog.Type.ASSIST, operator=self.request.user,
                  target_user=user, bed_no=production.bed_no,
                  style_no=style.style_no, bundle_no=ticket.bundle_no,
                  message=(f'协助登记：【床次{production.bed_no}】'
                           f'【款式编号：{style.style_no}】'
                           f'【扎号{ticket.bundle_no}】【用户：{user}】'
                           f'操作人：{self.request.user}'))
        return record


def _revoke_one(view, instance):
    """撤销单条登记：门禁 + 删除 + 留痕 + 成本 stale（单撤与批量撤销共用）。

    门禁口径：本人当日可撤；他人/跨日需 records.revoke 点（管理员岗位恒通过）；
    revoke_current_month_only 开关生效；已结算不可撤。
    """
    company, request = view.company, view.request
    is_admin = view.user_company.post.is_admin
    if instance.user_id == request.user.id:
        if (not is_admin
                and timezone.localtime(instance.registered_at).date()
                != timezone.localdate()):
            raise ValidationError('仅可撤销本人当日登记')
    else:
        # Plan A：撤销他人登记走权限点 records.revoke（旧 coarse key 不覆盖，管理员岗位恒通过）
        view.check_perm('records.revoke')

    if CompanySetting.get_bool(company, 'revoke_current_month_only'):
        reg = timezone.localtime(instance.registered_at)
        now = timezone.localtime()
        if (reg.year, reg.month) != (now.year, now.month):
            raise ValidationError('只能撤销当月登记')
    if instance.settlement_id:
        raise ValidationError('该登记已结算，不可撤销')

    ticket = instance.ticket
    instance.delete()
    # P1 成本域 stale 埋点②（撤销）：菲票所属床次的挂单订单成本翻 stale
    from apps.cost.services import mark_ticket_stale
    mark_ticket_stale(company, ticket)
    audit(company, AuditLog.Type.REVOKE, operator=request.user,
          target_user=instance.user, bed_no=ticket.production.bed_no,
          style_no=ticket.production.style.style_no,
          bundle_no=ticket.bundle_no,
          message=(f'撤销登记：【床次{ticket.production.bed_no}】'
                   f'【扎号{ticket.bundle_no}】操作人：{request.user}'))


class RecordDetailView(CompanyContextMixin, generics.DestroyAPIView):
    """撤销登记：本人当日可撤；管理员可撤任意；已结算/非当月受限。

    增量补登（2026-10-18）下的撤销口径：一次撤销 = 整条记录扣减（软删该行，
    该员工该扎该工序的累计件数与金额全部回退）。不做部分扣减——累计行不存
    历史增量，且 DDL 变更未获授权；如需部分撤销，管理员可先撤整条再按正确
    数量重新登记（单价沿用工序现价解析，与首次登记口径一致）。
    """

    def get_object(self):
        try:
            return (Record.objects.select_related(
                'ticket', 'ticket__production', 'ticket__production__style',
                'process', 'user').get(company=self.company,
                                       pk=self.kwargs['pk']))
        except Record.DoesNotExist:
            raise Http404('登记记录不存在')

    def perform_destroy(self, instance):
        # 门禁+删除+留痕抽至 _revoke_one（批量撤销共用，口径不漂移）
        _revoke_one(self, instance)

    # ============ 修改数量填原因（竞品对齐 GAP-3，2026-11-04） ============

    def put(self, request, pk):
        """PUT /records/{id} {qty, reason}：修改登记数量，原因必填（留痕）。

        用 PUT 而非 PATCH：wx.request 运行时不支持 PATCH（utils/request.ts）。
        门禁与撤销同口径：本人当日可改；他人/跨日需 records.revoke 点；
        已结算不可改；revoke_current_month_only 开关同样生效。
        AuditLog Type.QTY 留痕（改前/改后 + 原因），成本 stale 联动。
        """
        instance = self.get_object()
        is_admin = self.user_company.post.is_admin
        if instance.user_id == request.user.id:
            if (not is_admin
                    and timezone.localtime(instance.registered_at).date()
                    != timezone.localdate()):
                raise ValidationError('仅可修改本人当日登记')
        else:
            self.check_perm('records.revoke')
        if CompanySetting.get_bool(self.company, 'revoke_current_month_only'):
            reg = timezone.localtime(instance.registered_at)
            now = timezone.localtime()
            if (reg.year, reg.month) != (now.year, now.month):
                raise ValidationError('只能修改当月登记')
        if instance.settlement_id:
            raise ValidationError('该登记已结算，不可修改')

        reason = str(request.data.get('reason') or '').strip()
        if not reason:
            raise ValidationError({'reason': '修改数量必须填写原因'})
        try:
            qty = int(request.data.get('qty'))
        except (TypeError, ValueError):
            raise ValidationError({'qty': '数量必须为正整数'})
        if qty <= 0:
            raise ValidationError({'qty': '数量必须为正整数'})

        ticket = instance.ticket
        if ticket.production.is_locked:
            raise ValidationError('床次已锁定，禁止修改登记')
        # 封顶口径同登记：该扎该工序全员累计 ≤ 扎数
        with transaction.atomic():
            cur = (Record.objects.filter(company=self.company, ticket=ticket,
                                         process=instance.process)
                   .aggregate(s=Sum('qty'))['s'] or 0)
            if cur - instance.qty + qty > ticket.qty:
                raise ValidationError(
                    {'qty': f'修改后超限：该扎该工序已登记 {cur} 件（含本条 '
                            f'{instance.qty} 件），改为 {qty} 件将超过扎数 '
                            f'{ticket.qty} 件'})
            before = instance.qty
            instance.qty = qty
            instance.save(update_fields=['qty', 'updated_at'])

        from apps.cost.services import mark_ticket_stale
        mark_ticket_stale(self.company, ticket)
        audit(self.company, AuditLog.Type.QTY, operator=request.user,
              target_user=instance.user, bed_no=ticket.production.bed_no,
              style_no=ticket.production.style.style_no,
              bundle_no=ticket.bundle_no,
              message=(f'修改登记数量：【床次{ticket.production.bed_no}】'
                       f'【扎号{ticket.bundle_no}】'
                       f'{before} 件 → {qty} 件，原因：{reason}；'
                       f'操作人：{request.user}'))
        return Response(RecordSerializer(instance).data)


class ProcessSelectorView(CompanyContextMixin, APIView):
    """扫菲工序选择器：固定工序 ∪ 员工指定工序；单选项前端可免选登记。"""

    def get(self, request):
        from apps.accounts.models import UserCompany

        ticket = None
        if request.query_params.get('ticket_id'):
            try:
                ticket = (Ticket.objects
                          .select_related('production', 'production__style')
                          .get(company=self.company,
                               pk=request.query_params['ticket_id']))
            except Ticket.DoesNotExist:
                raise ValidationError({'ticket_id': '菲票不存在'})

        user = _resolve_target_user(request.query_params.get('user_id')) \
            or request.user
        try:
            uc = UserCompany.objects.get(company=self.company, user=user)
            assigned_ids = uc.assigned_processes or []
        except UserCompany.DoesNotExist:
            assigned_ids = []

        proc_map = {}
        for p in Process.objects.filter(company=self.company, is_fixed=True):
            proc_map[p.id] = p
        for p in Process.objects.filter(company=self.company, pk__in=assigned_ids):
            proc_map.setdefault(p.id, p)
        # P0 满扎提示（§2.7.1）：每工序带「当前累计/扎数」（如 96/100），工人先看再登。
        # registered_qty = 该扎该工序全员累计登记（非仅本人）；无票上下文时为 0。
        reg_map = {}
        if ticket is not None:
            rows = (Record.objects.filter(company=self.company, ticket=ticket)
                    .order_by().values('process_id')
                    .annotate(s=Sum('qty')))
            reg_map = {r['process_id']: r['s'] or 0 for r in rows}
        # cap = 扎数上限；无票上下文或 0 扎（无限扎口径，§3.3 SCAN-031）→ null
        cap = ticket.qty if ticket is not None and ticket.qty else None
        show_price = _can_see_price(self.user_company)
        results = [{'id': p.id, 'name': p.name, 'is_fixed': p.is_fixed,
                    'price': (str(_resolve_price(ticket, p))
                              if (show_price or not p.hide_price) else None),
                    'price_hidden': bool(p.hide_price) and not show_price,
                    'registered_qty': reg_map.get(p.id, 0),
                    'cap': cap}
                   for p in proc_map.values()]
        # 工序流转推导（2026-10-21 需求）：带票上下文时下发当前应登记工序，
        # 前端据此免选直达 + 件数固定不可改；无款式序配置/全部完成 → null 走旧交互
        current = None
        if ticket is not None and ticket.qty:
            allowed, style_map = _process_config(ticket.production)
            if allowed:
                cur = _derive_current_process(self.company, ticket, allowed, style_map)
                if cur:
                    pid, remaining = cur
                    p = Process.objects.filter(company=self.company, pk=pid).first()
                    current = {'id': pid, 'name': p.name if p else '',
                               'remaining': remaining}
        return Response({'results': results, 'single': len(results) == 1,
                         'current_process': current})


class TicketLookupView(CompanyContextMixin, APIView):
    """手输码兜底查询（§2.7.2）：床次号+扎号精确定位候选菲票。

    - 权限复用 records.create（不新增权限点，2026-10-18 拍板）；
    - 限本公司（跨厂一律查不到 → 404，与扫码口径一致）；
    - bed_no / bundle_no 精确匹配，二者至少传一个（全缺 → 400）；
      只传其一即得候选列表（同床多扎/同扎号跨床）；
    - 0 命中 → 404；命中 → {'results': [...], 'count': n}。
    """

    def get(self, request):
        self.check_perm('records.create')
        bed_no = (request.query_params.get('bed_no') or '').strip()
        bundle_no = (request.query_params.get('bundle_no') or '').strip()
        if not bed_no and not bundle_no:
            raise ValidationError(
                {'bed_no': '床次号与扎号至少填写一项'})

        qs = (Ticket.objects.filter(company=self.company)
              .select_related('production', 'production__style'))
        if bed_no:
            qs = qs.filter(bed_no=bed_no)
        if bundle_no:
            qs = qs.filter(bundle_no=bundle_no)
        tickets = list(qs.order_by('bed_no', 'id'))
        if not tickets:
            raise Http404('未找到匹配的菲票')

        results = []
        for t in tickets:
            prod = t.production
            # 菲票状态：随床次派生（锁定/已裁完归档/进行中），登记可行性看 locked
            if prod.is_locked:
                status, status_display = 'locked', '已锁定'
            elif prod.is_cut_done:
                status, status_display = 'cut_done', '已裁完'
            else:
                status, status_display = 'normal', '进行中'
            results.append({
                'id': t.id, 'bed_no': t.bed_no, 'bundle_no': t.bundle_no,
                'style_id': prod.style_id,
                'style_no': prod.style.style_no, 'style_name': prod.style.name,
                'color': t.color, 'size': t.size, 'qty': t.qty,
                # 2026-10-29 与 /scan/parse 同构补齐：床次 id 供手输码流程
                # 与扫码一致启用「查看菲票/补打」入口（此前前端恒 prodId=0 禁用）
                'production_id': prod.id,
                'status': status, 'status_display': status_display,
            })
        return Response({'results': results, 'count': len(results)})


class BundleProgressView(CompanyContextMixin, APIView):
    """扎级完成矩阵（2026-10-22 需求：订单/床次矩阵显示床次+扎信息，完工扎显示参与者）。

    - production_ids 逗号分隔（订单详情逐床传入，上限 50 防失控）；
    - done/state 口径唯一：distinct 已登记工序数 ≥ 配置工序数（stats.py 口径，
      pc=0 不算完工），与 ProductionBundleListView 一致；
    - users：该扎有登记的员工去重，附各自累计件数（件数降序），完工扎前端展示「谁做的」。
    """

    MAX_PRODUCTIONS = 50

    def get(self, request):
        from django.db.models import Count

        from apps.production.stats import production_pc_map

        raw = (request.query_params.get('production_ids') or '').strip()
        if not raw:
            raise ValidationError({'production_ids': '床次ID不能为空'})
        try:
            pids = [int(x) for x in raw.split(',') if x.strip()]
        except ValueError:
            raise ValidationError({'production_ids': '床次ID格式不正确'})
        pids = list(dict.fromkeys(pids))[:self.MAX_PRODUCTIONS]
        if not pids:
            raise ValidationError({'production_ids': '床次ID不能为空'})

        tickets = (Ticket.objects.filter(company=self.company, production_id__in=pids)
                   .order_by('bed_no', 'id'))
        pc_map = production_pc_map(pids)

        # order_by() 必须前置：Record.Meta.ordering 引用菲票字段会混入 GROUP BY
        pn_map = {}
        for row in (Record.objects.filter(company=self.company,
                                          ticket__production_id__in=pids)
                    .order_by().values('ticket_id')
                    .annotate(pn=Count('process_id', distinct=True), qty=Sum('qty'))):
            pn_map[row['ticket_id']] = row
        users_map = {}
        for row in (Record.objects.filter(company=self.company,
                                          ticket__production_id__in=pids)
                    .order_by().values('ticket_id', 'user_id', 'user__real_name')
                    .annotate(qty=Sum('qty'))):
            users_map.setdefault(row['ticket_id'], []).append({
                'id': row['user_id'],
                'name': row['user__real_name'] or '',
                'qty': int(row['qty'] or 0),
            })
        for lst in users_map.values():
            lst.sort(key=lambda u: -u['qty'])

        results = []
        for t in tickets:
            row = pn_map.get(t.id)
            pn = int(row['pn']) if row else 0
            reg_qty = int(row['qty'] or 0) if row else 0
            pc = pc_map.get(t.production_id) or 0
            done = bool(pc and pn >= pc)
            if done:
                state, state_display = 'done', '完成'
            elif pn:
                state, state_display = 'partial', '部分登记'
            else:
                state, state_display = 'none', '未登记'
            results.append({
                'ticket_id': t.id, 'production_id': t.production_id,
                'bed_no': t.bed_no, 'bundle_no': t.bundle_no,
                'color': t.color, 'size': t.size, 'qty': t.qty,
                'registered_qty': reg_qty,
                'registered_process_count': pn, 'process_count': pc,
                'state': state, 'state_display': state_display,
                'done': done,
                'users': users_map.get(t.id, []),
            })
        return Response({'count': len(results), 'results': results})


class WorkbenchView(CompanyContextMixin, APIView):
    """扫菲工作台：进行中床次进度 + 本人今日登记。"""

    def get(self, request):
        from apps.production.models import Production
        from apps.production.stats import bed_progress_stats

        productions = (Production.objects.filter(company=self.company,
                                                 is_cut_done=False)
                       .select_related('style').order_by('-id'))
        pids = [p.id for p in productions]
        # 口径唯一（§2.9 stats.py）：床次进度（登记件数/完工扎数）统一调
        # bed_progress_stats（完工=distinct 工序数≥配置工序数，配置工序数=0
        # 不产生完工扎——统一后的行为，与床次列表/dashboard 同口径）。
        # 返回字段不增不减（my_today_amount 归 SA3，勿动）。
        progress = bed_progress_stats(self.company, pids)

        beds = [{'id': p.id, 'bed_no': p.bed_no,
                 'style_no': p.style.style_no, 'style_name': p.style.name,
                 'total_qty': p.total_qty,
                 'registered_qty': progress.get(p.id, (0, 0, 0, 0.0))[0],
                 'done_ticket_count': progress.get(p.id, (0, 0, 0, 0.0))[1]}
                for p in productions]

        my_today = (Record.objects.filter(company=self.company,
                                          user=request.user,
                                          registered_at__date=timezone.localdate())
                    .select_related('ticket', 'ticket__production__style',
                                    'process')[:50])
        # P0 常驻「今日预估工资」（§2.8）：本人今日 Σqty×price，两位小数字符串
        # （与结算单 amount 等 salary 工资字段同口径）。不受 hide_scan_price 影响
        # ——该开关只管看别人的单价，本人自己的预估工资恒返回。
        my_amount = (Record.objects.filter(
            company=self.company, user=request.user,
            registered_at__date=timezone.localdate())
            .aggregate(a=Sum(F('qty') * F('price'),
                             output_field=DecimalField(max_digits=12,
                                                       decimal_places=3)))['a'] or 0)
        return Response({'beds': beds,
                         'my_today': RecordSerializer(my_today, many=True).data,
                         'my_today_amount': f'{q2(my_amount):.2f}'})


class BatchRegisterView(RecordListCreateView):
    """批量协助登记（竞品对齐 GAP-2，2026-11-04）：选员工 → 选工序 → 多扎一次登记。

    POST /records/batch-register
      {process_id, user_id?, entries: [{ticket_id, qty?}, ...≤50]}
    - 逐扎复用单条登记口径（_create_record：工序集/流转/封顶/增量补登/协助门禁
      全同单条），单扎失败不影响其余（部分成功语义，按扎独立校验）；
    - 返回 {'total','ok','fail','results':[{ticket_id,bundle_no,ok,error,record_id,qty,amount}]}。
    """

    MAX_ENTRIES = 50

    def post(self, request, *args, **kwargs):
        entries = request.data.get('entries')
        if not isinstance(entries, list) or not entries:
            raise ValidationError({'entries': 'entries 不能为空'})
        if len(entries) > self.MAX_ENTRIES:
            raise ValidationError({'entries': f'单次最多登记 {self.MAX_ENTRIES} 扎'})
        process_id = request.data.get('process_id')
        if not process_id:
            raise ValidationError({'process_id': '工序不能为空'})
        user_id = request.data.get('user_id')
        results, ok = [], 0
        for e in entries:
            tid = e.get('ticket_id') if isinstance(e, dict) else None
            if not tid:
                results.append({'ticket_id': None, 'ok': False,
                                'error': 'ticket_id 缺失'})
                continue
            data = {'ticket_id': tid, 'process_id': process_id,
                    'qty': e.get('qty') if isinstance(e, dict) else None}
            if user_id:
                data['user_id'] = user_id
            try:
                record = self._create_record(data)
                ok += 1
                results.append({'ticket_id': tid,
                                'bundle_no': record.ticket.bundle_no,
                                'ok': True, 'record_id': record.id,
                                'qty': record.qty,
                                'amount': str(q2(record.qty * record.price))})
            except ValidationError as exc:
                results.append({'ticket_id': tid, 'ok': False,
                                'error': self._flat_error(exc)})
            except PermissionDenied as exc:
                results.append({'ticket_id': tid, 'ok': False,
                                'error': str(exc.detail if exc.detail else exc)})
            except Http404 as exc:
                results.append({'ticket_id': tid, 'ok': False,
                                'error': str(exc)})
        return Response({'total': len(entries), 'ok': ok,
                         'fail': len(entries) - ok, 'results': results})

    @staticmethod
    def _flat_error(exc) -> str:
        detail = getattr(exc, 'detail', None)
        if isinstance(detail, dict):
            return '；'.join(
                f'{k}：{v[0] if isinstance(v, list) else v}' for k, v in detail.items())
        return str(detail or exc)


class BatchUndoView(CompanyContextMixin, APIView):
    """批量撤销（竞品对齐 GAP-2，2026-11-04）：POST /records/batch-undo。

    {record_ids: [...≤50]}；逐条复用单撤门禁（_revoke_one：records.revoke/
    本人当日/当月开关/已结算拦截），部分成功语义；返回 per-id 结果。
    """

    MAX_IDS = 50

    def post(self, request):
        raw = request.data.get('record_ids')
        if not isinstance(raw, list) or not raw:
            raise ValidationError({'record_ids': 'record_ids 不能为空'})
        try:
            ids = [int(x) for x in raw]
        except (TypeError, ValueError):
            raise ValidationError({'record_ids': 'record_ids 须为 ID 数组'})
        if len(ids) > self.MAX_IDS:
            raise ValidationError({'record_ids': f'单次最多撤销 {self.MAX_IDS} 条'})
        results, ok = [], 0
        for rid in ids:
            try:
                record = (Record.objects.select_related(
                    'ticket', 'ticket__production', 'ticket__production__style',
                    'process', 'user').get(company=self.company, pk=rid))
            except Record.DoesNotExist:
                results.append({'record_id': rid, 'ok': False,
                                'error': '登记记录不存在'})
                continue
            try:
                _revoke_one(self, record)
                ok += 1
                results.append({'record_id': rid, 'ok': True})
            except ValidationError as exc:
                results.append({'record_id': rid, 'ok': False,
                                'error': str(exc)})
            except PermissionDenied as exc:
                results.append({'record_id': rid, 'ok': False,
                                'error': str(exc.detail if exc.detail else exc)})
        return Response({'total': len(ids), 'ok': ok,
                         'fail': len(ids) - ok, 'results': results})


class RecordExportView(CompanyContextMixin, APIView):
    """计件明细导出 xlsx（竞品对齐 GAP-7，2026-11-04）：GET /records/export。

    筛选参数与登记列表完全同口径（user_id/ticket_id/process_id/date_from/
    date_to/settlement=unsettled）；权限 salary.view（导出他人计件属薪酬查看域）。
    上限 20000 行防失控；小程序端 wx.downloadFile + openDocument 打开。
    """

    MAX_ROWS = 20000

    def get(self, request):
        self.check_perm('salary.view')
        qs = (Record.objects.filter(company=self.company)
              .select_related('ticket', 'ticket__production__style',
                              'process', 'user', 'operator'))
        params = request.query_params
        if params.get('user_id'):
            qs = qs.filter(user_id=params['user_id'])
        if params.get('ticket_id'):
            qs = qs.filter(ticket_id=params['ticket_id'])
        if params.get('process_id'):
            qs = qs.filter(process_id=params['process_id'])
        date_from = parse_date(params.get('date_from') or '')
        if date_from:
            qs = qs.filter(registered_at__date__gte=date_from)
        date_to = parse_date(params.get('date_to') or '')
        if date_to:
            qs = qs.filter(registered_at__date__lte=date_to)
        if params.get('settlement') == 'unsettled':
            qs = qs.filter(settlement__isnull=True)

        headers = ['日期', '员工', '款号', '床次', '扎号', '颜色', '尺码',
                   '工序', '数量', '单价', '金额', '操作人']
        rows = []
        for r in qs.order_by('-registered_at')[:self.MAX_ROWS]:
            rows.append([
                timezone.localtime(r.registered_at).strftime('%Y-%m-%d %H:%M'),
                str(r.user or ''),
                r.ticket.production.style.style_no,
                r.ticket.production.bed_no,
                r.ticket.bundle_no,
                r.ticket.color,
                r.ticket.size,
                r.process.name,
                r.qty,
                str(r.price),
                str(q2(r.qty * r.price)),
                str(r.operator or ''),
            ])
        from apps.core.export import xlsx_response
        return xlsx_response(headers, rows, '计件明细')
