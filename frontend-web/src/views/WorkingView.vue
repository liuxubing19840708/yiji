<script setup>
import { onMounted, ref } from 'vue'
import { api, rows } from '../api'
import { fullDateTime, money, number, pct } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'

const emit = defineEmits(['toast', 'navigate'])
const loading = ref(true), error = ref(''), revoking = ref(0)
const beds = ref([]), records = ref([])
function progress(bed) { return bed.total_qty ? pct(Number(bed.registered_qty || 0) / Number(bed.total_qty) * 100) : 0 }
async function load() {
  loading.value = true; error.value = ''
  try {
    const [productionData, workbench] = await Promise.all([
      api('/productions', { params: { is_cut_done: 0, is_locked: 0, page_size: 100 } }),
      api('/records/workbench'),
    ])
    beds.value = rows(productionData).filter((bed) => !bed.is_cut_done && !bed.is_locked)
    records.value = workbench.my_today || []
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
async function revoke(record) {
  if (!confirm(`确认撤销 ${record.process_name} ${record.qty} 件登记？`)) return
  revoking.value = record.id
  try { await api(`/records/${record.id}`, { method: 'DELETE' }); emit('toast', '登记已撤销'); await load() }
  catch (e) { emit('toast', e.message) } finally { revoking.value = 0 }
}
onMounted(load)
</script>
<template>
  <div class="welcome"><div><h2>工作中</h2><p>进行中床次与本人今日登记。</p></div><button class="button secondary" @click="load"><AppIcon name="refresh" :size="16" />刷新</button></div>
  <PageState :loading="loading" :error="error" @retry="load">
    <section class="working-section"><div class="mobile-section-title"><h3>进行中床次</h3><span>{{ beds.length }} 个</span></div>
      <div v-if="beds.length" class="working-bed-list"><button v-for="bed in beds" :key="bed.id" class="working-bed-card" @click="emit('navigate','production')"><div class="mobile-row-head"><div><strong>{{ bed.bed_no }}</strong><small class="cell-subtitle">{{ bed.style_no }} · {{ bed.style_name }}</small></div><span class="badge producing">生产中</span></div><div class="mobile-meta"><span>裁货数 <strong>{{ number(bed.cut_total || bed.total_qty) }}</strong></span><span>登记量 <strong>{{ number(bed.registered_qty) }}</strong></span></div><div class="row-progress"><div class="meter-track"><div class="meter-fill" :style="{width:`${progress(bed)}%`}" /></div><span>{{ progress(bed).toFixed(0) }}%</span></div></button></div>
      <div v-else class="card mobile-empty">暂无进行中床次</div>
    </section>
    <section class="working-section"><div class="mobile-section-title"><h3>本人今日登记</h3><span>{{ records.length }} 条</span></div>
      <div v-if="records.length" class="working-record-list"><article v-for="record in records" :key="record.id" class="working-record-card"><div class="mobile-row-head"><div><strong>{{ record.bed_no }} · 第 {{ record.bundle_no }} 扎</strong><small class="cell-subtitle">{{ record.process_name }} · {{ fullDateTime(record.registered_at) }}</small></div><strong class="working-qty">{{ number(record.qty) }} 件</strong></div><div class="mobile-record-foot"><span>{{ money(record.amount) }}</span><button v-if="!record.settlement" class="button danger small" :disabled="revoking===record.id" @click="revoke(record)">撤销登记</button><span v-else class="badge settled">已结算</span></div></article></div>
      <div v-else class="card mobile-empty">今天还没有计件登记</div>
    </section>
  </PageState>
</template>
