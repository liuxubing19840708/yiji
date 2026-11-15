<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api, clearSession, getSession, saveSession, upload } from '../api'
import PageState from '../components/PageState.vue'

const emit = defineEmits(['toast', 'saved', 'navigate'])
const session = getSession()
const companies = ref(session.companies || [])
const currentCompany = computed(() => companies.value.find((item) => item.id === session.companyId))
const pendingCompanies = computed(() => companies.value.filter((item) => item.status === 0))
const joinNo = ref('')
const joining = ref(false)
const refreshingCompanies = ref(false)
const companyError = ref('')
const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
const uploading = ref(false)
const changingPassword = ref(false)
const notificationPermission = ref(globalThis.Notification?.permission || 'unsupported')
const requestingNotification = ref(false)
const notificationError = ref('')
const deactivateStep = ref(0)
const deactivatePassword = ref('')
const deactivating = ref(false)
const deactivateError = ref('')
const profileError = ref('')
const passwordError = ref('')
const avatarPreview = ref('')
const fileInput = ref(null)
const today = new Date().toISOString().slice(0, 10)
const form = reactive({ phone: '', real_name: '', gender: 0, birthday: '', avatar: '' })
const password = reactive({ old_password: '', new_password: '', confirm_password: '' })
const baseline = ref('')
let localAvatarUrl = ''

const dirty = computed(() => JSON.stringify([form.real_name, form.gender, form.birthday, form.avatar]) !== baseline.value)
const initials = computed(() => (form.real_name || session.user?.username || '用户').slice(0, 1))
const notificationText = computed(() => ({
  granted: '浏览器已允许通知', denied: '浏览器已拒绝通知，请在浏览器网站设置中调整',
  default: '尚未请求浏览器通知权限', unsupported: '当前浏览器不支持 Notification API',
}[notificationPermission.value]))
function hasCompanyPermission(permission) {
  const post = currentCompany.value?.post || {}
  const permissions = post.effective_perms || post.permissions || []
  return Boolean(post.is_admin || permissions.includes('*') || permissions.includes(permission))
}
const canManageEmployees = computed(() => hasCompanyPermission('employees.manage'))
const canReviewSalary = computed(() => hasCompanyPermission('salary.review'))
const hasTodos = computed(() => canManageEmployees.value || canReviewSalary.value)

function applyUser(user) {
  Object.assign(form, {
    phone: user.phone || '', real_name: user.real_name || '', gender: Number(user.gender || 0),
    birthday: user.birthday || '', avatar: user.avatar || '',
  })
  baseline.value = JSON.stringify([form.real_name, form.gender, form.birthday, form.avatar])
  if (!localAvatarUrl) avatarPreview.value = user.avatar || ''
}
async function load() {
  loading.value = true; loadError.value = ''
  try {
    const user = await api('/auth/me')
    applyUser(user)
    saveSession({ user })
  } catch (e) { loadError.value = e.message } finally { loading.value = false }
}
async function refreshCompanies() {
  if (refreshingCompanies.value) return
  refreshingCompanies.value = true; companyError.value = ''
  try {
    const data = await api('/companies/mine')
    const list = Array.isArray(data) ? data : (data.results || [])
    companies.value = list
    // 只更新成员关系，保留当前公司上下文；加入申请不触发 switch。
    saveSession({ companies: list })
  } catch (e) { companyError.value = e.message } finally { refreshingCompanies.value = false }
}
async function joinCompany() {
  if (!joinNo.value.trim()) { companyError.value = '请输入公司编号'; return }
  if (joining.value || refreshingCompanies.value) return
  joining.value = true; companyError.value = ''
  try {
    await api('/companies/join', { method: 'POST', body: { company_no: joinNo.value.trim() } })
    joinNo.value = ''
    await refreshCompanies()
    if (!companyError.value) emit('toast', '加入申请已提交，等待公司管理员审核')
  } catch (e) { companyError.value = e.message } finally { joining.value = false }
}
function chooseAvatar() { fileInput.value?.click() }
async function avatarChanged(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || uploading.value) return
  if (file.size > 5 * 1024 * 1024) { profileError.value = '头像图片不能超过 5MB'; return }
  uploading.value = true; profileError.value = ''
  try {
    const result = await upload('/uploads', file)
    if (!result.url) throw new Error('上传接口未返回图片地址')
    if (localAvatarUrl) URL.revokeObjectURL(localAvatarUrl)
    localAvatarUrl = URL.createObjectURL(file)
    avatarPreview.value = localAvatarUrl
    form.avatar = result.url
  } catch (e) { profileError.value = e.message } finally { uploading.value = false }
}
async function saveProfile() {
  if (!dirty.value || saving.value || uploading.value) return
  if (!form.real_name.trim()) { profileError.value = '请输入真实姓名'; return }
  saving.value = true; profileError.value = ''
  try {
    const user = await api('/auth/me', {
      method: 'PUT',
      body: { real_name: form.real_name.trim(), gender: Number(form.gender), birthday: form.birthday || null, avatar: form.avatar || null },
    })
    if (localAvatarUrl) { URL.revokeObjectURL(localAvatarUrl); localAvatarUrl = '' }
    saveSession({ user })
    applyUser(user)
    emit('saved', user)
    emit('toast', '个人资料已保存')
  } catch (e) { profileError.value = e.message } finally { saving.value = false }
}
async function changePassword() {
  passwordError.value = ''
  if (!password.old_password || !password.new_password) { passwordError.value = '请填写原密码与新密码'; return }
  if (password.new_password.length < 6) { passwordError.value = '新密码至少 6 位'; return }
  if (password.new_password !== password.confirm_password) { passwordError.value = '两次输入的新密码不一致'; return }
  changingPassword.value = true
  try {
    await api('/auth/password', { method: 'PUT', body: { old_password: password.old_password, new_password: password.new_password } })
    password.old_password = ''; password.new_password = ''; password.confirm_password = ''
    emit('toast', '密码修改成功')
  } catch (e) { passwordError.value = e.message } finally { changingPassword.value = false }
}
async function requestNotifications() {
  if (!globalThis.Notification || requestingNotification.value) return
  requestingNotification.value = true; notificationError.value = ''
  try {
    notificationPermission.value = await globalThis.Notification.requestPermission()
  } catch (e) { notificationError.value = e.message || '浏览器通知权限请求失败' } finally { requestingNotification.value = false }
}
function openDeactivate() {
  deactivateStep.value = 1; deactivatePassword.value = ''; deactivateError.value = ''
}
async function deactivateAccount() {
  if (deactivating.value) return
  deactivating.value = true; deactivateError.value = ''
  try {
    await api('/auth/deactivate', {
      method: 'POST',
      body: deactivatePassword.value ? { password: deactivatePassword.value } : {},
    })
    clearSession()
    deactivateStep.value = 0
    window.dispatchEvent(new CustomEvent('web-auth-expired', { detail: { deactivated: true } }))
  } catch (e) {
    deactivateError.value = e.message
  } finally { deactivating.value = false }
}
onMounted(() => { load(); refreshCompanies() })
onBeforeUnmount(() => { if (localAvatarUrl) URL.revokeObjectURL(localAvatarUrl) })
</script>

<template>
  <div class="profile-view">
    <div class="welcome"><div><h2>个人资料</h2><p>查看账号信息，维护头像、姓名与登录密码。</p></div></div>
    <PageState :loading="loading" :error="loadError" @retry="load">
      <section class="card profile-summary">
        <button type="button" class="profile-avatar" :disabled="uploading" aria-label="更换头像" @click="chooseAvatar">
          <img v-if="avatarPreview" :src="avatarPreview" alt="个人头像" />
          <span v-else>{{ initials }}</span>
        </button>
        <div class="profile-identity"><h3>{{ form.real_name || '未填写姓名' }}</h3><p>{{ form.phone || '未绑定手机号' }}</p><span>{{ currentCompany?.name || '当前公司' }}<template v-if="currentCompany?.post?.name"> · {{ currentCompany.post.name }}</template></span></div>
        <button class="button secondary" :disabled="uploading" @click="chooseAvatar"><span v-if="uploading" class="spinner" />{{ uploading ? '上传中…' : '更换头像' }}</button>
        <input ref="fileInput" class="profile-file" type="file" accept="image/jpeg,image/png,image/gif,image/webp" @change="avatarChanged" />
      </section>

      <section v-if="hasTodos" class="card profile-card profile-todos" aria-label="待我处理">
        <div class="card-header"><div><h3>待我处理</h3><p>按当前公司的岗位权限显示，处理进度以目标页面数据为准</p></div></div>
        <div class="profile-todo-list">
          <button v-if="canManageEmployees" type="button" @click="emit('navigate', 'organization', 'pending')"><span><strong>员工审核</strong><small>审核申请加入公司的员工</small></span><span aria-hidden="true">›</span></button>
          <button v-if="canReviewSalary" type="button" @click="emit('navigate', 'salary', 'applies')"><span><strong>工资审核</strong><small>处理待审核的工资结算申请</small></span><span aria-hidden="true">›</span></button>
          <button v-if="canReviewSalary" type="button" @click="emit('navigate', 'salary', 'payments')"><span><strong>工资发放</strong><small>处理待发放工资与实付记录</small></span><span aria-hidden="true">›</span></button>
        </div>
      </section>

      <section class="card profile-card profile-companies">
        <div class="card-header"><div><h3>我的公司</h3><p>申请加入其他公司不会切换当前公司</p></div><button type="button" class="button secondary small" :disabled="refreshingCompanies || joining" @click="refreshCompanies">{{ refreshingCompanies ? '刷新中…' : '刷新状态' }}</button></div>
        <div v-if="companies.length" class="profile-company-list">
          <article v-for="company in companies" :key="company.id">
            <div><strong>{{ company.name }}</strong><span>编号 {{ company.company_no }}<template v-if="company.post?.name"> · {{ company.post.name }}</template></span></div>
            <span class="badge" :class="{ producing: company.status === 0 }">{{ company.id === session.companyId ? '当前公司' : company.status_display || (company.status === 0 ? '待审核' : '成员') }}</span>
          </article>
        </div>
        <p v-else class="muted">暂无公司成员关系</p>
        <p v-if="pendingCompanies.length" class="company-pending-note">有 {{ pendingCompanies.length }} 个加入申请待审核，可刷新查看状态。</p>
        <form class="company-join-form" @submit.prevent="joinCompany">
          <label class="field"><span>申请加入其他公司</span><input v-model="joinNo" inputmode="numeric" maxlength="12" placeholder="请输入公司编号" /></label>
          <button class="button primary" :disabled="joining || refreshingCompanies"><span v-if="joining" class="spinner light" />{{ joining ? '提交中…' : '申请加入' }}</button>
        </form>
        <p v-if="companyError" class="form-error" role="alert">{{ companyError }}</p>
      </section>

      <div class="profile-columns">
        <section class="card profile-card">
          <div class="card-header"><div><h3>基本资料</h3><p>公司切换请使用页面现有的公司选择入口</p></div></div>
          <form @submit.prevent="saveProfile">
            <label class="field"><span>手机号</span><input :value="form.phone || '未绑定'" disabled /></label>
            <label class="field"><span>真实姓名</span><input v-model="form.real_name" maxlength="30" required placeholder="请输入真实姓名" /></label>
            <label class="field"><span>性别</span><select v-model.number="form.gender"><option :value="0">未知</option><option :value="1">男</option><option :value="2">女</option></select></label>
            <label class="field"><span>出生日期</span><input v-model="form.birthday" type="date" :max="today" /></label>
            <p v-if="profileError" class="form-error" role="alert">{{ profileError }}</p>
            <div class="form-actions"><button class="button primary" :disabled="!dirty || saving || uploading"><span v-if="saving" class="spinner light" />{{ saving ? '保存中…' : '保存资料' }}</button></div>
          </form>
        </section>

        <section class="card profile-card">
          <div class="card-header"><div><h3>修改密码</h3><p>新密码至少 6 位，修改后请妥善保管</p></div></div>
          <form @submit.prevent="changePassword">
            <label class="field"><span>原密码</span><input v-model="password.old_password" type="password" autocomplete="current-password" required /></label>
            <label class="field"><span>新密码</span><input v-model="password.new_password" type="password" autocomplete="new-password" minlength="6" maxlength="64" required /></label>
            <label class="field"><span>确认新密码</span><input v-model="password.confirm_password" type="password" autocomplete="new-password" minlength="6" maxlength="64" required /></label>
            <p v-if="passwordError" class="form-error" role="alert">{{ passwordError }}</p>
            <div class="form-actions"><button class="button primary" :disabled="changingPassword"><span v-if="changingPassword" class="spinner light" />{{ changingPassword ? '修改中…' : '修改密码' }}</button></div>
          </form>
        </section>
      </div>

      <section class="card profile-card notification-settings">
        <div class="card-header"><div><h3>消息设置</h3><p>浏览器通知与微信小程序订阅消息是两套不同能力</p></div><span class="badge">{{ notificationText }}</span></div>
        <p>这里仅请求当前浏览器的通知权限，不会开通微信订阅消息。小程序订阅需配置微信消息模板并在小程序内操作；关闭浏览器后也无法保证接收 Web 通知。</p>
        <p v-if="notificationError" class="form-error" role="alert">{{ notificationError }}</p>
        <button class="button secondary" :disabled="requestingNotification || notificationPermission !== 'default'" @click="requestNotifications">{{ requestingNotification ? '正在请求权限…' : notificationPermission === 'default' ? '请求浏览器通知权限' : '查看浏览器权限状态' }}</button>
      </section>

      <section class="card profile-card deactivate-card">
        <div><h3>注销账号</h3><p>注销后无法登录，生产与工资记录按后端规则匿名保留。最后一名管理员等业务限制由服务端校验，不会被前端绕过。</p></div>
        <button class="button danger" @click="openDeactivate">注销账号</button>
      </section>
    </PageState>

    <Teleport to="body">
      <div v-if="deactivateStep" class="modal-backdrop" @click.self="!deactivating && (deactivateStep = 0)">
        <section class="modal" role="dialog" aria-modal="true" aria-label="注销账号确认">
          <div class="modal-header"><div><h2>{{ deactivateStep === 1 ? '确认注销账号？' : '再次确认，不可恢复' }}</h2><p>第 {{ deactivateStep }} 步，共 2 步</p></div><button class="icon-button" aria-label="关闭" :disabled="deactivating" @click="deactivateStep = 0"><span aria-hidden="true">×</span></button></div>
          <template v-if="deactivateStep === 1"><p>注销后此账号将无法登录所有所属公司。确定继续吗？</p><div class="form-actions"><button class="button secondary" @click="deactivateStep = 0">取消</button><button class="button danger" @click="deactivateStep = 2">继续注销</button></div></template>
          <form v-else @submit.prevent="deactivateAccount">
            <p>这是不可恢复的操作。若账号设有密码，服务端会要求输入当前登录密码进行身份验证。</p>
            <label class="field"><span>当前登录密码</span><input v-model="deactivatePassword" type="password" autocomplete="current-password" placeholder="设密码账号必填；无密码账号可留空" /></label>
            <p v-if="deactivateError" class="form-error" role="alert">{{ deactivateError }}</p>
            <div class="form-actions"><button type="button" class="button secondary" :disabled="deactivating" @click="deactivateStep = 1">上一步</button><button class="button danger" :disabled="deactivating">{{ deactivating ? '正在注销…' : '确认永久注销' }}</button></div>
          </form>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.profile-view { max-width: 1100px; }
.profile-summary { display: flex; align-items: center; gap: 18px; padding: 22px; margin-bottom: 18px; }
.profile-avatar { display: grid; place-items: center; flex: 0 0 76px; width: 76px; height: 76px; padding: 0; overflow: hidden; border: 0; border-radius: 50%; background: var(--orange-soft); color: var(--orange-dark); font-size: 28px; font-weight: 700; }
.profile-avatar img { width: 100%; height: 100%; object-fit: cover; }
.profile-identity { flex: 1; min-width: 0; }
.profile-identity h3 { margin: 0 0 7px; }
.profile-identity p { margin: 0 0 7px; color: var(--text-secondary); }
.profile-identity span { color: var(--muted); font-size: 13px; }
.profile-file { display: none; }
.profile-columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; align-items: start; }
.profile-card { padding: 22px; }
.profile-card .card-header { margin-bottom: 20px; }
.profile-card .card-header h3 { margin: 0 0 6px; }
.profile-card .card-header p { margin: 0; color: var(--muted); font-size: 12px; }
.profile-card .field + .field { margin-top: 14px; }
.profile-card .form-actions { justify-content: flex-end; }
.profile-card .spinner { width: 14px; height: 14px; }
.notification-settings { margin-top: 18px; }
.notification-settings .card-header { align-items: flex-start; gap: 12px; }
.notification-settings .card-header .badge { flex-shrink: 0; }
.notification-settings > p { color: var(--text-secondary); line-height: 1.7; }
.deactivate-card { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-top: 18px; border-color: #fecaca; }
.deactivate-card h3 { margin: 0 0 7px; color: var(--danger); }
.deactivate-card p { margin: 0; color: var(--text-secondary); line-height: 1.7; }
.deactivate-card .button { flex-shrink: 0; }
@media (max-width: 760px) {
  .profile-summary { flex-wrap: wrap; padding: 16px; gap: 13px; }
  .profile-avatar { flex-basis: 60px; width: 60px; height: 60px; font-size: 22px; }
  .profile-summary > .button { width: 100%; }
  .profile-columns { grid-template-columns: 1fr; gap: 12px; }
  .profile-card { padding: 16px; }
}
</style>
