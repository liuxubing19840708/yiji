# -*- coding: utf-8 -*-
"""AI 报表问答端点（POST /ai/report/query）。

流程：登录成员 → 内容长度校验 → 可选 QueryPlan Planner → 同一权限/ORM 执行器；
未识别或模型不可用时回落旧五卡/自由问答。每问留痕 AiQueryLog，并做每日频次兜底。
"""
from django.conf import settings
from django.utils import timezone

from apps.core.mixins import CompanyContextMixin
from apps.core.permissions import has_perm
from rest_framework.exceptions import PermissionDenied, Throttled, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AiQueryLog
from .plan_views import PlanExecutionMixin
from . import planner, services

# 每人每日提问上限（防滥用刷 token 费用；正常查报表远达不到）
DAILY_LIMIT = 100


class AiReportQueryView(PlanExecutionMixin, CompanyContextMixin, APIView):
    """自然语言问报表；query_plan 模式优先走受治理动态报表。"""

    def post(self, request):
        question = str(request.data.get('question') or '').strip()
        if not question:
            raise ValidationError({'question': '请输入问题'})
        if len(question) > 200:
            raise ValidationError({'question': '问题太长（≤200 字）'})
        history = self._sanitize_history(request.data.get('history'))
        dynamic_mode = request.data.get('mode') == 'query_plan'

        # 频次兜底：当天已问次数
        today_cnt = AiQueryLog.objects.filter(user=request.user,
                                              created_at__date=timezone.localdate()).count()
        if today_cnt >= DAILY_LIMIT:
            raise Throttled(detail='今日提问已达上限，明天再来')

        # Web 新聊天显式 opt-in，旧客户端不传 mode，契约完全不变。
        if dynamic_mode:
            dynamic_response = self._query_plan(request, question, history)
            if dynamic_response is not None:
                return dynamic_response

        # 意图解析：本地关键词短路优先（零延迟零费用），GLM 兜模糊问法
        intent = services.local_intent(question) or services.parse_intent(question, history)
        report_key = (intent or {}).get('report_key') or ''
        valid = report_key in services._REPORT_BUILDERS or report_key == 'my_salary'
        date_range = (intent or {}).get('date_range') or 'today'

        # 白名单外 → 测试阶段自由问答（AI_FREE_MODE）：带数据快照交给 GLM 作答
        free = None
        free_meta = {}
        if not valid and not dynamic_mode and getattr(settings, 'AI_FREE_MODE', False):
            free = services.free_answer(question, self.company, request.user,
                                        self._is_admin(), history, date_range,
                                        meta=free_meta)
            if free:
                # 留痕记选块结果（排障用：错答可复现 GLM 路由了哪个块）
                intent = {'free': True, 'blocks': free_meta.get('blocks', []),
                          'cached': free_meta.get('cached', False)}

        ok = valid or bool(free)
        # 留痕（失败不阻塞回答）
        try:
            AiQueryLog.objects.create(company=self.company, user=request.user,
                                      question=question[:200], intent=intent, ok=ok)
        except Exception:
            pass

        if not ok:
            # 未配 key / 模型没听懂且自由问答失败 → 快捷问题兜底
            return Response({'fallback': True,
                             'quick_questions': services.QUICK_QUESTIONS})

        if not valid:
            return Response({'fallback': False, 'free': True, 'answer': free})

        # 个人薪酬恒为本人；其余为公司级聚合（与全员可看生产看板同口径）
        if report_key != 'my_salary' and not self._company_level_allowed():
            raise PermissionDenied('无权限查看公司级报表')

        report = services.build_report(report_key, self.company, request.user, date_range)
        return Response({'fallback': False, 'intent': intent, 'report': report})

    def _query_plan(self, request, question, history):
        """候选计划也走正式执行链路；权限/结构错误不得回落旧自由问答。"""
        previous_plan = None
        recent_logs = AiQueryLog.objects.filter(
            company=self.company, user=request.user, ok=True).order_by('-id')[:10]
        for log in recent_logs:
            if isinstance(log.intent, dict) and isinstance(
                    log.intent.get('query_plan'), dict):
                previous_plan = log.intent['query_plan']
                break
        candidate, meta = planner.plan_question(
            question, history, self.user_company, previous_plan=previous_plan)
        if candidate is None:
            return None

        query_log = None
        try:
            query_log = AiQueryLog.objects.create(
                company=self.company, user=request.user, question=question[:200],
                intent={'planner': meta, 'candidate_plan': candidate}, ok=False)
        except Exception:
            pass

        response = self.execute(request, 'preview', raw_plan_override=candidate)
        response.data['planner'] = meta
        response.data['fallback'] = False
        if query_log is not None:
            try:
                query_log.intent = {'planner': meta, 'query_plan': response.data['plan']}
                query_log.ok = True
                query_log.save(update_fields=['intent', 'ok'])
            except Exception:
                pass
        return response

    def _sanitize_history(self, raw):
        """对话历史清洗：最多 5 轮、每轮 ≤200 字、只留 user/ai 文本（防灌垃圾）。"""
        if not isinstance(raw, list):
            return []
        out = []
        for turn in raw[-5:]:
            if not isinstance(turn, dict):
                continue
            content = str(turn.get('content') or '')[:200]
            if content:
                out.append({'role': 'ai' if turn.get('role') == 'ai' else 'user',
                            'content': content})
        return out

    def _is_admin(self):
        """提问人在本厂是否管理员岗（决定自由问答是否携带他人明细数据）。"""
        from apps.accounts.models import UserCompany
        uc = (UserCompany.objects.filter(user=self.request.user, company=self.company)
              .select_related('post').first())
        return bool(uc and uc.post and uc.post.is_admin)

    def _company_level_allowed(self):
        """公司级报表门槛：与生产看板一致（在职成员均可看，管理员/员工不区分）。

        预留权限点开关：若公司后续要收窄，配置后仅持 salary.view / 生产管理类
        权限的岗位可见——当前默认全员可问（看板本身就是全员可见）。
        """
        return True
