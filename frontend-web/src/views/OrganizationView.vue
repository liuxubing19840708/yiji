<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, companyId, rows } from '../api'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import AdminModal from '../components/AdminModal.vue'

const emit = defineEmits(['toast'])
const section = ref('employees')
const employeeTab = ref('all')
const employees = ref([])
const posts = ref([])
const groups = ref([])
const processOptions = ref([])
const company = ref(null)
const loading = reactive({ employees: true, posts: true })
const errors = reactive({ employees: '', posts: '', permissions: '', processes: '', mutation: '' })
const filters = reactive({ keyword: '', post: '' })
const busy = ref('')
const modal = ref('')
const saving = ref(false)
const formError = ref('')
const invite = reactive({ phone: '', real_name: '', post_id: '' })
const assignment = reactive({ id: 0, name: '', post_id: '', group_no: 1 })
const assignedForm = reactive({ id: 0, name: '', process_ids: [] })
const salaryForm = reactive({ user_id: 0, name: '', salary_type: 'process', fixed_salary: '0', fixed_type: 'month', piece_rate: '', commission_rate: '' })
const postForm = reactive({ id: 0, name: '', permissions: [], all: false, is_admin: false })
let searchTimer

const currentPosts = computed(() => posts.value)
const permissionLabels = computed(() => Object.fromEntries(groups.value.flatMap((group) => group.points.map((point) => [point.key, point.label]))))
const canSubmitPost = computed(() => postForm.name.trim() && (postForm.all || postForm.permissions.length))
const capacityPercent = computed(() => company.value?.capacity > 0 ? Math.min(100, Math.round(((company.value.member_count || 0) / company.value.capacity) * 100)) : 0)

function statusClass(status) { return status === 1 ? 'done' : status === 0 ? 'producing' : 'cancelled' }
function employeeInitial(employee) { return employee.name?.slice(0, 1) || '?' }
function employeeAvatarColor(employee) { return employee.name ? employee.name.charCodeAt(0) % 6 : 0 }
function employeeGender(employee) { return employee.gender === 1 ? '♂' : employee.gender === 2 ? '♀' : '' }
function permissionNames(post) {
  if (post.is_admin || post.permissions?.includes('*')) return ['全部权限']
  const keys = post.effective_perms?.length ? post.effective_perms : (post.permissions || [])
  return keys.map((key) => permissionLabels.value[key] || key)
}
async function loadCompany() {
  try {
    const list = await api('/companies/mine')
    company.value = rows(list).find((item) => item.id === Number(companyId())) || rows(list)[0] || null
  } catch { company.value = null }
}
async function loadEmployees() {
  loading.employees = true; errors.employees = ''; errors.mutation = ''
  try {
    employees.value = rows(await api('/employees', { params: { tab: employeeTab.value, keyword: filters.keyword.trim(), post: filters.post } }))
  } catch (e) { errors.employees = e.message; employees.value = [] } finally { loading.employees = false }
}
async function loadPosts() {
  loading.posts = true; errors.posts = ''
  try { posts.value = rows(await api('/posts')) } catch (e) { errors.posts = e.message; posts.value = [] } finally { loading.posts = false }
}
async function loadPermissions() {
  errors.permissions = ''
  try { groups.value = (await api('/permissions')).groups || [] } catch (e) { errors.permissions = e.message; groups.value = [] }
}
async function loadProcesses() {
  errors.processes = ''
  try { processOptions.value = rows(await api('/processes', { params: { page: 1, page_size: 200 } })) } catch (e) { errors.processes = e.message; processOptions.value = [] }
}
function searchEmployees() { clearTimeout(searchTimer); searchTimer = setTimeout(loadEmployees, 300) }
function switchEmployeeTab(tab) { employeeTab.value = tab; loadEmployees() }
function pickPost(post) { filters.post = post; loadEmployees() }
function showModal(name) { formError.value = ''; modal.value = name }
function closeModal() { if (!saving.value) modal.value = '' }
function openInvite() {
  Object.assign(invite, { phone: '', real_name: '', post_id: '' })
  showModal('invite')
}
async function submitInvite() {
  formError.value = ''
  if (!/^\d{11}$/.test(invite.phone.trim())) { formError.value = '请输入 11 位手机号'; return }
  saving.value = true
  try {
    await api('/employees/invite', { method: 'POST', body: { phone: invite.phone.trim(), real_name: invite.real_name.trim(), post_id: invite.post_id || null } })
    modal.value = ''; emit('toast', '已邀请，等待对方登录后由管理员审核')
    employeeTab.value = 'pending'; await Promise.all([loadEmployees(), loadCompany()])
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function employeeAction(employee, action, success, confirmation = '') {
  if (confirmation && !window.confirm(confirmation)) return
  busy.value = `${action}-${employee.id}`; errors.mutation = ''
  try {
    await api(`/employees/${employee.id}/${action}`, { method: 'POST', body: {} })
    emit('toast', success); await Promise.all([loadEmployees(), loadCompany()])
  } catch (e) { errors.mutation = e.message } finally { busy.value = '' }
}
async function togglePin(employee) {
  busy.value = `pin-${employee.id}`; errors.mutation = ''
  try {
    await api(`/employees/${employee.id}/pin`, { method: 'POST', body: { is_pinned: !employee.is_pinned } })
    employee.is_pinned = !employee.is_pinned
    employees.value = [...employees.value].sort((a, b) => Number(b.is_pinned) - Number(a.is_pinned) || a.id - b.id)
    emit('toast', employee.is_pinned ? '已置顶' : '已取消置顶')
  } catch (e) { errors.mutation = e.message } finally { busy.value = '' }
}
function openPost(employee) {
  Object.assign(assignment, { id: employee.id, name: employee.name, post_id: employee.post_id || '', group_no: employee.group_no || 1 })
  showModal('post')
}
function openGroup(employee) {
  Object.assign(assignment, { id: employee.id, name: employee.name, post_id: employee.post_id || '', group_no: employee.group_no || 1 })
  showModal('group')
}
async function saveAssignment(kind) {
  formError.value = ''
  if (kind === 'post' && !assignment.post_id) { formError.value = '请选择岗位'; return }
  const group = Number(assignment.group_no)
  if (kind === 'group' && (!Number.isInteger(group) || group < 1 || group > 999)) { formError.value = '组编号须为 1~999 的整数'; return }
  saving.value = true
  try {
    const isPost = kind === 'post'
    await api(`/employees/${assignment.id}/${isPost ? 'set-post' : 'set-group'}`, { method: 'POST', body: isPost ? { post_id: Number(assignment.post_id) } : { group_no: group } })
    modal.value = ''; emit('toast', isPost ? '岗位已更新' : `已设为 ${group} 组`); await loadEmployees()
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
function openAssigned(employee) {
  Object.assign(assignedForm, { id: employee.id, name: employee.name, process_ids: (employee.assigned_processes || []).map(Number) })
  showModal('assigned')
}
async function saveAssigned() {
  formError.value = ''; saving.value = true
  try {
    await api(`/employees/${assignedForm.id}/assigned-processes`, { method: 'PUT', body: { process_ids: assignedForm.process_ids.map(Number) } })
    modal.value = ''; emit('toast', '员工指定工序已保存'); await loadEmployees()
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
function openSalary(employee) {
  Object.assign(salaryForm, {
    user_id: employee.user_id, name: employee.name, salary_type: employee.salary_type || 'process',
    fixed_salary: String(employee.fixed_salary ?? '0'), fixed_type: employee.fixed_type || 'month',
    piece_rate: employee.piece_rate == null ? '' : String(employee.piece_rate),
    commission_rate: employee.commission_rate == null ? '' : String(employee.commission_rate),
  })
  showModal('salary')
}
async function saveSalary() {
  formError.value = ''
  const body = { salary_type: salaryForm.salary_type }
  if (salaryForm.salary_type === 'fixed') {
    const amount = salaryForm.fixed_salary === '' ? 0 : Number(salaryForm.fixed_salary)
    if (!Number.isFinite(amount) || amount < 0) { formError.value = '固定工资须为不小于 0 的数字'; return }
    body.fixed_salary = String(amount); body.fixed_type = salaryForm.fixed_type
  } else if (salaryForm.salary_type === 'whole') {
    const rate = Number(salaryForm.piece_rate)
    if (!Number.isFinite(rate) || rate <= 0) { formError.value = '整件单价须大于 0'; return }
    body.piece_rate = String(rate)
  } else if (salaryForm.salary_type === 'commission') {
    const rate = Number(salaryForm.commission_rate)
    if (!Number.isFinite(rate) || rate <= 0 || rate > 100) { formError.value = '提成比例须大于 0 且不超过 100'; return }
    body.commission_rate = String(rate)
  }
  saving.value = true
  try {
    await api(`/salary/employee/${salaryForm.user_id}/config`, { method: 'PUT', body })
    modal.value = ''; emit('toast', '员工工资方式已更新'); await loadEmployees()
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
function openPostForm(post = null) {
  formError.value = ''
  Object.assign(postForm, {
    id: post?.id || 0,
    name: post?.name || '',
    permissions: post ? [...(post.effective_perms || post.permissions || [])].filter((key) => key !== '*') : [],
    all: Boolean(post?.is_admin || post?.permissions?.includes('*')),
    is_admin: Boolean(post?.is_admin),
  })
  showModal('post-form')
}
function toggleAllPermissions() {
  if (postForm.all) postForm.permissions = []
}
async function savePost() {
  formError.value = ''
  if (!postForm.name.trim()) { formError.value = '请输入岗位名称'; return }
  if (!postForm.all && !postForm.permissions.length) { formError.value = '请至少勾选一个权限点，或开启全部权限'; return }
  saving.value = true
  try {
    const body = { name: postForm.name.trim(), permissions: postForm.all ? ['*'] : [...postForm.permissions] }
    await api(postForm.id ? `/posts/${postForm.id}` : '/posts', { method: postForm.id ? 'PUT' : 'POST', body })
    modal.value = ''; emit('toast', postForm.id ? '岗位已更新' : '岗位已创建')
    await Promise.all([loadPosts(), loadEmployees()])
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function copyPost(post) {
  busy.value = `copy-post-${post.id}`; errors.posts = ''
  try { await api(`/posts/${post.id}/copy`, { method: 'POST', body: {} }); emit('toast', '岗位已复制'); await loadPosts() } catch (e) { errors.posts = e.message } finally { busy.value = '' }
}
async function deletePost(post) {
  if (!window.confirm(`确定删除岗位“${post.name}”吗？仍有在职成员的岗位无法删除。`)) return
  busy.value = `delete-post-${post.id}`; errors.posts = ''
  try { await api(`/posts/${post.id}`, { method: 'DELETE' }); emit('toast', '岗位已删除'); await loadPosts() } catch (e) { errors.posts = e.message } finally { busy.value = '' }
}
function viewMembers(post) { section.value = 'employees'; employeeTab.value = 'all'; filters.post = String(post.id); loadEmployees() }

onMounted(() => { loadEmployees(); loadPosts(); loadPermissions(); loadProcesses(); loadCompany() })
</script>

<template>
  <div class="welcome"><div><h2>组织系统</h2><p>管理公司员工、入离职流程、生产组与岗位权限。</p></div><div class="admin-head-actions"><button class="button secondary" @click="section === 'employees' ? loadEmployees() : loadPosts()"><AppIcon name="refresh" :size="16" />刷新</button><button v-if="section === 'employees'" class="button primary organization-invite-head" @click="openInvite">邀请员工</button><button v-else class="button primary" @click="openPostForm()">新建岗位</button></div></div>
  <div class="filter-bar organization-section-filter"><div class="segmented organization-section-tabs"><button :class="{ active: section === 'employees' }" @click="section = 'employees'">员工管理</button><button :class="{ active: section === 'posts' }" @click="section = 'posts'">岗位与权限</button></div><span v-if="company" class="section-note admin-capacity">在职 {{ company.member_count || 0 }} / {{ company.capacity || 0 }} 人 · {{ company.plan_display }}</span></div>

  <template v-if="section === 'employees'">
    <section v-if="company" class="org-capacity-card"><div><h3>员工容量</h3><strong>{{ company.member_count || 0 }}/{{ company.capacity || 0 }}</strong><button class="org-capacity-invite" aria-label="邀请员工" @click="openInvite">⊕ 邀请员工</button></div><div class="org-capacity-track"><span :style="{ width: `${capacityPercent}%` }" /></div></section><section v-else class="org-capacity-card org-capacity-fallback"><div><h3>添加员工</h3><button class="org-capacity-invite" aria-label="邀请员工" @click="openInvite">⊕ 邀请员工</button></div></section>
    <form class="filter-bar organization-employee-filter" @submit.prevent="loadEmployees"><div class="segmented employee-status-tabs"><button v-for="tab in [['all','在职'],['pending','待审核'],['resigned','离职']]" :key="tab[0]" type="button" :class="{ active: employeeTab === tab[0] }" @click="switchEmployeeTab(tab[0])">{{ tab[1] }}</button></div><div class="organization-search-row"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.keyword" placeholder="搜索姓名或手机号" @input="searchEmployees" /></label><button class="button primary organization-search-button">查询</button></div><select v-model="filters.post" class="employee-post-select" aria-label="按岗位筛选" @change="loadEmployees"><option value="">全部岗位</option><option value="0">未设置岗位</option><option v-for="post in currentPosts" :key="post.id" :value="String(post.id)">{{ post.name }}</option></select></form>
    <div class="org-post-filter-row"><div class="org-post-chips"><button :class="{ active: filters.post === '' }" @click="pickPost('')">全部</button><button :class="{ active: filters.post === '0' }" @click="pickPost('0')">未设岗位</button><button v-for="post in currentPosts" :key="post.id" :class="{ active: filters.post === String(post.id) }" @click="pickPost(String(post.id))">{{ post.name }}</button></div><button class="org-posts-entry" @click="section = 'posts'">岗位管理</button></div>
    <div v-if="errors.mutation" class="form-error admin-error">{{ errors.mutation }}</div>
    <div v-if="errors.processes" class="form-error admin-error">指定工序选项加载失败：{{ errors.processes }} <button class="button ghost small" @click="loadProcesses">重试</button></div>
    <section class="card table-card organization-employee-list"><PageState :loading="loading.employees" :error="errors.employees" :empty="!employees.length" :empty-text="employeeTab === 'pending' ? '暂无待审核申请' : employeeTab === 'resigned' ? '暂无离职员工' : '暂无在职员工'" @retry="loadEmployees">
      <div class="table-wrap desktop-table"><table><thead><tr><th>员工</th><th>状态</th><th>岗位</th><th>生产组</th><th>入职日期</th><th>操作</th></tr></thead><tbody><tr v-for="employee in employees" :key="employee.id"><td><span class="cell-title">{{ employee.is_pinned ? '置顶 · ' : '' }}{{ employee.name }}</span><small class="cell-subtitle">{{ employee.phone || '未留手机号' }}</small></td><td><span class="badge" :class="statusClass(employee.status)">{{ employee.status_display }}</span></td><td>{{ employee.post_name || '未设置' }}<small v-if="employee.is_admin" class="cell-subtitle">管理员</small></td><td>{{ employee.group_no ? `${employee.group_no} 组` : '—' }}<small class="cell-subtitle">{{ employee.salary_type_display || '工序计件' }}</small></td><td>{{ employee.hired_date || '—' }}</td><td><div class="admin-actions"><button class="button ghost small" :disabled="!!busy" @click="togglePin(employee)">{{ employee.is_pinned ? '取消置顶' : '置顶' }}</button><template v-if="employee.status === 0"><button class="button primary small" :disabled="!!busy" @click="employeeAction(employee, 'approve', '已通过入职申请')">通过</button><button class="button danger small" :disabled="!!busy" @click="employeeAction(employee, 'reject', '已拒绝申请', `确定拒绝 ${employee.name} 的入职申请吗？`)">拒绝</button></template><template v-else-if="employee.status === 1"><button class="button secondary small" :disabled="!!busy" @click="openPost(employee)">设岗</button><button class="button secondary small" :disabled="!!busy" @click="openGroup(employee)">设组</button><button class="button secondary small" :disabled="!!busy" @click="openAssigned(employee)">指定工序</button><button class="button secondary small" :disabled="!!busy" @click="openSalary(employee)">工资</button><button class="button danger small" :disabled="!!busy" @click="employeeAction(employee, 'resign', '已办理离职', `确定为 ${employee.name} 办理离职吗？存在未结算计件记录时后端会拒绝。`)">离职</button></template><button v-else class="button secondary small" :disabled="!!busy" @click="employeeAction(employee, 'rehire', '已复职', `确定让 ${employee.name} 复职吗？`)">复职</button></div></td></tr></tbody></table></div>
      <div class="mobile-cards org-employee-list"><article v-for="employee in employees" :key="employee.id" class="mobile-row org-employee-card" :class="{ 'org-employee-pinned': employee.is_pinned }"><div class="org-employee-main"><img v-if="employee.avatar" class="org-employee-avatar" :src="employee.avatar" :alt="`${employee.name}头像`" /><span v-else class="org-employee-avatar" :class="`org-avatar-${employeeAvatarColor(employee)}`">{{ employeeInitial(employee) }}</span><div class="org-employee-info"><div class="org-employee-name"><h3>{{ employee.name }}</h3><span v-if="employeeGender(employee)" class="org-employee-gender" :class="employee.gender === 1 ? 'male' : 'female'">{{ employeeGender(employee) }}</span><span v-if="employee.is_admin" class="org-employee-badge">管理员</span><span class="badge" :class="statusClass(employee.status)">{{ employee.status_display }}</span><span v-if="employee.is_pinned" class="org-pinned-star">★</span></div><p>{{ employee.post_name || '未设置岗位' }} · {{ employee.group_no || '—' }}组<template v-if="employee.phone"> · {{ employee.phone }}</template></p></div><button v-if="employee.status === 1 || employee.status === 2" class="org-pin-button" :class="{ active: employee.is_pinned }" :aria-label="employee.is_pinned ? '取消置顶' : '置顶'" :disabled="!!busy" @click="togglePin(employee)">★</button><details v-if="employee.status === 1 || employee.status === 2" class="org-employee-menu"><summary :aria-label="`${employee.name}更多操作`">⋯</summary><div class="org-employee-menu-actions"><template v-if="employee.status === 1"><button class="button secondary small" :disabled="!!busy" @click="openPost(employee)">设岗</button><button class="button secondary small" :disabled="!!busy" @click="openGroup(employee)">设组</button><button class="button secondary small" :disabled="!!busy" @click="openAssigned(employee)">指定工序</button><button class="button secondary small" :disabled="!!busy" @click="openSalary(employee)">工资</button><button class="button danger small" :disabled="!!busy" @click="employeeAction(employee, 'resign', '已办理离职', `确定为 ${employee.name} 办理离职吗？存在未结算计件记录时后端会拒绝。`)">离职</button></template><button v-else class="button secondary small" :disabled="!!busy" @click="employeeAction(employee, 'rehire', '已复职', `确定让 ${employee.name} 复职吗？`)">复职</button></div></details></div><div class="mobile-meta org-employee-subline"><span>{{ employee.salary_type_display || '工序计件' }}</span><span>入职 {{ employee.hired_date || '—' }}</span></div><div v-if="employee.status === 0" class="org-review-actions"><button class="button secondary small" :disabled="!!busy" @click="employeeAction(employee, 'reject', '已拒绝申请', `确定拒绝 ${employee.name} 的入职申请吗？`)">拒绝</button><button class="button primary small" :disabled="!!busy" @click="employeeAction(employee, 'approve', '已通过入职申请')">通过</button></div></article></div>
    </PageState></section>
  </template>

  <template v-else>
    <div v-if="errors.permissions" class="form-error admin-error">权限字典加载失败：{{ errors.permissions }} <button class="button ghost small" @click="loadPermissions">重试</button></div>
    <section class="card table-card"><PageState :loading="loading.posts" :error="errors.posts" :empty="!posts.length" empty-text="暂无岗位，请先新建岗位" @retry="loadPosts"><div class="admin-post-grid"><article v-for="post in posts" :key="post.id" class="admin-post-card"><div class="mobile-row-head"><div><h3>{{ post.name }}</h3><p>{{ post.member_count || 0 }} 名在职成员<span v-if="post.is_system"> · 系统岗位</span></p></div><span v-if="post.is_admin" class="badge done">管理员</span></div><div class="admin-permissions"><span v-for="label in permissionNames(post).slice(0, 8)" :key="label">{{ label }}</span><span v-if="permissionNames(post).length > 8">等 {{ permissionNames(post).length }} 项</span></div><div class="admin-actions"><button class="button secondary small" :disabled="!!busy" @click="openPostForm(post)">编辑权限</button><button class="button secondary small" :disabled="!!busy" @click="copyPost(post)">复制</button><button class="button ghost small" @click="viewMembers(post)">查看成员</button><button v-if="!post.is_system" class="button danger small" :disabled="!!busy" @click="deletePost(post)">删除</button></div></article></div></PageState></section>
  </template>

  <AdminModal v-if="modal === 'invite'" title="邀请员工" description="邀请后进入待审核列表，对方登录后还需审核通过。" :saving="saving" @close="closeModal"><form class="organization-invite-form" @submit.prevent="submitInvite"><div class="form-grid"><label class="field"><span>手机号 *</span><input v-model="invite.phone" inputmode="numeric" maxlength="11" required placeholder="11 位手机号" /></label><label class="field"><span>姓名</span><input v-model="invite.real_name" maxlength="30" placeholder="选填" /></label><label class="field full"><span>初始岗位</span><select v-model="invite.post_id"><option value="">暂不指定</option><option v-for="post in posts" :key="post.id" :value="post.id">{{ post.name }}</option></select></label></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving"><span v-if="saving" class="spinner light" />发送邀请</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'post'" :title="`设置岗位 · ${assignment.name}`" :saving="saving" @close="closeModal"><form @submit.prevent="saveAssignment('post')"><label class="field"><span>本公司岗位 *</span><select v-model="assignment.post_id" required><option value="">请选择</option><option v-for="post in posts" :key="post.id" :value="post.id">{{ post.name }}{{ post.is_admin ? '（管理员）' : '' }}</option></select></label><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving">保存岗位</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'group'" :title="`设置生产组 · ${assignment.name}`" :saving="saving" @close="closeModal"><form @submit.prevent="saveAssignment('group')"><label class="field"><span>组编号（1~999）*</span><input v-model.number="assignment.group_no" type="number" min="1" max="999" step="1" required /></label><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving">保存组号</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'assigned'" :title="`指定工序 · ${assignedForm.name}`" description="固定工序对所有员工开放，此处配置员工额外可登记的工序；取消全部勾选即清空指定。" :saving="saving" wide @close="closeModal"><form @submit.prevent="saveAssigned"><div v-if="processOptions.length" class="admin-assigned-processes"><label v-for="process in processOptions" :key="process.id"><input v-model="assignedForm.process_ids" type="checkbox" :value="process.id" /><span>{{ process.name }}<small>{{ process.is_fixed ? '固定工序（无需重复指定）' : '普通工序' }}</small></span></label></div><div v-else class="page-state">暂无本公司工序，请先在基础档案中创建。</div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving"><span v-if="saving" class="spinner light" />保存指定工序</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'salary'" :title="`工资配置 · ${salaryForm.name}`" description="四类工资制互斥；新配置影响后续核算，历史已结算快照不变。" :saving="saving" @close="closeModal"><form @submit.prevent="saveSalary"><label class="field org-salary-type-select"><span>工资计算方式 *</span><select v-model="salaryForm.salary_type"><option value="process">工序计件</option><option value="fixed">固定工资</option><option value="whole">整件计件</option><option value="commission">计件提成</option></select></label><div class="org-salary-type-chips"><button v-for="kind in [['process','工序计件'],['whole','整件计件'],['commission','计件提成'],['fixed','固定工资']]" :key="kind[0]" type="button" :class="{ active: salaryForm.salary_type === kind[0] }" @click="salaryForm.salary_type = kind[0]">{{ kind[1] }}</button></div><div v-if="salaryForm.salary_type === 'fixed'" class="form-grid admin-salary-fields"><label class="field"><span>固定工资金额（0 = 取消金额）</span><input v-model="salaryForm.fixed_salary" type="number" min="0" step="0.01" required /></label><label class="field org-fixed-type-select"><span>固定工资类型</span><select v-model="salaryForm.fixed_type"><option value="day">按天</option><option value="month">按月</option></select></label><div class="org-fixed-type-chips"><button type="button" :class="{ active: salaryForm.fixed_type === 'day' }" @click="salaryForm.fixed_type = 'day'">按天</button><button type="button" :class="{ active: salaryForm.fixed_type === 'month' }" @click="salaryForm.fixed_type = 'month'">按月</button></div></div><label v-else-if="salaryForm.salary_type === 'whole'" class="field admin-salary-fields"><span>整件单价（元 / 件）*</span><input v-model="salaryForm.piece_rate" type="number" min="0.01" step="0.01" required placeholder="须大于 0" /></label><label v-else-if="salaryForm.salary_type === 'commission'" class="field admin-salary-fields"><span>计件提成比例（%）*</span><input v-model="salaryForm.commission_rate" type="number" min="0.01" max="100" step="0.01" required placeholder="大于 0 且不超过 100" /></label><p v-else class="section-note admin-salary-fields">按员工已登记工序及对应工价核算，无需额外参数。</p><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving">保存工资配置</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'post-form'" :title="postForm.id ? '编辑岗位与权限' : '新建岗位'" description="权限点字典来自后端；管理员岗位始终拥有全部权限。" :saving="saving" wide @close="closeModal"><form @submit.prevent="savePost"><label class="field"><span>岗位名称 *</span><input v-model="postForm.name" maxlength="30" required placeholder="如：平车工、质检、组长" /></label><label class="admin-all-perms"><input v-model="postForm.all" type="checkbox" @change="toggleAllPermissions" /> 全部权限（保存为 *）</label><div v-if="!postForm.all && groups.length" class="admin-permission-groups"><fieldset v-for="group in groups" :key="group.module"><legend>{{ group.label }}</legend><label v-for="point in group.points" :key="point.key"><input v-model="postForm.permissions" type="checkbox" :value="point.key" />{{ point.label }}</label></fieldset></div><div v-else-if="!postForm.all" class="page-state">权限字典不可用，请关闭弹窗并重试。</div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving || !canSubmitPost"><span v-if="saving" class="spinner light" />保存岗位</button></div></form></AdminModal>
</template>

<style scoped>
.admin-head-actions,.admin-actions{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.admin-head-actions{justify-content:flex-end}.admin-capacity{margin:0 0 0 auto}.org-capacity-card,.org-post-filter-row,.organization-search-button,.org-salary-type-chips,.org-fixed-type-chips{display:none}.organization-search-row{display:contents}.admin-error{margin-bottom:16px}.admin-post-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding:20px}.admin-post-card{border:1px solid var(--line);border-radius:12px;padding:18px;min-width:0}.admin-post-card h3{font-size:16px;margin:0 0 5px}.admin-post-card p{font-size:12px;color:var(--muted);margin:0}.admin-permissions{display:flex;gap:6px;flex-wrap:wrap;margin:16px 0;min-height:30px}.admin-permissions span{font-size:11px;color:#675f59;background:#f5f2ee;border-radius:5px;padding:5px 7px}.admin-all-perms{display:flex;align-items:center;gap:8px;margin:18px 0;font-size:14px;font-weight:600}.admin-all-perms input,.admin-permission-groups input,.admin-assigned-processes input{width:16px;min-height:16px;margin:0}.admin-permission-groups{display:grid;grid-template-columns:1fr 1fr;gap:12px}.admin-permission-groups fieldset{border:1px solid var(--line);border-radius:10px;padding:12px;display:grid;grid-template-columns:1fr 1fr;gap:10px;min-width:0}.admin-permission-groups legend{font-size:12px;color:var(--orange-dark);font-weight:600;padding:0 5px}.admin-permission-groups label{font-size:12px;display:flex;align-items:flex-start;gap:6px;line-height:1.5}.admin-assigned-processes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;max-height:50vh;overflow:auto}.admin-assigned-processes label{display:flex;gap:8px;align-items:flex-start;border:1px solid var(--line);border-radius:9px;padding:11px;font-size:13px}.admin-assigned-processes small{display:block;color:var(--muted);font-size:10px;margin-top:4px}.admin-salary-fields{margin-top:16px}.admin-mobile-actions{margin-top:13px}.form-error{margin-top:14px}
@media(max-width:900px){.admin-post-grid{grid-template-columns:1fr}.admin-permission-groups{grid-template-columns:1fr}.admin-assigned-processes{grid-template-columns:1fr 1fr}.admin-actions{justify-content:flex-start}}
@media(max-width:760px){
  .admin-head-actions{gap:5px;justify-content:flex-end}.admin-head-actions .button{padding:8px 10px;font-size:12px}.admin-capacity{display:none}.welcome>.admin-head-actions .secondary,.welcome>.admin-head-actions .organization-invite-head{display:none}.organization-section-filter{margin-bottom:12px}.organization-section-tabs{width:100%;padding:4px;border-radius:12px}.organization-section-tabs button{flex:1;border-radius:999px}.organization-section-tabs button.active{background:var(--orange-soft);color:var(--orange);box-shadow:none}
  .org-capacity-card{display:block;margin-bottom:12px;padding:15px 16px;border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.org-capacity-card>div:first-child{display:flex;align-items:center;gap:10px}.org-capacity-card h3{margin:0;font-size:15px}.org-capacity-card strong{margin-left:auto;font-size:13px;font-variant-numeric:tabular-nums}.org-capacity-invite{margin-left:auto;padding:7px 13px;border:0;border-radius:999px;background:var(--orange);color:#fff;font-size:11px;white-space:nowrap}.org-capacity-card strong+.org-capacity-invite{margin-left:4px}.org-capacity-track{height:6px;margin-top:12px;overflow:hidden;border-radius:999px;background:var(--line)}.org-capacity-track span{display:block;height:100%;border-radius:999px;background:var(--orange)}.org-capacity-fallback .org-capacity-invite{margin-left:auto}
  .organization-employee-filter{display:block;margin-bottom:10px}.employee-status-tabs{display:flex;width:100%;gap:4px;padding:4px;border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.employee-status-tabs button{flex:1;padding:8px 10px;border-radius:999px;color:var(--muted);font-size:12px}.employee-status-tabs button.active{background:var(--orange-soft);color:var(--orange);box-shadow:none;font-weight:600}.organization-search-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;margin-top:10px;padding:9px 10px;border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.organization-search-row .search-box{width:100%;min-width:0;max-width:none}.organization-search-row input{min-height:36px;border:0;border-radius:999px;background:#f5f6f8;font-size:12px}.organization-search-button{display:inline-flex;min-height:36px;padding:7px 16px;font-size:12px}.employee-post-select{display:none}
  .org-post-filter-row{display:flex;align-items:center;gap:7px;margin-bottom:12px}.org-post-chips{display:flex;flex:1;min-width:0;gap:7px;overflow-x:auto;padding:2px 1px;scrollbar-width:none}.org-post-chips button,.org-posts-entry{flex:0 0 auto;padding:7px 13px;border:0;border-radius:999px;background:#fff;color:var(--text-secondary);box-shadow:0 2px 8px rgba(31,35,41,.06);font-size:11px;white-space:nowrap}.org-post-chips button.active{background:var(--orange);color:#fff;font-weight:600}.org-posts-entry{background:var(--orange-soft);color:var(--orange);box-shadow:none}
  .organization-employee-list.card{padding:0;background:transparent;box-shadow:none;overflow:visible}.organization-employee-list :deep(.page-state){border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.org-employee-list{gap:12px}.org-employee-card.mobile-row{padding:15px;border:0;border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(31,35,41,.06)}.org-employee-pinned{outline:1px solid #ffd9bd}.org-employee-main{display:flex;align-items:center;min-width:0}.org-employee-avatar{width:44px;height:44px;flex:0 0 44px;margin-right:11px;border-radius:50%;object-fit:cover;display:grid;place-items:center;background:var(--orange-soft);color:var(--orange);font-size:17px;font-weight:600}.org-avatar-0{background:#e8f3ff;color:#2f7ef6}.org-avatar-1{background:#ffece8;color:#f0603f}.org-avatar-2{background:#e8f7ee;color:#2ba471}.org-avatar-3{background:#fff4e5;color:#d97a00}.org-avatar-4{background:#f1ecff;color:#7a5af5}.org-avatar-5{background:#fdeaf4;color:#d64f97}.org-employee-info{flex:1;min-width:0}.org-employee-name{display:flex;align-items:center;gap:5px;flex-wrap:wrap}.org-employee-name h3{margin:0;font-size:15px}.org-employee-name .badge{font-size:9px;padding:3px 6px}.org-employee-gender{font-size:13px}.org-employee-gender.male{color:#2f7ef6}.org-employee-gender.female{color:#f0568c}.org-employee-badge{padding:2px 6px;border-radius:4px;background:var(--orange-soft);color:var(--orange);font-size:9px}.org-pinned-star{color:#f7ba1e;font-size:13px}.org-employee-info>p{margin:5px 0 0;color:var(--muted);font-size:11px;line-height:1.6;overflow-wrap:anywhere}.org-pin-button{flex:0 0 auto;align-self:flex-start;padding:2px 4px;border:0;background:transparent;color:#c9cdd4;font-size:23px;line-height:1}.org-pin-button.active{color:#f7ba1e}.org-employee-menu{position:relative;flex:0 0 auto;align-self:flex-start}.org-employee-menu summary{padding:0 0 5px 6px;color:var(--text-secondary);font-size:25px;font-weight:700;line-height:1;cursor:pointer;list-style:none}.org-employee-menu summary::-webkit-details-marker{display:none}.org-employee-menu[open] summary{color:var(--orange)}.org-employee-menu-actions{position:absolute;z-index:5;top:29px;right:0;display:grid;grid-template-columns:1fr 1fr;gap:7px;width:190px;padding:11px;border-radius:12px;background:#fff;box-shadow:0 8px 28px rgba(31,35,41,.18)}.org-employee-menu-actions .button:last-child:nth-child(odd){grid-column:1/-1}.org-employee-subline{display:flex;justify-content:space-between;gap:10px;margin:12px 0 0;padding-top:10px;border-top:1px solid #f2f3f5;color:var(--muted);font-size:10px}.org-review-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}.org-review-actions .button{min-width:70px}
  .org-salary-type-select,.org-fixed-type-select{display:none}.org-salary-type-chips,.org-fixed-type-chips{display:flex;gap:7px;flex-wrap:wrap}.org-salary-type-chips{margin-bottom:15px}.org-salary-type-chips button,.org-fixed-type-chips button{padding:7px 13px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--text-secondary);font-size:11px}.org-salary-type-chips button.active,.org-fixed-type-chips button.active{border-color:var(--orange);background:var(--orange);color:#fff}.org-fixed-type-chips{align-self:center;margin:0}.organization-invite-form .field{padding:9px 2px;border-bottom:1px solid var(--line)}.organization-invite-form .field>span{font-size:12px}.organization-invite-form input,.organization-invite-form select{padding:4px 0;border:0;border-radius:0;min-height:32px;background:transparent}.organization-invite-form .form-actions .button{flex:1}
  .admin-post-grid{padding:14px}.admin-permission-groups fieldset{grid-template-columns:1fr}.admin-assigned-processes{grid-template-columns:1fr}
}
@media(max-width:350px){.org-employee-card.mobile-row{padding:13px}.org-employee-avatar{width:40px;height:40px;flex-basis:40px;margin-right:9px}.org-employee-name{gap:4px}.org-employee-name .badge{font-size:8px}.org-post-chips button,.org-posts-entry{padding-inline:11px}.organization-search-row{padding:8px}.org-capacity-card{padding-inline:13px}}
</style>
