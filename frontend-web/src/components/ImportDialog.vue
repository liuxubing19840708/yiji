<script setup>
import { computed, ref } from 'vue'
import { upload } from '../api'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  title: { type: String, required: true },
  endpoint: { type: String, required: true },
  description: { type: String, default: '请选择后端约定模板的 .xlsx 文件；导入采用整批成功或整批失败。' },
})
const emit = defineEmits(['close', 'imported'])
const file = ref(null), saving = ref(false), error = ref(''), result = ref(null)
const summary = computed(() => Object.entries(result.value || {}).filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value)))
const errors = computed(() => Array.isArray(result.value?.errors) ? result.value.errors : [])
function choose(event) {
  const selected = event.target.files?.[0] || null
  error.value = ''; result.value = null
  if (selected && !selected.name.toLowerCase().endsWith('.xlsx')) {
    file.value = null; event.target.value = ''; error.value = '仅支持 .xlsx 文件'; return
  }
  file.value = selected
}
async function submit() {
  if (!file.value) { error.value = '请先选择 .xlsx 文件'; return }
  if (!window.confirm(`确认导入“${file.value.name}”？请核对公司及模板，失败时整批不会写入。`)) return
  saving.value = true; error.value = ''; result.value = null
  try {
    result.value = await upload(props.endpoint, file.value)
    emit('imported', result.value)
  } catch (e) {
    error.value = e.message
    if (Array.isArray(e.data?.errors)) result.value = e.data
  } finally { saving.value = false }
}
</script>

<template>
  <Teleport to="body"><div class="modal-backdrop" @click.self="!saving && emit('close')"><section class="modal" role="dialog" aria-modal="true" :aria-label="title"><div class="modal-header"><div><h2>{{ title }}</h2><p>{{ description }}</p></div><button class="icon-button" aria-label="关闭" :disabled="saving" @click="emit('close')"><AppIcon name="close" /></button></div><form @submit.prevent="submit"><label class="field"><span>Excel 文件（.xlsx）*</span><input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required @change="choose" /></label><p v-if="file" class="section-note">已选择：{{ file.name }} · {{ (file.size / 1024).toFixed(1) }} KB</p><p v-if="error" class="form-error" role="alert">{{ error }}</p><div v-if="summary.length" class="detail-grid import-summary"><div v-for="([key, value]) in summary" :key="key" class="detail-item"><small>{{ key }}</small><strong>{{ value }}</strong></div></div><div v-if="errors.length" class="table-wrap import-errors"><table><thead><tr><th>行</th><th>错误</th></tr></thead><tbody><tr v-for="(item, index) in errors" :key="index"><td>{{ item.row ?? item.row_no ?? index + 1 }}</td><td>{{ item.reason || item.error || item.message || JSON.stringify(item) }}</td></tr></tbody></table></div><div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="emit('close')">关闭</button><button class="button primary" :disabled="saving || !file"><span v-if="saving" class="spinner light" />{{ saving ? '正在校验并导入…' : result ? '重新导入' : '开始导入' }}</button></div></form></section></div></Teleport>
</template>

<style scoped>
.import-summary{margin-top:18px}.import-errors{margin-top:16px;max-height:280px;overflow:auto}.form-error{margin-top:14px}
</style>
