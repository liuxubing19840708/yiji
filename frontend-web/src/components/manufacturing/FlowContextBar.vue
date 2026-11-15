<script setup>
import { computed, inject } from 'vue'
import TicketIdentity from './TicketIdentity.vue'

const flow = inject('manufacturingFlow')
const instances = computed(() => flow.state.routes.filter((route) => !route.is_template))
const templates = computed(() => flow.state.routes.filter((route) => route.is_template))
function routeChanged() {
  flow.selectRoute(flow.state.selectedRouteId)
}
</script>

<template>
  <section class="card flow-context-bar">
    <div class="flow-selectors">
      <label class="field"><span>生产路线 *</span><select v-model="flow.state.selectedRouteId" :disabled="!flow.state.routes.length" @change="routeChanged"><option value="">请选择路线</option><optgroup v-if="instances.length" label="生产实例"><option v-for="route in instances" :key="route.id" :value="String(route.id)">{{ route.code }} · {{ route.name }} · {{ flow.routeAnchor(route) }}</option></optgroup><optgroup v-if="templates.length" label="路线模板（需先实例化）"><option v-for="route in templates" :key="route.id" :value="String(route.id)">{{ route.code }} · {{ route.name }}</option></optgroup></select></label>
      <label class="field"><span>菲票（床次 / 款色码 / 扎号）*</span><select v-model="flow.state.selectedTicketId" :disabled="!flow.route.value || flow.route.value.is_template || flow.state.ticketLoading"><option value="">{{ flow.state.ticketLoading ? '正在读取菲票…' : '请选择菲票' }}</option><option v-for="ticket in flow.state.tickets" :key="ticket.id" :value="String(ticket.id)">{{ ticket.bed_no }} · {{ ticket.style_no }} · {{ ticket.color }}/{{ ticket.size }} · 第{{ ticket.bundle_no }}扎（{{ ticket.qty }}件）</option></select></label>
    </div>
    <TicketIdentity v-if="flow.ticket.value" :ticket="flow.ticket.value" />
    <p v-else-if="flow.route.value?.is_template" class="section-note">当前是路线模板，请在“路线执行”中实例化到床次或订单后报工。</p>
    <p v-else-if="flow.route.value && !flow.state.ticketLoading && !flow.state.tickets.length" class="section-note">该路线范围内暂无菲票，请先在“生产床次”完成编菲。</p>
    <p v-if="flow.state.contextError" class="form-error" role="alert">{{ flow.state.contextError }}</p>
  </section>
</template>

<style scoped>
.flow-context-bar{margin-bottom:16px;padding:16px 18px}.flow-selectors{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);gap:14px;margin-bottom:12px}.flow-selectors .section-note{margin:0}.form-error{margin:10px 0 0}
@media(max-width:760px){.flow-context-bar{padding:12px}.flow-selectors{grid-template-columns:1fr;gap:10px}.flow-selectors select{font-size:12px}}
</style>
