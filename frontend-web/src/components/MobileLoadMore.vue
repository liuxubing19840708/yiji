<script setup>
defineProps({
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  hasMore: { type: Boolean, default: false },
  count: { type: Number, default: 0 },
})
const emit = defineEmits(['more'])
</script>

<template>
  <div v-if="count || error" class="mobile-load-more" aria-live="polite">
    <p v-if="error" class="form-error">{{ error }} <button class="button ghost small" @click="emit('more')">重试</button></p>
    <button v-else-if="hasMore" class="button ghost" :disabled="loading" @click="emit('more')">{{ loading ? '加载中…' : '加载更多' }}</button>
    <span v-else>没有更多了</span>
  </div>
</template>

<style scoped>
.mobile-load-more{display:none}
@media(max-width:760px){.mobile-load-more{display:block;padding:4px 0 16px;text-align:center;color:var(--muted);font-size:12px}.mobile-load-more>.button{width:100%}.mobile-load-more .form-error{margin:0}.mobile-load-more .form-error .button{margin-left:6px}}
</style>
