<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, provide, ref } from 'vue'
import { api, clearSession, getSession, saveSession } from './api'
import { hasPermission } from './permissions'
import AppIcon from './components/AppIcon.vue'
import LoginView from './views/LoginView.vue'

const DashboardView = defineAsyncComponent(() => import('./views/DashboardView.vue'))
const OrdersView = defineAsyncComponent(() => import('./views/OrdersView.vue'))
const ProductionView = defineAsyncComponent(() => import('./views/ProductionView.vue'))
const ManufacturingFlowView = defineAsyncComponent(() => import('./views/ManufacturingFlowView.vue'))
const PreproductionView = defineAsyncComponent(() => import('./views/PreproductionView.vue'))
const HandoverView = defineAsyncComponent(() => import('./views/HandoverView.vue'))
const ScanView = defineAsyncComponent(() => import('./views/ScanView.vue'))
const SalaryView = defineAsyncComponent(() => import('./views/SalaryView.vue'))
const NotificationsView = defineAsyncComponent(() => import('./views/NotificationsView.vue'))
const AiView = defineAsyncComponent(() => import('./views/AiView.vue'))
const RecordsView = defineAsyncComponent(() => import('./views/RecordsView.vue'))
const BatchRegisterView = defineAsyncComponent(() => import('./views/BatchRegisterView.vue'))
const TechView = defineAsyncComponent(() => import('./views/TechView.vue'))
const WarehouseView = defineAsyncComponent(() => import('./views/WarehouseView.vue'))
const ShipmentsView = defineAsyncComponent(() => import('./views/ShipmentsView.vue'))
const FinanceView = defineAsyncComponent(() => import('./views/FinanceView.vue'))
const CostsView = defineAsyncComponent(() => import('./views/CostsView.vue'))
const QuotationsView = defineAsyncComponent(() => import('./views/QuotationsView.vue'))
const OrganizationView = defineAsyncComponent(() => import('./views/OrganizationView.vue'))
const SettingsView = defineAsyncComponent(() => import('./views/SettingsView.vue'))
const MasterDataView = defineAsyncComponent(() => import('./views/MasterDataView.vue'))
const QualityView = defineAsyncComponent(() => import('./views/QualityView.vue'))
const OutsourceView = defineAsyncComponent(() => import('./views/OutsourceView.vue'))
const WorkingView = defineAsyncComponent(() => import('./views/WorkingView.vue'))
const KanbanView = defineAsyncComponent(() => import('./views/KanbanView.vue'))
const CompanyQrView = defineAsyncComponent(() => import('./views/CompanyQrView.vue'))
const AssignedProcessView = defineAsyncComponent(() => import('./views/AssignedProcessView.vue'))
const PrintingView = defineAsyncComponent(() => import('./views/PrintingView.vue'))
const ProfileView = defineAsyncComponent(() => import('./views/ProfileView.vue'))
const HelpView = defineAsyncComponent(() => import('./views/HelpView.vue'))

const groups = [
  { label: '现场工作', items: [
    { key: 'dashboard', label: '工作台', icon: 'grid', component: DashboardView },
    { key: 'scan', label: '扫菲', icon: 'scan', component: ScanView },
    { key: 'records', label: '计件记录', icon: 'order', component: RecordsView },
    { key: 'batch', label: '批量登记', icon: 'scan', component: BatchRegisterView },
    { key: 'notifications', label: '通知', icon: 'bell', component: NotificationsView },
    { key: 'ai', label: 'AI 问报', icon: 'spark', component: AiView },
  ] },
  { label: '生产协同', items: [
    { key: 'orders', label: '订单管理', icon: 'order', component: OrdersView },
    { key: 'production', label: '生产床次', icon: 'factory', component: ProductionView },
    { key: 'manufacturing-flow', label: '全流程生产', icon: 'factory', component: ManufacturingFlowView },
    { key: 'preproduction', label: '产前准备', icon: 'check', component: PreproductionView, hidden: true },
    { key: 'handovers', label: '交接中心', icon: 'building', component: HandoverView },
    { key: 'kanban', label: '生产看板', icon: 'grid', component: KanbanView },
    { key: 'working', label: '工作中', icon: 'order', component: WorkingView },
    { key: 'assigned', label: '指定工序', icon: 'check', component: AssignedProcessView },
    { key: 'shipments', label: '出货装箱', icon: 'factory', component: ShipmentsView },
    { key: 'quality', label: '质量管理', icon: 'check', component: QualityView },
    { key: 'outsource', label: '外发管理', icon: 'building', component: OutsourceView },
  ] },
  { label: '经营管理', items: [
    { key: 'salary', label: '薪酬结算', icon: 'wallet', component: SalaryView },
    { key: 'finance', label: '应收财务', icon: 'wallet', component: FinanceView },
    { key: 'costs', label: '成本损益', icon: 'grid', component: CostsView },
    { key: 'quotations', label: '报价管理', icon: 'order', component: QuotationsView },
  ] },
  { label: '基础与系统', items: [
    { key: 'warehouse', label: '采购仓库', icon: 'factory', component: WarehouseView },
    { key: 'tech', label: '物料与 BOM', icon: 'grid', component: TechView },
    { key: 'master', label: '基础档案', icon: 'building', component: MasterDataView },
    { key: 'organization', label: '员工与岗位', icon: 'user', component: OrganizationView },
    { key: 'printing', label: '打印管理', icon: 'menu', component: PrintingView },
    { key: 'company-qr', label: '公司二维码', icon: 'grid', component: CompanyQrView },
    { key: 'profile', label: '个人资料', icon: 'user', component: ProfileView },
    { key: 'help', label: '使用帮助', icon: 'menu', component: HelpView },
    { key: 'settings', label: '系统设置', icon: 'menu', component: SettingsView },
  ] },
]
const nav = groups.flatMap((group) => group.items)
const allNav = nav
const navPermissions = {
  scan: ['records.create'], records: ['working.view'], batch: ['scan.assist'],
  orders: ['orders.manage'], production: ['production.manage'],
  'manufacturing-flow': ['manufacturing.view', 'quality.view', 'cutting.view', 'finished_goods.view', 'preproduction.view'],
  preproduction: ['preproduction.view'],
  handovers: ['handover.view', 'handover.create', 'handover.receive', 'handover.manage'],
  kanban: ['production.manage', 'kanban.view', 'kanban.manage'], working: ['working.view'],
  assigned: ['production.manage'], shipments: ['shipment.view'], quality: ['quality.view'],
  outsource: ['outsource.view'], salary: ['salary.view', 'my_salary.view'],
  finance: ['finance.view'], costs: ['cost.view'], quotations: ['quotation.view'],
  warehouse: ['warehouse.manage', 'warehouse.supplier', 'trace.view'],
  tech: ['tech.material', 'tech.bom'], master: ['styles.manage', 'customers.manage'],
  organization: ['employees.manage'], printing: ['print.manage'], 'company-qr': ['company.qr'],
  settings: ['settings.manage', 'company.manage', 'logs.view', 'logs.client'],
}
const visibleGroups = computed(() => groups.map((group) => ({
  ...group,
  items: group.items.filter((item) => (
    !item.hidden && (!navPermissions[item.key] || navPermissions[item.key].some((permission) => hasPermission(permission)))
  )),
})).filter((group) => group.items.length))
provide('erpNavigation', allNav)
const initial = getSession()
const authenticated = ref(Boolean(initial.access && initial.companyId))
function hashParts() { const [key = 'dashboard', section = ''] = location.hash.slice(1).split('/'); return { key, section } }
const initialRoute = hashParts()
const currentKey = ref(initialRoute.key)
const currentSection = ref(initialRoute.section)
const session = ref(initial)
const unread = ref(0)
const switching = ref(false)
const moreOpen = ref(false)
const moreFunctions = ref(false)
const toast = ref('')
let toastTimer
const current = computed(() => {
  const item = allNav.find((entry) => entry.key === currentKey.value) || nav[0]
  const sectionTitles = {
    'salary/mine': '我的薪酬', 'salary/orders': '订单薪酬', 'salary/completion': '完成汇总',
    'master/styles': '款式管理', 'master/processes': '工序', 'master/customers': '客户管理',
    'tech/materials': '物料管理', 'tech/boms': 'BOM管理',
    'warehouse/lots': '轻量仓库', 'warehouse/issues': '按床领料', 'warehouse/receipts': '暂收IQC',
    'warehouse/suppliers': '供应商', 'warehouse/trace': '质量追溯',
    'settings/audit': '日志管理', 'settings/client': '客户端日志',
    'finance/receivables': '应收管理', 'finance/payables': '资金管理', 'costs/costs': '成本管理',
  }
  return { ...item, label: sectionTitles[`${currentKey.value}/${currentSection.value}`] || item.label }
})
const currentCompany = computed(() => session.value.companies?.find((c) => c.id === session.value.companyId))
function navigate(key, section = '') { currentKey.value = key; currentSection.value = section; moreOpen.value = false; moreFunctions.value = false; location.hash = `${key}${section ? `/${section}` : ''}`; window.scrollTo({ top: 0 }) }
function toggleMore() { moreOpen.value = !moreOpen.value; if (moreOpen.value) moreFunctions.value = false }
function showToast(message) { toast.value = message; clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.value = '' }, 2800) }
async function switchCompany() {
  switching.value = true
  try {
    await api(`/companies/${session.value.companyId}/switch`, { method: 'POST', body: {} })
    session.value = saveSession({ companyId: Number(session.value.companyId) })
    unread.value = 0
    showToast(`已切换至 ${currentCompany.value?.name || '新工厂'}`)
  } catch (e) { showToast(e.message); session.value = getSession() } finally { switching.value = false }
}
function logout() { clearSession(); session.value = {}; authenticated.value = false; navigate('dashboard') }
function expired() { authenticated.value = false; session.value = {}; showToast('登录已失效，请重新登录') }
async function refreshNotifications() {
  if (!authenticated.value) return
  try { const data = await api('/notifications', { params: { is_read: 0, page_size: 1 } }); unread.value = data.count || 0 } catch { /* 导航不阻断业务页面 */ }
}
function syncHash() { const { key, section } = hashParts(); if (allNav.some((item) => item.key === key)) { currentKey.value = key; currentSection.value = section } }
onMounted(() => { window.addEventListener('web-auth-expired', expired); window.addEventListener('hashchange', syncHash); refreshNotifications() })
onBeforeUnmount(() => { window.removeEventListener('web-auth-expired', expired); window.removeEventListener('hashchange', syncHash) })
</script>
<template>
  <LoginView v-if="!authenticated" @logged-in="authenticated = true; session = getSession(); refreshNotifications()" />
  <div v-else class="app-shell">
    <aside class="sidebar">
      <button class="brand" aria-label="返回工作台" @click="navigate('dashboard')"><span class="brand-mark">衣</span><span>衣计<small>服装生产管理</small></span></button>
      <nav class="side-nav" aria-label="主导航"><section v-for="group in visibleGroups" :key="group.label" class="nav-group"><p>{{ group.label }}</p><button v-for="item in group.items" :key="item.key" :class="{ active: currentKey === item.key }" @click="navigate(item.key)"><AppIcon :name="item.icon" /><span>{{ item.label }}</span><b v-if="item.key === 'notifications' && unread">{{ unread > 99 ? '99+' : unread }}</b></button></section></nav>
      <div class="sidebar-foot"><span class="status-dot" />Web 工作台<span class="version">v0.1</span></div>
    </aside>
    <div class="main-column">
      <header class="topbar">
        <div><p class="topbar-company"><AppIcon name="building" :size="16" />{{ currentCompany?.name || '我的工厂' }}</p><h1>{{ current.label }}</h1></div>
        <div class="topbar-actions">
          <label class="company-switch"><span class="sr-only">切换工厂</span><select v-model.number="session.companyId" :disabled="switching" @change="switchCompany"><option v-for="company in session.companies" :key="company.id" :value="company.id">{{ company.name }}</option></select></label>
          <button class="icon-button notification-button" aria-label="查看通知" @click="navigate('notifications'); refreshNotifications()"><AppIcon name="bell" /><i v-if="unread" /></button>
          <div class="user-chip"><span>{{ session.user?.real_name?.slice(0, 1) || '员' }}</span><div><strong>{{ session.user?.real_name || '员工' }}</strong><small>{{ currentCompany?.post?.name || '工厂成员' }}</small></div></div>
          <button class="icon-button" title="退出登录" aria-label="退出登录" @click="logout"><AppIcon name="logout" /></button>
        </div>
      </header>
      <main class="content"><component :is="current.component" :key="`${session.companyId}-${currentKey}-${currentSection}`" :section="currentSection" @navigate="navigate" @toast="showToast" @refresh-unread="refreshNotifications" /></main>
    </div>
    <nav class="bottom-nav" aria-label="手机导航">
      <button :class="{ active: currentKey === 'dashboard' }" @click="navigate('dashboard')"><AppIcon name="grid" /><span>首页</span></button>
      <button :class="{ active: currentKey === 'scan' }" @click="navigate('scan')"><AppIcon name="scan" /><span>扫菲</span></button>
      <button class="ai-tab" :class="{ active: currentKey === 'ai' }" aria-label="AI问报" @click="navigate('ai')"><span class="ai-fab">AI</span><span class="ai-tab-label">AI问报</span></button>
      <button class="mobile-notice-tab" :class="{ active: currentKey === 'notifications' }" @click="navigate('notifications'); refreshNotifications()"><AppIcon name="bell" /><span>通知</span><b v-if="unread">{{ unread > 99 ? '99+' : unread }}</b></button>
      <button :class="{ active: moreOpen }" aria-label="我的" @click="toggleMore"><AppIcon name="user" /><span>我的</span></button>
    </nav>
    <div v-if="moreOpen" class="mobile-more-backdrop" @click.self="moreOpen=false">
      <section class="mobile-more" aria-label="我的与全部功能">
        <div class="card-header"><h3>{{ moreFunctions ? '全部功能' : '我的' }}</h3><button class="icon-button" aria-label="关闭我的" @click="moreOpen=false"><AppIcon name="close" /></button></div>
        <div class="mobile-profile-head">
          <span class="mobile-profile-avatar">{{ session.user?.real_name?.slice(0, 1) || '员' }}</span>
          <div><strong>{{ session.user?.real_name || '员工' }}<span class="badge">{{ currentCompany?.post?.name || '工厂成员' }}</span></strong><p>{{ session.user?.phone || '' }}</p><small>{{ currentCompany?.name || '我的工厂' }}</small></div>
        </div>
        <label class="field mobile-profile-company"><span>当前工厂</span><select v-model.number="session.companyId" :disabled="switching" @change="switchCompany"><option v-for="company in session.companies" :key="company.id" :value="company.id">{{ company.name }}</option></select></label>
        <div v-if="!moreFunctions" class="mobile-profile-menu">
          <button @click="navigate('profile')"><span>个人资料<small>头像、姓名、生日与修改密码</small></span><AppIcon name="chevron" /></button>
          <button @click="moreFunctions=true"><span>全部功能<small>订单、生产、薪资、仓库与系统设置</small></span><AppIcon name="chevron" /></button>
          <button @click="navigate('help')"><span>使用帮助<small>角色操作指引与常见问题</small></span><AppIcon name="chevron" /></button>
          <button class="mobile-profile-danger" @click="logout"><span>退出登录</span><AppIcon name="logout" /></button>
        </div>
        <template v-else>
          <button class="mobile-profile-back" @click="moreFunctions=false"><AppIcon name="chevron" :size="14" />返回个人中心</button>
          <DashboardView class="mobile-all-functions" @navigate="navigate" />
        </template>
        <p class="mobile-profile-foot">衣计 · 计件生产管理<br />Web 工作台 v0.1</p>
      </section>
    </div>
    <Transition name="toast"><div v-if="toast" class="toast" role="status">{{ toast }}</div></Transition>
  </div>
</template>
