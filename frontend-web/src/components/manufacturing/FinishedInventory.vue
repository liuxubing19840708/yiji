<script setup>
import { inject, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../../api'
import PageState from '../PageState.vue'

const flow = inject('manufacturingFlow')
const data = ref({ results: [], total_available: 0 })
const loading = ref(true)
const error = ref('')
const filters = reactive({ color: '', size: '', include_zero: false })
async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api('/finished-goods/inventory', { params: {
      route_id: flow.route.value && !flow.route.value.is_template ? flow.route.value.id : undefined,
      ticket_id: flow.ticket.value?.id,
      color: filters.color.trim(), size: filters.size.trim(), include_zero: filters.include_zero ? 1 : 0,
    } })
  } catch (reason) { error.value = reason.message; data.value = { results: [], total_available: 0 } } finally { loading.value = false }
}
function clearTicket() { flow.state.selectedTicketId = ''; load() }
onMounted(load)
</script>

<template>
  <section class="card inventory-card">
    <div class="card-header"><div><h3>成品现货</h3><p>唯一口径：成品入库节点的净在手数量，不与包装数量或旧计件量混用。</p></div><div class="inventory-total"><small>当前可售现货</small><strong>{{ data.total_available || 0 }}</strong><span>件</span></div></div>
    <form class="inventory-filters" @submit.prevent="load"><label class="field"><span>颜色</span><input v-model="filters.color" placeholder="全部颜色" /></label><label class="field"><span>尺码</span><input v-model="filters.size" placeholder="全部尺码" /></label><label class="inventory-zero"><input v-model="filters.include_zero" type="checkbox" />显示零库存</label><button class="button primary">查询现货</button><button v-if="flow.ticket.value" type="button" class="button secondary" @click="clearTicket">清除菲票筛选</button></form>
    <p v-if="flow.ticket.value" class="inventory-context">正在按菲票 #{{ flow.ticket.value.id }} · {{ flow.ticket.value.bed_no }} · {{ flow.ticket.value.style_no }} · {{ flow.ticket.value.color }}/{{ flow.ticket.value.size }} · 第{{ flow.ticket.value.bundle_no }}扎筛选。</p>
    <PageState :loading="loading" :error="error" :empty="!rows(data).length" empty-text="当前筛选下暂无成品现货" @retry="load"><div class="table-wrap"><table><thead><tr><th>路线 / 床次</th><th>款号</th><th>菲票 / 扎号</th><th>颜色 / 尺码</th><th class="numeric">入库接收</th><th class="numeric">已转出 / 报废</th><th class="numeric">现货</th></tr></thead><tbody><tr v-for="item in rows(data)" :key="`${item.route_id}-${item.ticket_id}`"><td><span class="cell-title">路线 #{{ item.route_id }} · {{ item.bed_no }}</span></td><td>{{ item.style_no || '—' }}</td><td>#{{ item.ticket_id }} · 第{{ item.bundle_no }}扎</td><td>{{ item.color || '—' }} / {{ item.size || '—' }}</td><td class="numeric">{{ item.received }}</td><td class="numeric">{{ item.transferred }} / {{ item.scrapped }}</td><td class="numeric inventory-available">{{ item.available }}</td></tr></tbody></table></div></PageState>
  </section>
</template>

<style scoped>
.inventory-card{padding:0;overflow:hidden}.inventory-card>.card-header{padding:20px}.inventory-total{display:flex;align-items:baseline;gap:6px;color:var(--orange-dark)}.inventory-total small{color:var(--muted)}.inventory-total strong{font-size:30px}.inventory-total span{font-size:12px}.inventory-filters{display:flex;align-items:end;gap:12px;padding:0 20px 16px;flex-wrap:wrap}.inventory-filters .field{width:160px}.inventory-zero{display:flex;align-items:center;gap:7px;min-height:40px;font-size:12px;color:var(--text-secondary)}.inventory-zero input{width:17px;min-height:17px}.inventory-context{margin:0 20px 16px;padding:10px 12px;border-radius:8px;background:var(--orange-soft);color:var(--text-secondary);font-size:12px}.inventory-available{color:var(--good);font-size:16px;font-weight:650}
@media(max-width:760px){.inventory-card>.card-header{padding:14px 12px}.inventory-filters{padding:0 12px 14px;display:grid;grid-template-columns:1fr 1fr}.inventory-filters .field{width:auto}.inventory-filters .button{padding-inline:10px;font-size:12px}.inventory-context{margin:0 12px 12px}.inventory-total strong{font-size:24px}}
</style>
