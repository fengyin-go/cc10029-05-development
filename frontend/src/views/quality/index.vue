<template>
  <section class="page" data-module="quality">
    <header class="page-head">
      <div><h2>质量报告</h2><p class="page-desc">定义采集质量统计的指标口径和阈值，生成质量报告。</p></div>
    </header>
    <div class="analysis-summary">
      <article v-for="m in summaryMetrics" :key="m.label" class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">{{ m.label }}</span>
        <strong style="font-size:22px;display:block">{{ m.value }}</strong>
        <span :class="m.trend > 0 ? 'trend-up' : 'trend-down'" style="font-size:12px">{{ m.trend > 0 ? '↑' : '↓' }}{{ Math.abs(m.trend) }}%</span>
      </article>
    </div>
    <form class="analysis-form" @submit.prevent="runAnalysis">
      <label v-for="field in paramFields" :key="field"><span>{{ field }}</span>
        <select v-if="field.includes('方法')||field.includes('策略')||field.includes('粒度')" v-model="params[field]"><option v-for="o in options[field]" :key="o" :value="o">{{ o }}</option></select>
        <input v-else v-model="params[field]" :placeholder="`请输入${field}`" />
      </label>
      <button class="btn primary" type="submit" data-testid="run-analysis">运行分析</button>
    </form>
    <div v-if="chartData.length" class="analysis-chart">
      <div v-for="(bar, i) in chartData" :key="i" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }"><span>{{ bar.label }}</span></div>
    </div>
    <table class="data-table" v-if="results.length">
      <thead><tr><th v-for="c in resultColumns" :key="c">{{ c }}</th></tr></thead>
      <tbody><tr v-for="row in results" :key="row.id"><td v-for="c in resultColumns" :key="c">{{ row[c] ?? '—' }}</td></tr></tbody>
    </table>
    <p v-if="!results.length && !loading" style="text-align:center;color:var(--muted)">请配置参数后运行分析</p>

    <!-- 质量指标数据集：读取校验异常数据处置包 -->
    <div class="dataset-wrap" data-testid="dataset-panel">
      <header class="page-head">
        <div>
          <h3>质量指标数据集</h3>
          <p class="page-desc">读取校验异常数据处置包生成，异常样本数量与阻断级别必须与处置包明细一致，已替换处置包不进入下游清单。</p>
        </div>
        <div class="page-actions">
          <button class="btn primary" type="button" data-testid="sync-dataset" @click="syncDatasets">同步处置包结论</button>
        </div>
      </header>
      <p v-if="syncMessage" :class="syncOk ? '' : 'error-text'" data-testid="sync-message">{{ syncMessage }}</p>
      <table class="data-table" data-testid="dataset-table">
        <thead><tr><th>数据集编号</th><th>校验批次</th><th>来源处置编号</th><th>规则版本</th><th>样本数量</th><th>异常样本数量</th><th>异常证据数</th><th>阻断级别统计</th><th>阻断率</th><th>达标率</th></tr></thead>
        <tbody>
          <tr v-for="d in datasets" :key="d['数据集编号']" :data-testid="`dataset-row-${d['校验批次']}`">
            <td>{{ d['数据集编号'] }}</td><td>{{ d['校验批次'] }}</td><td>{{ d['来源处置编号'] }}</td><td>{{ d['规则版本'] }}</td>
            <td data-testid="dataset-sample-total">{{ d['样本数量'] }}</td>
            <td data-testid="dataset-abnormal">{{ d['异常样本数量'] }}</td>
            <td>{{ d['异常证据数'] }}</td>
            <td data-testid="dataset-levels">{{ formatStats(d['阻断级别统计']) }}</td>
            <td>{{ (d['阻断率'] * 100).toFixed(1) }}%</td><td>{{ (d['达标率'] * 100).toFixed(1) }}%</td>
          </tr>
          <tr v-if="!datasets.length"><td colspan="10" class="empty-state">暂无生效处置包，先到采集校验生成校验异常数据处置包</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
const ENDPOINT = '/api/quality'
const summaryMetrics = ref<{label:string;value:number;trend:number}[]>([])
const results = ref<any[]>([]); const chartData = ref<{label:string;value:number}[]>([])
const loading = ref(false)
const params = ref<Record<string,string>>({})
const paramFields = ["指标编号", "指标名称", "统计口径", "阈值下界", "阈值上界", "统计周期", "样本数量", "达标率"].filter((f: string) => !['编号','名称','版本'].includes(f)).slice(0, 4)
const options: Record<string,string[]> = { '统计粒度': ['按日','按周','按月'], '分组字段': ['全量','按类型','按区域'], '比较方法': ['环比','同比','绝对值'], '时间范围': ['最近7天','最近30天','最近90天'] }
const resultColumns = computed(() => ["指标编号", "指标名称", "统计口径", "阈值下界", "阈值上界", "统计周期", "样本数量", "达标率"].slice(0, 5))
function barHeight(v: number) { const max = Math.max(...chartData.value.map(d => d.value), 1); return Math.max(10, (v / max) * 140) }
async function fetchSummary() { try { const res = await request(`${ENDPOINT}/summary`); summaryMetrics.value = (await res.json()).metrics || [] } catch(e) {} }
async function runAnalysis() {
  loading.value = true
  try { const res = await request(`${ENDPOINT}/run`, { method: 'POST', body: JSON.stringify({ values: params.value }) }); const data = await res.json(); results.value = data.items || []; chartData.value = (data.items || []).map((r: any) => ({ label: Object.values(r)[0] as string, value: Object.values(r).filter(v => typeof v === 'number')[0] as number || 0 })).slice(0, 6) } catch(e) { results.value = [] }
  loading.value = false
}

// ---- 质量指标数据集 ----
type DatasetRow = Record<string, any>
const datasets = ref<DatasetRow[]>([])
const syncMessage = ref('')
const syncOk = ref(true)
function formatStats(stats: Record<string, number> | undefined): string {
  if (!stats) return '—'
  return Object.entries(stats).map(([k, v]) => `${k} ${v}`).join(' / ')
}
async function reloadDatasets() {
  const res = await request(`${ENDPOINT}/datasets`)
  datasets.value = (await res.json()).items || []
}
async function syncDatasets() {
  syncMessage.value = ''
  const res = await request(`${ENDPOINT}/datasets/sync`, { method: 'POST', body: JSON.stringify({}) })
  const data = await res.json()
  syncOk.value = !!data.ok
  syncMessage.value = data.message
  await reloadDatasets()
}

onMounted(async () => {
  await fetchSummary()
  await reloadDatasets()
})
</script>
<style scoped>
.dataset-wrap { margin-top: 24px; border-top: 2px solid var(--border); padding-top: 12px; }
</style>
