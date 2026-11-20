<template>
  <section class="page" data-module="handover">
    <header class="page-head">
      <div>
        <h2>交班记录</h2>
        <p class="page-desc">
          用计时事件划定可交付的连续观测区间，已确认的相邻时段不重复计入，仍待现场核对的区间连同依据样本交给下一班。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">生成交班记录</button>
        <button class="btn" type="button" @click="loadPreview">刷新区间预览</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="panel">
      <div class="panel-head">
        <h3>区间预览</h3>
        <span class="muted">按当前计时事件划分；已计入 / 已移交的历史时段不会再重复交付。</span>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>来源</th>
            <th>区间类型</th>
            <th>采样区间</th>
            <th>样本数</th>
            <th>依据样本</th>
            <th>依据事件</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in preview" :key="`${row['来源编号']}-${index}`" :class="{ abnormal: row['区间类型'] === '待核对' }">
            <td>{{ row['来源名称'] }}<div class="muted mono">{{ row['来源编号'] }}</div></td>
            <td>
              <span class="tag" :class="kindClass(row)">{{ rowLabel(row) }}</span>
            </td>
            <td class="mono">{{ row['采样区间'] }}</td>
            <td>{{ row['样本数'] }}</td>
            <td class="mono">{{ row['依据样本'] }}</td>
            <td class="mono">{{ row['依据事件'] || '—' }}</td>
            <td class="basis">{{ row['区间说明'] }}</td>
          </tr>
          <tr v-if="!preview.length">
            <td colspan="7" class="empty-state">暂无区间，请先在漂移明细页计算并生成计时事件</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>交班记录</h3>
        <span class="muted">共 {{ recordTotal }} 条</span>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>交班编号</th>
            <th>班次</th>
            <th>涉及来源</th>
            <th>可交付</th>
            <th>仅参考</th>
            <th>待核对</th>
            <th>摘要</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in records" :key="String(row['交班编号'])" :class="{ abnormal: row['待核对区间数'] > 0 }">
            <td>{{ row['交班编号'] }}</td>
            <td>{{ row['班次'] }}<div class="muted">接班：{{ row['接班班次'] }}</div></td>
            <td class="mono">{{ (row['涉及来源'] || []).join('、') }}</td>
            <td>{{ row['可交付区间数'] }} 段 / {{ minutes(row['可交付时长秒']) }} 分</td>
            <td>{{ row['仅参考区间数'] || 0 }} 段</td>
            <td>{{ row['待核对区间数'] }} 段</td>
            <td class="basis">{{ row['交班摘要'] }}</td>
            <td><button class="link" type="button" @click="openRecord(row)">查看 / 核对</button></td>
          </tr>
          <tr v-if="!records.length">
            <td colspan="8" class="empty-state">暂无交班记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <Teleport to="body">
      <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>生成交班记录</h3>
            <button class="btn ghost" type="button" @click="showCreate = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label class="modal-field">
              <span>班次</span>
              <select v-model="createForm['班次']">
                <option v-for="shift in shifts" :key="shift" :value="shift">{{ shift }}</option>
              </select>
            </label>
            <label class="modal-field">
              <span>来源编号（留空表示全部来源）</span>
              <input v-model="createForm['来源编号']" placeholder="例如 SRC-PRESS-B" />
            </label>
            <p class="muted">
              已确认时段不会重复计入；待核对区间会连同依据样本写入下一班核对清单。
            </p>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreate = false">取消</button>
              <button class="btn primary" type="submit">生成</button>
            </footer>
          </form>
        </div>
      </div>

      <div v-if="activeRecord" class="modal-overlay" @click.self="activeRecord = null">
        <div class="modal-card wide">
          <header class="modal-head">
            <h3>{{ activeRecord['交班编号'] }} · {{ activeRecord['班次'] }}</h3>
            <button class="btn ghost" type="button" @click="activeRecord = null">&times;</button>
          </header>
          <div class="modal-body">
            <p class="muted">{{ activeRecord['交接说明'] }}</p>
            <h4>待现场核对</h4>
            <div v-for="item in activeRecord['待核对清单']" :key="item['区间编号']" class="interval-card">
              <div class="interval-head">
                <strong>{{ item['来源编号'] }} · {{ item['采样区间'] }}</strong>
                <span class="mono muted">依据 {{ item['依据样本'] }}</span>
              </div>
              <p>{{ item['说明'] }}</p>
              <p v-if="item['依据事件']" class="muted">依据事件：{{ item['依据事件'] }}</p>
              <div class="resolve-row">
                <select v-model="resolveForm[item['区间编号']].结论">
                  <option value="确认可交付">确认可交付</option>
                  <option value="确认不可用">确认不可用</option>
                </select>
                <input v-model="resolveForm[item['区间编号']].核对人" placeholder="核对人" />
                <input v-model="resolveForm[item['区间编号']].核对说明" placeholder="核对说明" />
                <button class="btn" type="button" @click="resolve(item)">回填结论</button>
              </div>
            </div>
            <p v-if="!activeRecord['待核对清单'].length" class="muted">本次没有待核对区间。</p>

            <h4>可交付</h4>
            <ul class="plain-list">
              <li v-for="item in activeRecord['可交付清单']" :key="item['区间编号']">
                <span class="mono">{{ item['来源编号'] }} {{ item['采样区间'] }}</span>
                <span class="muted">依据 {{ item['依据样本'] }}</span>
              </li>
            </ul>
            <template v-if="activeRecord['仅参考清单'] && activeRecord['仅参考清单'].length">
              <h4>仅参考</h4>
              <ul class="plain-list">
                <li v-for="item in activeRecord['仅参考清单']" :key="item['区间编号']">
                  <span class="mono">{{ item['来源编号'] }} {{ item['采样区间'] }}</span>
                  <span class="muted">依据 {{ item['依据样本'] }}</span>
                </li>
              </ul>
            </template>
          </div>
        </div>
      </div>
    </Teleport>

    <footer class="page-foot">
      <span>区间预览 {{ preview.length }} 段 · 交班记录 {{ recordTotal }} 条</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const preview = ref<Row[]>([])
const records = ref<Row[]>([])
const recordTotal = ref(0)
const shifts = ref<string[]>(['白班 08:00-20:00', '夜班 20:00-08:00'])
const errorMessage = ref('')
const showCreate = ref(false)
const activeRecord = ref<Row | null>(null)
const createForm = ref<Record<string, string>>({ 班次: '白班 08:00-20:00', 来源编号: '' })
const resolveForm = reactive<Record<string, { 结论: string; 核对人: string; 核对说明: string }>>({})

const stats = ref([
  { label: '交班记录', value: 0 },
  { label: '可交付区间', value: 0 },
  { label: '待核对区间', value: 0 },
  { label: '区间预览', value: 0 },
])

function minutes(seconds: number | undefined) {
  return Math.round(Number(seconds ?? 0) / 60)
}

function kindClass(row: Row) {
  if (row['已计入'] || row['已移交']) return 'info'
  if (row['区间类型'] === '可交付') return 'ok'
  if (row['区间类型'] === '仅参考') return 'warn'
  return 'danger'
}

function rowLabel(row: Row) {
  if (row['已计入']) return '已计入'
  if (row['已移交']) return '已移交·仅参考'
  return row['区间类型']
}

function openCreate() {
  errorMessage.value = ''
  createForm.value = { 班次: shifts.value[0], 来源编号: '' }
  showCreate.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const values: Record<string, string> = { 班次: createForm.value['班次'] }
    if (createForm.value['来源编号']) values['来源编号'] = createForm.value['来源编号'].trim()
    const response = await request('/api/handover/records', {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message || '生成失败')
    showCreate.value = false
    await loadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '生成失败'
  }
}

async function loadPreview() {
  const response = await request('/api/handover/preview')
  if (!response.ok) throw new Error('区间预览读取失败')
  const payload = await response.json()
  preview.value = payload['区间'] ?? []
}

async function loadRecords() {
  const response = await request('/api/handover/records?size=100')
  if (!response.ok) throw new Error('交班记录读取失败')
  const payload = await response.json()
  records.value = payload.items ?? []
  recordTotal.value = payload.total ?? records.value.length
}

async function loadSummary() {
  const response = await request('/api/handover/summary')
  if (!response.ok) return
  const payload = await response.json()
  shifts.value = payload['可选班次'] ?? shifts.value
  stats.value = [
    { label: '交班记录', value: payload['交班记录数'] ?? 0 },
    { label: '可交付区间', value: payload['可交付区间'] ?? 0 },
    { label: '待核对区间', value: payload['待核对区间'] ?? 0 },
    { label: '区间预览', value: preview.value.length },
  ]
}

async function loadAll() {
  errorMessage.value = ''
  try {
    await loadPreview()
    await loadRecords()
    await loadSummary()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '页面数据读取失败'
  }
}

async function openRecord(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`/api/handover/records/${row.id}`)
    if (!response.ok) throw new Error('交班记录读取失败')
    const payload = await response.json()
    for (const item of payload['待核对清单'] ?? []) {
      resolveForm[item['区间编号']] = { 结论: '确认可交付', 核对人: '', 核对说明: '' }
    }
    activeRecord.value = payload
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交班记录读取失败'
  }
}

async function resolve(item: Row) {
  errorMessage.value = ''
  try {
    const intervalId = item['id']
    const response = await request(`/api/handover/intervals/${intervalId}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ values: resolveForm[item['区间编号']] ?? {} }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '回填失败')
    await loadAll()
    if (activeRecord.value) {
      const refreshed = await request(`/api/handover/records/${activeRecord.value.id}`)
      if (refreshed.ok) activeRecord.value = await refreshed.json()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回填失败'
  }
}

onMounted(async () => {
  await loadAll()
})
</script>

<style scoped>
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; overflow-x: auto; }
.panel h3 { margin: 0 0 8px; font-size: 14px; }
.panel-head { display: flex; justify-content: space-between; align-items: baseline; }
.basis { max-width: 360px; font-size: 12px; color: #475569; }
.mono { font-family: Menlo, Consolas, monospace; font-size: 12px; }
.muted { color: var(--muted); font-size: 12px; }
.tag { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 12px; }
.tag.ok { background: #ecfdf3; color: #067647; }
.tag.warn { background: #fffaeb; color: #b54708; }
.tag.info { background: #eff8ff; color: #175cd3; }
.tag.danger { background: #fef3f2; color: #b42318; }
tr.abnormal td { background: #fffbfa; }
.modal-card.wide { width: 820px; }
.modal-field select { padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.interval-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin-bottom: 8px; }
.interval-card p { font-size: 12px; color: #475569; margin: 4px 0; }
.interval-head { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.resolve-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
.resolve-row select, .resolve-row input { border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; font-size: 12px; }
.plain-list { margin: 4px 0 12px; padding-left: 18px; font-size: 12px; display: flex; flex-direction: column; gap: 4px; }
h4 { margin: 14px 0 4px; font-size: 13px; }
</style>
