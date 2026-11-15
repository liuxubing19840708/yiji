<script setup>
defineProps({
  ticket: { type: Object, default: null },
  compact: Boolean,
})
</script>

<template>
  <div class="ticket-identity" :class="{ compact }" data-testid="ticket-identity">
    <span><small>床次</small><strong>{{ ticket?.bed_no || '—' }}</strong></span>
    <span><small>款号</small><strong>{{ ticket?.style_no || '—' }}</strong></span>
    <span><small>颜色</small><strong>{{ ticket?.color || '—' }}</strong></span>
    <span><small>尺码</small><strong>{{ ticket?.size || '—' }}</strong></span>
    <span><small>菲票 / 扎号</small><strong>#{{ ticket?.id || '—' }} · {{ ticket?.bundle_no || '—' }}扎</strong></span>
  </div>
</template>

<style scoped>
.ticket-identity{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;padding:12px 14px;border:1px solid #F0DCCB;border-radius:10px;background:#FFFAF6}.ticket-identity span{min-width:0}.ticket-identity small,.ticket-identity strong{display:block}.ticket-identity small{color:var(--muted);font-size:10px}.ticket-identity strong{margin-top:4px;font-size:12px;overflow-wrap:anywhere}.ticket-identity.compact{padding:8px 10px;gap:6px}.ticket-identity.compact small{display:inline;margin-right:5px}.ticket-identity.compact strong{display:inline;font-size:11px}
@media(max-width:760px){.ticket-identity{grid-template-columns:repeat(2,minmax(0,1fr));padding:10px;gap:9px}.ticket-identity>span:last-child{grid-column:1/-1}.ticket-identity.compact{grid-template-columns:1fr 1fr}.ticket-identity.compact>span:last-child{grid-column:1/-1}}
</style>
