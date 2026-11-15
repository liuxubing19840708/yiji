<script setup>
defineProps({
  page: { type: Number, default: 1 },
  count: { type: Number, default: 0 },
  pageSize: { type: Number, default: 20 },
  disabled: Boolean,
})
defineEmits(['change'])
</script>

<template>
  <div class="pagination">
    <button class="button ghost small" type="button" :disabled="disabled || page <= 1" @click="$emit('change', page - 1)">上一页</button>
    <span>第 {{ page }} 页 · 共 {{ count || 0 }} 条</span>
    <button class="button ghost small" type="button" :disabled="disabled || page * pageSize >= (count || 0)" @click="$emit('change', page + 1)">下一页</button>
  </div>
</template>
