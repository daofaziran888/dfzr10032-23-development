<template>
  <section class="page" data-module="timing_event">
    <header class="page-head">
      <div>
        <h2>计时事件</h2>
        <p class="page-desc">
          消费采样时钟漂移明细，按受影响的来源和采样区间生成可核对的计时事件，并注明这段结果是否允许按修正时间继续使用。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="generateAll">重新生成计时事件</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>来源编号</span>
        <select v-model="filters.source_code">
          <option value="">全部来源</option>
          <option v-for="source in sources" :key="source['来源编号']" :value="source['来源编号']">
            {{ source['来源名称'] }}（{{ source['来源编号'] }}）
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>类型</span>
        <select v-model="filters.event_type">
          <option value="">全部类型</option>
          <option v-for="kind in types" :key="kind" :value="kind">{{ kind }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>修正时间可用性</span>
        <select v-model="filters.usability">
          <option value="">全部</option>
          <option value="允许">允许</option>
          <option value="仅参考">仅参考</option>
          <option value="不允许">不允许</option>
        </select>
      </label>
      <label class="filter-item checkbox">
        <input v-model="filters.pending_only" type="checkbox" />
        <span>只看待现场核对</span>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>事件编号</th>
          <th>来源</th>
          <th>类型</th>
          <th>采样区间</th>
          <th>计时影响</th>
          <th>判定依据</th>
          <th>依据样本</th>
          <th>修正时间</th>
          <th>下一班</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row['事件编号'])" :class="{ abnormal: row['待现场核对'] }">
          <td>{{ row['事件编号'] }}</td>
          <td>{{ row['来源名称'] }}<div class="muted mono">{{ row['来源编号'] }}</div></td>
          <td><span class="tag" :class="tagClass(row['类型'])">{{ row['类型'] }}</span></td>
          <td class="mono">{{ row['采样区间'] }}</td>
          <td class="basis">{{ row['计时影响'] }}</td>
          <td class="basis">{{ row['判定依据'] }}</td>
          <td class="mono">{{ row['依据样本'] }}</td>
          <td>
            <span class="tag" :class="usableClass(row['允许继续使用'])">{{ row['可用性说明'] }}</span>
            <div v-if="row['时钟偏移秒']" class="muted">偏移 {{ row['时钟偏移秒'] }}s</div>
          </td>
          <td class="basis">
            <span v-if="row['待现场核对']" class="tag danger">待现场核对</span>
            <span v-else class="muted">无需核对</span>
            <div v-if="row['核对提示']" class="muted">{{ row['核对提示'] }}</div>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="9" class="empty-state">暂无计时事件，可点击右上角重新生成</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条计时事件</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const types = ['时钟跳变', '缓存补送', '重启交界', '无法断定', '采样缺口']
const rows = ref<Row[]>([])
const sources = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref({ source_code: '', event_type: '', usability: '', pending_only: false })

const stats = computed(() => {
  const pending = rows.value.filter((row: Row) => row['待现场核对']).length
  const allowed = rows.value.filter((row: Row) => row['允许继续使用'] === '允许').length
  const blocked = rows.value.filter((row: Row) => row['允许继续使用'] === '不允许').length
  return [
    { label: '计时事件', value: total.value },
    { label: '待现场核对', value: pending },
    { label: '允许继续使用', value: allowed },
    { label: '不允许使用', value: blocked },
  ]
})

function tagClass(kind: string) {
  if (kind === '时钟跳变') return 'warn'
  if (kind === '缓存补送') return 'info'
  if (kind === '重启交界') return 'danger'
  return 'danger'
}

function usableClass(usable: string) {
  if (usable === '允许') return 'ok'
  if (usable === '仅参考') return 'warn'
  return 'danger'
}

function resetFilters() {
  filters.value = { source_code: '', event_type: '', usability: '', pending_only: false }
  void reload()
}

async function loadSources() {
  const response = await request('/api/clock_drift/sources?size=200')
  if (!response.ok) throw new Error('来源列表读取失败')
  sources.value = (await response.json()).items ?? []
}

async function generateAll() {
  errorMessage.value = ''
  try {
    const response = await request('/api/timing_event/generate', { method: 'POST' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '生成失败')
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '生成失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.value.source_code) query.set('source_code', filters.value.source_code)
  if (filters.value.event_type) query.set('event_type', filters.value.event_type)
  if (filters.value.usability) query.set('usability', filters.value.usability)
  if (filters.value.pending_only) query.set('pending_only', 'true')
  query.set('size', '200')
  try {
    const response = await request(`/api/timing_event?${query.toString()}`)
    if (!response.ok) throw new Error('计时事件读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '计时事件读取失败'
  }
}

onMounted(async () => {
  try {
    await loadSources()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '页面数据读取失败'
  }
})
</script>

<style scoped>
.basis { max-width: 320px; font-size: 12px; color: #475569; }
.mono { font-family: Menlo, Consolas, monospace; font-size: 12px; }
.muted { color: var(--muted); font-size: 12px; }
.tag { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 12px; }
.tag.ok { background: #ecfdf3; color: #067647; }
.tag.warn { background: #fffaeb; color: #b54708; }
.tag.info { background: #eff8ff; color: #175cd3; }
.tag.danger { background: #fef3f2; color: #b42318; }
tr.abnormal td { background: #fffbfa; }
.filter-item select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.filter-item.checkbox { flex-direction: row; align-items: center; display: flex; gap: 6px; }
</style>
