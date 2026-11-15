<script setup>
import { onBeforeUnmount, onMounted } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
  saving: Boolean,
  wide: Boolean,
})
const emit = defineEmits(['close'])
function close() { if (!props.saving) emit('close') }
function onKeydown(event) { if (event.key === 'Escape') close() }
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div class="modal-backdrop" @click.self="close">
      <section class="modal" :style="wide ? 'width:min(820px,100%)' : ''" role="dialog" aria-modal="true" :aria-label="title">
        <div class="modal-header">
          <div><h2>{{ title }}</h2><p v-if="description">{{ description }}</p></div>
          <button type="button" class="icon-button" aria-label="关闭" :disabled="saving" @click="close"><AppIcon name="close" /></button>
        </div>
        <slot />
      </section>
    </div>
  </Teleport>
</template>
