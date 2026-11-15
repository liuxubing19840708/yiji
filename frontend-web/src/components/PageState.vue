<script setup>
defineProps({ loading: Boolean, error: String, empty: Boolean, emptyText: { type: String, default: '暂无数据' } })
defineEmits(['retry'])
</script>
<template>
  <div v-if="loading" class="page-state" role="status"><span class="spinner" />正在读取业务数据…</div>
  <div v-else-if="error" class="page-state error-state" role="alert"><span>{{ error }}</span><button class="button secondary" @click="$emit('retry')">重新加载</button></div>
  <div v-else-if="empty" class="page-state"><span class="empty-symbol">⌁</span><p>{{ emptyText }}</p></div>
  <slot v-else />
</template>
