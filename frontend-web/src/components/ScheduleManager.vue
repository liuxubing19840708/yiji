<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, createRequestKey } from '../api'
import { fullDateTime } from '../format'

const props = defineProps({
  savedReports: { type: Array, default: () => [] },
  selectedSavedId: { type: String, default: '' },
  getPlan: { type: Function, required: true },
})
const emit = defineEmits(['toast'])
const schedules = ref([])
const runs = ref([])
const loading = ref(false)
const runsLoading = ref(false)
const submitting = ref(false)
const actingId = ref(null)
const runActingKey = ref('')
const error = ref('')
const runsError = ref('')
const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const form = reactive({
  name: '', source: 'current', savedReportId: '', frequency: 'daily',
  hour: 9, minute: 0, weekday: 0, dayOfMonth: 1, enabled: true,
})
const ownedReports = computed(() => props.savedReports.filter((item) => item.is_owner))

watch(() => props.selectedSavedId, (value) => {
  if (value && ownedReports.value.some((item) => String(item.id) === value)) {
    form.source = 'saved'
    form.savedReportId = value
  }
}, { immediate: true })
watch(ownedReports, () => {
  if (form.source === 'saved' && !ownedReports.value.some((item) => String(item.id) === form.savedReportId)) {
    form.savedReportId = ownedReports.value[0] ? String(ownedReports.value[0].id) : ''
  }
})
watch(() => form.source, (source) => {
  if (source === 'saved' && !ownedReports.value.some((item) => String(item.id) === form.savedReportId)) {
    form.savedReportId = ownedReports.value[0] ? String(ownedReports.value[0].id) : ''
  }
})

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await api('/ai/report/schedules')
    schedules.value = result.results || []
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}
async function loadRuns() {
  if (runsLoading.value) return
  runsLoading.value = true
  runsError.value = ''
  try {
    const result = await api('/ai/report/schedules/runs', { params: { limit: 50 } })
    runs.value = result.results || []
  } catch (err) {
    runsError.value = err.message
  } finally {
    runsLoading.value = false
  }
}
async function refresh() {
  await Promise.all([load(), loadRuns()])
}
const runKinds = { natural: '自然调度', manual: '手动执行', retry: '失败重试' }
const runStatuses = {
  pending: '等待执行', running: '正在执行', success: '执行成功',
  denied: '权限拒绝', invalid: '计划无效', failed: '执行失败',
}
function runStatus(item) {
  return runStatuses[item.status] || '未知状态'
}
function canRetry(item) {
  return ['failed', 'denied', 'invalid'].includes(item.status)
}
function timeText(value) {
  return value ? fullDateTime(value) : '—'
}
function upsertRun(run) {
  const index = runs.value.findIndex((item) => item.id === run.id)
  if (index >= 0) runs.value.splice(index, 1, run)
  else runs.value.unshift(run)
}
async function executeSchedule(item) {
  if (runActingKey.value) return
  runActingKey.value = `run-${item.id}`
  runsError.value = ''
  try {
    const result = await api(`/ai/report/schedules/${item.id}/run`, {
      method: 'POST', body: { request_id: createRequestKey() },
    })
    upsertRun(result)
    emit('toast', result.status === 'success' ? '手动执行完成，请从通知进入报表查看' : '本次执行未成功，可在运行中心查看并处理')
  } catch (err) {
    runsError.value = err.message
  } finally {
    runActingKey.value = ''
  }
}
async function retryRun(item) {
  if (runActingKey.value || !canRetry(item)) return
  runActingKey.value = `retry-${item.id}`
  runsError.value = ''
  try {
    const result = await api(`/ai/report/schedules/runs/${item.id}/retry`, {
      method: 'POST', body: { request_id: createRequestKey() },
    })
    upsertRun(result)
    emit('toast', result.status === 'success' ? '失败运行已重试完成' : '重试未成功，已保留新的运行记录')
  } catch (err) {
    runsError.value = err.message
  } finally {
    runActingKey.value = ''
  }
}
function validInteger(value, minimum, maximum) {
  return Number.isInteger(value) && value >= minimum && value <= maximum
}
function scheduleText(item) {
  const time = `${String(item.hour).padStart(2, '0')}:${String(item.minute).padStart(2, '0')}`
  if (item.frequency === 'weekly') return `每${weekdays[item.weekday] || '周'} ${time}`
  if (item.frequency === 'monthly') return `每月 ${item.day_of_month} 日 ${time}`
  return `每天 ${time}`
}
async function createSchedule() {
  if (submitting.value) return
  error.value = ''
  submitting.value = true
  try {
    if (!form.name.trim()) throw new Error('请输入定时任务名称')
    if (!validInteger(form.hour, 0, 23) || !validInteger(form.minute, 0, 59)) throw new Error('执行时间须为有效整数：小时 0–23，分钟 0–59')
    if (form.frequency === 'weekly' && !validInteger(form.weekday, 0, 6)) throw new Error('请选择每周执行日')
    if (form.frequency === 'monthly' && !validInteger(form.dayOfMonth, 1, 31)) throw new Error('每月执行日须为 1 至 31 的整数')
    const body = {
      name: form.name.trim(), frequency: form.frequency,
      hour: form.hour, minute: form.minute, enabled: form.enabled,
    }
    if (form.frequency === 'weekly') body.weekday = form.weekday
    if (form.frequency === 'monthly') body.day_of_month = form.dayOfMonth
    if (form.source === 'saved') {
      const saved = ownedReports.value.find((item) => String(item.id) === form.savedReportId)
      if (!saved) throw new Error('只能引用自己拥有的已保存报表，请重新选择')
      body.saved_report_id = Number(saved.id)
    } else {
      body.plan = props.getPlan()
    }
    const result = await api('/ai/report/schedules', { method: 'POST', body })
    schedules.value.unshift(result)
    form.name = ''
    emit('toast', `定时报表已创建，下次执行：${fullDateTime(result.next_run_at)}`)
  } catch (err) {
    error.value = err.message
  } finally {
    submitting.value = false
  }
}
async function toggle(item) {
  if (actingId.value !== null) return
  actingId.value = item.id
  error.value = ''
  try {
    const result = await api(`/ai/report/schedules/${item.id}`, {
      method: 'PATCH', body: { enabled: !item.enabled },
    })
    const index = schedules.value.findIndex((row) => row.id === item.id)
    if (index >= 0) schedules.value.splice(index, 1, result)
    emit('toast', result.enabled ? '定时报表已启用' : '定时报表已停用')
  } catch (err) {
    error.value = err.message
  } finally {
    actingId.value = null
  }
}
async function remove(item) {
  if (actingId.value !== null || !window.confirm(`确定删除定时任务“${item.name}”？`)) return
  actingId.value = item.id
  error.value = ''
  try {
    await api(`/ai/report/schedules/${item.id}`, { method: 'DELETE' })
    schedules.value = schedules.value.filter((row) => row.id !== item.id)
    emit('toast', '定时报表已删除')
  } catch (err) {
    error.value = err.message
  } finally {
    actingId.value = null
  }
}
onMounted(refresh)
</script>

<template>
  <section class="schedule-manager" aria-labelledby="schedule-manager-title">
    <div class="schedule-heading">
      <div><h4 id="schedule-manager-title">定时报表</h4><p>按日、周或月执行计划，并在通知中提供查看入口。</p></div>
      <button type="button" class="button ghost small" :disabled="loading || runsLoading" @click="refresh">刷新任务与运行记录</button>
    </div>
    <form class="schedule-form" @submit.prevent="createSchedule">
      <label class="field schedule-name"><span>定时任务名称</span><input v-model="form.name" maxlength="80" required placeholder="例如：每周一生产汇总" /></label>
      <label class="field"><span>计划来源</span><select v-model="form.source" aria-label="定时计划来源"><option value="current">当前设计器条件</option><option value="saved" :disabled="!ownedReports.length">自己拥有的已保存报表</option></select></label>
      <label v-if="form.source === 'saved'" class="field"><span>选择自己的报表</span><select v-model="form.savedReportId" required aria-label="定时已保存报表"><option value="" disabled>请选择</option><option v-for="item in ownedReports" :key="item.id" :value="String(item.id)">{{ item.name }}</option></select></label>
      <label class="field"><span>执行频率</span><select v-model="form.frequency" aria-label="定时执行频率"><option value="daily">每天</option><option value="weekly">每周</option><option value="monthly">每月</option></select></label>
      <label v-if="form.frequency === 'weekly'" class="field"><span>每周执行日</span><select v-model.number="form.weekday" aria-label="每周执行日"><option v-for="(label, index) in weekdays" :key="label" :value="index">{{ label }}</option></select></label>
      <label v-if="form.frequency === 'monthly'" class="field"><span>每月执行日</span><input v-model.number="form.dayOfMonth" type="number" min="1" max="31" step="1" required aria-label="每月执行日" /><small>日期不存在的月份会跳过。</small></label>
      <label class="field schedule-time"><span>小时（0–23）</span><input v-model.number="form.hour" type="number" min="0" max="23" step="1" required aria-label="定时小时" /></label>
      <label class="field schedule-time"><span>分钟（0–59）</span><input v-model.number="form.minute" type="number" min="0" max="59" step="1" required aria-label="定时分钟" /></label>
      <label class="schedule-enabled"><input v-model="form.enabled" type="checkbox" />创建后立即启用</label>
      <button type="submit" class="button primary small schedule-create" :disabled="submitting"><span v-if="submitting" class="spinner light" />创建定时报表</button>
    </form>
    <p class="section-note">当前条件会先按设计器规则校验；只能引用自己拥有的 SavedReport，同事分享的报表不能作为引用来源。通知仅发送给届时仍有相应查询与数据权限的接收人，不包含也不保存查询结果、行数或筛选值。</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="loading && !schedules.length" class="section-note"><span class="spinner" />正在加载定时任务…</p>
    <div v-if="schedules.length" class="schedule-list">
      <article v-for="item in schedules" :key="item.id" class="schedule-item">
        <div class="schedule-item-main">
          <strong>{{ item.name }}</strong>
          <span>{{ scheduleText(item) }} · {{ item.saved_report ? `引用：${item.saved_report.name}` : '独立查询计划' }}</span>
          <small>{{ item.enabled ? `下次执行：${fullDateTime(item.next_run_at)}` : '已停用；启用后重新计算下次执行时间' }} · 时区：{{ item.timezone }}</small>
        </div>
        <div class="schedule-actions">
          <button type="button" class="button primary small" :disabled="runActingKey !== ''" @click="executeSchedule(item)">{{ runActingKey === `run-${item.id}` ? '执行中…' : '手动执行' }}</button>
          <button type="button" class="button secondary small" :disabled="actingId !== null" @click="toggle(item)">{{ item.enabled ? '停用' : '启用' }}</button>
          <button type="button" class="button ghost small" :disabled="actingId !== null" :aria-label="`删除定时任务 ${item.name}`" @click="remove(item)">删除定时任务</button>
        </div>
      </article>
    </div>
    <p v-else-if="!loading" class="schedule-empty">暂无定时任务</p>

    <section class="run-center" aria-labelledby="schedule-run-center-title">
      <div class="run-center-heading">
        <div><h4 id="schedule-run-center-title">运行中心</h4><p>仅显示你创建的任务运行；每次手动执行和失败重试都会留下独立审计记录。</p></div>
        <button type="button" class="button ghost small" :disabled="runsLoading" @click="loadRuns">刷新运行记录</button>
      </div>
      <p class="section-note">通知仅提供报表查看入口，不包含业务结果、行数或筛选值；失败信息只显示安全摘要。</p>
      <p v-if="runsError" class="form-error" role="alert">{{ runsError }}</p>
      <p v-if="runsLoading && !runs.length" class="section-note"><span class="spinner" />正在加载运行记录…</p>
      <div v-if="runs.length" class="run-list">
        <article v-for="item in runs" :key="item.id" class="schedule-run" :data-status="item.status">
          <div class="run-main">
            <div class="run-title"><strong>{{ item.schedule_name }}</strong><span class="run-status" :class="item.outcome">{{ runStatus(item) }}</span></div>
            <span>{{ runKinds[item.kind] || item.kind }} · 计划时间：{{ timeText(item.scheduled_at) }}</span>
            <small>开始：{{ timeText(item.started_at) }} · 完成：{{ timeText(item.finished_at) }}</small>
            <small v-if="item.retry_of_id">重试来源运行 ID：{{ item.retry_of_id }}</small>
            <p v-if="item.error_summary" class="run-error">{{ item.error_summary }}<template v-if="item.error_code">（{{ item.error_code }}）</template></p>
          </div>
          <button v-if="canRetry(item)" type="button" class="button secondary small" :disabled="runActingKey !== ''" @click="retryRun(item)">{{ runActingKey === `retry-${item.id}` ? '重试中…' : '失败重试' }}</button>
        </article>
      </div>
      <p v-else-if="!runsLoading" class="schedule-empty">暂无运行记录，可在上方手动执行一个任务</p>
    </section>
  </section>
</template>

<style scoped>
.schedule-manager{padding-top:18px}.schedule-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.schedule-heading h4{margin:0 0 5px;font-size:14px}.schedule-heading p{margin:0;color:var(--muted);font-size:12px}.schedule-heading>.button{flex:0 0 auto}.schedule-form{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));align-items:end;gap:12px;margin-top:14px}.schedule-form input,.schedule-form select{width:100%;min-width:0;min-height:38px;padding:8px 9px;border:1px solid var(--line);border-radius:8px;background:#fff}.schedule-form small{color:var(--muted)}.schedule-name{grid-column:span 2}.schedule-time{max-width:150px}.schedule-enabled{display:flex;align-items:center;gap:7px;min-height:38px;font-size:12px;white-space:nowrap}.schedule-enabled input{width:16px;min-height:16px;margin:0}.schedule-create{justify-self:start;white-space:nowrap}.schedule-manager>.section-note{margin:12px 0 0}.schedule-list{margin-top:14px;border-top:1px solid var(--line)}.schedule-item{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px 0;border-bottom:1px solid var(--line)}.schedule-item-main{display:grid;gap:4px;min-width:0}.schedule-item-main strong{overflow-wrap:anywhere}.schedule-item-main span,.schedule-item-main small{color:var(--muted);font-size:12px;line-height:1.5}.schedule-actions{display:flex;flex:0 0 auto;gap:7px}.schedule-empty{margin:14px 0 0;color:var(--muted);font-size:12px}
.run-center{margin-top:24px;padding-top:18px;border-top:1px solid var(--line)}.run-center-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.run-center-heading h4{margin:0 0 5px;font-size:14px}.run-center-heading p{margin:0;color:var(--muted);font-size:12px}.run-list{display:grid;gap:10px;margin-top:14px}.schedule-run{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--surface-soft,#fafafa)}.run-main{display:grid;gap:4px;min-width:0}.run-title{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.run-title strong{overflow-wrap:anywhere}.run-main>span,.run-main>small{color:var(--muted);font-size:12px;line-height:1.5}.run-status{display:inline-flex;padding:2px 8px;border-radius:999px;background:#f0f0f0;color:var(--muted);font-size:11px}.run-status.success{background:#e9f7ef;color:#19734a}.run-status.failed{background:#fff0ed;color:#b5412d}.run-error{margin:3px 0 0;color:#b5412d;font-size:12px;overflow-wrap:anywhere}.schedule-run>.button{flex:0 0 auto}
@media(max-width:900px){.schedule-form{grid-template-columns:repeat(2,minmax(0,1fr))}.schedule-name{grid-column:1/-1}.schedule-time{max-width:none}}
@media(max-width:560px){.schedule-heading,.run-center-heading{display:grid}.schedule-heading>.button,.run-center-heading>.button{justify-self:start}.schedule-form{grid-template-columns:repeat(2,minmax(0,1fr))}.schedule-form>.field:not(.schedule-time),.schedule-create,.schedule-enabled{grid-column:1/-1}.schedule-create{width:100%}.schedule-item,.schedule-run{align-items:stretch;flex-direction:column}.schedule-actions{justify-content:flex-end;flex-wrap:wrap}.schedule-actions .button{flex:1 1 auto}.schedule-run>.button{width:100%}}
</style>
