<script setup>
import AppIcon from './AppIcon.vue'

defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  label: { type: String, default: '' },
  wide: Boolean,
})
defineEmits(['close'])
</script>

<template>
  <Teleport to="body">
    <div class="modal-backdrop" @click.self="$emit('close')">
      <section class="modal" :class="{ 'commercial-modal-wide': wide }" role="dialog" aria-modal="true" :aria-label="label || title">
        <div class="modal-header">
          <div><h2>{{ title }}</h2><p v-if="subtitle">{{ subtitle }}</p></div>
          <button class="icon-button" type="button" aria-label="关闭" @click="$emit('close')"><AppIcon name="close" /></button>
        </div>
        <slot />
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.commercial-modal-wide{width:min(1040px,100%)}
</style>
