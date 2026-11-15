# -*- coding: utf-8 -*-
"""
批量核销（test_finance_batch）端到端后端验证 —— 评审 Top10 #5（2026-11-02）。

覆盖 POST /finance/customers/{customer_id}/receipts-batch 全口径：
  端点  FB-API-01~08（401/403/客户404/跨厂404/金额校验/FIFO 分配/
                      partial 续收至 settled/响应字段形状）
  红线  FB-RED-01~02（溢收款 400 硬拦含超出明细/正常分配无 leftover）
  留痕  FB-AUD-01~02（逐行 ReceiptLine/状态流转 settled）

独立工厂「财批测试厂(可删)/财批B厂(可删)」：与 test_finance.py 工厂分离，
避免套件互相 preclean 踩数据（可并行跑）。
跑法：manage.py shell -c "exec(compile(open('test_finance_batch.py',encoding='utf-8').read(),'test_finance_batch.py','exec'))"
幂等：自建厂跨次复用（Company.creator PROTECT 不可删）；每轮 preclean 硬清
本厂业务行；绝不触碰真实厂（company_id=86 等）。
"""
import json
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.test import Client

from apps.accounts.models import Company, Post as PostM, User, UserCompany
from apps.accounts.services import create_company_with_owner
from apps.catalog.models import Customer
from apps.finance.models import Receivable, ReceiptLine
from apps.finance.services import add_receipt, create_receivable

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('PASS' if cond else 'FAIL'), name, ('' if cond else f'| {detail}'))


def api(method, path, token=None, cid=None, data=None):
    c = Client()
    extra = {}
    if token:
        extra['HTTP_AUTHORIZATION'] = f'Bearer {token}'
    if cid:
        extra['HTTP_X_COMPANY_ID'] = str(cid)
    fn = getattr(c, method.lower())
    kw = {'content_type': 'application/json', **extra}
    r = fn(f'/api{path}', data=json.dumps(data) if data is not None else None, **kw)
    try:
        body = json.loads(r.content.decode('utf-8'))
    except Exception:
        body = {}
    return r.status_code, body


def login(phone, password='yisao1234'):
    _, d = api('POST', '/auth/login', data={'phone': phone, 'password': password})
    return d.get('access', '')


def ensure_user(phone, name):
    u = User.objects.filter(phone=phone).first()
    if u is None:
        u = User.objects.create(phone=phone, username=phone, real_name=name,
                                password=make_password('yisao1234'), is_active=True)
    return u


def ensure_post(company, name, perms):
    p = PostM.objects.filter(company=company, name=name, is_deleted=False).first()
    if p is None:
        p = PostM.objects.create(company=company, name=name, permissions=perms)
    else:
        PostM.objects.filter(pk=p.pk).update(permissions=perms)
        p.refresh_from_db()
    return p


def ensure_member(u, company, post):
    uc = UserCompany.all_objects.filter(user=u, company=company).first()
    if uc is None:
        uc = UserCompany.objects.create(user=u, company=company, post=post,
                                        status=UserCompany.Status.ACTIVE)
    uc.is_deleted = False
    uc.status = UserCompany.Status.ACTIVE
    uc.post = post
    uc.save()
    return uc


OWNER, H_PERM, H_VIEW, H_OWNER_B = (
    '13988713101', '13988713102', '13988713103', '13988713104')
CO_A, CO_B = '财批测试厂(可删)', '财批B厂(可删)'


def preclean(company):
    """幂等自清扫：硬清本厂业务行（流水→应收→审计→客户）。"""
    ReceiptLine.all_objects.filter(company=company).delete()
    Receivable.all_objects.filter(company=company).delete()
    from apps.ops.models import AuditLog
    AuditLog.objects.filter(company=company).delete()
    Customer.all_objects.filter(company=company).delete()


# ================= 建厂（复用） + preclean =================
company = Company.objects.filter(name=CO_A).first()
if company is None:
    owner = ensure_user(OWNER, '财批厂长')
    company, _ = create_company_with_owner(owner, CO_A)
else:
    owner = ensure_user(OWNER, '财批厂长')
company_b = Company.objects.filter(name=CO_B).first()
if company_b is None:
    owner_b = ensure_user(H_OWNER_B, '财批B厂长')
    company_b, _ = create_company_with_owner(owner_b, CO_B)
else:
    owner_b = ensure_user(H_OWNER_B, '财批B厂长')
CID, CID_B = company.id, company_b.id

preclean(company)
preclean(company_b)

post_view = ensure_post(company, '财批查看岗', ['finance.view'])
post_perm = ensure_post(company, '财批管理岗', ['finance.view', 'finance.manage'])
perm_u = ensure_user(H_PERM, '财批管理员')
view_u = ensure_user(H_VIEW, '财批查看员')
ensure_member(perm_u, company, post_perm)
ensure_member(view_u, company, post_view)

TOK_PERM = login(H_PERM)
TOK_VIEW = login(H_VIEW)
TOK_OWNER_B = login(H_OWNER_B)
check('FB-SETUP 管理员登录', bool(TOK_PERM))
check('FB-SETUP 查看岗登录', bool(TOK_VIEW))

admin = User.objects.get(phone=OWNER)
cu = Customer.objects.create(company=company, customer_no='FBAT-CU-001',
                             name='财批客户', phone='13900000001')
# 三张应收：r1 400 未收 / r2 600 已收 150 / r3 300 未收（created_at 递增）
r1 = create_receivable(company, customer=cu, amount=Decimal('400.00'),
                       remark='fb-1', user=admin)
r2 = create_receivable(company, customer=cu, amount=Decimal('600.00'),
                       remark='fb-2', user=admin)
add_receipt(company, r2, amount=Decimal('150.00'), user=admin)
r2.refresh_from_db()
r3 = create_receivable(company, customer=cu, amount=Decimal('300.00'),
                       remark='fb-3', user=admin)
check('FB-SETUP 应收三张', (r1.status, r2.status, r3.status)
      == ('unpaid', 'partial', 'unpaid'),
      f'{r1.status}/{r2.status}/{r3.status}')

BATCH = f'/finance/customers/{cu.id}/receipts-batch'

# ================= 权限 / 归属 =================
st, _ = api('POST', BATCH)
check('FB-API-01 无 token 401', st == 401, st)

st, d = api('POST', BATCH, token=TOK_VIEW, cid=CID, data={'total': '100'})
check('FB-API-02 查看岗 403', st == 403, f'{st} {d}')

st, d = api('POST', '/finance/customers/999999/receipts-batch',
            token=TOK_PERM, cid=CID, data={'total': '100'})
check('FB-API-03 客户不存在 404', st == 404, f'{st} {d}')

# B 厂长带自己厂 CID 访问 A 厂客户 → 404（公司隔离）
st, d = api('POST', BATCH, token=TOK_OWNER_B, cid=CID_B, data={'total': '100'})
check('FB-API-04 跨厂 404', st == 404, f'{st} {d}')

st, d = api('POST', BATCH, token=TOK_PERM, cid=CID, data={'total': '0'})
check('FB-API-05 total=0 400', st == 400, f'{st} {d}')
st, d = api('POST', BATCH, token=TOK_PERM, cid=CID, data={'total': '-5'})
check('FB-API-06 total<0 400', st == 400, f'{st} {d}')
st, d = api('POST', BATCH, token=TOK_PERM, cid=CID, data={'total': 'abc'})
check('FB-API-07 total 非数 400', st == 400, f'{st} {d}')
st, d = api('POST', BATCH, token=TOK_PERM, cid=CID,
            data={'total': '100', 'method': 'bitcoin'})
check('FB-API-08 method 非法 400', st == 400, f'{st} {d}')

# ================= FIFO 分配主流程 =================
# 未结总额 = 400 + 450 + 300 = 1150；收 700 → r1 400 全清 + r2 300（至 450/600 partial）
st, d = api('POST', BATCH, token=TOK_PERM, cid=CID,
            data={'total': '700', 'method': 'cash', 'handler': '财批测试',
                  'remark': 'fb-fifo'})
check('FB-FIFO-01 201', st == 201, f'{st} {d}')
check('FB-FIFO-02 分配两行', len(d.get('allocated', [])) == 2, str(d))
alloc = d.get('allocated', [])
check('FB-FIFO-03 先收最早 r1 400',
      alloc and alloc[0]['receivable_id'] == r1.id
      and alloc[0]['amount'] == '400.00', str(alloc))
check('FB-FIFO-04 再收 r2 300',
      len(alloc) > 1 and alloc[1]['receivable_id'] == r2.id
      and alloc[1]['amount'] == '300.00', str(alloc))
check('FB-FIFO-05 total_allocated 700',
      d.get('total_allocated') == '700.00', str(d))
check('FB-FIFO-06 leftover 恒 0', d.get('leftover') == '0.00', str(d))
r1.refresh_from_db(); r2.refresh_from_db()
check('FB-FIFO-07 r1 settled 400',
      r1.status == 'settled' and str(r1.received_amount) == '400.00',
      f'{r1.status}/{r1.received_amount}')
check('FB-FIFO-08 r2 partial 450/600',
      r2.status == 'partial' and str(r2.received_amount) == '450.00',
      f'{r2.status}/{r2.received_amount}')
check('FB-FIFO-09 r3 未动', r3.status == 'unpaid'
      and r3.received_amount == Decimal('0'),
      f'{r3.status}/{r3.received_amount}')

# ================= 红线 / 续收 =================
# 当前未结：r2 欠 150 + r3 欠 300 = 450；收 451 → 溢收 400
st, d = api('POST', BATCH, token=TOK_PERM, cid=CID, data={'total': '451'})
check('FB-RED-01 溢收 400', st == 400, f'{st} {d}')
check('FB-RED-02 报错含「溢收」',
      '溢收' in json.dumps(d, ensure_ascii=False), str(d))

st, d = api('POST', BATCH, token=TOK_PERM, cid=CID,
            data={'total': '100', 'method': 'monthly'})
check('FB-RED-03 部分 100→r2（仍最早）', st == 201
      and len(d.get('allocated', [])) == 1
      and d['allocated'][0]['receivable_id'] == r2.id
      and d['allocated'][0]['amount'] == '100.00', f'{st} {d}')
r2.refresh_from_db()
check('FB-RED-04 r2 partial 550/600',
      r2.status == 'partial' and str(r2.received_amount) == '550.00',
      f'{r2.status}/{r2.received_amount}')

# 收 150 → r2 清尾 50 + r3 100 → r2 settled / r3 partial 100/300
st, d = api('POST', BATCH, token=TOK_PERM, cid=CID, data={'total': '150'})
check('FB-RED-05 续收 150 跨两行', st == 201
      and [a['receivable_id'] for a in d.get('allocated', [])] == [r2.id, r3.id]
      and d['allocated'][0]['amount'] == '50.00'
      and d['allocated'][1]['amount'] == '100.00', f'{st} {d}')
r2.refresh_from_db(); r3.refresh_from_db()
check('FB-RED-06 r2 settled 600 / r3 partial 100/300',
      r2.status == 'settled' and str(r2.received_amount) == '600.00'
      and r3.status == 'partial' and str(r3.received_amount) == '100.00',
      f'{r2.status}/{r2.received_amount}|{r3.status}/{r3.received_amount}')

st, d = api('POST', BATCH, token=TOK_PERM, cid=CID, data={'total': '200'})
check('FB-RED-07 r3 清尾 200', st == 201, f'{st} {d}')
r3.refresh_from_db()
check('FB-RED-08 r3 settled 300',
      r3.status == 'settled' and str(r3.received_amount) == '300.00',
      f'{r3.status}/{r3.received_amount}')

st, d = api('POST', BATCH, token=TOK_PERM, cid=CID, data={'total': '0.01'})
check('FB-RED-09 全结清后再收溢收 400', st == 400, f'{st} {d}')

# ================= 留痕 =================
line_cnt = ReceiptLine.all_objects.filter(company=company,
                                          receivable_id__in=[r1.id, r2.id, r3.id],
                                          remark='fb-fifo').count()
check('FB-AUD-01 FIFO 核销流水两行', line_cnt == 2, line_cnt)
check('FB-AUD-02 流水挂在各自应收',
      ReceiptLine.all_objects.filter(company=company, receivable_id=r1.id,
                                     amount=Decimal('400.00')).exists()
      and ReceiptLine.all_objects.filter(company=company, receivable_id=r2.id,
                                         amount=Decimal('300.00')).exists())

# ================= cleanup =================
print('\n---- cleanup ----')
try:
    preclean(company)
    preclean(company_b)
    for p in (post_view, post_perm):
        PostM.objects.filter(pk=p.pk).update(permissions=[])
    print('cleanup: done（业务行已清，测试厂/账号复用保留）')
except Exception as e:  # noqa: BLE001
    print('cleanup WARN:', e)

print(f'\n==== 批量核销套件：{len(PASS)} pass / {len(FAIL)} fail ====')
if FAIL:
    print('FAILED:', FAIL)
    raise SystemExit(1)
