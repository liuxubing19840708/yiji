<script setup>
import { onMounted, ref } from 'vue'
import { api, createRequestKey } from '../api'
import AppIcon from './AppIcon.vue'
import WebBluetoothPanel from './printing/WebBluetoothPanel.vue'

const props = defineProps({ tickets: { type: Array, required: true }, production: { type: Object, required: true } })
const emit = defineEmits(['close', 'toast'])
const rows = ref([]), error = ref(''), reporting = ref(false)
const printSettings = ref(null), settingsError = ref(''), settingsLoading = ref(false)

function doPrint() { window.print() }

async function loadPrintSettings() {
  settingsLoading.value = true
  settingsError.value = ''
  try {
    printSettings.value = await api('/settings')
  } catch (err) {
    settingsError.value = `标签模板读取失败：${err.message}。蓝牙菲票未开放发送，浏览器打印仍可使用。`
  } finally {
    settingsLoading.value = false
  }
}

onMounted(async () => {
  try {
    const { default: QRCode } = await import('qrcode')
    rows.value = await Promise.all(props.tickets.map(async (ticket) => ({
      ...ticket,
      image: await QRCode.toDataURL(ticket.qr_code, { width: 180, margin: 1, errorCorrectionLevel: 'M' }),
    })))
  } catch (err) {
    error.value = `二维码生成失败：${err.message}`
  }
})
onMounted(loadPrintSettings)

async function report() {
  if (!window.confirm(`请确认本次全部 ${rows.value.length} 张菲票已成功打印。确认后将增加打印计数并记录补打状态。蓝牙“指令已写入”本身不代表可以上报。`)) return
  reporting.value = true
  error.value = ''
  try {
    const result = await api('/tickets/print', {
      method: 'POST',
      body: { ticket_ids: rows.value.map((ticket) => ticket.id), print_key: createRequestKey() },
    })
    emit('toast', `已上报 ${result.reported} 张，重复 ${result.duplicated.length} 张`)
    emit('close')
  } catch (err) {
    error.value = err.message
  } finally {
    reporting.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="modal-backdrop print-backdrop">
      <section class="modal print-dialog ticket-print-dialog" role="dialog" aria-modal="true" aria-label="菲票打印预览">
        <div class="modal-header no-print">
          <div><h2>菲票打印预览</h2><p>{{ production.bed_no }} · {{ production.style_no }} · 共 {{ rows.length }} 张</p></div>
          <button class="icon-button" aria-label="关闭" @click="emit('close')"><AppIcon name="close" /></button>
        </div>
        <p class="section-note no-print">可使用真实中文位图蓝牙打印，或使用浏览器打印。两种方式都不会自动上报；只有逐张现场确认实际打印成功后，才点击下方“确认已打印并上报”。</p>
        <div class="no-print">
          <p v-if="settingsLoading" class="section-note"><span class="spinner" /> 正在读取公司标签模板…</p>
          <p v-else-if="settingsError" class="form-error" role="alert">{{ settingsError }} <button type="button" class="button ghost small" @click="loadPrintSettings">重试</button></p>
          <WebBluetoothPanel v-else-if="printSettings && tickets.length" :settings="printSettings" :tickets="tickets" :production="production" :show-test="false" />
        </div>
        <div class="ticket-print-grid">
          <article v-for="ticket in rows" :key="ticket.id" class="ticket-label">
            <div><strong>{{ production.style_no }} · {{ production.style_name }}</strong><h3>{{ production.bed_no }} / {{ ticket.bundle_no }} 扎</h3><p>{{ ticket.color }} / {{ ticket.size }}　数量：<b>{{ ticket.qty }}</b></p><small>菲票 ID：{{ ticket.id }}</small></div>
            <img :src="ticket.image" :alt="`菲票 ${ticket.bundle_no} 二维码`" />
          </article>
        </div>
        <p v-if="error" class="form-error no-print">{{ error }}</p>
        <div class="form-actions no-print">
          <button type="button" class="button secondary" @click="emit('close')">关闭</button>
          <button type="button" class="button secondary" :disabled="!rows.length" @click="doPrint">浏览器打印</button>
          <button type="button" class="button primary" :disabled="reporting || !rows.length" @click="report">{{ reporting ? '上报中…' : `确认本次 ${rows.length} 张均已打印并上报` }}</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.ticket-print-dialog{width:min(1100px,100%)}
@media print{.ticket-print-dialog>.no-print{display:none!important}}
</style>
