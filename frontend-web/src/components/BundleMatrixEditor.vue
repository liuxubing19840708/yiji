<script setup>
import { computed, reactive, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { MATRIX_VIEWS, applyArrayQuantity, fillMatrixLine, matrixItems, matrixModel, positiveInteger, splitBundleQuantities } from '../order-matrix'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  colors: { type: Array, default: () => [] },
  sizes: { type: Array, default: () => [] },
  orderItems: { type: Array, default: () => [] },
  remaining: { type: Object, default: () => ({}) },
  limit: { type: Number, default: 20 },
  disabled: Boolean,
})
const emit = defineEmits(['update:modelValue'])
const state = reactive({ colors: [], sizes: [], cells: {}, multi_counts: {}, matrix_view: 'h', multi_base: 1, array_qty: 1 })
const newColor = reactive({ value: '' }), newSize = reactive({ value: '' })
const matrix = computed(() => matrixModel(state.colors, state.sizes, state.cells, state.multi_counts, state.matrix_view, state.multi_base))
const preview = computed(() => splitBundleQuantities(props.modelValue, props.limit))
const bundleCount = computed(() => preview.value.reduce((sum, row) => sum + row.full + (row.tail ? 1 : 0), 0))
watch(() => props.modelValue, (items) => {
  const cells = {}
  ;(items || []).forEach((item) => { cells[`${item.color}|${item.size}`] = Number(item.qty) || 0 })
  state.colors = [...new Set([...state.colors.filter((color) => props.colors.includes(color) && !props.orderItems.length), ...(items || []).map((item) => item.color), ...props.orderItems.map((item) => item.color)])].filter(Boolean)
  state.sizes = [...new Set([...state.sizes.filter((size) => props.sizes.includes(size) && !props.orderItems.length), ...(items || []).map((item) => item.size), ...props.orderItems.map((item) => item.size)])].filter(Boolean)
  state.cells = cells
  state.multi_counts = {}
}, { immediate: true, deep: true })
function publish() { emit('update:modelValue', matrixItems(state.colors, state.sizes, state.cells)) }
function updateCell(key, value) { if (positiveInteger(value)) state.cells[key] = Number(value); else delete state.cells[key]; delete state.multi_counts[key]; publish() }
function updateMultiCount(key, value) {
  if (positiveInteger(value)) { state.multi_counts[key] = Number(value); state.cells[key] = positiveInteger(state.multi_base) * Number(value) }
  else { delete state.multi_counts[key]; delete state.cells[key] }
  publish()
}
function switchView(view) {
  if (view === 'multi' && state.matrix_view !== 'multi') {
    const base = positiveInteger(state.multi_base), counts = {}
    if (base) Object.entries(state.cells).forEach(([key, qty]) => { if (qty % base === 0) counts[key] = qty / base })
    state.multi_counts = counts
  }
  state.matrix_view = view
}
function changeBase() { Object.entries(state.multi_counts).forEach(([key, count]) => { state.cells[key] = positiveInteger(state.multi_base) * positiveInteger(count) }); publish() }
function fillLine(axis, index) {
  const result = fillMatrixLine({ colors: state.colors, sizes: state.sizes, cells: state.cells, multiCounts: state.multi_counts, view: state.matrix_view, axis, index, multiBase: state.multi_base })
  state.cells = result.cells; state.multi_counts = result.multiCounts; publish()
}
function fillArray() { state.cells = applyArrayQuantity(state.colors, state.sizes, state.cells, state.array_qty); state.multi_counts = {}; publish() }
function addValue(kind) {
  const target = kind === 'color' ? newColor : newSize, list = kind === 'color' ? state.colors : state.sizes
  const value = target.value.trim()
  if (!value || list.includes(value)) return
  list.push(value); target.value = ''
}
function removeValue(kind, index) {
  const list = kind === 'color' ? state.colors : state.sizes, [removed] = list.splice(index, 1)
  Object.keys(state.cells).forEach((key) => { if (key.split('|')[kind === 'color' ? 0 : 1] === removed) { delete state.cells[key]; delete state.multi_counts[key] } })
  publish()
}
function excess(key, qty) { return Math.max(0, Number(qty || 0) - Number(props.remaining[key] || 0)) }
</script>

<template>
  <div class="bundle-matrix-editor">
    <div class="bundle-axis-add"><select v-if="orderItems.length" v-model="newColor.value" aria-label="添加矩阵颜色" :disabled="disabled"><option value="">选择颜色</option><option v-for="color in colors" :key="color" :value="color">{{ color }}</option></select><input v-else v-model="newColor.value" list="bundle-colors" aria-label="添加矩阵颜色" placeholder="选择或输入颜色" :disabled="disabled"><button type="button" class="button secondary small" :disabled="disabled" @click="addValue('color')">添加颜色</button><select v-if="orderItems.length" v-model="newSize.value" aria-label="添加矩阵尺码" :disabled="disabled"><option value="">选择尺码</option><option v-for="size in sizes" :key="size" :value="size">{{ size }}</option></select><input v-else v-model="newSize.value" list="bundle-sizes" aria-label="添加矩阵尺码" placeholder="选择或输入尺码" :disabled="disabled"><button type="button" class="button secondary small" :disabled="disabled" @click="addValue('size')">添加尺码</button></div>
    <datalist id="bundle-colors"><option v-for="color in colors" :key="color" :value="color" /></datalist><datalist id="bundle-sizes"><option v-for="size in sizes" :key="size" :value="size" /></datalist>
    <div class="bundle-axis-chips"><span v-for="(color,index) in state.colors" :key="`c-${color}`">{{ color }}<button type="button" :aria-label="`删除颜色 ${color}`" :disabled="disabled" @click="removeValue('color',index)"><AppIcon name="close" :size="12" /></button></span><span v-for="(size,index) in state.sizes" :key="`s-${size}`">{{ size }}<button type="button" :aria-label="`删除尺码 ${size}`" :disabled="disabled" @click="removeValue('size',index)"><AppIcon name="close" :size="12" /></button></span></div>
    <template v-if="state.colors.length && state.sizes.length">
      <div class="segmented bundle-matrix-tabs" aria-label="编菲数量矩阵视图"><button v-for="view in MATRIX_VIEWS" :key="view.key" type="button" :class="{ active: state.matrix_view === view.key }" @click="switchView(view.key)">{{ view.label }}</button></div>
      <div v-if="state.matrix_view === 'multi'" class="bundle-quick-settings"><label>倍数基数<input v-model.number="state.multi_base" type="number" min="1" step="1" aria-label="编菲倍数基数" @change="changeBase"></label><span>每格数量 = 基数 × 份数</span></div><div v-else-if="state.matrix_view === 'array'" class="bundle-quick-settings"><label>每格件数<input v-model.number="state.array_qty" type="number" min="1" step="1" aria-label="编菲每格件数"></label><button type="button" class="button secondary small" @click="fillArray">一键铺满</button></div>
      <div class="bundle-matrix-scroll"><table><thead><tr><th>{{ state.matrix_view === 'v' ? '尺码＼颜色' : '颜色＼尺码' }}</th><th v-for="(column,index) in matrix.columns" :key="column"><button type="button" :aria-label="`编菲铺满列 ${column}`" @click="fillLine('column',index)">{{ column }}</button></th><th>合计</th></tr></thead><tbody><tr v-for="(row,rowIndex) in matrix.rows" :key="row.label"><th><button type="button" :aria-label="`编菲铺满行 ${row.label}`" @click="fillLine('row',rowIndex)">{{ row.label }}</button></th><td v-for="cell in row.values" :key="cell.key" :class="{ excess: orderItems.length && cell.qty && excess(cell.key,cell.qty) }"><input v-if="state.matrix_view === 'multi'" :value="cell.count || ''" type="number" min="1" step="1" :aria-label="`${cell.key} 编菲份数`" @input="updateMultiCount(cell.key,$event.target.value)"><input v-else :value="cell.qty || ''" :readonly="state.matrix_view === 'array'" type="number" min="1" step="1" :aria-label="`${cell.key} 编菲数量`" @input="updateCell(cell.key,$event.target.value)"><small v-if="state.matrix_view === 'multi' && cell.count">{{ cell.qty }} 件</small><small v-if="orderItems.length && cell.qty">订单余量 {{ remaining[cell.key] || 0 }}<b v-if="excess(cell.key,cell.qty)"> · 超 {{ excess(cell.key,cell.qty) }}</b></small></td><td class="numeric">{{ row.total }}</td></tr></tbody></table></div>
    </template><p v-else class="editor-hint">请添加颜色与尺码，再使用横向、纵向、倍数或排列快捷录入。</p>
    <section v-if="preview.length" class="bundle-tail-preview" aria-label="尾扎预览"><div><strong>拆扎预览</strong><span>共 {{ bundleCount }} 扎</span></div><p v-for="row in preview" :key="row.key">{{ row.color }} / {{ row.size }}：{{ row.qty }} 件 → {{ row.full }} 个满扎<template v-if="row.tail"> + 尾扎 {{ row.tail }} 件</template><template v-else>，无尾扎</template></p></section>
  </div>
</template>

<style scoped>
.bundle-axis-add{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr) auto;gap:8px;margin:14px 0 8px}.bundle-axis-chips{display:flex;flex-wrap:wrap;gap:6px}.bundle-axis-chips>span{display:inline-flex;align-items:center;gap:3px;padding:4px 8px;border-radius:99px;background:var(--orange-soft);font-size:12px}.bundle-axis-chips button{display:grid;place-items:center;border:0;background:none;padding:0;min-height:0;color:inherit}.bundle-matrix-tabs{margin-top:14px}.bundle-matrix-tabs button{flex:1}.bundle-quick-settings{display:flex;align-items:center;gap:12px;margin-top:12px;padding:9px;background:#f7f8fa;border-radius:8px;font-size:12px}.bundle-quick-settings label{display:flex;align-items:center;gap:6px}.bundle-quick-settings input{width:90px;min-height:34px}.bundle-quick-settings span{color:var(--muted)}.bundle-matrix-scroll{overflow-x:auto;margin-top:12px}.bundle-matrix-scroll table{min-width:520px}.bundle-matrix-scroll th button{border:0;background:none;color:inherit;font-weight:600}.bundle-matrix-scroll td{min-width:96px;text-align:center}.bundle-matrix-scroll td input{width:78px;min-height:36px;text-align:center}.bundle-matrix-scroll td small{display:block;color:var(--muted);font-size:10px}.bundle-matrix-scroll td.excess{background:#fff1f0}.bundle-matrix-scroll td.excess b{color:var(--danger)}.bundle-tail-preview{margin-top:16px;padding:12px;border:1px solid var(--line);border-radius:9px;background:#fafbfc}.bundle-tail-preview>div{display:flex;justify-content:space-between;gap:8px}.bundle-tail-preview>div span{color:var(--orange);font-size:12px}.bundle-tail-preview p{margin:8px 0 0;color:var(--text-secondary);font-size:12px}.editor-hint{color:var(--muted);font-size:12px}@media(max-width:760px){.bundle-axis-add{grid-template-columns:minmax(0,1fr) auto}.bundle-quick-settings{align-items:flex-start;flex-wrap:wrap}}
</style>
