<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows, upload } from '../api'
import { money, number } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import AdminModal from '../components/AdminModal.vue'
import AdminPagination from '../components/AdminPagination.vue'
import MobileLoadMore from '../components/MobileLoadMore.vue'

const emit = defineEmits(['toast'])
const props = defineProps({ section: { type: String, default: '' } })
const tabs = [['styles', '款式'], ['processes', '工序'], ['templates', '工序模板'], ['customers', '客户'], ['groups', '客户分组']]
const active = ref(tabs.some(([key]) => key === props.section) ? props.section : 'styles')
const visibleTabs = computed(() => {
  if (!props.section) return tabs
  if (props.section === 'customers' || props.section === 'groups') return [['groups', '客户分组'], ['customers', '全部客户']]
  return tabs.filter(([key]) => key === active.value)
})
const pageTitle = computed(() => props.section
  ? ({ styles: '款式管理', processes: '工序管理', templates: '工序模板', customers: '客户管理', groups: '客户管理' }[active.value] || '基础档案')
  : '基础档案')
const pageDescription = computed(() => props.section
  ? ({ styles: '维护款式、图片、工艺说明及工序工价。', processes: '维护本公司工序、默认工价与登记属性。', templates: '将常用工序与工价保存为可复用模板。', customers: '维护客户分组、客户档案与联系方式。', groups: '维护客户分组、客户档案与联系方式。' }[active.value])
  : '统一维护本公司客户、款式、工序及工序模板。')
const data = ref({ count: 0 })
const loading = ref(true)
const loadingMore = ref(false)
const error = ref('')
const appendError = ref('')
const filters = reactive({ keyword: '', customer: '', group: '', is_fixed: '', page: 1 })
const customerOptions = ref([])
const groupOptions = ref([])
const processOptions = ref([])
const optionError = ref('')
const busy = ref('')
const modal = ref('')
const saving = ref(false)
const uploading = ref('')
const formError = ref('')
const styleForm = reactive({ id: 0, style_no: '', name: '', customer: '', fabric: '', remark: '', craft_note: '', images: [], craft_images: [], video: '', process_items: [] })
const processForm = reactive({ id: 0, name: '', default_price: '0', is_fixed: false, is_key: false, hide_price: false, sort: 0 })
const templateForm = reactive({ id: 0, name: '', items: [] })
const customerForm = reactive({ id: 0, customer_no: '', name: '', phone: '', group: '', remark: '' })
const groupForm = reactive({ id: 0, name: '' })
let searchTimer
const list = computed(() => rows(data.value))
const activeLabel = computed(() => tabs.find((tab) => tab[0] === active.value)?.[1] || '')
const hasMore = computed(() => list.value.length < Number(data.value.count || 0))

async function load(append = false) {
  if (!append && window.matchMedia('(max-width: 760px)').matches) filters.page = 1
  if (append) loadingMore.value = true
  else loading.value = true
  error.value = ''; appendError.value = ''
  const requestedPage = filters.page
  const paths = { styles: '/styles', processes: '/processes', templates: '/process-templates', customers: '/customers', groups: '/customer-groups' }
  const params = { page: requestedPage, page_size: 20 }
  if (active.value !== 'groups' && filters.keyword.trim()) params.keyword = filters.keyword.trim()
  if (active.value === 'styles' && filters.customer) params.customer_id = filters.customer
  if (active.value === 'customers' && filters.group) params.group = filters.group
  if (active.value === 'processes' && filters.is_fixed !== '') params.is_fixed = filters.is_fixed
  try {
    const result = await api(paths[active.value], { params })
    data.value = append
      ? { ...result, results: [...list.value, ...rows(result)] }
      : result
  } catch (e) {
    if (append) { appendError.value = e.message; filters.page = Math.max(1, requestedPage - 1) }
    else { error.value = e.message; data.value = { count: 0 } }
  } finally { loading.value = false; loadingMore.value = false }
}
function loadMore() {
  if (!hasMore.value || loadingMore.value || loading.value) return
  filters.page += 1
  load(true)
}
async function loadOptions() {
  optionError.value = ''
  const results = await Promise.allSettled([
    api('/customers', { params: { page: 1, page_size: 200 } }),
    api('/customer-groups', { params: { page: 1, page_size: 200 } }),
    api('/processes', { params: { page: 1, page_size: 200 } }),
  ])
  const labels = ['客户', '客户分组', '工序']
  const failed = []
  results.forEach((result, index) => {
    if (result.status === 'fulfilled') {
      const values = rows(result.value)
      if (index === 0) customerOptions.value = values
      else if (index === 1) groupOptions.value = values
      else processOptions.value = values
    } else failed.push(`${labels[index]}：${result.reason.message}`)
  })
  optionError.value = failed.join('；')
}
function refresh() { load(); loadOptions() }
function switchTab(tab) {
  active.value = tab; filters.keyword = ''; filters.page = 1; error.value = ''
  load()
}
function search() { clearTimeout(searchTimer); searchTimer = setTimeout(() => { filters.page = 1; load() }, 300) }
function resetPage() { filters.page = 1; load() }
function openModal(name) { formError.value = ''; modal.value = name }
function closeModal() { if (!saving.value && !uploading.value) modal.value = '' }
function processName(id) { return processOptions.value.find((item) => item.id === Number(id))?.name || `工序 #${id}` }
function addProcessItem(target) { target.push({ process_id: '', price: '', sort: target.length + 1, allow_skip: true }) }
function fillDefaultPrice(item) {
  const process = processOptions.value.find((option) => option.id === Number(item.process_id))
  if (process && item.price === '') item.price = String(process.default_price ?? '0')
}
function validateItems(items) {
  const ids = new Set()
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index]
    if (!item.process_id) return `第 ${index + 1} 行请选择本公司工序`
    if (ids.has(Number(item.process_id))) return `第 ${index + 1} 行工序重复`
    ids.add(Number(item.process_id))
    if (item.price === '' || !Number.isFinite(Number(item.price)) || Number(item.price) < 0) return `第 ${index + 1} 行工价须为不小于 0 的数字`
  }
  return ''
}
async function uploadStyleMedia(event, target) {
  const input = event.target
  const file = input.files?.[0]
  if (!file) return
  const video = target === 'video'
  const extension = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : ''
  const allowed = video ? ['mp4', 'm4v', 'mov', '3gp'] : ['jpg', 'jpeg', 'png', 'gif', 'webp']
  const maxSize = video ? 100 * 1024 * 1024 : 5 * 1024 * 1024
  formError.value = ''
  if (!allowed.includes(extension)) { formError.value = video ? '视频仅支持 mp4/m4v/mov/3gp' : '图片仅支持 jpg/jpeg/png/gif/webp'; input.value = ''; return }
  if (file.size > maxSize) { formError.value = video ? '视频不能超过 100MB' : '图片不能超过 5MB'; input.value = ''; return }
  uploading.value = target
  try {
    const result = await upload(video ? '/uploads/video' : '/uploads', file)
    if (!result.url?.startsWith('/media/')) throw new Error('服务器未返回有效媒体路径')
    if (video) styleForm.video = result.url
    else if (!styleForm[target].includes(result.url)) styleForm[target].push(result.url)
  } catch (e) { formError.value = e.message } finally { uploading.value = ''; input.value = '' }
}
function openStyle(style = null) {
  uploading.value = ''
  Object.assign(styleForm, {
    id: style?.id || 0, style_no: style?.style_no || '', name: style?.name || '', customer: style?.customer || '',
    fabric: style?.fabric || '', remark: style?.remark || '', craft_note: style?.craft_note || '',
    images: [...(style?.images || (style?.image ? [style.image] : []))], craft_images: [...(style?.craft_images || [])], video: style?.video || '',
    process_items: (style?.processes || []).map((item) => ({ process_id: item.process, price: String(item.price ?? '0'), sort: item.sort || 0, allow_skip: item.allow_skip !== false })),
  })
  openModal('style')
}
async function saveStyle() {
  if (uploading.value) { formError.value = '请等待媒体上传完成'; return }
  formError.value = validateItems(styleForm.process_items)
  if (!styleForm.style_no.trim() || !styleForm.name.trim()) { formError.value = '款号和款式名称不能为空'; return }
  if (formError.value) return
  saving.value = true
  try {
    const body = {
      style_no: styleForm.style_no.trim(), name: styleForm.name.trim(), customer: styleForm.customer || null,
      fabric: styleForm.fabric.trim(), remark: styleForm.remark.trim(), craft_note: styleForm.craft_note.trim(),
      images: styleForm.images, craft_images: styleForm.craft_images, video: styleForm.video,
      process_items: styleForm.process_items.map((item, index) => ({ process_id: Number(item.process_id), price: String(item.price), sort: Number(item.sort) || index + 1, allow_skip: item.allow_skip !== false })),
    }
    await api(styleForm.id ? `/styles/${styleForm.id}` : '/styles', { method: styleForm.id ? 'PUT' : 'POST', body })
    modal.value = ''; emit('toast', styleForm.id ? '款式及工序已保存' : '款式已创建'); await load()
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function copyStyle(style) {
  busy.value = `copy-${style.id}`; error.value = ''
  try { const created = await api(`/styles/${style.id}/copy`, { method: 'POST', body: {} }); emit('toast', `已复制为 ${created.style_no}`); await load() } catch (e) { error.value = e.message } finally { busy.value = '' }
}
async function syncStyle(style) {
  busy.value = `sync-${style.id}`; error.value = ''
  try {
    let result = await api(`/styles/${style.id}/sync-process`, { method: 'POST', body: { confirm: false } })
    if (result.need_confirm) {
      if (!window.confirm(`该款式有 ${result.ticket_count} 张已登记菲票。确定同步最新工序配置并写入操作日志吗？`)) return
      result = await api(`/styles/${style.id}/sync-process`, { method: 'POST', body: { confirm: true } })
    }
    emit('toast', `工序配置已同步，涉及 ${result.ticket_count || 0} 张已登记菲票`)
  } catch (e) { error.value = e.message } finally { busy.value = '' }
}
function openProcess(process = null) {
  Object.assign(processForm, { id: process?.id || 0, name: process?.name || '', default_price: String(process?.default_price ?? '0'), is_fixed: Boolean(process?.is_fixed), is_key: Boolean(process?.is_key), hide_price: Boolean(process?.hide_price), sort: process?.sort || 0 })
  openModal('process')
}
async function saveProcess() {
  formError.value = ''
  if (!processForm.name.trim()) { formError.value = '请输入工序名称'; return }
  if (processForm.default_price === '' || !Number.isFinite(Number(processForm.default_price)) || Number(processForm.default_price) < 0) { formError.value = '默认工价须为不小于 0 的数字'; return }
  saving.value = true
  try {
    const body = { name: processForm.name.trim(), default_price: String(processForm.default_price), is_fixed: processForm.is_fixed, is_key: processForm.is_key, hide_price: processForm.hide_price, sort: Number(processForm.sort) || 0 }
    await api(processForm.id ? `/processes/${processForm.id}` : '/processes', { method: processForm.id ? 'PUT' : 'POST', body })
    modal.value = ''; emit('toast', processForm.id ? '工序已更新' : '工序已创建'); await Promise.all([load(), loadOptions()])
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
function openTemplate(template = null) {
  Object.assign(templateForm, { id: template?.id || 0, name: template?.name || '', items: (template?.items || []).map((item) => ({ process_id: item.process_id, price: String(item.price ?? '0') })) })
  openModal('template')
}
async function saveTemplate() {
  formError.value = validateItems(templateForm.items)
  if (!templateForm.name.trim()) { formError.value = '请输入模板名称'; return }
  if (formError.value) return
  saving.value = true
  try {
    const body = { name: templateForm.name.trim(), items: templateForm.items.map((item) => ({ process_id: Number(item.process_id), price: String(item.price) })) }
    await api(templateForm.id ? `/process-templates/${templateForm.id}` : '/process-templates', { method: templateForm.id ? 'PUT' : 'POST', body })
    modal.value = ''; emit('toast', templateForm.id ? '模板已更新' : '模板已创建'); await load()
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
function openCustomer(customer = null) {
  Object.assign(customerForm, { id: customer?.id || 0, customer_no: customer?.customer_no || '', name: customer?.name || '', phone: customer?.phone || '', group: customer?.group || '', remark: customer?.remark || '' })
  openModal('customer')
}
async function saveCustomer() {
  formError.value = ''
  if (!customerForm.name.trim()) { formError.value = '请输入客户名称'; return }
  saving.value = true
  try {
    const body = { customer_no: customerForm.customer_no.trim(), name: customerForm.name.trim(), phone: customerForm.phone.trim(), group: customerForm.group || null, remark: customerForm.remark.trim() }
    await api(customerForm.id ? `/customers/${customerForm.id}` : '/customers', { method: customerForm.id ? 'PUT' : 'POST', body })
    modal.value = ''; emit('toast', customerForm.id ? '客户已更新' : '客户已创建'); await Promise.all([load(), loadOptions()])
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
function openGroup(group = null) { Object.assign(groupForm, { id: group?.id || 0, name: group?.name || '' }); openModal('group') }
async function saveGroup() {
  formError.value = ''
  if (!groupForm.name.trim()) { formError.value = '请输入分组名称'; return }
  saving.value = true
  try {
    await api(groupForm.id ? `/customer-groups/${groupForm.id}` : '/customer-groups', { method: groupForm.id ? 'PUT' : 'POST', body: { name: groupForm.name.trim() } })
    modal.value = ''; emit('toast', groupForm.id ? '分组已更新' : '分组已创建'); await Promise.all([load(), loadOptions()])
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function remove(kind, item) {
  const config = {
    style: [`款式“${item.style_no} ${item.name}”`, `/styles/${item.id}`, '已有菲票的款式无法删除。'],
    process: [`工序“${item.name}”`, `/processes/${item.id}`, '已被款式使用或有登记记录的工序无法删除。'],
    template: [`工序模板“${item.name}”`, `/process-templates/${item.id}`, ''],
    customer: [`客户“${item.name}”`, `/customers/${item.id}`, ''],
    group: [`客户分组“${item.name}”`, `/customer-groups/${item.id}`, '组内客户将变为未分组。'],
  }[kind]
  if (!window.confirm(`确定删除${config[0]}吗？${config[2]}`)) return
  busy.value = `${kind}-${item.id}`; error.value = ''
  try {
    await api(config[1], { method: 'DELETE' }); emit('toast', '已删除')
    if (list.value.length === 1 && filters.page > 1) filters.page -= 1
    await Promise.all([load(), loadOptions()])
  } catch (e) { error.value = e.message } finally { busy.value = '' }
}
function openCreate() { ({ styles: openStyle, processes: openProcess, templates: openTemplate, customers: openCustomer, groups: openGroup })[active.value]() }
onMounted(() => { load(); loadOptions() })
</script>

<template>
  <div class="welcome"><div><h2>{{ pageTitle }}</h2><p>{{ pageDescription }}</p></div><div class="admin-master-head"><button class="button secondary" @click="refresh"><AppIcon name="refresh" :size="16" />刷新</button><button class="button primary" @click="openCreate">新建{{ activeLabel }}</button></div></div>
  <div v-if="visibleTabs.length > 1" class="filter-bar"><div class="segmented admin-master-tabs"><button v-for="tab in visibleTabs" :key="tab[0]" :class="{ active: active === tab[0] }" @click="switchTab(tab[0])">{{ tab[1] }}</button></div></div>
  <div class="filter-bar"><label v-if="active !== 'groups'" class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.keyword" :placeholder="active === 'styles' ? '搜索款号或名称' : active === 'customers' ? '搜索客户编号或名称' : '搜索名称'" @input="search" /></label><select v-if="active === 'styles'" v-model="filters.customer" aria-label="按客户筛选" @change="resetPage"><option value="">全部客户</option><option v-for="customer in customerOptions" :key="customer.id" :value="customer.id">{{ customer.name }}</option></select><select v-if="active === 'customers'" v-model="filters.group" aria-label="按客户分组筛选" @change="resetPage"><option value="">全部分组</option><option v-for="group in groupOptions" :key="group.id" :value="group.id">{{ group.name }}</option></select><select v-if="active === 'processes'" v-model="filters.is_fixed" aria-label="按固定工序筛选" @change="resetPage"><option value="">全部工序</option><option value="1">固定工序</option><option value="0">非固定工序</option></select></div>
  <p v-if="optionError" class="form-error admin-option-error">部分选项加载失败：{{ optionError }} <button class="button ghost small" @click="loadOptions">重试选项</button></p>
  <section class="card table-card"><PageState :loading="loading" :error="error" :empty="!list.length" :empty-text="`暂无${activeLabel}档案`" @retry="load">
    <template v-if="active === 'styles'"><div class="table-wrap desktop-table"><table><thead><tr><th>款号 / 名称</th><th>客户</th><th>总单价</th><th class="numeric">工序</th><th class="numeric">菲票</th><th>操作</th></tr></thead><tbody><tr v-for="style in list" :key="style.id"><td><span class="cell-title">{{ style.style_no }}</span><small class="cell-subtitle">{{ style.name }}</small></td><td>{{ style.customer_name || '未关联' }}</td><td>{{ money(style.price) }}</td><td class="numeric">{{ style.process_count || style.processes?.length || 0 }}</td><td class="numeric">{{ number(style.ticket_count) }}</td><td><div class="admin-row-actions"><button class="button secondary small" :disabled="!!busy" @click="openStyle(style)">编辑 / 工序</button><button class="button ghost small" :disabled="!!busy" @click="syncStyle(style)">同步工序</button><button class="button ghost small" :disabled="!!busy" @click="copyStyle(style)">复制</button><button class="button danger small" :disabled="!!busy" @click="remove('style', style)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="style in list" :key="style.id" class="mobile-row mobile-style-row"><div class="mobile-style-card-body"><img v-if="style.image" class="mobile-style-image" :src="style.image" :alt="`${style.name}款式图片`" /><div v-else class="mobile-style-image mobile-style-placeholder">图</div><div class="mobile-style-content"><div class="mobile-row-head"><div><div class="mobile-row-title">{{ style.style_no }}</div><small class="cell-subtitle">{{ style.name }}</small></div><strong>{{ money(style.price) }}</strong></div><div class="mobile-meta"><span>客户 <strong>{{ style.customer_name || '未关联' }}</strong></span><span>工序 / 菲票 <strong>{{ style.process_count || style.processes?.length || 0 }} / {{ style.ticket_count || 0 }}</strong></span></div><div class="admin-row-actions admin-mobile-actions"><button class="button secondary small" :disabled="!!busy" @click="openStyle(style)">编辑 / 工序</button><button class="button ghost small" :disabled="!!busy" @click="syncStyle(style)">同步工序</button><button class="button ghost small" :disabled="!!busy" @click="copyStyle(style)">复制</button><button class="button danger small" :disabled="!!busy" @click="remove('style', style)">删除</button></div></div></div></article></div></template>
    <template v-else-if="active === 'processes'"><div class="table-wrap desktop-table"><table><thead><tr><th>工序名称</th><th class="numeric">默认工价</th><th>属性</th><th class="numeric">排序</th><th>操作</th></tr></thead><tbody><tr v-for="process in list" :key="process.id"><td class="cell-title">{{ process.name }}</td><td class="numeric">{{ money(process.default_price) }}</td><td><span v-if="process.is_fixed" class="badge done">固定</span> <span v-if="process.is_key" class="badge producing">关键</span><small v-if="process.hide_price" class="cell-subtitle">隐藏单价</small></td><td class="numeric">{{ process.sort }}</td><td><div class="admin-row-actions"><button class="button secondary small" :disabled="!!busy" @click="openProcess(process)">编辑</button><button class="button danger small" :disabled="!!busy" @click="remove('process', process)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="process in list" :key="process.id" class="mobile-row"><div class="mobile-row-head"><span class="mobile-row-title">{{ process.name }}</span><strong>{{ money(process.default_price) }}</strong></div><div class="mobile-meta"><span>固定工序 <strong>{{ process.is_fixed ? '是' : '否' }}</strong></span><span>关键 / 隐藏价 <strong>{{ process.is_key ? '关键' : '普通' }} / {{ process.hide_price ? '隐藏' : '显示' }}</strong></span></div><div class="admin-row-actions admin-mobile-actions"><button class="button secondary small" :disabled="!!busy" @click="openProcess(process)">编辑</button><button class="button danger small" :disabled="!!busy" @click="remove('process', process)">删除</button></div></article></div></template>
    <template v-else-if="active === 'templates'"><div class="table-wrap desktop-table"><table><thead><tr><th>模板名称</th><th>工序配置</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="template in list" :key="template.id"><td class="cell-title">{{ template.name }}</td><td>{{ template.items?.length || 0 }} 道工序<small class="cell-subtitle">{{ (template.items || []).slice(0, 4).map((item) => processName(item.process_id)).join('、') || '未配置' }}</small></td><td>{{ (template.created_at || '').slice(0, 10) }}</td><td><div class="admin-row-actions"><button class="button secondary small" :disabled="!!busy" @click="openTemplate(template)">编辑</button><button class="button danger small" :disabled="!!busy" @click="remove('template', template)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="template in list" :key="template.id" class="mobile-row"><div class="mobile-row-head"><span class="mobile-row-title">{{ template.name }}</span><span class="badge">{{ template.items?.length || 0 }} 道</span></div><small class="cell-subtitle">{{ (template.items || []).map((item) => processName(item.process_id)).join('、') || '未配置工序' }}</small><div class="admin-row-actions admin-mobile-actions"><button class="button secondary small" :disabled="!!busy" @click="openTemplate(template)">编辑</button><button class="button danger small" :disabled="!!busy" @click="remove('template', template)">删除</button></div></article></div></template>
    <template v-else-if="active === 'customers'"><div class="table-wrap desktop-table"><table><thead><tr><th>客户编号 / 名称</th><th>分组</th><th>联系电话</th><th>备注</th><th>操作</th></tr></thead><tbody><tr v-for="customer in list" :key="customer.id"><td><span class="cell-title">{{ customer.customer_no }}</span><small class="cell-subtitle">{{ customer.name }}</small></td><td>{{ customer.group_name || '未分组' }}</td><td>{{ customer.phone || '—' }}</td><td>{{ customer.remark || '—' }}</td><td><div class="admin-row-actions"><button class="button secondary small" :disabled="!!busy" @click="openCustomer(customer)">编辑</button><button class="button danger small" :disabled="!!busy" @click="remove('customer', customer)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="customer in list" :key="customer.id" class="mobile-row"><div class="mobile-row-head"><div><div class="mobile-row-title">{{ customer.name }}</div><small class="cell-subtitle">{{ customer.customer_no }}</small></div><span class="badge">{{ customer.group_name || '未分组' }}</span></div><div class="mobile-meta"><span>电话 <strong>{{ customer.phone || '—' }}</strong></span><span>备注 <strong>{{ customer.remark || '—' }}</strong></span></div><div class="admin-row-actions admin-mobile-actions"><button class="button secondary small" :disabled="!!busy" @click="openCustomer(customer)">编辑</button><button class="button danger small" :disabled="!!busy" @click="remove('customer', customer)">删除</button></div></article></div></template>
    <template v-else><div class="table-wrap desktop-table"><table><thead><tr><th>分组名称</th><th class="numeric">客户数</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="group in list" :key="group.id"><td class="cell-title">{{ group.name }}</td><td class="numeric">{{ group.customer_count || 0 }}</td><td>{{ (group.created_at || '').slice(0, 10) }}</td><td><div class="admin-row-actions"><button class="button secondary small" :disabled="!!busy" @click="openGroup(group)">重命名</button><button class="button danger small" :disabled="!!busy" @click="remove('group', group)">删除</button></div></td></tr></tbody></table></div><div class="mobile-cards"><article v-for="group in list" :key="group.id" class="mobile-row"><div class="mobile-row-head"><span class="mobile-row-title">{{ group.name }}</span><span class="badge">{{ group.customer_count || 0 }} 位客户</span></div><div class="admin-row-actions"><button class="button secondary small" :disabled="!!busy" @click="openGroup(group)">重命名</button><button class="button danger small" :disabled="!!busy" @click="remove('group', group)">删除</button></div></article></div></template>
  </PageState><MobileLoadMore :loading="loadingMore" :error="appendError" :has-more="hasMore" :count="list.length" @more="loadMore" /><AdminPagination class="desktop-pagination" :page="filters.page" :count="data.count || 0" :disabled="loading" @change="filters.page = $event; load()" /></section>
  <p class="section-note">款式图片与工艺图片单张不超过 5MB；工艺视频不超过 100MB。上传后仍需保存款式，才会写入档案。</p>

  <AdminModal v-if="modal === 'style'" :title="styleForm.id ? '编辑款式与工序' : '新建款式'" description="媒体、工序及工价随款式一次保存；上传完成前不可关闭或提交。" :saving="saving || !!uploading" wide @close="closeModal"><form @submit.prevent="saveStyle"><div class="form-grid"><label class="field"><span>款号 *</span><input v-model="styleForm.style_no" required maxlength="64" /></label><label class="field"><span>款式名称 *</span><input v-model="styleForm.name" required maxlength="100" /></label><label class="field"><span>本公司客户</span><select v-model="styleForm.customer"><option value="">不关联客户</option><option v-for="customer in customerOptions" :key="customer.id" :value="customer.id">{{ customer.customer_no }} · {{ customer.name }}</option></select></label><label class="field"><span>面料</span><input v-model="styleForm.fabric" maxlength="100" /></label><label class="field full"><span>备注</span><textarea v-model="styleForm.remark" rows="2" maxlength="500" /></label><label class="field full"><span>工艺说明</span><textarea v-model="styleForm.craft_note" rows="3" /></label></div><section class="admin-media-section"><h3>款式媒体</h3><div class="admin-media-grid"><div><label class="field"><span>上传款式图片</span><input type="file" accept=".jpg,.jpeg,.png,.gif,.webp" :disabled="!!uploading" @change="uploadStyleMedia($event, 'images')" /></label><div class="admin-media-list"><div v-for="(url, index) in styleForm.images" :key="url" class="admin-media-item"><img :src="url" :alt="`款式图片 ${index + 1}`" /><button type="button" class="button danger small" :disabled="!!uploading" @click="styleForm.images.splice(index, 1)">移除款式图片</button></div></div></div><div><label class="field"><span>上传工艺图片</span><input type="file" accept=".jpg,.jpeg,.png,.gif,.webp" :disabled="!!uploading" @change="uploadStyleMedia($event, 'craft_images')" /></label><div class="admin-media-list"><div v-for="(url, index) in styleForm.craft_images" :key="url" class="admin-media-item"><img :src="url" :alt="`工艺图片 ${index + 1}`" /><button type="button" class="button danger small" :disabled="!!uploading" @click="styleForm.craft_images.splice(index, 1)">移除工艺图片</button></div></div></div><div><label class="field"><span>上传工艺视频</span><input type="file" accept=".mp4,.m4v,.mov,.3gp" :disabled="!!uploading" @change="uploadStyleMedia($event, 'video')" /></label><div v-if="styleForm.video" class="admin-media-item admin-video-item"><video :src="styleForm.video" controls preload="metadata" aria-label="工艺视频预览" /><button type="button" class="button danger small" :disabled="!!uploading" @click="styleForm.video = ''">移除工艺视频</button></div></div></div><p v-if="uploading" class="section-note" role="status">正在上传{{ uploading === 'video' ? '工艺视频' : uploading === 'images' ? '款式图片' : '工艺图片' }}…</p></section><div class="admin-editor-head"><h3>款式工序</h3><button type="button" class="button secondary small" @click="addProcessItem(styleForm.process_items)">添加工序</button></div><div v-if="!styleForm.process_items.length" class="admin-editor-empty">尚未配置工序，可保存空配置或点击“添加工序”。</div><div v-for="(item, index) in styleForm.process_items" :key="index" class="admin-process-editor"><label class="field"><span>工序 {{ index + 1 }}</span><select v-model="item.process_id" @change="fillDefaultPrice(item)"><option value="">请选择</option><option v-for="process in processOptions" :key="process.id" :value="process.id">{{ process.name }}</option></select></label><label class="field"><span>工价</span><input v-model="item.price" type="number" min="0" step="0.001" /></label><label class="field"><span>排序</span><input v-model.number="item.sort" type="number" step="1" /></label><label class="admin-check"><input v-model="item.allow_skip" type="checkbox" />允许跳序</label><button type="button" class="button danger small" @click="styleForm.process_items.splice(index, 1)">移除</button></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving || !!uploading" @click="closeModal">取消</button><button class="button primary" :disabled="saving || !!uploading"><span v-if="saving" class="spinner light" />保存款式与工序</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'process'" :title="processForm.id ? '编辑工序' : '新建工序'" :saving="saving" @close="closeModal"><form @submit.prevent="saveProcess"><div class="form-grid"><label class="field full"><span>工序名称 *</span><input v-model="processForm.name" required maxlength="50" /></label><label class="field"><span>默认工价 *</span><input v-model="processForm.default_price" type="number" min="0" step="0.001" required /></label><label class="field"><span>排序</span><input v-model.number="processForm.sort" type="number" step="1" /></label></div><div class="admin-checks"><label><input v-model="processForm.is_fixed" type="checkbox" />固定工序（所有员工可选）</label><label><input v-model="processForm.is_key" type="checkbox" />关键工序</label><label><input v-model="processForm.hide_price" type="checkbox" />隐藏单价</label></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving">保存工序</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'template'" :title="templateForm.id ? '编辑工序模板' : '新建工序模板'" :saving="saving" wide @close="closeModal"><form @submit.prevent="saveTemplate"><label class="field"><span>模板名称 *</span><input v-model="templateForm.name" required maxlength="50" /></label><div class="admin-editor-head"><h3>模板工序</h3><button type="button" class="button secondary small" @click="addProcessItem(templateForm.items)">添加工序</button></div><div v-if="!templateForm.items.length" class="admin-editor-empty">尚未配置工序。</div><div v-for="(item, index) in templateForm.items" :key="index" class="admin-template-editor"><label class="field"><span>工序 {{ index + 1 }}</span><select v-model="item.process_id" @change="fillDefaultPrice(item)"><option value="">请选择</option><option v-for="process in processOptions" :key="process.id" :value="process.id">{{ process.name }}</option></select></label><label class="field"><span>工价</span><input v-model="item.price" type="number" min="0" step="0.001" /></label><button type="button" class="button danger small" @click="templateForm.items.splice(index, 1)">移除</button></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving">保存模板</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'customer'" :title="customerForm.id ? '编辑客户' : '新建客户'" description="客户编号留空时由后端自动生成。" :saving="saving" @close="closeModal"><form @submit.prevent="saveCustomer"><div class="form-grid"><label class="field"><span>客户编号</span><input v-model="customerForm.customer_no" maxlength="32" placeholder="留空自动取号" /></label><label class="field"><span>客户名称 *</span><input v-model="customerForm.name" required maxlength="100" /></label><label class="field"><span>本公司分组</span><select v-model="customerForm.group"><option value="">未分组</option><option v-for="group in groupOptions" :key="group.id" :value="group.id">{{ group.name }}</option></select></label><label class="field"><span>联系电话</span><input v-model="customerForm.phone" maxlength="30" /></label><label class="field full"><span>备注</span><textarea v-model="customerForm.remark" rows="3" maxlength="500" /></label></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving">保存客户</button></div></form></AdminModal>

  <AdminModal v-if="modal === 'group'" :title="groupForm.id ? '重命名客户分组' : '新建客户分组'" :saving="saving" @close="closeModal"><form @submit.prevent="saveGroup"><label class="field"><span>分组名称 *</span><input v-model="groupForm.name" required maxlength="50" placeholder="如：内销、外单、批发" /></label><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="closeModal">取消</button><button class="button primary" :disabled="saving">保存分组</button></div></form></AdminModal>
</template>

<style scoped>
.admin-master-head{display:flex;gap:8px}.admin-master-tabs{max-width:100%;overflow:auto}.admin-option-error{margin-bottom:16px}.admin-row-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.admin-mobile-actions{margin-top:14px}.admin-editor-head{display:flex;align-items:center;justify-content:space-between;margin:24px 0 12px}.admin-editor-head h3{font-size:14px;margin:0}.admin-editor-empty{padding:16px;background:#F5F6F8;border-radius:9px;color:var(--muted);font-size:12px}.admin-process-editor{display:grid;grid-template-columns:minmax(130px,1.5fr) minmax(90px,.7fr) 80px auto auto;align-items:end;gap:10px;padding:12px 0;border-bottom:1px solid var(--line)}.admin-template-editor{display:grid;grid-template-columns:minmax(160px,1fr) 140px auto;align-items:end;gap:12px;padding:12px 0;border-bottom:1px solid var(--line)}.admin-check{display:flex;align-items:center;gap:6px;min-height:40px;font-size:12px;white-space:nowrap}.admin-check input,.admin-checks input{width:16px;min-height:16px;margin:0}.admin-checks{display:flex;gap:18px;flex-wrap:wrap;margin-top:18px}.admin-checks label{display:flex;align-items:center;gap:7px;font-size:13px}.form-error{margin-top:15px}
.admin-media-section{margin-top:22px;padding-top:18px;border-top:1px solid var(--line)}.admin-media-section h3{font-size:14px;margin:0 0 14px}.admin-media-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.admin-media-list{display:grid;gap:10px;margin-top:12px}.admin-media-item{display:flex;align-items:center;gap:10px;min-width:0}.admin-media-item img{width:76px;height:76px;object-fit:cover;border:1px solid var(--line);border-radius:8px;background:#F5F6F8}.admin-media-item .button{flex-shrink:0}.admin-video-item{align-items:flex-start;flex-direction:column;margin-top:12px}.admin-video-item video{width:100%;max-height:180px;background:#111;border-radius:8px}
@media(max-width:760px){.desktop-pagination{display:none}.table-card{background:transparent;box-shadow:none;padding:0}.mobile-style-card-body{display:flex;gap:10px}.mobile-style-image{width:72px;height:72px;flex:none;border-radius:12px;object-fit:cover;background:var(--bg)}.mobile-style-placeholder{display:flex;align-items:center;justify-content:center;border:1px dashed var(--line);color:var(--muted)}.mobile-style-content{flex:1;min-width:0}.mobile-style-content .mobile-row-head strong{color:var(--orange);font-size:15px}.admin-master-head .secondary{display:none}.admin-master-head .button{font-size:12px;padding:8px 10px}.admin-process-editor{grid-template-columns:1fr 1fr;padding:15px 0}.admin-process-editor .field:first-child{grid-column:1/-1}.admin-process-editor .admin-check{grid-column:1}.admin-template-editor{grid-template-columns:1fr 100px}.admin-template-editor .button{grid-column:1/-1;justify-self:start}.admin-media-grid{grid-template-columns:1fr}}
</style>
