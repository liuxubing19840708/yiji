<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, getSession, rows } from '../api'
import { money, number, pct } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'

const emit = defineEmits(['navigate'])
const loading = ref(true)
const error = ref('')
const workbench = ref({ beds: [], my_today: [], my_today_amount: '0' })
const orders = ref([])
const session = getSession()
const name = session.user?.real_name || '你好'
const company = session.companies?.find((item) => item.id === session.companyId)
const post = company?.post || {}
const isAdmin = Boolean(post.is_admin || post.permissions?.includes('*') || post.effective_perms?.includes('*'))
const permissions = post.effective_perms || post.permissions || []
const doneBeds = computed(() => workbench.value.beds.reduce((sum, bed) => sum + Number(bed.done_ticket_count || 0), 0))
const todayQty = computed(() => workbench.value.my_today.reduce((sum, record) => sum + Number(record.qty || 0), 0))

const modules = [
  { key: 'orders', label: '订单管理', icon: '📋', route: 'orders', perms: ['orders.manage'] },
  { key: 'production', label: '生产管理', icon: '🧵', route: 'production', perms: ['production.manage'] },
  { key: 'manufacturing-flow', label: '全流程生产', icon: '🏭', route: 'manufacturing-flow', perms: ['manufacturing.view', 'quality.view', 'cutting.view', 'finished_goods.view', 'preproduction.view'] },
  { key: 'kanban', label: '生产看板', icon: '📊', route: 'kanban', perms: ['production.manage', 'kanban.view', 'kanban.manage'] },
  { key: 'salary', label: '薪酬管理', icon: '💰', route: 'salary', section: 'overview', perms: ['salary.view'] },
  { key: 'my-salary', label: '我的薪酬', icon: '💴', route: 'salary', section: 'mine', always: true },
  { key: 'order-salary', label: '订单薪酬', icon: '🧾', route: 'salary', section: 'orders', perms: ['salary.view'] },
  { key: 'completion', label: '完成汇总', icon: '📊', route: 'salary', section: 'completion', perms: ['salary.view'] },
  { key: 'styles', label: '款式管理', icon: '👗', route: 'master', section: 'styles', perms: ['styles.manage'] },
  { key: 'tech-materials', label: '物料管理', icon: '🧶', route: 'tech', section: 'materials', perms: ['tech.material'] },
  { key: 'tech-boms', label: 'BOM管理', icon: '📐', route: 'tech', section: 'boms', perms: ['tech.bom'] },
  { key: 'customers', label: '客户管理', icon: '🤝', route: 'master', section: 'customers', perms: ['customers.manage'] },
  { key: 'employees', label: '员工管理', icon: '👥', route: 'organization', section: 'employees', perms: ['employees.manage'] },
  { key: 'in-progress', label: '工作中', icon: '⏳', route: 'working', always: true },
  { key: 'assist', label: '协助扫菲', icon: '📷', route: 'scan', perms: ['records.create', 'scan.assist'] },
  { key: 'batch-register', label: '批量代登', icon: '✍️', route: 'batch', perms: ['scan.assist'] },
  { key: 'quality', label: '质检管理', icon: '🔬', route: 'quality', perms: ['quality.view'] },
  { key: 'outsource', label: '外发管理', icon: '📤', route: 'outsource', perms: ['outsource.view'] },
  { key: 'handovers', label: '交接中心', icon: '🤝', route: 'handovers', perms: ['handover.view', 'handover.create', 'handover.receive', 'handover.manage'] },
  { key: 'process', label: '工序', icon: '⚙️', route: 'master', section: 'processes', always: true },
  { key: 'warehouse', label: '轻量仓库', icon: '📦', route: 'warehouse', section: 'lots', perms: ['warehouse.manage'] },
  { key: 'material-issue', label: '按床领料', icon: '🧶', route: 'warehouse', section: 'issues', perms: ['warehouse.manage'] },
  { key: 'suppliers', label: '供应商', icon: '🏭', route: 'warehouse', section: 'suppliers', perms: ['warehouse.supplier'] },
  { key: 'receipts', label: '暂收IQC', icon: '📥', route: 'warehouse', section: 'receipts', perms: ['warehouse.manage'] },
  { key: 'shipment', label: '出货管理', icon: '🚚', route: 'shipments', perms: ['shipment.view'] },
  { key: 'cost', label: '成本管理', icon: '🧮', route: 'costs', section: 'costs', perms: ['cost.view'] },
  { key: 'quotation', label: '报价管理', icon: '📄', route: 'quotations', perms: ['quotation.view'] },
  { key: 'receivable', label: '应收管理', icon: '💳', route: 'finance', section: 'receivables', perms: ['finance.view'] },
  { key: 'funds', label: '资金管理', icon: '🏦', route: 'finance', section: 'payables', perms: ['finance.view'] },
  { key: 'trace', label: '质量追溯', icon: '🔍', route: 'warehouse', section: 'trace', perms: ['trace.view'] },
  { key: 'print', label: '打印管理', icon: '🖨️', route: 'printing', perms: ['print.manage'] },
  { key: 'assigned-process', label: '指定工序', icon: '🛠️', route: 'assigned', perms: ['production.manage'] },
  { key: 'logs', label: '日志管理', icon: '📋', route: 'settings', section: 'audit', perms: ['logs.view'] },
  { key: 'client-logs', label: '客户端日志', icon: '📟', route: 'settings', section: 'client', perms: ['logs.client'] },
  { key: 'company-qr', label: '公司二维码', icon: '🔳', route: 'company-qr', perms: ['company.qr'] },
]
const groupKeys = [
  ['订单生产', ['orders', 'quotation', 'production', 'manufacturing-flow', 'assigned-process', 'in-progress', 'styles', 'tech-boms', 'tech-materials']],
  ['车间计件', ['batch-register', 'assist', 'handovers', 'quality', 'outsource', 'process']],
  ['薪资对账', ['salary', 'my-salary', 'order-salary', 'completion', 'receivable', 'funds']],
  ['供应链', ['warehouse', 'material-issue', 'receipts', 'suppliers', 'shipment']],
  ['经营报表', ['kanban', 'cost', 'trace']],
  ['协同档案', ['customers', 'employees', 'company-qr']],
  ['系统', ['print', 'logs', 'client-logs']],
]
const visibleModules = computed(() => modules.filter((item) => {
  if (!isAdmin) {
    return item.always || (item.key === 'handovers' && item.perms?.some((key) => permissions.includes(key)))
  }
  return item.always || isAdmin || item.perms?.some((key) => permissions.includes(key))
}))
const mobileGroups = computed(() => {
  if (!isAdmin) return [{ label: '我的工作', items: visibleModules.value }]
  return groupKeys.map(([label, keys]) => ({
    label,
    items: keys.map((key) => visibleModules.value.find((item) => item.key === key)).filter(Boolean),
  })).filter((group) => group.items.length)
})

function isoDate(date) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}
function monthRange(offset = 0) {
  const now = new Date()
  return { start: isoDate(new Date(now.getFullYear(), now.getMonth() + offset, 1)), end: isoDate(new Date(now.getFullYear(), now.getMonth() + offset + 1, 0)) }
}
const initialRange = monthRange()
const startDate = ref(initialRange.start)
const endDate = ref(initialRange.end)
const quick = ref('month')
function setQuick(value) {
  quick.value = value
  if (value === 'today') startDate.value = endDate.value = isoDate(new Date())
  if (value === 'month' || value === 'last') {
    const range = monthRange(value === 'last' ? -1 : 0)
    startDate.value = range.start; endDate.value = range.end
  }
}
function customDate() { quick.value = '' }
function openModule(item) { emit('navigate', item.route, item.section || '') }
function progress(bed) {
  const denominator = Number(bed.total_qty || 0)
  return denominator ? pct(Number(bed.registered_qty || 0) / denominator * 100) : 0
}
async function load() {
  loading.value = true; error.value = ''
  try {
    const [wb, orderData] = await Promise.all([
      api('/records/workbench'), api('/orders', { params: { page_size: 50 } }),
    ])
    workbench.value = wb; orders.value = rows(orderData).filter((o) => o.status === 'producing').slice(0, 4)
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
onMounted(load)
</script>
<template>
  <div class="dashboard-view">
  <div class="welcome"><div><h2>{{ name }}，今天也辛苦了</h2><p>这里是工厂最新的生产与登记概况。</p></div><button class="button primary" @click="emit('navigate', 'scan')"><AppIcon name="scan" :size="18" />开始扫菲</button></div>
  <PageState :loading="loading" :error="error" @retry="load">
    <div class="mini-dashboard">
      <section class="today-strip" aria-label="今日计件概况"><div class="today-cell"><strong class="today-num">{{ number(todayQty) }}</strong><span>今日件数</span></div><i class="today-divider" /><div class="today-cell"><strong class="today-num accent">{{ money(workbench.my_today_amount) }}</strong><span>今日预估工资</span></div></section>
      <section class="dashboard-date-range" aria-label="工作台日期范围">
        <div class="dashboard-date-inputs"><input v-model="startDate" type="date" aria-label="开始日期" @change="customDate" /><span>至</span><input v-model="endDate" type="date" aria-label="结束日期" @change="customDate" /></div>
        <div class="dashboard-date-tabs"><button :class="{on:quick==='today'}" @click="setQuick('today')">今日</button><button :class="{on:quick==='month'}" @click="setQuick('month')">本月</button><button :class="{on:quick==='last'}" @click="setQuick('last')">上月</button></div>
      </section>
      <section v-for="group in mobileGroups" :key="group.label" class="module-group"><h3>{{ group.label }}</h3><div class="module-grid"><button v-for="item in group.items" :key="item.key" :aria-label="item.label" @click="openModule(item)"><span class="module-icon" :class="`module-${item.key}`">{{ item.icon }}</span><span>{{ item.label }}</span></button></div></section>
      <p class="mini-copyright">衣计 · 计件生产管理</p>
    </div>
    <section class="stats-grid" aria-label="今日概况">
      <article class="stat-card"><div class="stat-label">今日登记<AppIcon class="stat-icon" name="scan" /></div><div class="stat-value">{{ number(workbench.my_today.length) }}<small> 条</small></div><p class="stat-note">我的今日扫菲记录</p></article>
      <article class="stat-card"><div class="stat-label">今日预估工资<AppIcon class="stat-icon" name="wallet" /></div><div class="stat-value">{{ money(workbench.my_today_amount) }}</div><p class="stat-note">按本人登记记录快照计算</p></article>
      <article class="stat-card"><div class="stat-label">进行中床次<AppIcon class="stat-icon" name="factory" /></div><div class="stat-value">{{ number(workbench.beds.length) }}<small> 个</small></div><p class="stat-note">当前未锁定的生产床次</p></article>
      <article class="stat-card"><div class="stat-label">已完工扎数<AppIcon class="stat-icon" name="check" /></div><div class="stat-value">{{ number(doneBeds) }}<small> 扎</small></div><p class="stat-note">进行中床次的完工合计</p></article>
    </section>
    <div class="dashboard-grid">
      <section class="card"><div class="card-header"><div><h3>床次生产进度</h3><p>登记件数 / 裁货件数，完整明细见生产模块</p></div><button class="text-button" @click="emit('navigate', 'production')">全部床次<AppIcon name="chevron" :size="14" /></button></div>
        <div v-if="workbench.beds.length"><div v-for="bed in workbench.beds.slice(0, 6)" :key="bed.id" class="progress-row"><div class="progress-name"><strong>{{ bed.bed_no }}</strong><small>{{ bed.style_no }} · {{ bed.style_name }}</small></div><div class="meter-track" role="meter" :aria-label="`${bed.bed_no}登记进度`" :aria-valuenow="progress(bed)" aria-valuemin="0" aria-valuemax="100"><div class="meter-fill" :style="{ width: `${progress(bed)}%` }" /></div><div class="progress-percent">{{ progress(bed).toFixed(0) }}%</div></div></div>
        <div v-else class="page-state">暂无进行中床次</div><p class="section-note">登记件数包含各工序登记量，仅用于查看现场工作量；完工情况以扎级工序为准。</p>
      </section>
      <section class="card"><div class="card-header"><div><h3>快捷入口</h3><p>常用现场操作</p></div></div><div class="quick-actions"><button class="quick-action" @click="emit('navigate', 'scan')"><AppIcon class="stat-icon" name="scan" /><strong>扫菲登记</strong><small>扫码或手工查扎</small></button><button class="quick-action" @click="emit('navigate', 'orders')"><AppIcon class="stat-icon" name="order" /><strong>订单进度</strong><small>{{ orders.length }} 个近期订单</small></button><button class="quick-action" @click="emit('navigate', 'salary', 'mine')"><AppIcon class="stat-icon" name="wallet" /><strong>我的薪酬</strong><small>查看计件与结算</small></button><button class="quick-action" @click="emit('navigate', 'ai')"><AppIcon class="stat-icon" name="spark" /><strong>AI 问报</strong><small>用一句话查数据</small></button></div></section>
    </div>
  </PageState>
  </div>
</template>
