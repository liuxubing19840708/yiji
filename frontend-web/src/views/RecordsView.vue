<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, download, rows } from '../api'
import { dateTime, money, number } from '../format'
import AppIcon from '../components/AppIcon.vue'
import DateRangeBar from '../components/DateRangeBar.vue'
import PageState from '../components/PageState.vue'

const emit = defineEmits(['toast'])
const props = defineProps({ section: { type: String, default: '' } })
const PAGE_SIZE = 20
const pad = (value) => String(value).padStart(2, '0')
const now = new Date()
const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
const loading = ref(true)
const loadingMore = ref(false)
const error = ref('')
const moreError = ref('')
const hasMore = ref(false)
const data = ref({ count: 0 })
const items = ref([])
const employees = ref([])
const processes = ref([])
const editing = ref(null)
const saving = ref(false)
const actingId = ref(0)
const batching = ref(false)
const exporting = ref(false)
const actionError = ref('')
const selected = ref([])
const quick = ref('today')
const filters = reactive({
  user_id: '', process_id: '', date_from: today, date_to: today,
  settlement: '', ticket_id: '', page: 1,
})
let requestSerial = 0

async function load({ append = false, page = filters.page } = {}) {
  if (append && (loading.value || loadingMore.value || !hasMore.value)) return
  const serial = ++requestSerial
  if (append) {
    loadingMore.value = true
    moreError.value = ''
  } else {
    loading.value = true
    loadingMore.value = false
    error.value = ''
    moreError.value = ''
    hasMore.value = false
    selected.value = []
    items.value = []
    data.value = { count: 0 }
  }
  try {
    const result = await api('/records', {
      params: { ...filters, page, page_size: PAGE_SIZE },
    })
    if (serial !== requestSerial) return
    const nextRows = rows(result)
    data.value = result
    items.value = append ? items.value.concat(nextRows) : nextRows
    filters.page = page
    hasMore.value = result.next !== undefined
      ? Boolean(result.next)
      : page * PAGE_SIZE < (result.count || 0)
  } catch (e) {
    if (serial !== requestSerial) return
    if (append) moreError.value = e.message
    else error.value = e.message
  } finally {
    if (serial === requestSerial) {
      loading.value = false
      loadingMore.value = false
    }
  }
}

function reload() {
  return load({ page: 1 })
}

function loadMore() {
  return load({ append: true, page: filters.page + 1 })
}

function goToPage(page) {
  if (loading.value || page < 1) return
  load({ page })
}

function changeRange({ start, end, quick: quickKey }) {
  filters.date_from = start
  filters.date_to = end
  quick.value = quickKey || ''
  reload()
}

async function loadOptions() {
  try {
    const [employeeData, processData] = await Promise.all([
      api('/employees'), api('/processes', { params: { page_size: 200 } }),
    ])
    employees.value = rows(employeeData)
    processes.value = rows(processData).slice().sort((a, b) => (a.sort || 0) - (b.sort || 0) || a.id - b.id)
  } catch { /* 筛选项加载失败不阻断记录查询，可刷新后重试 */ }
}

function edit(row) {
  editing.value = { id: row.id, qty: row.qty, reason: '' }
  actionError.value = ''
}

async function saveQty() {
  const qty = Number(editing.value.qty)
  if (!Number.isInteger(qty) || qty <= 0 || !editing.value.reason.trim()) {
    actionError.value = '请输入正整数数量和改数原因'
    return
  }
  saving.value = true
  actingId.value = editing.value.id
  try {
    await api(`/records/${editing.value.id}`, {
      method: 'PUT', body: { qty, reason: editing.value.reason.trim() },
    })
    editing.value = null
    emit('toast', '登记数量已修改')
    await reload()
  } catch (e) {
    actionError.value = e.message
  } finally {
    saving.value = false
    actingId.value = 0
  }
}

async function undo(row) {
  if (actingId.value || !confirm(`确认撤销 ${row.user_name} 的 ${row.process_name} ${row.qty} 件登记？`)) return
  actingId.value = row.id
  try {
    await api(`/records/${row.id}`, { method: 'DELETE' })
    emit('toast', '登记已撤销')
    await reload()
  } catch (e) {
    emit('toast', e.message)
  } finally {
    actingId.value = 0
  }
}

async function batchUndo() {
  if (!selected.value.length || batching.value) return
  if (selected.value.length > 50) {
    emit('toast', '单次最多撤销 50 条，请减少选择')
    return
  }
  if (!confirm(`确认尝试撤销选中的 ${selected.value.length} 条登记？`)) return
  batching.value = true
  try {
    const result = await api('/records/batch-undo', {
      method: 'POST', body: { record_ids: selected.value },
    })
    emit('toast', `成功 ${result.ok} 条，失败 ${result.fail} 条`)
    await reload()
  } catch (e) {
    emit('toast', e.message)
  } finally {
    batching.value = false
  }
}

async function exportRecords() {
  if (exporting.value) return
  exporting.value = true
  try {
    await download('/records/export', filters)
    emit('toast', '计件明细已导出')
  } catch (e) {
    emit('toast', e.message)
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  if (props.section && Number.isInteger(Number(props.section)) && Number(props.section) > 0) {
    filters.ticket_id = String(Number(props.section))
    filters.date_from = ''
    filters.date_to = ''
    quick.value = ''
  }
  reload(); loadOptions()
})
</script>

<template>
  <div class="records-view">
    <div class="welcome records-welcome">
      <div><h2>计件记录</h2><p>查询登记明细，留痕改数或按权限撤销。</p></div>
      <div class="records-welcome-actions">
        <button class="button secondary" :disabled="exporting" @click="exportRecords">{{ exporting ? '导出中…' : '导出 Excel' }}</button>
        <button class="button secondary" :disabled="!selected.length || batching" @click="batchUndo">{{ batching ? '撤销中…' : '批量撤销' }}</button>
        <button class="button secondary" :disabled="loading" @click="reload"><AppIcon name="refresh" :size="16" />刷新</button>
      </div>
    </div>

    <form class="filter-bar records-desktop-filters" @submit.prevent="reload">
      <select v-model="filters.user_id" aria-label="员工筛选"><option value="">全部员工</option><option v-for="e in employees" :key="e.user_id" :value="e.user_id">{{ e.name }}</option></select>
      <select v-model="filters.process_id" aria-label="工序筛选"><option value="">全部工序</option><option v-for="p in processes" :key="p.id" :value="p.id">{{ p.name }}</option></select>
      <input v-model="filters.date_from" type="date" aria-label="开始日期" @input="quick = ''" />
      <input v-model="filters.date_to" type="date" aria-label="结束日期" @input="quick = ''" />
      <select v-model="filters.settlement" aria-label="结算状态筛选"><option value="">全部结算状态</option><option value="unsettled">未结算</option></select>
      <button class="button primary">查询</button>
    </form>

    <div v-if="filters.ticket_id" class="record-ticket-filter" role="status">正在查看菲票 #{{ filters.ticket_id }} 的登记记录（不限日期）<button type="button" @click="filters.ticket_id = ''; reload()">清除菲票筛选</button></div>

    <section class="card records-mobile-filters" aria-label="手机记录筛选">
      <div class="records-mobile-pickers">
        <label class="records-mobile-picker">
          <span>员工</span>
          <select v-model="filters.user_id" aria-label="手机员工筛选" @change="reload"><option value="">全部员工</option><option v-for="e in employees" :key="e.user_id" :value="e.user_id">{{ e.name }}</option></select>
        </label>
        <label class="records-mobile-picker">
          <span>工序</span>
          <select v-model="filters.process_id" aria-label="手机工序筛选" @change="reload"><option value="">全部工序</option><option v-for="p in processes" :key="p.id" :value="p.id">{{ p.name }}</option></select>
        </label>
        <label class="records-mobile-picker">
          <span>结算</span>
          <select v-model="filters.settlement" aria-label="手机结算状态筛选" @change="reload"><option value="">全部状态</option><option value="unsettled">未结算</option></select>
        </label>
      </div>
      <DateRangeBar :start="filters.date_from" :end="filters.date_to" :quick="quick" @change="changeRange" />
    </section>

    <div class="records-mobile-summary">
      <span>共 {{ data.count || 0 }} 条登记</span>
      <div>
        <button v-if="selected.length" type="button" :disabled="batching" @click="batchUndo">批量撤销（{{ selected.length }}）</button>
        <button type="button" :disabled="exporting" @click="exportRecords">{{ exporting ? '导出中…' : '导出 Excel' }}</button>
        <button class="records-mobile-refresh" type="button" aria-label="刷新记录" :disabled="loading" @click="reload"><AppIcon name="refresh" :size="15" /></button>
      </div>
    </div>

    <section class="card table-card records-table-card">
      <PageState :loading="loading" :error="error" :empty="!items.length" empty-text="暂无登记记录，调整筛选条件或日期区间试试" @retry="reload">
        <div class="table-wrap desktop-table">
          <table>
            <thead><tr><th></th><th>时间 / 员工</th><th>床次 / 扎号</th><th>款式</th><th>工序</th><th class="numeric">数量</th><th class="numeric">单价 / 金额</th><th>结算</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="row in items" :key="row.id">
                <td><input type="checkbox" class="record-checkbox" :value="row.id" v-model="selected" :disabled="!!row.settlement" :aria-label="`选择记录 ${row.id}`" /></td>
                <td>{{ dateTime(row.registered_at) }}<small class="cell-subtitle">{{ row.user_name }}<template v-if="row.operator_name && row.operator_name !== row.user_name"> · 代登 {{ row.operator_name }}</template></small></td>
                <td>{{ row.bed_no }} / {{ row.bundle_no }}</td>
                <td>{{ row.style_no }}<small class="cell-subtitle">{{ row.style_name }}</small></td>
                <td>{{ row.process_name }}</td>
                <td class="numeric">{{ number(row.qty) }}</td>
                <td class="numeric">¥{{ row.price }} / {{ money(row.amount) }}</td>
                <td><span class="badge" :class="row.settlement ? 'settled' : 'producing'">{{ row.settlement ? '已结算' : '未结算' }}</span></td>
                <td><button class="button ghost small" :disabled="actingId === row.id" @click="edit(row)">改数</button><button class="button ghost small" :disabled="actingId === row.id" @click="undo(row)">{{ actingId === row.id ? '处理中' : '撤销' }}</button></td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="mobile-cards records-mobile-cards">
          <article v-for="row in items" :key="row.id" class="mobile-row record-mobile-card">
            <div class="record-mobile-head">
              <div class="record-mobile-title">{{ row.style_no || '-' }}<small v-if="row.style_name">{{ row.style_name }}</small></div>
              <span class="badge" :class="row.settlement ? 'settled' : 'producing'">{{ row.settlement ? '已结算' : '未结算' }}</span>
            </div>
            <p class="record-mobile-subtitle">{{ row.user_name || '-' }} · 床{{ row.bed_no || '-' }} · 扎{{ row.bundle_no || '-' }} · {{ row.color || '-' }}/{{ row.size || '-' }}</p>
            <div class="record-mobile-meta">
              <span class="record-process">{{ row.process_name || '-' }}</span>
              <strong>{{ money(row.amount || row.price) }}<small>（{{ number(row.qty) }} 件）</small></strong>
            </div>
            <div class="record-mobile-time">{{ dateTime(row.registered_at) }}<span v-if="row.operator_name"> · 代登：{{ row.operator_name }}</span></div>
            <div class="record-mobile-actions">
              <label class="record-mobile-select"><input type="checkbox" :value="row.id" v-model="selected" :disabled="!!row.settlement" :aria-label="`选择记录 ${row.id}`" />选择</label>
              <button type="button" :disabled="actingId === row.id" @click="edit(row)">改数</button>
              <button type="button" :disabled="actingId === row.id" @click="undo(row)">{{ actingId === row.id ? '处理中' : '撤销' }}</button>
            </div>
          </article>
        </div>
      </PageState>

      <div class="pagination desktop-pagination">
        <button class="button ghost small" :disabled="loading || filters.page <= 1" @click="goToPage(filters.page - 1)">上一页</button>
        <span>第 {{ filters.page }} 页 · 共 {{ data.count || 0 }} 条</span>
        <button class="button ghost small" :disabled="loading || !hasMore" @click="goToPage(filters.page + 1)">下一页</button>
      </div>

      <div v-if="items.length" class="records-mobile-more">
        <div v-if="moreError" class="records-more-error" role="alert">
          <span>{{ moreError }}</span><button type="button" @click="loadMore">重试</button>
        </div>
        <button v-else-if="hasMore" class="records-load-more" type="button" :disabled="loadingMore" @click="loadMore"><span v-if="loadingMore" class="spinner" />{{ loadingMore ? '加载更多…' : '加载更多' }}</button>
        <p v-else>没有更多了</p>
      </div>
    </section>

    <Teleport to="body">
      <div v-if="editing" class="modal-backdrop" @click.self="editing = null">
        <form class="modal" @submit.prevent="saveQty">
          <div class="modal-header"><h2>修改登记数量</h2><button type="button" class="icon-button" @click="editing = null"><AppIcon name="close" /></button></div>
          <label class="field"><span>新数量</span><input v-model.number="editing.qty" type="number" min="1" step="1" required /></label>
          <label class="field record-reason-field"><span>改数原因（留痕）</span><textarea v-model="editing.reason" required /></label>
          <p v-if="actionError" class="form-error">{{ actionError }}</p>
          <div class="form-actions"><button type="button" class="button secondary" @click="editing = null">取消</button><button class="button primary" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button></div>
        </form>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.records-welcome-actions {
  display: flex;
  gap: 8px;
}
.record-checkbox {
  width: 16px;
  min-height: 16px;
}
.record-ticket-filter{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;padding:10px 14px;border-radius:9px;background:var(--orange-soft);color:var(--orange-dark);font-size:13px}.record-ticket-filter button{border:0;background:none;color:inherit;text-decoration:underline;white-space:nowrap}
.record-reason-field {
  margin-top: 15px;
}
.records-mobile-filters,
.records-mobile-summary,
.records-mobile-more {
  display: none;
}
@media (max-width: 760px) {
  .records-welcome,
  .records-desktop-filters,
  .desktop-pagination {
    display: none;
  }
  .records-mobile-filters {
    display: block;
    margin-bottom: 12px;
    padding: 12px;
  }
  .records-mobile-pickers {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 7px;
    margin-bottom: 10px;
  }
  .records-mobile-picker {
    display: flex;
    align-items: center;
    min-width: 0;
    min-height: 38px;
    padding: 4px 7px;
    border-radius: 999px;
    background: #f5f6f8;
    color: var(--muted);
    font-size: 11px;
  }
  .records-mobile-picker > span {
    flex: none;
    margin-right: 3px;
  }
  .records-mobile-picker select {
    min-width: 0;
    min-height: 28px;
    padding: 2px 14px 2px 0;
    border: 0;
    background: transparent;
    color: var(--ink);
    font-size: 11px;
    text-overflow: ellipsis;
  }
  .records-mobile-summary {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0 3px 12px;
    color: var(--muted);
    font-size: 12px;
  }
  .records-mobile-summary > div {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px;
  }
  .records-mobile-summary button {
    min-height: 30px;
    padding: 5px 11px;
    border: 0;
    border-radius: 999px;
    background: var(--orange-soft);
    color: var(--orange-dark);
    font-size: 11px;
    white-space: nowrap;
  }
  .records-mobile-summary .records-mobile-refresh {
    display: grid;
    place-items: center;
    width: 30px;
    padding: 0;
    background: #fff;
    color: var(--muted);
  }
  .records-table-card {
    padding: 0;
    border: 0;
    background: transparent;
    box-shadow: none;
    overflow: visible;
  }
  .records-table-card :deep(.page-state) {
    min-height: 160px;
    border-radius: 12px;
    background: #fff;
    box-shadow: 0 2px 8px rgba(31, 35, 41, .06);
  }
  .records-mobile-cards {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .record-mobile-card {
    padding: 16px;
    border: 0;
    border-radius: 12px;
    background: #fff;
    box-shadow: 0 2px 8px rgba(31, 35, 41, .06);
  }
  .record-mobile-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
  }
  .record-mobile-title {
    min-width: 0;
    font-size: 15px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    overflow-wrap: anywhere;
  }
  .record-mobile-title small {
    display: block;
    margin-top: 3px;
    color: var(--text-secondary);
    font-size: 11px;
    font-weight: 400;
  }
  .record-mobile-subtitle {
    margin: 8px 0 0;
    color: var(--muted);
    font-size: 11px;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }
  .record-mobile-meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-top: 14px;
  }
  .record-process {
    padding: 4px 8px;
    border-radius: 999px;
    background: #f2f3f5;
    color: var(--text-secondary);
    font-size: 11px;
  }
  .record-mobile-meta strong {
    text-align: right;
    font-size: 14px;
    font-variant-numeric: tabular-nums;
  }
  .record-mobile-meta strong small {
    color: var(--muted);
    font-size: 11px;
    font-weight: 400;
  }
  .record-mobile-time {
    margin-top: 12px;
    color: var(--muted);
    font-size: 11px;
    line-height: 1.6;
  }
  .record-mobile-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 10px;
  }
  .record-mobile-select {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    margin-right: auto;
    color: var(--muted);
    font-size: 11px;
  }
  .record-mobile-select input {
    width: 16px;
    min-height: 16px;
    margin: 0;
  }
  .record-mobile-actions button {
    min-height: 30px;
    padding: 4px 13px;
    border: 1px solid var(--danger);
    border-radius: 999px;
    background: #fff;
    color: var(--danger);
    font-size: 12px;
  }
  .records-mobile-more {
    display: block;
    padding: 12px 0;
    text-align: center;
  }
  .records-load-more {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    min-width: 132px;
    min-height: 40px;
    padding: 8px 20px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: #fff;
    color: var(--text-secondary);
    font-size: 13px;
  }
  .records-mobile-more > p {
    margin: 0;
    padding: 6px 0;
    color: var(--muted);
    font-size: 12px;
  }
  .records-more-error {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-wrap: wrap;
    gap: 8px;
    color: var(--danger);
    font-size: 12px;
  }
  .records-more-error button {
    padding: 6px 12px;
    border: 1px solid var(--danger);
    border-radius: 999px;
    background: #fff;
    color: var(--danger);
  }
}
</style>
