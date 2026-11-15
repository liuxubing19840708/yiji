<script setup>
import { computed, reactive, ref } from 'vue'
import { api } from '../../api'
import AdminModal from '../AdminModal.vue'
import AppIcon from '../AppIcon.vue'

const props = defineProps({
  stages: { type: Array, default: () => [] },
  template: { type: Object, default: null },
  mode: { type: String, default: 'create' },
})
const emit = defineEmits(['close', 'saved'])
const saving = ref(false)
const error = ref('')
const source = props.template
const form = reactive({
  code: props.mode === 'create' ? '' : props.mode === 'edit' ? source.code : `${source.code}${props.mode === 'version' ? `-V${(source.version || 1) + 1}` : '-COPY'}`,
  name: props.mode === 'create' ? '' : props.mode === 'edit' ? source.name : `${source.name}${props.mode === 'version' ? ' 新版本' : ' 副本'}`,
  nodes: source ? source.nodes.map((node) => ({
    stage_id: node.stage, required: node.required,
    allow_parallel: node.allow_parallel, allow_skip: node.allow_skip,
  })) : [],
})
const types = [['cutting', '裁剪'], ['sewing', '车缝'], ['semi_inspection', '半成品检验'], ['special_machine', '专机'], ['ironing', '整烫'], ['final_inspection', '成品检验'], ['packing', '包装'], ['finished_inbound', '成品入库'], ['other', '其他']]
const title = computed(() => ({ create: '新建路线模板', edit: '编辑路线模板', copy: '复制独立模板', version: '创建路线新版本' }[props.mode]))
const submitLabel = computed(() => ({ create: '保存路线模板', edit: '保存路线模板', copy: '创建独立副本', version: '创建新版本' }[props.mode]))
function addNode() { form.nodes.push({ stage_id: '', required: true, allow_parallel: false, allow_skip: false }) }
async function save() {
  error.value = ''
  if (!form.code.trim() || !form.name.trim()) { error.value = '请填写路线编码和名称'; return }
  if (!form.nodes.length || form.nodes.some((node) => !node.stage_id)) { error.value = '请至少添加一个节点并选择节点类型'; return }
  saving.value = true
  const nodes = form.nodes.map((node, index) => ({ ...node, stage_id: Number(node.stage_id), sequence: index + 1 }))
  try {
    let route
    if (props.mode === 'edit') {
      route = await api(`/manufacturing/routes/${source.id}`, { method: 'PUT', body: { name: form.name.trim(), nodes } })
    } else if (['copy', 'version'].includes(props.mode)) {
      route = await api(`/manufacturing/routes/${source.id}/${props.mode === 'version' ? 'new-version' : 'copy'}`, {
        method: 'POST', body: { code: form.code.trim(), name: form.name.trim(), nodes },
      })
    } else {
      route = await api('/manufacturing/routes', { method: 'POST', body: { code: form.code.trim(), name: form.name.trim(), nodes } })
    }
    emit('saved', route)
  } catch (reason) { error.value = reason.message } finally { saving.value = false }
}
if (!form.nodes.length) addNode()
</script>

<template>
  <AdminModal :title="title" description="节点顺序即受控流转顺序；包装与入库节点用于后续成品单证。复制和新版本会保存当前窗口中的节点配置。" :saving="saving" wide @close="emit('close')">
    <form @submit.prevent="save">
      <div class="form-grid"><label class="field"><span>路线编码 *</span><input v-model="form.code" maxlength="64" required placeholder="厂内唯一编码" :disabled="props.mode === 'edit'" /></label><label class="field"><span>路线名称 *</span><input v-model="form.name" maxlength="64" required /></label></div>
      <p v-if="props.mode === 'version'" class="section-note">将从 V{{ source.version || 1 }} 创建 V{{ (source.version || 1) + 1 }}，保留上一版本关联；既有实例不受影响。</p>
      <p v-if="props.mode === 'copy'" class="section-note">独立副本从 V1 开始，不建立上一版本链，可单独调整节点。</p>
      <div class="card-header template-node-head"><h3>路线节点</h3><button type="button" class="button secondary small" @click="addNode">添加节点</button></div>
      <div v-for="(node, index) in form.nodes" :key="index" class="template-node-row">
        <span class="template-sequence">{{ index + 1 }}</span>
        <select v-model="node.stage_id" required aria-label="路线节点"><option value="">选择节点</option><option v-for="stage in props.stages" :key="stage.id" :value="stage.id" :disabled="stage.is_active === false && Number(node.stage_id) !== stage.id">{{ stage.name }} · {{ types.find((type) => type[0] === stage.stage_type)?.[1] || stage.stage_type }}{{ stage.is_active === false ? '（已停用，仅保留现有引用）' : '' }}</option></select>
        <label><input v-model="node.required" type="checkbox" />必过</label><label><input v-model="node.allow_parallel" type="checkbox" />并行</label><label><input v-model="node.allow_skip" type="checkbox" />可跳过</label>
        <button type="button" class="icon-button" aria-label="删除路线节点" :disabled="form.nodes.length === 1" @click="form.nodes.splice(index, 1)"><AppIcon name="close" :size="16" /></button>
      </div>
      <p v-if="!props.stages.length" class="section-note">暂无标准节点，请先关闭本窗口并新建节点。</p>
      <p v-if="error" class="form-error">{{ error }}</p>
      <div class="form-actions"><button type="button" class="button secondary" :disabled="saving" @click="emit('close')">取消</button><button class="button primary" :disabled="saving || !props.stages.length">{{ submitLabel }}</button></div>
    </form>
  </AdminModal>
</template>

<style scoped>
.template-node-head{margin:20px 0 10px}.template-node-row{display:grid;grid-template-columns:28px minmax(160px,1fr) repeat(3,auto) 34px;align-items:center;gap:9px;margin-bottom:9px}.template-node-row>label{display:flex;align-items:center;gap:4px;color:var(--text-secondary);font-size:12px;white-space:nowrap}.template-node-row>label input{width:16px;min-height:16px}.template-sequence{display:grid;place-items:center;width:24px;height:24px;border-radius:50%;background:var(--orange-soft);color:var(--orange-dark);font-size:11px}
@media(max-width:760px){.template-node-row{grid-template-columns:25px minmax(0,1fr) 32px;padding:10px 0;border-bottom:1px solid var(--line)}.template-node-row>select{grid-column:2;grid-row:1/3}.template-node-row>label{grid-row:3}.template-node-row>label:nth-of-type(1){grid-column:1/3}.template-node-row>.icon-button{grid-column:3;grid-row:1}.template-sequence{grid-row:1}}
</style>
