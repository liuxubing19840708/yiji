<script setup>
import { onMounted, ref } from 'vue'
import { api, createRequestKey } from '../api'
import { fullDateTime, number } from '../format'
import AppIcon from './AppIcon.vue'

const props = defineProps({ shipment: { type: Object, required: true }, boxes: { type: Array, default: () => [] } })
const emit = defineEmits(['close', 'toast'])
const template = ref('shipment'), logs = ref([]), loading = ref(false), reporting = ref(false), error = ref('')
async function loadLogs() {
  loading.value = true; error.value = ''
  try { logs.value = await api(`/shipments/${props.shipment.id}/print-logs`) } catch (e) { error.value = e.message } finally { loading.value = false }
}
function doPrint() { window.print() }
async function report() {
  if (!window.confirm(`请确认${template.value === 'packing' ? '装箱单' : '出货单'}已实际打印成功。确认后将记录打印次数和补打状态。`)) return
  reporting.value = true; error.value = ''
  try {
    const result = await api(`/shipments/${props.shipment.id}/print-logs`, { method: 'POST', body: { template: template.value, print_key: createRequestKey() } })
    emit('toast', result.reported ? '打印结果已上报' : '本次打印键已上报，未重复计数')
    await loadLogs()
  } catch (e) { error.value = e.message } finally { reporting.value = false }
}
onMounted(loadLogs)
</script>

<template>
  <Teleport to="body"><div class="modal-backdrop print-backdrop"><section class="modal print-dialog shipment-print" role="dialog" aria-modal="true" aria-label="出货打印预览"><div class="modal-header no-print"><div><h2>出货打印预览</h2><p>{{ shipment.shipment_no }} · {{ shipment.customer_name }}</p></div><button class="icon-button" aria-label="关闭" @click="emit('close')"><AppIcon name="close" /></button></div><div class="filter-bar no-print"><div class="segmented"><button :class="{ active: template === 'shipment' }" @click="template = 'shipment'">出货单</button><button :class="{ active: template === 'packing' }" @click="template = 'packing'">装箱单</button></div></div><p class="section-note no-print">先浏览器打印，实际成功后再上报；取消打印不会增加打印计数。</p><article class="shipment-paper"><h2>{{ template === 'packing' ? '装箱单' : '出货单' }}</h2><div class="detail-grid"><div class="detail-item"><small>出货单号</small><strong>{{ shipment.shipment_no }}</strong></div><div class="detail-item"><small>订单 / 客户</small><strong>{{ shipment.order_no }} · {{ shipment.customer_name }}</strong></div><div class="detail-item"><small>发货日期 / 物流</small><strong>{{ shipment.ship_date || '—' }} · {{ shipment.logistics_no || '—' }}</strong></div></div><table v-if="template === 'shipment'"><thead><tr><th>颜色</th><th>尺码</th><th class="numeric">数量</th></tr></thead><tbody><tr v-for="item in shipment.items || []" :key="item.id"><td>{{ item.color }}</td><td>{{ item.size }}</td><td class="numeric">{{ number(item.qty) }}</td></tr><tr><td colspan="2"><strong>合计</strong></td><td class="numeric"><strong>{{ number(shipment.total_qty) }}</strong></td></tr></tbody></table><table v-else><thead><tr><th>箱号</th><th>颜色 / 尺码</th><th class="numeric">数量</th><th class="numeric">毛重 / 净重</th><th>箱规</th></tr></thead><tbody><tr v-for="box in boxes" :key="box.id"><td>{{ box.box_no }}</td><td>{{ box.color || '—' }} / {{ box.size || '—' }}</td><td class="numeric">{{ number(box.qty) }}</td><td class="numeric">{{ box.gross_wt ?? '—' }} / {{ box.net_wt ?? '—' }}</td><td>{{ box.box_spec || '—' }}</td></tr><tr v-if="!boxes.length"><td colspan="5">暂无装箱明细</td></tr></tbody></table><p v-if="shipment.remark">备注：{{ shipment.remark }}</p></article><p v-if="error" class="form-error no-print">{{ error }}</p><section class="print-history no-print"><div class="card-header"><h3>打印历史</h3><button class="button ghost small" @click="loadLogs">刷新</button></div><p v-if="loading" class="section-note">正在读取…</p><p v-else-if="!logs.length" class="section-note">暂无上报记录</p><div v-else class="table-wrap"><table><thead><tr><th>时间</th><th>模板</th><th>操作人</th><th>类型</th></tr></thead><tbody><tr v-for="log in logs" :key="log.id"><td>{{ fullDateTime(log.created_at) }}</td><td>{{ log.template_display }}</td><td>{{ log.operator_name || '—' }}</td><td>{{ log.is_reprint ? '补打' : '首次' }}</td></tr></tbody></table></div></section><div class="form-actions no-print"><button class="button secondary" @click="emit('close')">关闭</button><button class="button secondary" @click="doPrint">浏览器打印</button><button class="button primary" :disabled="reporting" @click="report">{{ reporting ? '上报中…' : '确认已打印并上报' }}</button></div></section></div></Teleport>
</template>

<style scoped>
.shipment-print{width:min(900px,100%)}.shipment-paper{padding:12px}.shipment-paper h2{text-align:center;margin:10px 0 22px}.print-history{margin-top:24px;border-top:1px solid var(--line);padding-top:18px}.print-history .card-header{margin-bottom:8px}.form-error{margin-top:14px}@media print{.print-history{display:none!important}.shipment-paper{padding:0}.shipment-paper th,.shipment-paper td{border:1px solid #777}.shipment-paper .detail-item{border:1px solid #ccc}}
</style>
