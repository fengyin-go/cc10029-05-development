<template>
  <section class="page" data-module="validate">
    <header class="page-head">
      <div><h2>采集校验</h2><p class="page-desc">校验采集结果的行数、格式、空值和重复，对异常设定阻断或告警。</p></div>
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

    <!-- 校验异常数据处置包 -->
    <div class="disposal-wrap" data-testid="disposal-panel">
      <header class="page-head">
        <div>
          <h3>校验异常数据处置包</h3>
          <p class="page-desc">按校验规则计算空值、格式、重复异常的处置结果，处置包与规则版本同时保存，按校验批次追踪。</p>
        </div>
        <div class="page-actions">
          <button class="btn" type="button" data-testid="audit-disposal" @click="auditPackages">巡检处置包</button>
          <button class="btn" type="button" data-testid="export-disposal" @click="exportPackages">导出处置明细</button>
        </div>
      </header>

      <div class="stat-row">
        <article class="stat-card"><span class="stat-label">处置包总数</span><strong class="stat-value" data-testid="stat-package-total">{{ packages.length }}</strong></article>
        <article class="stat-card"><span class="stat-label">生效处置包</span><strong class="stat-value" data-testid="stat-package-active">{{ activePackages.length }}</strong></article>
        <article class="stat-card"><span class="stat-label">待处置异常证据</span><strong class="stat-value" data-testid="stat-evidence-pending">{{ pendingEvidence }}</strong></article>
        <article class="stat-card"><span class="stat-label">缺陷条目</span><strong class="stat-value" data-testid="stat-defect">{{ defects.length }}</strong></article>
      </div>

      <form class="filter-bar" @submit.prevent="reloadDisposal">
        <label class="filter-item"><span>校验批次</span>
          <select v-model="selectedBatch" data-testid="batch-select" @change="onBatchChange">
            <option value="">全部批次</option>
            <option v-for="b in batches" :key="b" :value="b">{{ b }}</option>
          </select>
        </label>
        <button class="btn" type="submit" data-testid="disposal-query">查询</button>
      </form>

      <form class="modal" style="width:100%;max-width:none;margin-bottom:12px" @submit.prevent="submitPackage">
        <h3 style="margin-top:0">生成 / 追加处置包</h3>
        <div class="filter-bar" style="margin-bottom:8px">
          <label class="filter-item"><span>校验批次</span><input v-model="form.batch" data-testid="form-batch" required placeholder="如 BATCH-20261117-01" /></label>
          <label class="filter-item"><span>规则版本</span><input v-model="form.version" data-testid="form-version" required placeholder="如 RV-2026.11" /></label>
        </div>
        <label class="create-field"><span>校验规则（JSON，缺少规则版本的规则不参与计算）</span>
          <textarea v-model="form.rules" data-testid="form-rules" rows="5" class="json-input"></textarea>
        </label>
        <label class="create-field"><span>待校验样本（JSON，无规则覆盖的样本不进入待处置范围）</span>
          <textarea v-model="form.samples" data-testid="form-samples" rows="5" class="json-input"></textarea>
        </label>
        <div class="page-actions">
          <button class="btn primary" type="submit" data-testid="submit-disposal" :disabled="submitting">{{ submitting ? '提交中…' : '提交处置包' }}</button>
          <span v-if="disposalMessage" :class="disposalOk ? '' : 'error-text'" data-testid="disposal-message">{{ disposalMessage }}</span>
        </div>
      </form>

      <table class="data-table" data-testid="package-table">
        <thead><tr><th>处置编号</th><th>校验批次</th><th>规则版本</th><th>包状态</th><th>异常样本数</th><th>异常证据数</th><th>提交次数</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="p in packages" :key="p['处置编号']" :data-testid="`package-row-${p['处置编号']}`" :data-status="p['包状态']">
            <td>{{ p['处置编号'] }}</td><td>{{ p['校验批次'] }}</td><td>{{ p['规则版本'] }}</td>
            <td><span :class="p['包状态'] === '生效' ? 'tag tag-active' : 'tag tag-old'">{{ p['包状态'] }}</span></td>
            <td>{{ p['异常样本数'] }}</td><td>{{ p['异常证据数'] }}</td><td>{{ p['提交次数'] }}</td><td>{{ p['创建时间'] }}</td>
            <td><button class="link" type="button" data-testid="view-package" @click="viewPackage(p)">查看明细</button></td>
          </tr>
          <tr v-if="!packages.length"><td colspan="9" class="empty-state">当前批次暂无处置包</td></tr>
        </tbody>
      </table>

      <div v-if="currentPackage" data-testid="package-detail">
        <h4 style="margin:14px 0 6px">处置包明细 · {{ currentPackage['处置编号'] }}（{{ currentPackage['规则版本'] }}）</h4>
        <p class="page-desc">阻断级别：{{ formatStats(currentPackage['阻断级别统计']) }} ｜ 异常类型：{{ formatStats(currentPackage['异常类型统计']) }}</p>
        <table class="data-table">
          <thead><tr><th>证据编号</th><th>样本编号</th><th>失败字段</th><th>异常类型</th><th>阻断级别</th><th>规则编号</th><th>规则版本</th><th>处置状态</th><th>证据次数</th></tr></thead>
          <tbody>
            <tr v-for="d in currentPackage['明细']" :key="d['证据编号']" :data-testid="`evidence-row-${d['证据编号']}`">
              <td>{{ d['证据编号'] }}</td><td>{{ d['样本编号'] }}</td><td>{{ d['失败字段'] }}</td><td>{{ d['异常类型'] }}</td>
              <td><span :class="levelClass(d['阻断级别'])">{{ d['阻断级别'] }}</span></td>
              <td>{{ d['规则编号'] }}</td><td>{{ d['规则版本'] }}</td><td>{{ d['处置状态'] }}</td>
              <td data-testid="evidence-count">{{ d['证据列表'].length }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h4 style="margin:14px 0 6px">缺陷清单（处置包报错、残留、不生效、丢失、错位与阻断冲突）</h4>
      <table class="data-table" data-testid="defect-table">
        <thead><tr><th>缺陷编号</th><th>缺陷类型</th><th>错误码</th><th>校验批次</th><th>处置编号</th><th>缺陷说明</th><th>状态</th><th>登记时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="d in defects" :key="d['缺陷编号']" :data-testid="`defect-row-${d['缺陷编号']}`">
            <td>{{ d['缺陷编号'] }}</td><td>{{ d['缺陷类型'] }}</td><td>{{ d['错误码'] }}</td><td>{{ d['校验批次'] || '—' }}</td>
            <td>{{ d['处置编号'] || '—' }}</td><td>{{ d['缺陷说明'] }}</td><td>{{ d['状态'] }}</td><td>{{ d['登记时间'] }}</td>
            <td><button v-if="d['状态'] === '待处理'" class="link" type="button" @click="resolveDefect(d['id'])">标记修复</button><span v-else>—</span></td>
          </tr>
          <tr v-if="!defects.length"><td colspan="9" class="empty-state">暂无缺陷记录</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
const ENDPOINT = '/api/validate'
const summaryMetrics = ref<{label:string;value:number;trend:number}[]>([])
const results = ref<any[]>([]); const chartData = ref<{label:string;value:number}[]>([])
const loading = ref(false)
const params = ref<Record<string,string>>({})
const paramFields = ["规则编号", "校验字段", "期望格式", "空值阈值", "重复判定", "异常策略", "阻断级别", "校验批次"].filter((f: string) => !['编号','名称','版本'].includes(f)).slice(0, 4)
const options: Record<string,string[]> = { '统计粒度': ['按日','按周','按月'], '分组字段': ['全量','按类型','按区域'], '比较方法': ['环比','同比','绝对值'], '时间范围': ['最近7天','最近30天','最近90天'] }
const resultColumns = computed(() => ["规则编号", "校验字段", "期望格式", "空值阈值", "重复判定", "异常策略", "阻断级别", "校验批次"].slice(0, 5))
function barHeight(v: number) { const max = Math.max(...chartData.value.map(d => d.value), 1); return Math.max(10, (v / max) * 140) }
async function fetchSummary() { try { const res = await request(`${ENDPOINT}/summary`); summaryMetrics.value = (await res.json()).metrics || [] } catch(e) {} }
async function runAnalysis() {
  loading.value = true
  try { const res = await request(`${ENDPOINT}/run`, { method: 'POST', body: JSON.stringify({ values: params.value }) }); const data = await res.json(); results.value = data.items || []; chartData.value = (data.items || []).map((r: any) => ({ label: Object.values(r)[0] as string, value: Object.values(r).filter(v => typeof v === 'number')[0] as number || 0 })).slice(0, 6) } catch(e) { results.value = [] }
  loading.value = false
}

// ---- 校验异常数据处置包 ----
type PackageRow = Record<string, any>
type DefectRow = Record<string, any>
const batches = ref<string[]>([])
const selectedBatch = ref('')
const packages = ref<PackageRow[]>([])
const defects = ref<DefectRow[]>([])
const currentPackage = ref<PackageRow | null>(null)
const submitting = ref(false)
const disposalMessage = ref('')
const disposalOk = ref(true)
const demoRules = JSON.stringify([
  { 规则编号: 'VALI-0001', 校验字段: '商品标题', 期望格式: '文本', 空值阈值: '启用', 重复判定: '启用', 阻断级别: '阻断', 规则版本: 'RV-2026.11' },
  { 规则编号: 'VALI-0002', 校验字段: '价格', 期望格式: '金额', 空值阈值: '启用', 重复判定: '不校验', 阻断级别: '阻断', 规则版本: 'RV-2026.11' },
  { 规则编号: 'VALI-0003', 校验字段: '商品链接', 期望格式: 'URL', 空值阈值: '启用', 重复判定: '不校验', 阻断级别: '告警', 规则版本: 'RV-2026.11' },
], null, 2)
const demoSamples = JSON.stringify([
  { 样本编号: 'S2001', 字段: { 商品标题: '机械键盘', 价格: '199.00', 商品链接: 'https://demo.example.com/p/2001' } },
  { 样本编号: 'S2002', 字段: { 商品标题: '', 价格: 'abc', 商品链接: 'https://demo.example.com/p/2002' } },
  { 样本编号: 'S2003', 字段: { 商品标题: '机械键盘', 价格: '59.90', 商品链接: 'bad-url' } },
], null, 2)
const form = ref<{ batch: string; version: string; rules: string; samples: string }>({
  batch: 'BATCH-20261117-01', version: 'RV-2026.11', rules: demoRules, samples: demoSamples,
})

const activePackages = computed(() => packages.value.filter((p) => p['包状态'] === '生效'))
const pendingEvidence = computed(() => packages.value.reduce(
  (sum, p) => sum + (p['明细'] || []).filter((d: any) => d['处置状态'] === '待处置').length, 0))

function formatStats(stats: Record<string, number> | undefined): string {
  if (!stats) return '—'
  return Object.entries(stats).map(([k, v]) => `${k} ${v}`).join(' / ')
}
function levelClass(level: string): string {
  if (level === '阻断') return 'tag tag-block'
  if (level === '告警') return 'tag tag-warn'
  return 'tag tag-info'
}

async function fetchBatches() {
  const res = await request(`${ENDPOINT}/disposal/batches`)
  batches.value = (await res.json()).items || []
}
async function reloadDisposal() {
  const query = selectedBatch.value ? `?batch=${encodeURIComponent(selectedBatch.value)}` : ''
  const [pkgRes, defectRes] = await Promise.all([
    request(`${ENDPOINT}/disposal/packages${query}`),
    request(`${ENDPOINT}/disposal/defects${query}`),
  ])
  packages.value = (await pkgRes.json()).items || []
  defects.value = (await defectRes.json()).items || []
  if (currentPackage.value) {
    const refreshed = packages.value.find((p) => p['处置编号'] === currentPackage.value!['处置编号'])
    currentPackage.value = refreshed ?? null
  }
}
function onBatchChange() {
  currentPackage.value = null
  void reloadDisposal()
}
function viewPackage(p: PackageRow) {
  currentPackage.value = p
}
async function submitPackage() {
  submitting.value = true
  disposalMessage.value = ''
  try {
    let rules: unknown = []
    let samples: unknown = []
    try {
      rules = JSON.parse(form.value.rules)
      samples = JSON.parse(form.value.samples)
    } catch {
      throw new Error('规则或样本不是合法 JSON')
    }
    const res = await request(`${ENDPOINT}/disposal/packages`, {
      method: 'POST',
      body: JSON.stringify({ 校验批次: form.value.batch, 规则版本: form.value.version, 规则: rules, 样本: samples }),
    })
    const data = await res.json()
    disposalOk.value = !!data.ok
    if (!data.ok) throw new Error(data.message || '处置包生成失败')
    disposalMessage.value = data.appended
      ? `${data.message}：处置编号 ${data.entry['处置编号']}（重复校验仅追加证据）`
      : `${data.message}：处置编号 ${data.entry['处置编号']}`
    selectedBatch.value = form.value.batch
    await fetchBatches()
    await reloadDisposal()
    currentPackage.value = packages.value.find((p) => p['处置编号'] === data.entry['处置编号']) ?? null
  } catch (error) {
    disposalOk.value = false
    disposalMessage.value = error instanceof Error ? error.message : '处置包提交失败'
  } finally {
    submitting.value = false
  }
}
async function auditPackages() {
  const res = await request(`${ENDPOINT}/disposal/audit`, { method: 'POST' })
  const data = await res.json()
  disposalOk.value = true
  disposalMessage.value = data.message
  await reloadDisposal()
}
async function resolveDefect(id: number) {
  await request(`${ENDPOINT}/disposal/defects/${id}/resolve`, { method: 'POST', body: JSON.stringify({}) })
  await reloadDisposal()
}
async function exportPackages() {
  const query = selectedBatch.value ? `?batch=${encodeURIComponent(selectedBatch.value)}` : ''
  const response = await request(`${ENDPOINT}/disposal/export${query}`)
  if (!response.ok) throw new Error('导出失败')
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = 'validate-disposal.csv'
  link.click()
  URL.revokeObjectURL(url)
}

onMounted(async () => {
  await fetchSummary()
  await fetchBatches()
  await reloadDisposal()
})
</script>
<style scoped>
.disposal-wrap { margin-top: 24px; border-top: 2px solid var(--border); padding-top: 12px; }
.json-input { width: 100%; font-family: monospace; font-size: 12px; border: 1px solid var(--border); border-radius: 6px; padding: 8px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.tag-active { background: #dcfce7; color: #15803d; }
.tag-old { background: #f1f5f9; color: var(--muted); }
.tag-block { background: #fee2e2; color: #b91c1c; }
.tag-warn { background: #fef3c7; color: #b45309; }
.tag-info { background: #e0f2fe; color: #0369a1; }
.filter-item select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
</style>
