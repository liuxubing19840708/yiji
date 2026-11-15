<script setup>
const props = defineProps({
  page: { type: Number, default: 1 },
  count: { type: Number, default: 0 },
  pageSize: { type: Number, default: 20 },
  disabled: Boolean,
})
const emit = defineEmits(['change'])
function go(page) {
  if (props.disabled || page < 1 || page === props.page) return
  if ((page - 1) * props.pageSize >= props.count && page > props.page) return
  emit('change', page)
}
</script>

<template>
  <div class="pagination">
    <button type="button" class="button ghost small" :disabled="disabled || page <= 1" @click="go(page - 1)">上一页</button>
    <span>第 {{ page }} 页 · 共 {{ count || 0 }} 条</span>
    <button type="button" class="button ghost small" :disabled="disabled || page * pageSize >= (count || 0)" @click="go(page + 1)">下一页</button>
  </div>
</template>
