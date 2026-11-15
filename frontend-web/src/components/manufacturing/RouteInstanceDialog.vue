<script setup>
import { computed, reactive, ref } from 'vue'
import { api } from '../../api'
import AdminModal from '../AdminModal.vue'

const props = defineProps({ template: { type: Object, required: true }, productions: { type: Array, default: () => [] }, orders: { type: Array, default: () => [] } })
const emit = defineEmits(['close', 'saved'])
const saving = ref(false)
const error = ref('')
const form = reactive({ targetType: 'production', targetId: '', code: '', name: props.template.name })
const targets = computed(() => form.targetType === 'production' ? props.productions : props.orders)
async function save() {
  if (!form.targetId) { error.value = '请选择路线绑定的床次或订单'; return }
  saving.value = true
  error.value = ''
  try {
    const body = { code: form.code.trim() || undefined, name: form.name.trim() || undefined }
    body[form.targetType === 'production' ? 'production_id' : 'order_id'] = Number(form.targetId)
    const route = await api(`/manufacturing/routes/${props.template.id}/instantiate`, { method: 'POST', body })
    emit('saved', route)
  } catch (reason) { error.value = reason.message } finally { saving.value = false }
}
</script>

<template>
  <AdminModal title="实例化生产路线" :description="`${props.template.code} · ${props.template.name}；实例将固定绑定一个床次或订单。`" :saving="saving" @close="emit('close')">
    <form @submit.prevent="save">
      <div class="segmented instance-types"><button type="button" :class="{ active: form.targetType === 'production' }" @click="form.targetType = 'production'; form.targetId = ''">绑定床次</button><button type="button" :class="{ active: form.targetType === 'order' }" @click="form.targetType = 'order'; form.targetId = ''">绑定订单</button></div>
      <label class="field instance-target"><span>{{ form.targetType === 'production' ? '目标床次' : '目标订单' }} *</span><select v-model="form.targetId" required><option value="">请选择</option><option v-for="target in targets" :key="target.id" :value="target.id">{{ form.targetType === 'production' ? `${target.bed_no} · ${target.style_no} · ${target.order_no || '未关联订单'}` : `${target.order_no} · ${target.style_no}` }}</option></select></label>
      <div class="form-grid"><label class="field"><span>实例编码（留空自动生成）</span><input v-model="form.code" maxlength="64" /></label><label class="field"><span>实例名称</span><input v-model="form.name" maxlength="64" /></label></div>
      <p v-if="!targets.length" class="section-note">暂无可绑定对象，请先创建床次或订单。</p>
      <p v-if="error" class="form-error">{{ error }}</p>
      <div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="emit('close')">取消</button><button class="button primary" :disabled="saving || !targets.length">生成实例路线</button></div>
    </form>
  </AdminModal>
</template>

<style scoped>
.instance-types{margin-bottom:16px}.instance-target{margin-bottom:16px}
</style>
