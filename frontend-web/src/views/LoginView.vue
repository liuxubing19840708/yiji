<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api, clearSession, getSession, saveSession } from '../api'
import AppIcon from '../components/AppIcon.vue'

const emit = defineEmits(['logged-in'])
const form = reactive({ phone: '', password: '', code: '' })
const createForm = reactive({ name: '', city: '' })
const mode = ref('password')
const agreed = ref(false)
const agreementDialog = ref('')
const smsCountdown = ref(0)
const sendingSms = ref(false)
let smsTimer
const stage = ref('form')
const activeCompanies = ref([])
const pendingCompanies = ref([])
const joinNo = ref(new URLSearchParams(window.location.search).get('companyNo') || '')
const createVisible = ref(false)
const loading = ref(false)
const actionLoading = ref('')
const error = ref('')
const wechatEnabled = ref(false)
const wechatAuthorizationUrl = ref('')
const wechatConfigLoaded = ref(false)
const wechatUnavailableMessage = '部署方需配置微信开放平台网站应用 WECHAT_OPEN_APPID/WECHAT_OPEN_SECRET 后启用。'

function loadCompanies(access) {
  return api('/companies/mine', { anonymous: true, headers: { Authorization: `Bearer ${access}` } })
}
async function routeCompanies(tokens) {
  const companies = await loadCompanies(tokens.access)
  activeCompanies.value = companies.filter((item) => item.status === 1)
  pendingCompanies.value = companies.filter((item) => item.status === 0)
  saveSession({ access: tokens.access, refresh: tokens.refresh, user: tokens.user, companies })
  if (activeCompanies.value.length > 1) {
    stage.value = 'companies'
    return
  }
  if (activeCompanies.value.length === 1) {
    await enterCompany(activeCompanies.value[0])
    return
  }
  stage.value = pendingCompanies.value.length ? 'pending' : 'noCompany'
}
async function enterCompany(company) {
  actionLoading.value = `switch-${company.id}`
  error.value = ''
  try {
    await api(`/companies/${company.id}/switch`, { method: 'POST', body: {} })
    saveSession({ companyId: company.id })
    emit('logged-in')
  } catch (e) { error.value = e.message } finally { actionLoading.value = '' }
}
function ensureAgreed() {
  if (agreed.value) return true
  error.value = '请先阅读并同意《隐私政策》与《用户协议》'
  return false
}
function validPhone() {
  if (!/^1\d{10}$/.test(form.phone.trim())) { error.value = '请输入正确的 11 位手机号'; return false }
  return true
}
async function sendSms() {
  if (sendingSms.value || smsCountdown.value > 0 || !ensureAgreed() || !validPhone()) return
  sendingSms.value = true; error.value = ''
  try {
    await api('/auth/sms-code', { method: 'POST', body: { phone: form.phone.trim() }, anonymous: true })
    smsCountdown.value = 60
    clearInterval(smsTimer)
    smsTimer = setInterval(() => {
      smsCountdown.value -= 1
      if (smsCountdown.value <= 0) clearInterval(smsTimer)
    }, 1000)
  } catch (e) { error.value = e.message } finally { sendingSms.value = false }
}
async function login() {
  if (!ensureAgreed() || !validPhone()) return
  if (mode.value === 'password' && !form.password) { error.value = '请输入密码'; return }
  if (mode.value === 'sms' && !form.code.trim()) { error.value = '请输入短信验证码'; return }
  loading.value = true; error.value = ''
  try {
    const sms = mode.value === 'sms'
    const tokens = await api(sms ? '/auth/login-sms' : '/auth/login', {
      method: 'POST',
      body: sms ? { phone: form.phone.trim(), code: form.code.trim() } : { phone: form.phone.trim(), password: form.password },
      anonymous: true,
    })
    await routeCompanies(tokens)
  } catch (e) {
    clearSession()
    error.value = e.message
  } finally { loading.value = false }
}
async function refreshCompanies() {
  const { access, refresh, user } = getSession()
  actionLoading.value = 'refresh'
  error.value = ''
  try { await routeCompanies({ access, refresh, user }) } catch (e) { error.value = e.message } finally { actionLoading.value = '' }
}
async function joinCompany() {
  if (!joinNo.value.trim()) { error.value = '请输入公司编号'; return }
  actionLoading.value = 'join'; error.value = ''
  try {
    await api('/companies/join', { method: 'POST', body: { company_no: joinNo.value.trim() } })
    await refreshCompanies()
  } catch (e) { error.value = e.message } finally { actionLoading.value = '' }
}
async function createCompany() {
  if (!createForm.name.trim()) { error.value = '请输入公司名称'; return }
  actionLoading.value = 'create'; error.value = ''
  try {
    await api('/companies/create', { method: 'POST', body: { name: createForm.name.trim(), city: createForm.city.trim() } })
    createVisible.value = false
    createForm.name = ''; createForm.city = ''
    await refreshCompanies()
  } catch (e) { error.value = e.message } finally { actionLoading.value = '' }
}
function logout() {
  clearSession()
  stage.value = 'form'
  activeCompanies.value = []; pendingCompanies.value = []
  form.password = ''; form.code = ''; agreed.value = false; joinNo.value = ''; error.value = ''
}
async function loadWechatConfig() {
  try {
    const config = await api('/auth/wechat-web', { anonymous: true })
    wechatEnabled.value = config.enabled === true && !!config.authorization_url
    wechatAuthorizationUrl.value = wechatEnabled.value ? config.authorization_url : ''
  } catch {
    wechatEnabled.value = false
    wechatAuthorizationUrl.value = ''
  } finally { wechatConfigLoaded.value = true }
}
async function startWechatLogin() {
  if (!wechatEnabled.value || !wechatAuthorizationUrl.value || !ensureAgreed()) return
  loading.value = true
  error.value = ''
  try {
    // 每次点击生成新的限时 state，不回放旧授权 URL。
    const config = await api('/auth/wechat-web', { anonymous: true })
    if (!config.enabled || !config.authorization_url) throw new Error(wechatUnavailableMessage)
    window.location.assign(config.authorization_url)
  } catch (e) {
    loading.value = false
    error.value = e.message
  }
}
function clearOauthQuery(url) {
  url.searchParams.delete('code')
  url.searchParams.delete('state')
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
}
async function handleWechatCallback() {
  const url = new URL(window.location.href)
  const code = url.searchParams.get('code')
  const state = url.searchParams.get('state')
  if (!code && !state) return false
  // code/state 不留在地址栏、浏览器历史或后续页面截图中。
  clearOauthQuery(url)
  loading.value = true
  error.value = ''
  if (!code || !state) {
    error.value = '微信授权回跳缺少 code 或 state，请重新登录'
    loading.value = false
    return true
  }
  try {
    const tokens = await api('/auth/wechat-web/login', {
      method: 'POST', body: { code, state }, anonymous: true,
    })
    await routeCompanies(tokens)
  } catch (e) {
    clearSession()
    error.value = e.message
  } finally { loading.value = false }
  return true
}
onMounted(async () => {
  const callback = await handleWechatCallback()
  if (!callback) await loadWechatConfig()
})
onBeforeUnmount(() => clearInterval(smsTimer))
</script>
<template>
  <main class="login-page">
    <section class="login-story">
      <div class="brand brand-light"><span class="brand-mark">衣</span><span>衣计</span></div>
      <div class="story-copy"><p class="eyebrow">服装厂数字化工作台</p><h1>每一扎都有进度，<br />每一件都有依据。</h1><p>扫码计件、订单进度、生产协同与工资核算，在一个清晰的工作台里完成。</p></div>
      <div class="story-tags"><span>扫码计件</span><span>订单追踪</span><span>工资核算</span></div>
    </section>
    <section class="login-panel">
      <form v-if="stage === 'form'" class="login-card" @submit.prevent="login">
        <div class="mobile-login-brand"><strong>衣计</strong><span>服装计件生产管理</span></div>
        <div class="mobile-login-tabs login-mode-tabs" aria-label="登录方式"><button type="button" :class="{ active: mode === 'sms' }" @click="mode = 'sms'; error = ''">验证码登录</button><button type="button" :class="{ active: mode === 'password' }" @click="mode = 'password'; error = ''">密码登录</button></div>
        <p class="eyebrow">WELCOME BACK</p><h2>登录工作台</h2><p class="muted">使用您的员工账号进入所属工厂</p>
        <label class="field"><span>手机号</span><input v-model="form.phone" inputmode="tel" autocomplete="username" maxlength="11" placeholder="请输入手机号" /></label>
        <label v-if="mode === 'password'" class="field"><span>密码</span><input v-model="form.password" type="password" autocomplete="current-password" placeholder="请输入密码" /></label>
        <label v-else class="field"><span>短信验证码</span><div class="sms-field"><input v-model="form.code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="请输入验证码" /><button type="button" class="button secondary small" :disabled="sendingSms || smsCountdown > 0" @click="sendSms">{{ sendingSms ? '发送中…' : smsCountdown > 0 ? `${smsCountdown} 秒后重发` : '获取验证码' }}</button></div></label>
        <div class="login-agreement">
          <label><input v-model="agreed" type="checkbox" aria-label="我已阅读并同意隐私政策与用户协议" /><span>我已阅读并同意</span></label>
          <button type="button" @click="agreementDialog = 'privacy'">《隐私政策》</button><span>与</span><button type="button" @click="agreementDialog = 'terms'">《用户协议》</button>
        </div>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="button primary login-button" :disabled="loading"><span v-if="loading" class="spinner light" />{{ loading ? '正在登录…' : '登录' }}<AppIcon v-if="!loading" name="chevron" /></button>
        <div class="wechat-web-note"><button type="button" class="button secondary login-button" :disabled="loading || !wechatEnabled" :title="wechatEnabled ? '跳转微信扫码授权' : wechatUnavailableMessage" @click="startWechatLogin">微信一键登录</button><small v-if="wechatEnabled">跳转微信官方页面扫码授权，登录后返回本公司选择流程。</small><small v-else>{{ wechatConfigLoaded ? `Web 未配置微信 OAuth。${wechatUnavailableMessage}` : '正在检查微信网页登录配置…' }}</small></div>
        <p class="login-note">Web 工作台 · 数据权限与小程序保持一致</p>
      </form>

      <section v-else class="login-card company-guide">
        <template v-if="stage === 'companies'">
          <p class="eyebrow">SELECT YOUR COMPANY</p><h2>选择公司</h2>
          <p class="muted">您的账号属于多个公司，请选择本次要进入的工作台。</p>
          <button v-for="company in activeCompanies" :key="company.id" type="button" class="company-option company-select" :disabled="!!actionLoading" :aria-label="`进入${company.name}`" @click="enterCompany(company)">
            <div><strong>{{ company.name }}</strong><span>{{ company.city || '未填写城市' }} · 编号 {{ company.company_no }}<template v-if="company.post?.name"> · {{ company.post.name }}</template></span></div>
            <span class="company-enter">{{ actionLoading === `switch-${company.id}` ? '正在进入…' : '进入公司' }}</span>
          </button>
        </template>
        <template v-else-if="stage === 'pending'">
          <p class="eyebrow">MEMBERSHIP REVIEW</p><h2>等待审核</h2>
          <p class="muted">您的入职申请已提交，管理员审核通过后即可进入。</p>
          <article v-for="company in pendingCompanies" :key="company.id" class="company-option">
            <div><strong>{{ company.name }}</strong><span>{{ company.city || '未填写城市' }} · 编号 {{ company.company_no }}</span></div>
            <span class="badge producing">{{ company.status_display || '待审核' }}</span>
          </article>
          <button class="button primary login-button" :disabled="!!actionLoading" @click="refreshCompanies">{{ actionLoading === 'refresh' ? '正在刷新…' : '刷新审核状态' }}</button>
        </template>
        <template v-else>
          <p class="eyebrow">JOIN YOUR COMPANY</p><h2>加入公司</h2>
          <p class="muted">输入管理员提供的公司编号，提交入职申请。</p>
          <form @submit.prevent="joinCompany">
            <label class="field"><span>公司编号</span><input v-model="joinNo" inputmode="numeric" maxlength="12" placeholder="请输入公司编号" /></label>
            <button class="button primary login-button" :disabled="!!actionLoading">{{ actionLoading === 'join' ? '正在提交…' : '申请加入' }}</button>
          </form>
          <div class="company-create-entry"><strong>还没有公司？</strong><p class="muted">创建后您将自动成为该公司的管理员。</p><button class="button secondary login-button" @click="createVisible = true">创建公司</button></div>
        </template>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="button ghost login-button" :disabled="!!actionLoading" @click="logout">退出登录</button>
      </section>
    </section>
    <Teleport to="body">
      <div v-if="createVisible" class="modal-backdrop" @click.self="!actionLoading && (createVisible = false)">
        <section class="modal" role="dialog" aria-modal="true" aria-label="创建公司">
          <div class="modal-header"><div><h2>创建公司</h2><p>创建后您将成为该公司的管理员</p></div><button class="icon-button" aria-label="关闭" :disabled="!!actionLoading" @click="createVisible = false"><AppIcon name="close" /></button></div>
          <form @submit.prevent="createCompany">
            <label class="field"><span>公司名称 *</span><input v-model="createForm.name" maxlength="30" required placeholder="请输入公司名称" /></label>
            <label class="field" style="margin-top:12px"><span>所在城市</span><input v-model="createForm.city" maxlength="20" placeholder="选填" /></label>
            <p v-if="error" class="form-error" role="alert">{{ error }}</p>
            <div class="form-actions"><button type="button" class="button secondary" :disabled="!!actionLoading" @click="createVisible = false">取消</button><button class="button primary" :disabled="!!actionLoading">{{ actionLoading === 'create' ? '正在创建…' : '创建' }}</button></div>
          </form>
        </section>
      </div>
    </Teleport>
    <Teleport to="body">
      <div v-if="agreementDialog" class="modal-backdrop" @click.self="agreementDialog = ''">
        <section class="modal agreement-modal" role="dialog" aria-modal="true" :aria-label="agreementDialog === 'privacy' ? '隐私政策' : '用户协议'">
          <div class="modal-header"><h2>{{ agreementDialog === 'privacy' ? '隐私政策' : '用户协议' }}</h2><button class="icon-button" aria-label="关闭" @click="agreementDialog = ''"><AppIcon name="close" /></button></div>
          <div v-if="agreementDialog === 'privacy'" class="agreement-copy">
            <h3>我们收集的信息</h3><p>为提供账号登录、公司协作、生产登记和薪酬核算服务，系统会处理手机号、姓名、公司及岗位信息，以及您在使用中产生的订单、计件、工资和操作审计记录。头像等资料由您自主选择提供。</p>
            <h3>信息使用与保护</h3><p>上述信息仅用于身份验证、权限控制、业务流程和记录追溯。不同公司的业务数据按公司上下文隔离，系统不会向无关第三方出售您的个人信息。</p>
            <h3>您的权利</h3><p>您可以在个人资料中更正资料、修改密码或申请注销账号。注销后账号无法登录，依法或为保持业务记录完整性而需保留的数据将按后端规则匿名化处理。</p>
          </div>
          <div v-else class="agreement-copy">
            <h3>账号与公司</h3><p>您应使用本人账号并妥善保管密码和短信验证码。加入或创建公司后，可见功能由所属公司的岗位权限决定；请勿代替他人执行未经授权的操作。</p>
            <h3>业务数据</h3><p>录入订单、生产和薪酬信息前应核对真实性。结算、撤销、注销等操作受业务规则约束，关键变更可能保留操作人和时间等审计记录。</p>
            <h3>服务使用</h3><p>不得利用系统破坏公司数据隔离、干扰服务或侵害他人权益。账号注销属于不可恢复操作，最后一名管理员等限制由服务端校验。</p>
          </div>
          <div class="form-actions"><button class="button primary" @click="agreementDialog = ''">我已阅读</button></div>
        </section>
      </div>
    </Teleport>
  </main>
</template>

<style scoped>
.login-mode-tabs { display: flex; gap: 8px; }
.login-mode-tabs button { flex: 1; padding: 8px; border: 0; border-radius: 8px; background: transparent; color: var(--muted); }
.login-mode-tabs button.active { background: var(--orange-soft); color: var(--orange-dark); font-weight: 600; }
.sms-field { display: flex; gap: 8px; }
.sms-field input { flex: 1; min-width: 0; }
.sms-field .button { flex-shrink: 0; min-width: 104px; }
.login-agreement { display: flex; align-items: center; flex-wrap: wrap; gap: 2px; margin: 16px 0; color: var(--text-secondary); font-size: 12px; }
.login-agreement label { display: inline-flex; align-items: center; gap: 6px; }
.login-agreement input { width: 16px; height: 16px; margin: 0; accent-color: var(--orange); }
.login-agreement button { padding: 2px; border: 0; background: transparent; color: var(--orange-dark); font-size: inherit; }
.wechat-web-note { margin-top: 12px; text-align: center; }
.wechat-web-note small { display: block; color: var(--muted); line-height: 1.6; }
.agreement-modal { max-width: 620px; }
.agreement-copy { max-height: 55vh; overflow-y: auto; color: var(--text-secondary); line-height: 1.8; }
.agreement-copy h3 { margin: 18px 0 6px; color: var(--ink); font-size: 15px; }
.agreement-copy h3:first-child { margin-top: 0; }
.agreement-copy p { margin: 0; }
.company-guide h2 { margin-bottom: 10px; }
.company-guide > .muted { line-height: 1.7; }
.company-option { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 16px 0; padding: 14px; border: 1px solid var(--line); border-radius: 10px; }
.company-option > div { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.company-option > div > span { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.company-select { width: 100%; border: 1px solid var(--line); background: var(--surface); color: var(--ink); text-align: left; cursor: pointer; }
.company-select:hover { border-color: var(--orange); background: var(--orange-soft); }
.company-select:disabled { cursor: wait; opacity: .7; }
.company-enter { flex-shrink: 0; color: var(--orange-dark); font-size: 13px; font-weight: 600; }
.company-create-entry { margin: 24px 0 16px; padding-top: 20px; border-top: 1px solid var(--line); }
.company-create-entry .muted { margin: 7px 0 14px; font-size: 12px; }
.company-guide > .button.ghost { margin-top: 8px; }
</style>
