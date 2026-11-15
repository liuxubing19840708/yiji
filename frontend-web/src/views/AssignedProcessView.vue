<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../api'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'

const emit = defineEmits(['toast'])
const loading = ref(true), error = ref(''), keyword = ref(''), saving = ref(false), formError = ref('')
const employees = ref([]), processes = ref([]), editing = ref(null)
const form = reactive({ id: 0, name: '', process_ids: [] })
const filtered = computed(() => employees.value.filter((item) => !keyword.value || `${item.name}${item.phone}${item.post_name}`.toLowerCase().includes(keyword.value.toLowerCase())))
function processName(id) { return processes.value.find((item) => item.id === Number(id))?.name || `工序 ${id}` }
async function load() {
  loading.value = true; error.value = ''
  try {
    const [employeeData, processData] = await Promise.all([api('/employees', { params: { tab: 'all', page_size: 200 } }), api('/processes', { params: { page_size: 200 } })])
    employees.value = rows(employeeData); processes.value = rows(processData)
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
function open(employee) { Object.assign(form, { id: employee.id, name: employee.name, process_ids: (employee.assigned_processes || []).map(Number) }); formError.value = ''; editing.value = employee }
async function save() {
  saving.value = true; formError.value = ''
  try { await api(`/employees/${form.id}/assigned-processes`, { method: 'PUT', body: { process_ids: form.process_ids.map(Number) } }); editing.value = null; emit('toast', '员工指定工序已保存'); await load() }
  catch (e) { formError.value = e.message } finally { saving.value = false }
}
onMounted(load)
</script>
<template>
  <div class="welcome"><div><h2>指定工序</h2><p>为员工配置固定工序之外的专属可选工序。</p></div><button class="button secondary" @click="load"><AppIcon name="refresh" :size="16" />刷新</button></div>
  <label class="search-box assigned-search"><AppIcon name="search" :size="17" /><input v-model="keyword" placeholder="搜索员工、手机号或岗位" /></label>
  <PageState :loading="loading" :error="error" :empty="!filtered.length" empty-text="没有匹配的员工" @retry="load">
    <div class="assigned-list"><button v-for="employee in filtered" :key="employee.id" class="assigned-card" @click="open(employee)"><div class="mobile-row-head"><div><strong>{{ employee.name }}</strong><small class="cell-subtitle">{{ employee.post_name || '未设置岗位' }} · {{ employee.group_no || 0 }} 组</small></div><AppIcon name="chevron" /></div><div class="assigned-chips"><span v-for="id in employee.assigned_processes || []" :key="id">{{ processName(id) }}</span><small v-if="!employee.assigned_processes?.length">未指定，使用全员固定工序</small></div></button></div>
  </PageState>
  <Teleport to="body"><div v-if="editing" class="modal-backdrop" @click.self="editing=null"><section class="modal" role="dialog" aria-modal="true" aria-label="编辑指定工序"><div class="modal-header"><div><h2>{{ form.name }} · 指定工序</h2><p>勾选后，该员工扫菲时可选择这些工序。</p></div><button class="icon-button" aria-label="关闭" @click="editing=null"><AppIcon name="close" /></button></div><div class="assigned-process-options"><label v-for="process in processes" :key="process.id"><input v-model="form.process_ids" type="checkbox" :value="process.id" /><span>{{ process.name }}</span></label></div><p v-if="formError" class="form-error">{{ formError }}</p><div class="form-actions"><button class="button secondary" @click="editing=null">取消</button><button class="button primary" :disabled="saving" @click="save">保存指定工序</button></div></section></div></Teleport>
</template>
