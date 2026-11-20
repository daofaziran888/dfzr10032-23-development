<template>
  <section class="page" data-module="clock_drift">
    <header class="page-head">
      <div>
        <h2>采样时钟漂移</h2>
        <p class="page-desc">
          按来源关联原始时间、接入时点与采样间隔，计算采样时钟跳变并生成漂移明细；判定依据与依据样本逐条保留。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="recalcAll">重新计算全部来源</button>
        <button class="btn" type="button" @click="recalcSelected" :disabled="!selectedSource">
          重算 {{ selectedSource || '当前来源' }}
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="split-row">
      <aside class="panel source-panel">
        <h3>采样来源</h3>
        <button
          v-for="source in sources"
          :key="source['来源编号']"
          class="source-item"
          :class="{ active: source['来源编号'] === selectedSource }"
          type="button"
          @click="selectSource(source['来源编号'])"
        >
          <strong>{{ source['来源名称'] }}</strong>
          <span>{{ source['来源编号'] }} · 周期 {{ source['采样周期秒'] }}s</span>
          <span>容差 {{ source['时间容差秒'] }}s · 接入精度{{ source['接入时间精度'] }}</span>
        </button>
      </aside>

      <div class="panel main-panel">
        <h3>漂移明细 <small v-if="selectedSource">（{{ selectedSource }}）</small></h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>明细编号</th>
              <th>类型</th>
              <th>样本范围</th>
              <th>设备区间</th>
              <th>判定依据</th>
              <th>依据样本</th>
              <th>修正时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in details" :key="String(row['明细编号'])" :class="{ abnormal: row.pending }">
              <td>{{ row['明细编号'] }}</td>
              <td>
                <span class="tag" :class="tagClass(row['类型'])">{{ row['类型'] }}</span>
              </td>
              <td>{{ row['样本范围'] }}</td>
              <td class="mono">{{ row['设备区间'] }}</td>
              <td class="basis">{{ row['判定依据'] }}</td>
              <td class="mono">{{ row['依据样本'] }}</td>
              <td>
                <span class="tag" :class="usableClass(row['修正时间可用性'])">{{ row['可用性说明'] }}</span>
                <div v-if="row['修正后区间']" class="mono muted">{{ row['修正后区间'] }}</div>
              </td>
            </tr>
            <tr v-if="!details.length">
              <td colspan="7" class="empty-state">该来源暂无漂移明细，可点击右上角重新计算</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <section class="panel">
      <div class="panel-head">
        <h3>样本时间轴</h3>
        <span class="muted">
          原始先后按设备序号与设备时刻还原；接入顺序只用于并列判断，不直接改写时间。
        </span>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>读数编号</th>
            <th>时段</th>
            <th>设备序号</th>
            <th>设备时刻</th>
            <th>接入时点</th>
            <th>接入顺序</th>
            <th>时钟偏移</th>
            <th>修正时刻</th>
            <th>可用性</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in samples" :key="String(row['读数编号'])" :class="{ abnormal: row['修正时间可用性'] !== '允许按修正时间继续使用' }">
            <td>{{ row['读数编号'] }}</td>
            <td>{{ row['时段序号'] }}</td>
            <td>{{ row['设备序号'] }}</td>
            <td class="mono">{{ row['设备时刻'] }}</td>
            <td class="mono">{{ row['接入时点'] }}</td>
            <td>{{ row['接入顺序'] }}</td>
            <td>{{ row['时钟偏移秒'] }}s</td>
            <td class="mono">{{ row['修正时刻'] }}</td>
            <td>{{ row['修正时间可用性'] }}</td>
            <td class="basis">{{ row['说明'] || '—' }}</td>
          </tr>
          <tr v-if="!samples.length">
            <td colspan="10" class="empty-state">该来源暂无样本</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条漂移明细 · 样本 {{ samples.length }} 条</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const sources = ref<Row[]>([])
const details = ref<Row[]>([])
const samples = ref<Row[]>([])
const selectedSource = ref('')
const errorMessage = ref('')

const total = computed(() => details.value.length)
const stats = computed(() => {
  const pending = details.value.filter((row: Row) => row.pending).length
  const jumping = details.value.filter((row: Row) => row['类型'] === '时钟跳变').length
  const backfill = details.value.filter((row: Row) => row['类型'] === '缓存补送').length
  return [
    { label: '漂移明细', value: details.value.length },
    { label: '时钟跳变', value: jumping },
    { label: '缓存补送', value: backfill },
    { label: '待核对', value: pending },
  ]
})

function tagClass(kind: string) {
  if (kind === '时钟跳变') return 'warn'
  if (kind === '缓存补送') return 'info'
  return 'danger'
}

function usableClass(usable: string) {
  if (usable === '允许') return 'ok'
  if (usable === '仅参考') return 'warn'
  return 'danger'
}

async function loadSources() {
  const response = await request('/api/clock_drift/sources?size=200')
  if (!response.ok) throw new Error('来源列表读取失败')
  const payload = await response.json()
  sources.value = payload.items ?? []
  if (!selectedSource.value && sources.value.length) {
    selectedSource.value = String(sources.value[0]['来源编号'])
  }
}

async function loadSourceData() {
  if (!selectedSource.value) return
  const source = selectedSource.value
  const [detailRes, sampleRes] = await Promise.all([
    request(`/api/clock_drift/details?source_code=${encodeURIComponent(source)}&size=500`),
    request(`/api/clock_drift/samples?source_code=${encodeURIComponent(source)}&size=500`),
  ])
  if (!detailRes.ok || !sampleRes.ok) throw new Error('漂移明细读取失败')
  details.value = (await detailRes.json()).items ?? []
  samples.value = (await sampleRes.json()).items ?? []
}

async function selectSource(code: string) {
  selectedSource.value = code
  errorMessage.value = ''
  try {
    await loadSourceData()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '来源数据读取失败'
  }
}

async function recalcAll() {
  errorMessage.value = ''
  try {
    const response = await request('/api/clock_drift/analyze', { method: 'POST' })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '计算失败')
    await loadSourceData()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '计算失败'
  }
}

async function recalcSelected() {
  if (!selectedSource.value) return
  errorMessage.value = ''
  try {
    const response = await request(
      `/api/clock_drift/sources/${encodeURIComponent(selectedSource.value)}/analyze`,
      { method: 'POST' },
    )
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '计算失败')
    await loadSourceData()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '计算失败'
  }
}

onMounted(async () => {
  try {
    await loadSources()
    await loadSourceData()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '页面数据读取失败'
  }
})
</script>

<style scoped>
.split-row { display: flex; gap: 12px; align-items: flex-start; }
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.panel h3 { margin: 0 0 8px; font-size: 14px; }
.panel-head { display: flex; justify-content: space-between; align-items: baseline; }
.source-panel { width: 230px; flex: none; display: flex; flex-direction: column; gap: 6px; }
.source-item { text-align: left; border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 8px; cursor: pointer; display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--muted); }
.source-item strong { font-size: 13px; color: #1f2937; }
.source-item.active { border-color: var(--brand); background: #eef4ff; }
.main-panel { flex: 1; min-width: 0; overflow-x: auto; }
.basis { max-width: 340px; font-size: 12px; color: #475569; }
.mono { font-family: Menlo, Consolas, monospace; font-size: 12px; white-space: nowrap; }
.muted { color: var(--muted); font-size: 12px; }
.tag { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 12px; }
.tag.ok { background: #ecfdf3; color: #067647; }
.tag.warn { background: #fffaeb; color: #b54708; }
.tag.info { background: #eff8ff; color: #175cd3; }
.tag.danger { background: #fef3f2; color: #b42318; }
tr.abnormal td { background: #fffbfa; }
</style>
