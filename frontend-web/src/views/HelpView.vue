<script setup>
import { computed, ref } from 'vue'
import { getSession } from '../api'

const props = defineProps({ initialRole: { type: String, default: '' } })
const session = getSession()
const company = session.companies?.find((item) => item.id === session.companyId)
const articles = [
  {
    key: 'boss', tab: '老板 / 管理员', title: '订单、生产与工资管理',
    sections: [
      { title: '常用流程', lines: [
        '创建订单：在订单管理中新建订单，选择款式、客户和交期，录入颜色、尺码与数量，并按需配置工序工价。',
        '准备生产：先在基础档案维护款式、工序和工价，再进入生产管理开床编菲，生成并打印菲票后下发车间。',
        '跟进进度：在订单详情查看颜色尺码完成度，在生产看板按日期查看床次与工序进展；订单详情可查看变更留痕。',
        '核算工资：在薪酬核算中选择期间，核对员工完成数量、计件与固定工资，再申请或发起结算；结算单可查看薪资条并导出 XLSX。',
        '维护团队：在员工管理中审核加入申请、设置岗位与权限。岗位权限决定各账号可见和可执行的功能。',
      ] },
      { title: '常见问题', lines: [
        '订单数量与登记量为什么不同？订单数量是各色码订货件数，登记量可能包含多道工序工作量，不能直接当成成衣件数。',
        '历史工资会被改价影响吗？结算使用登记时的单价快照；核查争议时可按菲票、工序和审计记录逐笔追踪。',
        '如何撤销误操作？未结算登记可按权限撤销重登；已结算数据需先按结算流程处理，不能直接删除历史记录。',
      ] },
    ],
  },
  {
    key: 'cutter', tab: '裁床', title: '开床、编菲与裁床协同',
    sections: [
      { title: '常用流程', lines: [
        '开床：进入生产管理新建床次，可关联订单并带出款式信息，确认颜色、尺码、数量后编菲生成扎号。',
        '打印与补打：在打印管理中打印菲票；床次详情可按实际业务权限补扎或重打，避免同一扎号重复流转。',
        '领料追溯：在仓库按床次和批次办理领料，保留面料批次与生产床次之间的关联。',
        '查看任务：在生产看板检查已裁数量、登记数量和床次状态，按日期范围核对裁床任务。',
      ] },
      { title: '常见问题', lines: [
        '一床多色如何统计？菲票按颜色、尺码生成，已裁与登记按对应菲票分别汇总。',
        '菲票信息有误怎么办？先停止错误菲票继续流转，再由有权限的人员按床次业务规则更正、作废或重新生成。',
        '已裁数量与扫菲登记量有何区别？已裁用于开床余量，登记量是工序完成工作量，两者不可混用。',
      ] },
    ],
  },
  {
    key: 'sewer', tab: '车位 / 员工', title: '扫菲登记与个人工资',
    sections: [
      { title: '常用流程', lines: [
        '扫菲登记：进入扫菲，扫描菲票二维码，选择本人实际完成的工序并填写件数，确认后提交。',
        '无法扫码：使用手输码，按床号和扎号查找菲票，核对款式、颜色、尺码后再登记。',
        '检查记录：在我的登记核对当天工序与件数；发现错误时按页面提供的撤销条件及时处理。',
        '查看工资：在我的薪酬选择日期范围，按日期、床次、款式或工序查看完成数量和工资金额。',
      ] },
      { title: '常见问题', lines: [
        '为什么无法登记零工价工序？需要先联系管理员在款式或订单工序中配置工价，再重新提交。',
        '登错了怎么办？本人当日记录可按权限撤销重登；跨日或已结算记录应联系管理员按业务流程处理。',
        '协助登记会影响工资归属吗？协助扫菲会记录实际操作人，工资归属以登记所选员工为准。',
      ] },
    ],
  },
]
const inferredRole = company?.post?.is_admin ? 'boss' : 'sewer'
const current = ref(articles.some((item) => item.key === props.initialRole) ? props.initialRole : inferredRole)
const article = computed(() => articles.find((item) => item.key === current.value) || articles[0])
</script>

<template>
  <div class="help-view">
    <div class="welcome"><div><h2>使用帮助</h2><p>{{ company?.name || '当前公司' }} · 按岗位查看操作说明与常见流程。</p></div></div>
    <div class="segmented help-tabs" aria-label="帮助角色">
      <button v-for="item in articles" :key="item.key" type="button" :class="{ active: current === item.key }" @click="current = item.key">{{ item.tab }}</button>
    </div>
    <section class="card help-card">
      <header><p class="eyebrow">OPERATION GUIDE</p><h3>{{ article.title }}</h3><p>以下入口是否可用以当前账号的岗位权限为准。</p></header>
      <div v-for="section in article.sections" :key="section.title" class="help-section">
        <h4>{{ section.title }}</h4>
        <ol><li v-for="line in section.lines" :key="line">{{ line }}</li></ol>
      </div>
    </section>
    <p class="help-foot">仍有疑问，请联系贵厂管理员确认岗位权限和具体业务数据。</p>
  </div>
</template>

<style scoped>
.help-view { max-width: 900px; }
.help-tabs { display: inline-flex; margin-bottom: 16px; }
.help-tabs button { min-width: 120px; }
.help-card { padding: 24px; }
.help-card header { padding-bottom: 20px; border-bottom: 1px solid var(--line); }
.help-card header h3 { margin: 5px 0 9px; font-size: 21px; }
.help-card header > p:last-child { margin: 0; color: var(--muted); }
.help-section { padding-top: 22px; }
.help-section h4 { margin: 0 0 12px; font-size: 16px; }
.help-section ol { margin: 0; padding-left: 22px; }
.help-section li { padding: 6px 0 6px 4px; color: var(--text-secondary); line-height: 1.8; overflow-wrap: anywhere; }
.help-foot { margin: 16px 3px; color: var(--muted); font-size: 12px; }
@media (max-width: 760px) {
  .help-tabs { display: flex; width: 100%; }
  .help-tabs button { flex: 1; min-width: 0; padding-inline: 5px; }
  .help-card { padding: 17px; }
}
</style>
