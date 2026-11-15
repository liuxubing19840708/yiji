<script setup>
const props = defineProps({ name: { type: String, default: 'grid' }, size: { type: Number, default: 20 } })
const paths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  order: '<path d="M6 3h12v18H6z"/><path d="M9 8h6M9 12h6M9 16h3"/>',
  factory: '<path d="M3 21V9l6 3V8l6 4V4h6v17z"/><path d="M7 17h2m4 0h2m4 0h2"/>',
  scan: '<path d="M4 8V4h4m8 0h4v4M4 16v4h4m8 0h4v-4M7 12h10"/>',
  wallet: '<path d="M3 6h16a2 2 0 012 2v11H3z"/><path d="M3 6l12-3v3m1 6h5v4h-5z"/>',
  bell: '<path d="M18 9a6 6 0 00-12 0c0 7-3 7-3 8h18c0-1-3-1-3-8M10 21h4"/>',
  spark: '<path d="M12 3l1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',
  logout: '<path d="M10 4H4v16h6m5-4l4-4-4-4m4 4H9"/>',
  chevron: '<path d="M9 5l7 7-7 7"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M16 16l5 5"/>',
  close: '<path d="M5 5l14 14M19 5L5 19"/>',
  check: '<path d="M4 12l5 5L20 6"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a8 8 0 0113 2M18 17A8 8 0 015 15"/>',
  camera: '<path d="M3 7h4l2-3h6l2 3h4v13H3z"/><circle cx="12" cy="13" r="4"/>',
  building: '<path d="M4 21V4h10v17m0-11h6v11M8 8h2m-2 4h2m-2 4h2m8-3h1M2 21h20"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0116 0"/>',
}
</script>
<template><svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" v-html="paths[props.name] || paths.grid" /></template>
