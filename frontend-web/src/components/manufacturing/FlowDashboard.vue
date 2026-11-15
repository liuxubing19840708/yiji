<script setup>
import { inject, ref, watch } from 'vue'
import { api } from '../../api'
import PageState from '../PageState.vue'

const flow = inject('manufacturingFlow')
const data = ref(null)
const loading = ref(true)
const error = ref('')
async function load() {
  if (!flow.route.value || flow.route.value.is_template) { data.value = null; loading.value = false; return }
  loading.value = true
  error.value = ''
  try { data.value = await api(`/manufacturing/routes/${flow.route.value.id}/dashboard`) }
  catch (reason) { error.value = reason.message }
  finally { loading.value = false }
}
watch(() => flow.state.selectedRouteId, load, { immediate: true })
</script>

<template>
  <PageState :loading="loading" :error="error" :empty="!data" empty-text="请选择实例路线" @retry="load">
    <div v-if="data" class="flow-dashboard">
      <section class="summary-strip" aria-label="路线数量汇总">
        <div><span>菲票数</span><strong>{{ data.summary.tickets }}</strong></div>
        <div><span>菲票总量</span><strong>{{ data.summary.ticket_qty }}</strong></div>
        <div><span>生产在手</span><strong>{{ data.summary.wip }}</strong></div>
        <div><span>累计报废</span><strong>{{ data.summary.scrapped }}</strong></div>
        <div><span>成品现货</span><strong>{{ data.summary.finished_available }}</strong></div>
      </section>
      <section class="card dashboard-card">
        <div class="card-header"><div><h3>单证待办</h3><p>待办数量来自业务单证，不与数量流水相加。</p></div></div>
        <div class="pending-list"><span>待确认质检 <strong>{{ data.pending.quality }}</strong></span><span>待签收裁片 <strong>{{ data.pending.cutting }}</strong></span><span>待审核包装 <strong>{{ data.pending.packing }}</strong></span><span>待审核入库 <strong>{{ data.pending.inbound }}</strong></span></div>
        <p v-if="data.delivery" class="delivery-line">订单 {{ data.delivery.ordered }} 件 · 已审核出货 {{ data.delivery.shipped }} 件 · 未交 {{ data.delivery.remaining }} 件</p>
      </section>
      <section class="card dashboard-card">
        <div class="card-header"><div><h3>菲票节点数量</h3><p>展开可查看接收、合格、在手、返修、报废及正常可转数量。</p></div></div>
        <div class="table-wrap"><table><thead><tr><th>床次 / 款号</th><th>菲票</th><th>颜色 / 尺码</th><th>菲票数</th><th>生产在手</th><th>累计报废</th><th>成品现货</th><th>节点明细</th></tr></thead><tbody><tr v-for="row in data.rows" :key="row.ticket_id"><td>{{ row.bed_no }}<small>{{ row.style_no || '—' }}</small></td><td>#{{ row.ticket_id }} / {{ row.bundle_no }}扎</td><td>{{ row.color }} / {{ row.size }}</td><td>{{ row.ticket_qty }}</td><td>{{ row.wip }}</td><td>{{ row.scrapped }}</td><td>{{ row.finished_available }}</td><td><details><summary>查看 {{ row.nodes.length }} 个节点</summary><div class="node-detail"><div v-for="node in row.nodes" :key="node.node_id"><strong>{{ node.sequence }}. {{ node.stage_name }}</strong><span>接收 {{ node.received }} · 合格 {{ node.qualified }} · 在手 {{ node.available }} · 可转 {{ node.transferable }} · 返修入 {{ node.rework }} · 报废 {{ node.scrapped }}</span></div></div></details></td></tr></tbody></table></div>
      </section>
    </div>
  </PageState>
</template>

<style scoped>
.flow-dashboard{display:grid;gap:16px}.summary-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden}.summary-strip>div{padding:18px;background:var(--surface,#fff)}.summary-strip span,.summary-strip strong{display:block}.summary-strip span{color:var(--muted);font-size:12px}.summary-strip strong{margin-top:7px;font-size:25px}.dashboard-card{padding:0;overflow:hidden}.dashboard-card>.card-header{padding:18px 20px 10px}.pending-list{display:flex;flex-wrap:wrap;gap:10px;padding:8px 20px 18px}.pending-list span{padding:9px 12px;border:1px solid var(--line);border-radius:8px;color:var(--text-secondary);font-size:12px}.pending-list strong{margin-left:8px;color:var(--text)}.delivery-line{margin:0;padding:12px 20px;border-top:1px solid var(--line);font-size:13px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:11px 12px;border-top:1px solid var(--line);text-align:left;white-space:nowrap}th{color:var(--muted);font-weight:500}td small{display:block;margin-top:4px;color:var(--muted)}details{min-width:110px}summary{cursor:pointer;color:var(--orange-dark)}.node-detail{position:absolute;z-index:5;width:min(580px,80vw);padding:10px 14px;border:1px solid var(--line);border-radius:8px;background:var(--surface,#fff);box-shadow:0 8px 24px #1f232926;white-space:normal}.node-detail>div{padding:8px 0;border-bottom:1px solid var(--line)}.node-detail strong,.node-detail span{display:block}.node-detail span{margin-top:4px;color:var(--text-secondary);line-height:1.6}
@media(max-width:760px){.summary-strip{grid-template-columns:repeat(2,minmax(0,1fr))}.summary-strip>div:last-child{grid-column:1/-1}.node-detail{position:fixed;left:10%;top:30%}}
</style>
