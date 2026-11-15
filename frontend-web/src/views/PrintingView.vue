<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, rows } from '../api'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'
import TicketPrintDialog from '../components/TicketPrintDialog.vue'
import WebBluetoothPanel from '../components/printing/WebBluetoothPanel.vue'

const emit = defineEmits(['navigate'])
const loading = ref(true), error = ref(''), settings = ref({})
const history = ref([]), historyKeyword = ref(''), historyLoading = ref(false)
const historyError = ref(''), historyPage = ref(1), historyHasMore = ref(false)
const reprintTicket = ref(null)
const cloudConfigured = computed(() => {
  const value = settings.value.cloud_printer
  if (!value) return false
  if (typeof value === 'object') return Object.keys(value).length > 0
  try { return Object.keys(JSON.parse(value)).length > 0 } catch { return Boolean(String(value).trim()) }
})
async function loadSettings() {
  loading.value = true
  error.value = ''
  try { settings.value = await api('/settings') } catch (e) { error.value = e.message } finally { loading.value = false }
}
async function loadHistory(page = 1) {
  if (historyLoading.value) return
  historyLoading.value = true
  historyError.value = ''
  try {
    const base = { is_printed: true, page, page_size: 20 }
    const keyword = historyKeyword.value.trim()
    let result = await api('/tickets', { params: keyword ? { ...base, bed_no: keyword } : base })
    if (keyword && !rows(result).length && !result.next) {
      result = await api('/tickets', { params: { ...base, bundle_no: keyword } })
    }
    history.value = page === 1 ? rows(result) : history.value.concat(rows(result))
    historyPage.value = page
    historyHasMore.value = Boolean(result.next)
  } catch (e) { historyError.value = e.message } finally { historyLoading.value = false }
}
onMounted(() => { loadSettings(); loadHistory() })
</script>
<template>
  <div class="welcome"><div><h2>打印管理</h2><p>菲票、出货单与标签配置的 Web 工作入口。</p></div><button class="button secondary" @click="emit('navigate','settings','settings')"><AppIcon name="menu" :size="16" />打印参数设置</button></div>
  <PageState :loading="loading" :error="error" @retry="loadSettings">
    <section class="today-strip printing-status"><div class="today-cell"><strong class="today-num">{{ settings.default_print_mode === 'bluetooth' ? '蓝牙' : '云打印' }}</strong><span>公司默认打印方式</span></div><i class="today-divider" /><div class="today-cell"><strong class="today-num">{{ cloudConfigured ? '已配置' : '未配置' }}</strong><span>云打印机参数</span></div></section>
    <WebBluetoothPanel :settings="settings" />
    <section class="browser-print-section" aria-labelledby="browser-print-title">
      <h3 id="browser-print-title">浏览器打印（独立选项）</h3>
      <p class="section-note">不依赖蓝牙，进入业务页面预览后调用系统打印对话框。只有确认实际打印完成后，才在原业务页面人工上报打印结果。</p>
    </section>
    <div class="printing-actions"><button class="card" @click="emit('navigate','production')"><span class="module-icon">🖨️</span><strong>浏览器打印菲票</strong><small>进入生产床次，选择床次后预览并调用浏览器打印</small><AppIcon name="chevron" /></button><button class="card" @click="emit('navigate','shipments')"><span class="module-icon">🚚</span><strong>浏览器打印出货单</strong><small>进入出货管理，在出货详情中预览并调用浏览器打印</small><AppIcon name="chevron" /></button></div>
    <section class="card print-history" aria-labelledby="print-history-title">
      <div class="card-header"><div><h3 id="print-history-title">服务端菲票打印历史</h3><p>按床次或扎号查找已上报菲票并补打；本地蓝牙队列不是服务端历史。</p></div></div>
      <form class="filter-bar" @submit.prevent="loadHistory(1)"><label class="search-box"><AppIcon name="search" :size="16" /><input v-model="historyKeyword" placeholder="床次号或扎号" /></label><button class="button secondary" :disabled="historyLoading">查询</button></form>
      <p v-if="historyError" class="form-error" role="alert">{{ historyError }} <button type="button" class="text-button" @click="loadHistory(historyPage)">重试</button></p>
      <p v-else-if="!historyLoading && !history.length" class="section-note">暂无符合条件的打印历史</p>
      <article v-for="ticket in history" :key="ticket.id" class="print-history-row">
        <div><strong>床次 {{ ticket.bed_no }} · 第 {{ ticket.bundle_no }} 扎 × {{ ticket.qty }}</strong><p>{{ ticket.style_no }} {{ ticket.style_name }} · {{ ticket.color }}/{{ ticket.size }}</p><small>{{ String(ticket.print_at || '').replace('T', ' ').slice(0, 16) }} · 打印 {{ ticket.print_count }} 次</small></div>
        <button class="button secondary small" :disabled="!ticket.qr_code" @click="reprintTicket = ticket">补打</button>
      </article>
      <button v-if="historyHasMore" class="button ghost" :disabled="historyLoading" @click="loadHistory(historyPage + 1)">{{ historyLoading ? '加载中…' : '加载更多' }}</button>
    </section>
  </PageState>
  <TicketPrintDialog v-if="reprintTicket" :tickets="[reprintTicket]" :production="{ id: reprintTicket.production, bed_no: reprintTicket.bed_no, style_no: reprintTicket.style_no }" @close="reprintTicket = null; loadHistory(1)" />
</template>

<style scoped>
.browser-print-section{margin-top:24px}.browser-print-section h3{margin:0 0 6px;font-size:16px}
.print-history{margin-top:20px;padding:20px}.print-history-row{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 0;border-top:1px solid var(--line)}.print-history-row p{margin:4px 0;color:var(--text-secondary);font-size:13px}.print-history-row small{color:var(--muted)}.print-history-row .button{flex:none}
</style>
