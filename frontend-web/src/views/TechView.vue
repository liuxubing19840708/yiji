<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../api'
import { dateTime, money, number } from '../format'
import { COMMON_COLORS, COMMON_SIZES } from '../spec-options'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import ImportDialog from '../components/ImportDialog.vue'

const emit = defineEmits(['toast'])
const props = defineProps({ section: { type: String, default: '' } })
const PAGE_SIZE = 20
const activeTab = ref(['materials', 'boms'].includes(props.section) ? props.section : 'materials')
const categoryText = { fabric: '面料', lining: '里料', accessory: '辅料', packing: '包装' }
const bomStatusText = { draft: '草稿', active: '生效', deactivated: '已停用' }
const kindText = { main: '面料', lining: '里料', accessory: '辅料' }
const materialStatusText = { active: '启用', disabled: '停用' }

const lists = reactive({ materials: { count: 0, results: [] }, boms: { count: 0, results: [] } })
const states = reactive({ materials: { loading: false, error: '' }, boms: { loading: false, error: '' } })
const filters = reactive({
  materials: { keyword: '', category: '', page: 1 },
  boms: { style_id: '', version: '', status: '', page: 1 },
})
const lookups = reactive({ styles: [], materials: [] })
const lookupLoaded = reactive({ styles: false, materials: false })
const lookupLoading = ref(false)
const lookupError = ref('')
const modal = ref('')
const saving = ref(false)
const formError = ref('')
const actionId = ref('')
const materialForm = reactive({})
const materialEditingId = ref(null)
const bomDetail = ref(null)
const bomDetailLoading = ref(false)
const bomDetailError = ref('')
const references = ref(null)
const referencesError = ref('')
const bomForm = reactive({ style: '', version: '', remark: '', items: [] })
const bomEditingId = ref(null)
const attrsText = ref('{}')
let materialSearchTimer

const currentList = computed(() => lists[activeTab.value])
const currentState = computed(() => states[activeTab.value])

async function loadAll(path, params = {}) {
  const result = []
  let page = 1
  while (true) {
    const data = await api(path, { params: { ...params, page, page_size: 100 } })
    const batch = rows(data)
    result.push(...batch)
    if (!Array.isArray(data) && result.length >= (data.count || 0)) break
    if (!batch.length || batch.length < 100) break
    page += 1
  }
  return result
}

async function ensureLookups(names, refresh = false) {
  const pending = names.filter((name) => refresh || !lookupLoaded[name])
  if (!pending.length) return
  lookupLoading.value = true
  lookupError.value = ''
  try {
    await Promise.all(pending.map(async (name) => {
      const path = name === 'styles' ? '/styles' : '/tech/materials'
      lookups[name] = await loadAll(path)
      lookupLoaded[name] = true
    }))
  } catch (e) {
    lookupError.value = e.message
    throw e
  } finally { lookupLoading.value = false }
}

async function loadList(name = activeTab.value) {
  const state = states[name]
  state.loading = true
  state.error = ''
  try {
    lists[name] = await api(name === 'materials' ? '/tech/materials' : '/tech/boms', {
      params: { ...filters[name], page_size: PAGE_SIZE },
    })
  } catch (e) { state.error = e.message } finally { state.loading = false }
}

function switchTab(name) {
  activeTab.value = name
  loadList(name)
  if (name === 'boms') ensureLookups(['styles']).catch(() => {})
}
function materialSearch() {
  clearTimeout(materialSearchTimer)
  materialSearchTimer = setTimeout(() => { filters.materials.page = 1; loadList('materials') }, 300)
}
function bomFilter() { filters.boms.page = 1; loadList('boms') }
function page(name, direction) {
  filters[name].page += direction
  loadList(name)
}
function hasMore(name) { return filters[name].page * PAGE_SIZE < (lists[name].count || 0) }
function setMaterialCategory(category) {
  if (filters.materials.category === category) return
  filters.materials.category = category
  filters.materials.page = 1
  loadList('materials')
}
function setBomStatus(status) {
  if (filters.boms.status === status) return
  filters.boms.status = status
  bomFilter()
}
async function loadMore(name) {
  if (!hasMore(name) || states[name].loading) return
  const nextPage = filters[name].page + 1
  states[name].loading = true
  states[name].error = ''
  try {
    const data = await api(name === 'materials' ? '/tech/materials' : '/tech/boms', {
      params: { ...filters[name], page: nextPage, page_size: PAGE_SIZE },
    })
    lists[name] = { ...data, results: [...lists[name].results, ...rows(data)] }
    filters[name].page = nextPage
  } catch (e) { states[name].error = e.message }
  finally { states[name].loading = false }
}
function materialSearchNow() {
  clearTimeout(materialSearchTimer)
  filters.materials.page = 1
  loadList('materials')
}
function showToast(message) { emit('toast', message) }
function materialLabel(id) {
  const item = lookups.materials.find((m) => Number(m.id) === Number(id))
  return item ? `${item.code} · ${item.name}（${item.unit}）` : '请选择物料'
}
function materialUnit(id) { return lookups.materials.find((m) => Number(m.id) === Number(id))?.unit || '—' }
function grossUsage(item) { return (Number(item.unit_usage || 0) * (1 + Number(item.loss_rate || 0) / 100)).toFixed(4) }

function emptyMaterial() {
  return {
    code: '', name: '', category: 'fabric', unit: '', unit_alt: '', weight_gsm: '', width_cm: '',
    shrink_rate: '', price_reference: '', price_unit: '', safety_stock: '0', status: 'active',
    legacy_code: '', attrs: {},
  }
}
function openMaterial(item = null) {
  materialEditingId.value = item?.id || null
  Object.assign(materialForm, emptyMaterial(), item || {})
  attrsText.value = JSON.stringify(item?.attrs || {}, null, 2)
  formError.value = ''
  modal.value = 'material'
}
function nullableNumber(value) { return value === '' || value === null || value === undefined ? null : Number(value) }
async function saveMaterial() {
  formError.value = ''
  if (!materialForm.code?.trim() || !materialForm.name?.trim() || !materialForm.unit?.trim()) {
    formError.value = '请填写物料编码、品名和主单位'
    return
  }
  let attrs
  try {
    attrs = JSON.parse(attrsText.value || '{}')
    if (!attrs || Array.isArray(attrs) || typeof attrs !== 'object') throw new Error()
  } catch { formError.value = '扩展属性须为合法 JSON 对象'; return }
  if (materialForm.unit_alt && (materialForm.weight_gsm === '' || materialForm.width_cm === '')) {
    formError.value = '填写副单位后，必须同时填写克重与幅宽'
    return
  }
  saving.value = true
  try {
    const body = {
      ...materialForm,
      code: materialForm.code.trim(), name: materialForm.name.trim(), unit: materialForm.unit.trim(),
      unit_alt: materialForm.unit_alt || '', weight_gsm: nullableNumber(materialForm.weight_gsm),
      width_cm: nullableNumber(materialForm.width_cm), shrink_rate: nullableNumber(materialForm.shrink_rate),
      price_reference: nullableNumber(materialForm.price_reference),
      safety_stock: materialForm.safety_stock === '' ? 0 : Number(materialForm.safety_stock), attrs,
    }
    const id = materialEditingId.value
    await api(id ? `/tech/materials/${id}` : '/tech/materials', { method: id ? 'PUT' : 'POST', body })
    modal.value = ''
    lookupLoaded.materials = false
    showToast(id ? '物料已更新' : '物料已创建')
    await loadList('materials')
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function deleteMaterial(item) {
  if (!window.confirm(`确定删除物料“${item.code} · ${item.name}”吗？\n删除后不再出现在主档和选择列表中，已有 BOM 明细仍保留历史引用。`)) return
  actionId.value = `material-${item.id}`
  states.materials.error = ''
  try {
    await api(`/tech/materials/${item.id}`, { method: 'DELETE' })
    lookupLoaded.materials = false
    showToast('物料已删除')
    if (lists.materials.results.length === 1 && filters.materials.page > 1) filters.materials.page -= 1
    await loadList('materials')
  } catch (e) { states.materials.error = e.message } finally { actionId.value = '' }
}
async function toggleMaterialStatus(item) {
  const status = item.status === 'disabled' ? 'active' : 'disabled'
  actionId.value = `material-status-${item.id}`
  states.materials.error = ''
  try {
    await api(`/tech/materials/${item.id}`, { method: 'PUT', body: {
      code: item.code, name: item.name, category: item.category, unit: item.unit,
      unit_alt: item.unit_alt || '', weight_gsm: nullableNumber(item.weight_gsm),
      width_cm: nullableNumber(item.width_cm), shrink_rate: nullableNumber(item.shrink_rate),
      price_reference: nullableNumber(item.price_reference), price_unit: item.price_unit || '',
      safety_stock: item.safety_stock === '' ? 0 : Number(item.safety_stock || 0),
      legacy_code: item.legacy_code || '', attrs: item.attrs || {}, status,
    } })
    lookupLoaded.materials = false
    showToast(status === 'disabled' ? '物料已停用' : '物料已启用')
    await loadList('materials')
  } catch (e) { states.materials.error = e.message } finally { actionId.value = '' }
}

function newBomItem() {
  return { material: '', kind: 'main', color: '', size: '', unit_usage: '', loss_rate: '', position: '', remark: '' }
}
async function openBomEditor(source = null) {
  formError.value = ''
  try {
    await ensureLookups(['materials', 'styles'], true)
  } catch (e) { formError.value = e.message; return }
  bomEditingId.value = source?.id || null
  Object.assign(bomForm, {
    style: source?.style || '', version: source?.version || '', remark: source?.remark || '',
    items: source?.items?.length
      ? source.items.map((item) => ({ ...item, material: Number(item.material) }))
      : [newBomItem()],
  })
  modal.value = 'bom-editor'
}
function addBomItem() { bomForm.items.push(newBomItem()) }
function removeBomItem(index) {
  if (bomForm.items.length === 1) { bomForm.items[0] = newBomItem(); return }
  bomForm.items.splice(index, 1)
}
function validateBomItems() {
  if (!bomForm.items.length) return '请至少添加一项 BOM 明细'
  const seen = new Set()
  for (let i = 0; i < bomForm.items.length; i += 1) {
    const item = bomForm.items[i]
    if (!item.material) return `第 ${i + 1} 项请选择物料`
    if (!(Number(item.unit_usage) > 0)) return `第 ${i + 1} 项单耗必须大于 0`
    if (item.loss_rate !== '' && Number(item.loss_rate) < 0) return `第 ${i + 1} 项损耗率不能为负数`
    const key = `${item.material}|${item.color || ''}|${item.size || ''}`
    if (seen.has(key)) return `第 ${i + 1} 项与其他行同物料、同色码重复`
    seen.add(key)
  }
  return ''
}
async function saveBom() {
  formError.value = ''
  if (!bomEditingId.value && !bomForm.style) { formError.value = '请选择款式'; return }
  if (!bomEditingId.value && bomForm.version !== '' && (!(Number(bomForm.version) > 0) || !Number.isInteger(Number(bomForm.version)))) {
    formError.value = '版本须为正整数，也可留空自动取下一版本'; return
  }
  const itemError = validateBomItems()
  if (itemError) { formError.value = itemError; return }
  saving.value = true
  try {
    const items = bomForm.items.map((item) => ({
      material: Number(item.material), kind: item.kind || 'main', color: item.color || '', size: item.size || '',
      unit_usage: Number(item.unit_usage), loss_rate: item.loss_rate === '' ? null : Number(item.loss_rate),
      position: item.position || '', remark: item.remark || '',
    }))
    const id = bomEditingId.value
    const body = id ? { remark: bomForm.remark || '', items } : {
      style: Number(bomForm.style), version: bomForm.version === '' ? null : Number(bomForm.version),
      remark: bomForm.remark || '', items,
    }
    const result = await api(id ? `/tech/boms/${id}` : '/tech/boms', { method: id ? 'PUT' : 'POST', body })
    modal.value = ''
    showToast(id ? 'BOM 及明细已保存' : 'BOM 草稿已创建')
    await loadList('boms')
    if (id && bomDetail.value?.id === id) bomDetail.value = result
  } catch (e) { formError.value = e.message } finally { saving.value = false }
}
async function openMobileBomEditor(item) {
  formError.value = ''
  try {
    const detail = await api(`/tech/boms/${item.id}`)
    await openBomEditor(detail)
  } catch (e) { states.boms.error = e.message }
}
async function openBom(item) {
  bomDetail.value = item
  bomDetailLoading.value = true
  bomDetailError.value = ''
  references.value = null
  referencesError.value = ''
  modal.value = 'bom-detail'
  try { bomDetail.value = await api(`/tech/boms/${item.id}`) } catch (e) { bomDetailError.value = e.message } finally { bomDetailLoading.value = false }
}
async function loadReferences() {
  if (!bomDetail.value) return
  referencesError.value = ''
  references.value = null
  try { references.value = await api(`/tech/boms/${bomDetail.value.id}/references`) } catch (e) { referencesError.value = e.message }
}
async function openBomReferences(item) {
  await openBom(item)
  if (bomDetail.value && !bomDetailError.value) await loadReferences()
}
async function activateBom(item) {
  if (!window.confirm(`确定激活 ${item.style_no} v${item.version} 吗？\n同款当前生效版本将自动停用，此操作会影响后续生产取 BOM。`)) return
  actionId.value = `activate-${item.id}`
  bomDetailError.value = ''
  try {
    const result = await api(`/tech/boms/${item.id}/activate`, { method: 'POST', body: {} })
    bomDetail.value = result
    showToast(`已激活 ${result.style_no} v${result.version}`)
    await loadList('boms')
  } catch (e) { bomDetailError.value = e.message } finally { actionId.value = '' }
}
async function forkBom(item) {
  if (!window.confirm(`确定从 ${item.style_no} v${item.version} 创建下一版本草稿吗？${item.status === 'active' ? '\n原生效版本将自动停用，新草稿需另行激活。' : ''}`)) return
  actionId.value = `fork-${item.id}`
  bomDetailError.value = ''
  try {
    const result = await api(`/tech/boms/${item.id}/fork`, { method: 'POST', body: {} })
    showToast(`已创建 ${result.style_no} v${result.version} 草稿`)
    await loadList('boms')
    await openBom(result)
  } catch (e) { bomDetailError.value = e.message } finally { actionId.value = '' }
}
async function deleteBom(item) {
  if (!window.confirm(`确定删除 ${item.style_no} v${item.version} 吗？\n删除后该版本不再出现在列表中。`)) return
  actionId.value = `delete-bom-${item.id}`
  bomDetailError.value = ''
  try {
    await api(`/tech/boms/${item.id}`, { method: 'DELETE' })
    modal.value = ''
    showToast('BOM 已删除')
    if (lists.boms.results.length === 1 && filters.boms.page > 1) filters.boms.page -= 1
    await loadList('boms')
  } catch (e) { bomDetailError.value = e.message; states.boms.error = e.message } finally { actionId.value = '' }
}

onMounted(() => {
  loadList(activeTab.value)
  ensureLookups(['styles']).catch(() => {})
})
</script>

<template>
  <div class="tech-mobile">
    <div class="filter-bar tech-mobile-tabs"><div class="segmented"><button :class="{ active: activeTab === 'materials' }" @click="switchTab('materials')">物料主档</button><button :class="{ active: activeTab === 'boms' }" @click="switchTab('boms')">版式 BOM</button></div></div>
    <template v-if="activeTab === 'materials'">
      <div class="tech-mobile-toolbar"><label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.materials.keyword" placeholder="编码 / 品名 / 旧编码" @keyup.enter="materialSearchNow" /></label><button class="button secondary" @click="modal = 'import'">导入</button><button class="button primary" @click="openMaterial()">新增物料</button></div>
      <div class="tech-mobile-chips" aria-label="物料分类"><button :class="{ active: filters.materials.category === '' }" @click="setMaterialCategory('')">全部</button><button v-for="(label, value) in categoryText" :key="value" :class="{ active: filters.materials.category === value }" @click="setMaterialCategory(value)">{{ label }}</button></div>
      <PageState :loading="states.materials.loading && !lists.materials.results.length" :error="lists.materials.results.length ? '' : states.materials.error" :empty="!lists.materials.results.length" empty-text="暂无物料，请新建或导入物料主档" @retry="loadList('materials')">
        <div class="tech-mobile-list"><article v-for="item in lists.materials.results" :key="item.id" class="card tech-mobile-card">
          <div class="tech-mobile-head"><div><h3>{{ item.code }}</h3><p>{{ item.name }}</p></div><div class="tech-mobile-badges"><span v-if="!item.conversion_ready" class="badge producing">待补参数</span><span class="badge" :class="item.status === 'active' ? '' : 'cancelled'">{{ item.status === 'active' ? categoryText[item.category] : '已停用' }}</span></div></div>
          <div class="tech-mobile-params"><span>主单位 <strong>{{ item.unit }}</strong></span><span>副单位 <strong>{{ item.unit_alt || '—' }}</strong></span><span>克重 <strong>{{ item.weight_gsm ?? '—' }}</strong></span><span>幅宽 <strong>{{ item.width_cm ?? '—' }}</strong></span><span>缩率 <strong>{{ item.shrink_rate === null ? '—' : `${item.shrink_rate}%` }}</strong></span><span>旧编码 <strong>{{ item.legacy_code || '—' }}</strong></span></div>
          <div class="tech-mobile-actions"><button class="button secondary small" @click="openMaterial(item)">编辑物料</button><button class="button secondary small" :disabled="actionId === `material-status-${item.id}`" @click="toggleMaterialStatus(item)">{{ item.status === 'disabled' ? '启用' : '停用' }}</button></div>
        </article></div>
      </PageState>
      <div v-if="states.materials.error && lists.materials.results.length" class="form-error" role="alert">{{ states.materials.error }}</div><div v-if="lists.materials.results.length" class="tech-mobile-more"><button v-if="hasMore('materials')" :disabled="states.materials.loading" @click="loadMore('materials')">{{ states.materials.loading ? '加载中…' : '加载更多' }}</button><span v-else>没有更多了</span></div>
    </template>

    <template v-else>
      <div class="tech-mobile-toolbar"><select v-model="filters.boms.style_id" aria-label="按款式筛选" @change="bomFilter"><option value="">全部款式</option><option v-for="style in lookups.styles" :key="style.id" :value="style.id">{{ style.style_no }} · {{ style.name }}</option></select><button class="button primary" @click="openBomEditor()">新建 BOM</button></div>
      <div class="tech-mobile-chips" aria-label="BOM 状态"><button v-for="option in [['', '全部'], ['draft', '草稿'], ['active', '生效'], ['deactivated', '已停用']]" :key="option[0]" :class="{ active: filters.boms.status === option[0] }" @click="setBomStatus(option[0])">{{ option[1] }}</button><input v-model="filters.boms.version" type="number" min="1" placeholder="版本" aria-label="BOM 版本" @change="bomFilter" /></div>
      <section class="card tech-mobile-note">定额 = 净用量 × (1 + 缩率) × (1 + 损耗)。缩率在物料档维护，BOM 行只录净用量与损耗率。</section>
      <PageState :loading="states.boms.loading && !lists.boms.results.length" :error="lists.boms.results.length ? '' : states.boms.error" :empty="!lists.boms.results.length" empty-text="暂无 BOM，请新建款式 BOM" @retry="loadList('boms')">
        <div class="tech-mobile-list"><article v-for="item in lists.boms.results" :key="item.id" class="card tech-mobile-card">
          <div class="tech-mobile-head"><div><h3>{{ item.style_no }} · v{{ item.version }}</h3><p>{{ item.style_name }}</p></div><span class="badge" :class="item.status === 'active' ? 'done' : item.status === 'draft' ? 'producing' : 'cancelled'">{{ bomStatusText[item.status] }}</span></div><p class="tech-mobile-remark">{{ item.remark || `${item.item_count} 项明细` }}</p>
          <div class="tech-mobile-actions"><button class="button secondary small" @click="item.status === 'draft' ? openMobileBomEditor(item) : openBom(item)">{{ item.status === 'draft' ? '编辑明细' : '查看明细' }}</button><button v-if="item.status === 'draft'" class="button primary small" :disabled="actionId === `activate-${item.id}`" @click="activateBom(item)">激活生效</button><button class="button secondary small" :disabled="actionId === `fork-${item.id}`" @click="forkBom(item)">改版</button><button class="button secondary small" @click="openBomReferences(item)">引用反查</button><button v-if="item.status === 'draft'" class="button danger small" :disabled="actionId === `delete-bom-${item.id}`" @click="deleteBom(item)">删除</button></div>
        </article></div>
      </PageState>
      <div v-if="states.boms.error && lists.boms.results.length" class="form-error" role="alert">{{ states.boms.error }}</div><div v-if="lists.boms.results.length" class="tech-mobile-more"><button v-if="hasMore('boms')" :disabled="states.boms.loading" @click="loadMore('boms')">{{ states.boms.loading ? '加载中…' : '加载更多' }}</button><span v-else>没有更多了</span></div>
    </template>
  </div>

  <div class="tech-desktop">
  <div class="welcome">
    <div><h2>工艺与 BOM</h2><p>维护物料双单位参数和款式 BOM 版本，生效版本供生产定额与采购需求使用。</p></div>
    <button class="button secondary" @click="loadList()"><AppIcon name="refresh" :size="16" />刷新</button>
  </div>
  <div class="filter-bar tech-tabs">
    <div class="segmented"><button :class="{ active: activeTab === 'materials' }" @click="switchTab('materials')">物料主档</button><button :class="{ active: activeTab === 'boms' }" @click="switchTab('boms')">版式 BOM</button></div>
    <button class="button secondary" @click="modal = 'import'">导入 {{ activeTab === 'materials' ? '物料' : 'BOM' }}</button>
    <button v-if="activeTab === 'materials'" class="button primary" @click="openMaterial()">新增物料</button>
    <button v-else class="button primary" @click="openBomEditor()">新建 BOM</button>
  </div>

  <template v-if="activeTab === 'materials'">
    <div class="filter-bar">
      <label class="search-box"><AppIcon name="search" :size="17" /><input v-model="filters.materials.keyword" placeholder="搜索物料编码、品名或旧编码" @input="materialSearch" /></label>
      <select v-model="filters.materials.category" aria-label="物料分类" @change="filters.materials.page = 1; loadList('materials')"><option value="">全部分类</option><option v-for="(label, value) in categoryText" :key="value" :value="value">{{ label }}</option></select>
    </div>
    <section class="card table-card">
      <PageState :loading="states.materials.loading" :error="states.materials.error" :empty="!lists.materials.results.length" empty-text="暂无物料，请先新增物料主档" @retry="loadList('materials')">
        <div class="table-wrap desktop-table"><table><thead><tr><th>编码 / 品名</th><th>分类</th><th>主 / 副单位</th><th class="numeric">参考价</th><th class="numeric">安全库存</th><th>换算参数</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in lists.materials.results" :key="item.id"><td><span class="cell-title">{{ item.code }} · {{ item.name }}</span><small class="cell-subtitle">{{ item.legacy_code ? `旧编码 ${item.legacy_code}` : `更新于 ${dateTime(item.updated_at)}` }}</small></td><td>{{ categoryText[item.category] || item.category }}</td><td>{{ item.unit }}<small class="cell-subtitle">{{ item.unit_alt || '无副单位' }}</small></td><td class="numeric">{{ item.price_reference === null ? '—' : money(item.price_reference) }}<small v-if="item.price_unit" class="cell-subtitle">{{ item.price_unit === 'kg' ? '元/公斤' : '元/米' }}</small></td><td class="numeric">{{ number(item.safety_stock) }} {{ item.unit }}</td><td><span v-if="item.conversion_ready" class="badge done">参数齐全</span><span v-else class="badge producing">待补克重/幅宽</span></td><td><span class="badge" :class="item.status === 'active' ? 'done' : 'cancelled'">{{ materialStatusText[item.status] || item.status }}</span></td><td><div class="tech-row-actions"><button class="button ghost small" @click="openMaterial(item)">编辑</button><button class="button ghost small tech-danger-text" :disabled="actionId === `material-${item.id}`" @click="deleteMaterial(item)">删除</button></div></td></tr></tbody></table></div>
        <div class="mobile-cards"><article v-for="item in lists.materials.results" :key="item.id" class="mobile-row"><div class="mobile-row-head"><div><div class="mobile-row-title">{{ item.code }} · {{ item.name }}</div><small class="cell-subtitle">{{ categoryText[item.category] }} · {{ item.unit }}{{ item.unit_alt ? ` / ${item.unit_alt}` : '' }}</small></div><span class="badge" :class="item.status === 'active' ? 'done' : 'cancelled'">{{ materialStatusText[item.status] }}</span></div><div class="mobile-meta"><span>参考价 <strong>{{ item.price_reference === null ? '—' : money(item.price_reference) }}</strong></span><span>安全库存 <strong>{{ number(item.safety_stock) }} {{ item.unit }}</strong></span><span>克重 <strong>{{ item.weight_gsm ?? '—' }}</strong></span><span>幅宽 <strong>{{ item.width_cm ?? '—' }}</strong></span></div><div class="tech-mobile-actions"><button class="button secondary small" @click="openMaterial(item)">编辑</button><button class="button danger small" :disabled="actionId === `material-${item.id}`" @click="deleteMaterial(item)">删除</button></div></article></div>
      </PageState>
      <div class="pagination"><button class="button ghost small" :disabled="filters.materials.page <= 1" @click="page('materials', -1)">上一页</button><span>第 {{ filters.materials.page }} 页 · 共 {{ lists.materials.count || 0 }} 条</span><button class="button ghost small" :disabled="filters.materials.page * PAGE_SIZE >= (lists.materials.count || 0)" @click="page('materials', 1)">下一页</button></div>
    </section>
  </template>

  <template v-else>
    <div class="filter-bar">
      <select v-model="filters.boms.style_id" aria-label="按款式筛选" @change="bomFilter"><option value="">全部款式</option><option v-for="style in lookups.styles" :key="style.id" :value="style.id">{{ style.style_no }} · {{ style.name }}</option></select>
      <select v-model="filters.boms.status" aria-label="按 BOM 状态筛选" @change="bomFilter"><option value="">全部状态</option><option value="draft">草稿</option><option value="active">生效</option><option value="deactivated">已停用</option></select>
      <input v-model="filters.boms.version" class="tech-version-filter" type="number" min="1" placeholder="版本号" @change="bomFilter" />
      <span v-if="lookupError" class="tech-inline-error">{{ lookupError }}</span>
    </div>
    <section class="card table-card">
      <PageState :loading="states.boms.loading" :error="states.boms.error" :empty="!lists.boms.results.length" empty-text="暂无 BOM，请新建款式 BOM" @retry="loadList('boms')">
        <div class="table-wrap desktop-table"><table><thead><tr><th>款式</th><th class="numeric">版本</th><th class="numeric">明细数</th><th>状态</th><th>备注</th><th>更新时间</th><th></th></tr></thead><tbody><tr v-for="item in lists.boms.results" :key="item.id" class="clickable" @click="openBom(item)"><td><span class="cell-title">{{ item.style_no }} · {{ item.style_name }}</span><small class="cell-subtitle">BOM #{{ item.id }}</small></td><td class="numeric">v{{ item.version }}</td><td class="numeric">{{ item.item_count }}</td><td><span class="badge" :class="item.status === 'active' ? 'done' : item.status === 'draft' ? 'producing' : 'cancelled'">{{ bomStatusText[item.status] || item.status }}</span></td><td>{{ item.remark || '—' }}</td><td>{{ dateTime(item.updated_at) }}</td><td><AppIcon name="chevron" :size="16" /></td></tr></tbody></table></div>
        <div class="mobile-cards"><button v-for="item in lists.boms.results" :key="item.id" class="mobile-row" @click="openBom(item)"><div class="mobile-row-head"><div><div class="mobile-row-title">{{ item.style_no }} · v{{ item.version }}</div><small class="cell-subtitle">{{ item.style_name }}</small></div><span class="badge" :class="item.status === 'active' ? 'done' : item.status === 'draft' ? 'producing' : 'cancelled'">{{ bomStatusText[item.status] }}</span></div><div class="mobile-meta"><span>明细 <strong>{{ item.item_count }} 项</strong></span><span>更新 <strong>{{ dateTime(item.updated_at) }}</strong></span></div></button></div>
      </PageState>
      <div class="pagination"><button class="button ghost small" :disabled="filters.boms.page <= 1" @click="page('boms', -1)">上一页</button><span>第 {{ filters.boms.page }} 页 · 共 {{ lists.boms.count || 0 }} 条</span><button class="button ghost small" :disabled="filters.boms.page * PAGE_SIZE >= (lists.boms.count || 0)" @click="page('boms', 1)">下一页</button></div>
    </section>
  </template>
  </div>

  <Teleport to="body"><div v-if="modal === 'material'" class="modal-backdrop" @click.self="modal = ''"><section class="modal tech-modal-wide" role="dialog" aria-modal="true" :aria-label="materialEditingId ? '编辑物料' : '新增物料'"><div class="modal-header"><div><h2>{{ materialEditingId ? '编辑物料' : '新增物料' }}</h2><p>副单位用于公斤与米换算，填写后须补齐克重和幅宽。</p></div><button class="icon-button" aria-label="关闭" @click="modal = ''"><AppIcon name="close" /></button></div><form @submit.prevent="saveMaterial"><div class="form-grid"><label class="field"><span>物料编码 *</span><input v-model="materialForm.code" required maxlength="64" /></label><label class="field"><span>品名 *</span><input v-model="materialForm.name" required maxlength="128" /></label><label class="field"><span>分类</span><select v-model="materialForm.category"><option v-for="(label, value) in categoryText" :key="value" :value="value">{{ label }}</option></select></label><label class="field"><span>状态</span><select v-model="materialForm.status"><option value="active">启用</option><option value="disabled">停用</option></select></label><label class="field"><span>主单位 *</span><input v-model="materialForm.unit" required maxlength="16" placeholder="如：米、公斤、个" /></label><label class="field"><span>副单位</span><input v-model="materialForm.unit_alt" maxlength="16" placeholder="不需要换算则留空" /></label><label class="field"><span>克重（g/m²）</span><input v-model="materialForm.weight_gsm" type="number" min="0" step="0.01" /></label><label class="field"><span>幅宽（cm）</span><input v-model="materialForm.width_cm" type="number" min="0" step="0.01" /></label><label class="field"><span>缩率（%）</span><input v-model="materialForm.shrink_rate" type="number" min="0" step="0.01" /></label><label class="field"><span>安全库存（主单位）</span><input v-model="materialForm.safety_stock" type="number" min="0" step="0.01" /></label><label class="field"><span>参考价</span><input v-model="materialForm.price_reference" type="number" min="0" step="0.001" /></label><label class="field"><span>默认单价单位</span><select v-model="materialForm.price_unit"><option value="">按主单位</option><option value="kg">元/公斤</option><option value="m">元/米</option></select></label><label class="field"><span>旧编码</span><input v-model="materialForm.legacy_code" maxlength="64" /></label><label class="field full"><span>扩展属性（JSON 对象，可填 fabric_type、loss_rate 等）</span><textarea v-model="attrsText" rows="4" spellcheck="false" /></label></div><div v-if="formError" class="form-error" role="alert">{{ formError }}</div><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button primary" :disabled="saving">{{ saving ? '保存中…' : '保存物料' }}</button></div></form></section></div></Teleport>

  <Teleport to="body"><div v-if="modal === 'bom-editor'" class="modal-backdrop" @click.self="modal = ''"><section class="modal tech-bom-modal" role="dialog" aria-modal="true" :aria-label="bomEditingId ? '编辑 BOM' : '新建 BOM'"><div class="modal-header"><div><h2>{{ bomEditingId ? `编辑 BOM v${bomForm.version}` : '新建 BOM 草稿' }}</h2><p>{{ bomEditingId ? '款式和版本不可修改；明细保存时整表替换。' : '版本留空时由后端取该款式下一版本。' }}</p></div><button class="icon-button" aria-label="关闭" @click="modal = ''"><AppIcon name="close" /></button></div><form @submit.prevent="saveBom"><div class="form-grid"><label class="field"><span>款式 *</span><select v-model.number="bomForm.style" :disabled="!!bomEditingId" required><option value="">请选择本公司款式</option><option v-for="style in lookups.styles" :key="style.id" :value="style.id">{{ style.style_no }} · {{ style.name }}</option></select></label><label class="field"><span>版本</span><input v-model="bomForm.version" type="number" min="1" step="1" :disabled="!!bomEditingId" placeholder="留空自动取号" /></label><label class="field full"><span>备注</span><input v-model="bomForm.remark" maxlength="255" /></label></div><div class="tech-editor-heading"><h3>BOM 明细</h3><button type="button" class="button secondary small" @click="addBomItem">添加明细</button></div><div class="tech-bom-items"><div v-for="(item, index) in bomForm.items" :key="index" class="tech-bom-item"><div class="tech-item-title"><strong>第 {{ index + 1 }} 项</strong><span>单位：{{ materialUnit(item.material) }}</span><button type="button" class="button ghost small tech-danger-text" @click="removeBomItem(index)">移除</button></div><div class="tech-item-grid"><label class="field tech-material-field"><span>物料 *</span><select v-model.number="item.material" required><option value="">请选择物料</option><option v-for="material in lookups.materials" :key="material.id" :value="material.id">{{ material.code }} · {{ material.name }}（{{ material.unit }}）</option></select></label><label class="field"><span>类别</span><select v-model="item.kind"><option value="main">面料</option><option value="lining">里料</option><option value="accessory">辅料</option></select></label><label class="field"><span>单耗 *</span><input v-model="item.unit_usage" type="number" min="0.0001" step="0.0001" required /></label><label class="field"><span>损耗率（%）</span><input v-model="item.loss_rate" type="number" min="0" step="0.01" placeholder="留空用物料默认值" /></label><label class="field"><span>颜色</span><input v-model="item.color" maxlength="64" list="bom-color-options" placeholder="留空为通色" /></label><label class="field"><span>尺码</span><input v-model="item.size" maxlength="32" list="bom-size-options" placeholder="留空为通码" /></label><label class="field"><span>部位</span><input v-model="item.position" maxlength="64" /></label><label class="field"><span>行备注</span><input v-model="item.remark" maxlength="255" /></label></div></div></div><datalist id="bom-color-options"><option v-for="color in COMMON_COLORS" :key="color" :value="color" /></datalist><datalist id="bom-size-options"><option v-for="size in COMMON_SIZES" :key="size" :value="size" /></datalist><div v-if="formError" class="form-error" role="alert">{{ formError }}</div><div class="form-actions"><button type="button" class="button secondary" @click="modal = ''">取消</button><button class="button primary" :disabled="saving || lookupLoading">{{ saving ? '保存中…' : '保存 BOM' }}</button></div></form></section></div></Teleport>

  <Teleport to="body"><div v-if="modal === 'bom-detail' && bomDetail" class="modal-backdrop" @click.self="modal = ''"><section class="modal tech-bom-modal" role="dialog" aria-modal="true" :aria-label="`${bomDetail.style_no} BOM 详情`"><div class="modal-header"><div><h2>{{ bomDetail.style_no }} · v{{ bomDetail.version }}</h2><p>{{ bomDetail.style_name }} · {{ bomStatusText[bomDetail.status] || bomDetail.status }}</p></div><button class="icon-button" aria-label="关闭" @click="modal = ''"><AppIcon name="close" /></button></div><div v-if="bomDetailLoading" class="page-state"><span class="spinner" />正在读取 BOM 明细…</div><template v-else><div class="tech-detail-actions"><button class="button secondary small" @click="openBomEditor(bomDetail)">编辑</button><button v-if="bomDetail.status !== 'active'" class="button primary small" :disabled="actionId === `activate-${bomDetail.id}`" @click="activateBom(bomDetail)">激活版本</button><button class="button secondary small" :disabled="actionId === `fork-${bomDetail.id}`" @click="forkBom(bomDetail)">Fork 新版本</button><button class="button secondary small" @click="loadReferences">查看引用</button><button class="button danger small" :disabled="actionId === `delete-bom-${bomDetail.id}`" @click="deleteBom(bomDetail)">删除</button></div><div v-if="bomDetailError" class="form-error" role="alert">{{ bomDetailError }}</div><div class="detail-grid"><div class="detail-item"><small>状态</small><strong>{{ bomStatusText[bomDetail.status] }}</strong></div><div class="detail-item"><small>明细数</small><strong>{{ bomDetail.items?.length || 0 }} 项</strong></div><div class="detail-item"><small>最近更新</small><strong>{{ dateTime(bomDetail.updated_at) }}</strong></div></div><p class="section-note tech-remark">{{ bomDetail.remark || '无版本备注' }}</p><div class="table-wrap"><table><thead><tr><th>物料 / 类别</th><th>颜色 / 尺码</th><th class="numeric">净单耗</th><th class="numeric">损耗率</th><th class="numeric">损耗后用量</th><th>部位 / 备注</th></tr></thead><tbody><tr v-for="item in bomDetail.items || []" :key="item.id"><td><span class="cell-title">{{ item.material_code }} · {{ item.material_name }}</span><small class="cell-subtitle">{{ kindText[item.kind] || item.kind }} · {{ item.unit }}</small></td><td>{{ item.color || '通色' }} / {{ item.size || '通码' }}</td><td class="numeric">{{ item.unit_usage }} {{ item.unit }}</td><td class="numeric">{{ item.loss_rate }}%</td><td class="numeric">{{ grossUsage(item) }} {{ item.unit }}</td><td>{{ item.position || '—' }}<small class="cell-subtitle">{{ item.remark || '' }}</small></td></tr></tbody></table></div><div v-if="references" class="tech-reference"><strong>在途单据引用：{{ references.count }} 处</strong><p>{{ references.note }}</p></div><div v-if="referencesError" class="form-error">{{ referencesError }}</div></template></section></div></Teleport>
  <ImportDialog v-if="modal === 'import'" :title="activeTab === 'materials' ? '批量导入物料' : '批量导入 BOM'" :endpoint="activeTab === 'materials' ? '/tech/materials/import' : '/tech/boms/import'" @close="modal = ''" @imported="lookupLoaded[activeTab] = false; loadList(); showToast('导入完成，列表已刷新')" />
</template>

<style scoped>
.tech-mobile{display:none}.tech-mobile-toolbar{display:flex;align-items:center;gap:8px;margin-bottom:12px}.tech-mobile-toolbar .search-box{min-width:0;max-width:none;flex:1}.tech-mobile-toolbar>.button{flex:none;padding-inline:13px}.tech-mobile-toolbar>select{min-width:0;flex:1}.tech-mobile-chips{display:flex;align-items:center;gap:8px;overflow-x:auto;margin-bottom:12px;padding:2px}.tech-mobile-chips button{flex:none;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--text-secondary);padding:7px 16px;font-size:12px}.tech-mobile-chips button.active{border-color:var(--orange);background:var(--orange-soft);color:var(--orange)}.tech-mobile-chips input{width:76px;min-height:32px;flex:none;border-radius:999px;padding:5px 12px}.tech-mobile-list{display:flex;flex-direction:column;gap:12px}.tech-mobile-card h3{margin:0;font-size:15px;overflow-wrap:anywhere}.tech-mobile-card p{margin:5px 0 0;color:var(--muted);font-size:12px}.tech-mobile-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.tech-mobile-head>div:first-child{min-width:0}.tech-mobile-badges{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:5px}.tech-mobile-params{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px 6px;margin-top:14px;color:var(--muted);font-size:11px}.tech-mobile-params strong{display:block;margin-top:3px;color:var(--ink);font-size:12px;font-weight:500;overflow-wrap:anywhere}.tech-mobile-note{margin-bottom:12px;color:var(--text-secondary);font-size:12px;line-height:1.65}.tech-mobile-remark{margin:12px 0 0!important}.tech-mobile-more{padding:18px 0 8px;text-align:center;color:var(--muted);font-size:12px}.tech-mobile-more button{border:0;background:transparent;color:var(--orange);padding:8px 20px}.tech-mobile-more-error{margin-top:12px}
.tech-tabs{justify-content:space-between}.tech-row-actions,.tech-mobile-actions,.tech-detail-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.tech-danger-text{color:var(--danger)}.tech-mobile-actions{margin-top:14px;justify-content:flex-end}.tech-version-filter{width:120px}.tech-inline-error{color:var(--danger);font-size:12px}.tech-modal-wide{width:min(820px,100%)}.tech-bom-modal{width:min(1120px,100%)}.tech-editor-heading{display:flex;align-items:center;justify-content:space-between;margin:22px 0 12px}.tech-editor-heading h3{margin:0;font-size:15px}.tech-bom-items{display:flex;flex-direction:column;gap:12px;margin-bottom:18px}.tech-bom-item{border:1px solid var(--line);border-radius:11px;padding:14px;background:#fcfbfa}.tech-item-title{display:flex;align-items:center;gap:12px;margin-bottom:12px;color:var(--muted);font-size:12px}.tech-item-title strong{color:var(--ink)}.tech-item-title button{margin-left:auto}.tech-item-grid{display:grid;grid-template-columns:2fr repeat(4,minmax(100px,1fr));gap:12px}.tech-material-field{grid-column:span 2}.tech-detail-actions{margin-bottom:16px}.tech-remark{margin-top:-6px;margin-bottom:14px}.tech-reference{margin-top:18px;padding:14px;background:#f8f6f3;border-radius:9px;font-size:13px}.tech-reference p{margin:6px 0 0;color:var(--muted);font-size:12px}.form-error{margin-top:14px}@media(max-width:900px){.tech-item-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.tech-material-field{grid-column:1/-1}}@media(max-width:760px){.tech-desktop{display:none}.tech-mobile{display:block}.tech-mobile-tabs{margin-bottom:12px}.tech-mobile-actions{margin-top:14px;justify-content:flex-start}.tech-mobile-actions .button{flex:none}.tech-tabs{align-items:stretch}.tech-tabs .segmented{flex:1}.tech-tabs .segmented button{flex:1}.tech-item-grid{grid-template-columns:1fr}.tech-material-field{grid-column:auto}.tech-bom-item{padding:12px}.tech-detail-actions .button{flex:1}.tech-version-filter{width:110px}}
</style>
