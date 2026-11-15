<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api, rows } from '../api'
import { fullDateTime } from '../format'
import AppIcon from '../components/AppIcon.vue'
import PageState from '../components/PageState.vue'

const emit = defineEmits(['toast', 'navigate', 'refresh-unread'])
const PAGE_SIZE = 20
const data = ref({ count: 0 })
const items = ref([])
const loading = ref(true)
const loadingMore = ref(false)
const error = ref('')
const moreError = ref('')
const hasMore = ref(false)
const marking = ref(false)
const readingId = ref(0)
const filters = reactive({ is_read: '', page: 1 })
const filterOptions = [
  { value: '', label: '全部' },
  { value: 0, label: '未读' },
  { value: 1, label: '已读' },
]
const emptyText = computed(() => `暂无${filters.is_read === 0 ? '未读' : filters.is_read === 1 ? '已读' : ''}通知`)
let requestSerial = 0

async function load({ append = false, page = filters.page } = {}) {
  if (append && (loading.value || loadingMore.value || !hasMore.value)) return
  const serial = ++requestSerial
  if (append) {
    loadingMore.value = true
    moreError.value = ''
  } else {
    loading.value = true
    loadingMore.value = false
    error.value = ''
    moreError.value = ''
    hasMore.value = false
    items.value = []
    data.value = { count: 0 }
  }
  try {
    const result = await api('/notifications', {
      params: { ...filters, page, page_size: PAGE_SIZE },
    })
    if (serial !== requestSerial) return
    const nextRows = rows(result)
    data.value = result
    items.value = append ? items.value.concat(nextRows) : nextRows
    filters.page = page
    hasMore.value = result.next !== undefined
      ? Boolean(result.next)
      : page * PAGE_SIZE < (result.count || 0)
  } catch (e) {
    if (serial !== requestSerial) return
    if (append) moreError.value = e.message
    else error.value = e.message
  } finally {
    if (serial === requestSerial) {
      loading.value = false
      loadingMore.value = false
    }
  }
}

function reload() {
  return load({ page: 1 })
}

function loadMore() {
  return load({ append: true, page: filters.page + 1 })
}

function changeFilter(value) {
  if (filters.is_read === value) return
  filters.is_read = value
  reload()
}

function goToPage(page) {
  if (loading.value || page < 1) return
  load({ page })
}

async function read(item) {
  if (item.is_read || readingId.value) return
  readingId.value = item.id
  try {
    await api(`/notifications/${item.id}/read`, { method: 'POST', body: {} })
    item.is_read = 1
  } catch (e) {
    emit('toast', e.message)
  } finally {
    readingId.value = 0
    emit('refresh-unread')
  }
}

async function openNotification(item) {
  await read(item)
  if (item.code === 'handover_timeout') emit('navigate', 'handovers')
}

async function readAll() {
  if (marking.value) return
  marking.value = true
  try {
    await api('/notifications/read-all', { method: 'POST', body: {} })
    emit('toast', '已全部标为已读')
    await reload()
  } catch (e) {
    emit('toast', e.message)
  } finally {
    marking.value = false
    emit('refresh-unread')
  }
}

onMounted(reload)
</script>

<template>
  <div class="notifications-view">
    <div class="welcome notification-welcome">
      <div><h2>消息通知</h2><p>交期、生产与结算相关的站内消息。</p></div>
      <button class="button secondary" :disabled="marking" @click="readAll"><AppIcon name="check" :size="16" />全部已读</button>
    </div>

    <div class="filter-bar notification-filter-bar">
      <div class="segmented notification-chips" aria-label="通知已读状态">
        <button
          v-for="option in filterOptions"
          :key="option.label"
          type="button"
          :class="{ active: filters.is_read === option.value }"
          :aria-pressed="filters.is_read === option.value"
          @click="changeFilter(option.value)"
        >{{ option.label }}</button>
      </div>
      <button class="mobile-read-all" type="button" :disabled="marking" @click="readAll">{{ marking ? '处理中…' : '全部已读' }}</button>
    </div>

    <section class="card notification-list">
      <PageState :loading="loading" :error="error" :empty="!items.length" :empty-text="emptyText" @retry="reload">
        <article
          v-for="item in items"
          :key="item.id"
          class="notification-item"
          :class="{ unread: !item.is_read }"
          tabindex="0"
          role="button"
          :aria-label="`${item.title}${item.is_read ? '' : '，未读，点击标为已读'}${item.code === 'handover_timeout' ? '，前往交接中心' : ''}`"
          @click="openNotification(item)"
          @keyup.enter="openNotification(item)"
        >
          <span class="notification-dot" />
          <div class="notification-body">
            <div class="notification-title-line">
              <h3>{{ item.title }}</h3>
              <time class="mobile-notification-time">{{ fullDateTime(item.created_at) }}</time>
            </div>
            <p>{{ item.message }}</p>
            <span v-if="item.code === 'handover_timeout'" class="text-button">前往交接中心 ›</span>
            <time class="desktop-notification-time">{{ fullDateTime(item.created_at) }}</time>
          </div>
          <span v-if="!item.is_read" class="badge producing notice">未读</span>
        </article>
      </PageState>

      <div class="pagination desktop-pagination">
        <button class="button ghost small" :disabled="loading || filters.page <= 1" @click="goToPage(filters.page - 1)">上一页</button>
        <span>第 {{ filters.page }} 页 · 共 {{ data.count || 0 }} 条</span>
        <button class="button ghost small" :disabled="loading || !hasMore" @click="goToPage(filters.page + 1)">下一页</button>
      </div>

      <div v-if="items.length" class="mobile-notification-more">
        <div v-if="moreError" class="mobile-more-error" role="alert">
          <span>{{ moreError }}</span>
          <button type="button" @click="loadMore">重试</button>
        </div>
        <button v-else-if="hasMore" class="mobile-load-more" type="button" :disabled="loadingMore" @click="loadMore">
          <span v-if="loadingMore" class="spinner" />{{ loadingMore ? '加载中…' : '加载更多' }}
        </button>
        <p v-else class="mobile-no-more">没有更多了</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.mobile-read-all,
.mobile-notification-time,
.mobile-notification-more {
  display: none;
}
.notification-title-line {
  min-width: 0;
}
@media (max-width: 760px) {
  .notification-welcome {
    display: none;
  }
  .notification-filter-bar {
    flex-wrap: nowrap;
    margin-bottom: 12px;
  }
  .filter-bar > .segmented.notification-chips {
    width: auto;
    max-width: none;
    gap: 8px;
    padding: 0;
    overflow: visible;
    background: transparent;
  }
  .filter-bar > .segmented.notification-chips button {
    flex: none;
    min-height: 36px;
    padding: 8px 17px;
    border-radius: 999px;
    background: #fff;
    box-shadow: 0 2px 8px rgba(31, 35, 41, .06);
    color: var(--text-secondary);
    font-size: 13px;
  }
  .filter-bar > .segmented.notification-chips button.active {
    background: var(--orange);
    box-shadow: none;
    color: #fff;
  }
  .mobile-read-all {
    display: block;
    flex: none;
    margin-left: auto;
    padding: 8px 0 8px 8px;
    border: 0;
    background: transparent;
    color: var(--orange-dark);
    font-size: 13px;
    white-space: nowrap;
  }
  .notification-list {
    padding: 0;
    border: 0;
    background: transparent;
    box-shadow: none;
  }
  .notification-list :deep(.page-state) {
    min-height: 160px;
    border-radius: 12px;
    background: #fff;
    box-shadow: 0 2px 8px rgba(31, 35, 41, .06);
  }
  .notification-item {
    gap: 9px;
    margin-bottom: 12px;
    padding: 16px;
    border: 0;
    border-radius: 12px;
    background: #fff;
    box-shadow: 0 2px 8px rgba(31, 35, 41, .06);
  }
  .notification-item.unread,
  .notification-item:hover {
    background: #fff;
  }
  .notification-item:last-child {
    margin-bottom: 0;
  }
  .notification-dot {
    width: 7px;
    height: 7px;
    margin-top: 7px;
    background: transparent;
  }
  .notification-item.unread .notification-dot {
    background: var(--danger);
  }
  .notification-body {
    min-width: 0;
  }
  .notification-title-line {
    display: flex;
    align-items: flex-start;
    gap: 8px;
  }
  .notification-body h3 {
    flex: 1;
    min-width: 0;
    margin: 0;
    font-size: 14px;
    font-weight: 400;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  .notification-item.unread .notification-body h3 {
    font-weight: 600;
  }
  .mobile-notification-time {
    display: block;
    flex: none;
    color: var(--muted);
    font-size: 10px;
    line-height: 1.8;
    white-space: nowrap;
  }
  .notification-body p {
    margin: 8px 0 0;
    color: var(--ink);
    font-size: 13px;
    line-height: 1.65;
    overflow-wrap: anywhere;
  }
  .notification-item:not(.unread) .notification-body p {
    color: var(--text-secondary);
  }
  .desktop-notification-time,
  .notification-item .notice,
  .desktop-pagination {
    display: none;
  }
  .mobile-notification-more {
    display: block;
    padding: 4px 0 12px;
    text-align: center;
  }
  .mobile-load-more {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    min-width: 132px;
    min-height: 40px;
    padding: 8px 20px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: #fff;
    color: var(--text-secondary);
    font-size: 13px;
  }
  .mobile-no-more {
    margin: 0;
    padding: 10px 0;
    color: var(--muted);
    font-size: 12px;
  }
  .mobile-more-error {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-wrap: wrap;
    gap: 8px;
    color: var(--danger);
    font-size: 12px;
  }
  .mobile-more-error button {
    padding: 6px 12px;
    border: 1px solid var(--danger);
    border-radius: 999px;
    background: #fff;
    color: var(--danger);
  }
}
</style>
