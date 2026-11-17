import { expect, test, type APIRequestContext } from '@playwright/test'

/**
 * 校验异常数据处置包全链路验收：
 * 批次追踪、规则版本绑定、重复校验仅追加证据、并发/连续提交唯一生效版本、
 * 新旧规则冲突替换、质量指标数据集口径一致、缺陷清单登记。
 */

const VALIDATE = '/api/validate'
const QUALITY = '/api/quality'
// 每次跑测使用独立批次前缀，后端为内存仓库，避免与历史运行的数据相互干扰。
const RUN_TAG = `${Date.now()}`

type Rule = Record<string, string>
type Sample = Record<string, unknown>

function rules(version: string, level = '阻断'): Rule[] {
  return [
    { 规则编号: 'R1', 校验字段: 'name', 期望格式: '文本', 空值阈值: '启用', 重复判定: '启用', 阻断级别: level, 规则版本: version },
    { 规则编号: 'R2', 校验字段: 'email', 期望格式: '邮箱', 空值阈值: '启用', 重复判定: '不校验', 阻断级别: '告警', 规则版本: version },
  ]
}

const baseSamples: Sample[] = [
  { 样本编号: 'A1', 字段: { name: 'alpha', email: 'a@x.com' } },
  { 样本编号: 'A2', 字段: { name: '', email: 'bad-mail' } },
  { 样本编号: 'A3', 字段: { name: 'alpha', email: 'c@x.com' } },
]

async function api(request: APIRequestContext, method: string, path: string, body?: unknown) {
  const options = body === undefined
    ? undefined
    : { headers: { 'Content-Type': 'application/json' }, data: JSON.stringify(body) }
  const res = method === 'GET'
    ? await request.get(path)
    : await request.post(path, options ?? {})
  const json = await res.json()
  return { status: res.status(), json }
}

test('采集校验：选择校验批次查看处置包、异常样本、失败字段、阻断级别与规则版本', async ({ page }) => {
  await page.goto('/validate')
  await page.getByTestId('disposal-panel').waitFor()
  await expect(page.getByTestId('batch-select')).toContainText('BATCH-DEMO-01')

  // 操作人员选择校验批次后再核对明细
  await page.getByTestId('batch-select').selectOption('BATCH-DEMO-01')
  const packageRows = page.getByTestId(/^package-row-/)
  await expect(packageRows).toHaveCount(1)
  const row = packageRows.first()
  await expect(row).toContainText('BATCH-DEMO-01')
  await expect(row).toContainText('RV-DEMO-1')
  await expect(row).toContainText('生效')

  await page.getByTestId('view-package').first().click()
  const detail = page.getByTestId('package-detail')
  await expect(detail).toBeVisible()
  const evidenceHeaders = await detail.locator('thead th').allInnerTexts()
  for (const column of ['样本编号', '失败字段', '异常类型', '阻断级别', '规则版本']) {
    expect(evidenceHeaders).toContain(column)
  }
  const evidenceRows = detail.locator('tbody tr')
  expect(await evidenceRows.count()).toBeGreaterThan(0)
  // 明细必须带规则版本与阻断级别
  await expect(evidenceRows.first()).toContainText('RV-DEMO-1')
  await expect(evidenceRows.first()).toContainText(/阻断|告警|提示/)
})

test('采集校验：提交处置包并核对处置编号，重复校验只追加证据不复制整包', async ({ page, request }) => {
  const batch = `BATCH-E2E-APPEND-${RUN_TAG}`
  const first = await api(request, 'POST', `${VALIDATE}/disposal/packages`, {
    校验批次: batch, 规则版本: 'v1', 规则: rules('v1'), 样本: baseSamples,
  })
  expect(first.json.ok).toBe(true)
  const firstNo = first.json.entry['处置编号']

  await page.goto('/validate')
  await page.getByTestId('disposal-panel').waitFor()
  await page.getByTestId('form-batch').fill(batch)
  await page.getByTestId('form-version').fill('v1')
  await page.getByTestId('form-rules').fill(JSON.stringify(rules('v1')))
  await page.getByTestId('form-samples').fill(JSON.stringify([
    { 样本编号: 'A2', 字段: { name: '', email: 'still-bad' } },
    { 样本编号: 'A4', 字段: { name: '', email: 'd@x.com' } },
  ]))
  await page.getByTestId('submit-disposal').click()
  const message = page.getByTestId('disposal-message')
  await expect(message).toContainText('沿用原处置编号')
  await expect(message).toContainText(firstNo)

  const row = page.getByTestId(`package-row-${firstNo}`)
  await expect(row).toContainText('2') // 提交次数
  const packages = await api(request, 'GET', `${VALIDATE}/disposal/packages?batch=${batch}`)
  const active = packages.json.items.filter((p: any) => p['包状态'] === '生效')
  expect(active).toHaveLength(1)
  expect(active[0]['处置编号']).toBe(firstNo)
  const a2Null = active[0]['明细'].find((d: any) => d['样本编号'] === 'A2' && d['异常类型'] === '空值')
  expect(a2Null['证据列表']).toHaveLength(2)
  expect(a2Null['证据编号']).toMatch(/^EVD-/)
})

test('采集校验：并发与连续提交只保留一个成功版本，质量报告读取该版本', async ({ page, request }) => {
  const batch = `BATCH-E2E-CONCURRENT-${RUN_TAG}`
  const payload = { 校验批次: batch, 规则版本: 'v1', 规则: rules('v1'), 样本: baseSamples }
  const [r1, r2] = await Promise.all([
    api(request, 'POST', `${VALIDATE}/disposal/packages`, payload),
    api(request, 'POST', `${VALIDATE}/disposal/packages`, payload),
  ])
  expect(r1.json.ok && r2.json.ok).toBe(true)
  // 连续再提交一次
  const r3 = await api(request, 'POST', `${VALIDATE}/disposal/packages`, payload)
  expect(r3.json.ok).toBe(true)

  const packages = await api(request, 'GET', `${VALIDATE}/disposal/packages?batch=${batch}`)
  const active = packages.json.items.filter((p: any) => p['包状态'] === '生效')
  expect(active).toHaveLength(1)
  const noSet = new Set([r1.json.entry['处置编号'], r2.json.entry['处置编号'], r3.json.entry['处置编号']])
  expect(noSet.size).toBe(1)
  expect(active[0]['提交次数']).toBe(3)

  const dataset = await api(request, 'GET', `${QUALITY}/datasets/${batch}`)
  expect(dataset.json['来源处置编号']).toBe(active[0]['处置编号'])
  expect(dataset.json['异常样本数量']).toBe(active[0]['异常样本数'])
  expect(dataset.json['阻断级别统计']).toEqual(active[0]['阻断级别统计'])

  await page.goto('/quality')
  const row = page.getByTestId(`dataset-row-${batch}`)
  await expect(row).toBeVisible()
  await expect(row.getByTestId('dataset-abnormal')).toHaveText(String(active[0]['异常样本数']))
  await expect(row.getByTestId('dataset-levels')).toContainText('阻断')
})

test('采集校验：缺少规则依据的样本不进入待处置范围并登记缺陷', async ({ page, request }) => {
  const batch = `BATCH-E2E-NO-RULE-${RUN_TAG}`
  const result = await api(request, 'POST', `${VALIDATE}/disposal/packages`, {
    校验批次: batch,
    规则版本: 'v1',
    规则: rules('v1'),
    样本: [
      { 样本编号: 'A1', 字段: { name: 'alpha', email: 'a@x.com' } },
      { 样本编号: 'Z9', 字段: { unknown_field: 'x' } },
    ],
  })
  expect(result.json.ok).toBe(true)
  const sampleNos = result.json.entry['明细'].map((d: any) => d['样本编号'])
  expect(sampleNos).not.toContain('Z9')

  const defects = await api(request, 'GET', `${VALIDATE}/disposal/defects?batch=${batch}`)
  expect(defects.json.items.some((d: any) => d['缺陷类型'] === '规则依据缺失')).toBe(true)

  await page.goto('/validate')
  await page.getByTestId('batch-select').selectOption(batch)
  const defectRow = page.getByTestId(/^defect-row-/)
  await expect(defectRow.first()).toContainText('规则依据缺失')
})

test('采集校验：新旧规则阻断结论冲突时旧包替换，下游只读一份绑定版本的完整包', async ({ page, request }) => {
  const batch = `BATCH-E2E-CONFLICT-${RUN_TAG}`
  const v1 = await api(request, 'POST', `${VALIDATE}/disposal/packages`, {
    校验批次: batch, 规则版本: 'v1', 规则: rules('v1', '阻断'), 样本: baseSamples,
  })
  const oldNo = v1.json.entry['处置编号']
  const v2 = await api(request, 'POST', `${VALIDATE}/disposal/packages`, {
    校验批次: batch, 规则版本: 'v2', 规则: rules('v2', '告警'), 样本: baseSamples,
  })
  expect(v2.json.ok).toBe(true)
  const newNo = v2.json.entry['处置编号']
  expect(newNo).not.toBe(oldNo)

  const packages = await api(request, 'GET', `${VALIDATE}/disposal/packages?batch=${batch}`)
  const active = packages.json.items.filter((p: any) => p['包状态'] === '生效')
  expect(active).toHaveLength(1)
  expect(active[0]['处置编号']).toBe(newNo)
  const old = packages.json.items.find((p: any) => p['处置编号'] === oldNo)
  expect(old['包状态']).toBe('已替换')

  const defects = await api(request, 'GET', `${VALIDATE}/disposal/defects?batch=${batch}`)
  expect(defects.json.items.some((d: any) => d['缺陷类型'] === '阻断结论冲突')).toBe(true)

  const dataset = await api(request, 'GET', `${QUALITY}/datasets/${batch}`)
  expect(dataset.json['规则版本']).toBe('v2')
  const allDatasets = await api(request, 'GET', `${QUALITY}/datasets`)
  const batchDatasets = allDatasets.json.items.filter((d: any) => d['校验批次'] === batch)
  expect(batchDatasets).toHaveLength(1)

  await page.goto('/validate')
  await page.getByTestId('batch-select').selectOption(batch)
  await expect(page.getByTestId(`package-row-${oldNo}`)).toContainText('已替换')
  await expect(page.getByTestId(`package-row-${newNo}`)).toContainText('生效')
})

test('质量报告：同步处置包结论并生成口径一致的质量指标数据集', async ({ page, request }) => {
  const batch = `BATCH-E2E-QUALITY-${RUN_TAG}`
  const created = await api(request, 'POST', `${VALIDATE}/disposal/packages`, {
    校验批次: batch, 规则版本: 'v1', 规则: rules('v1'), 样本: baseSamples,
  })
  await page.goto('/quality')
  await page.getByTestId('dataset-panel').waitFor()
  const row = page.getByTestId(`dataset-row-${batch}`)
  await expect(row).toBeVisible()
  await expect(row.getByTestId('dataset-sample-total')).toHaveText(String(created.json.entry['样本总数']))
  await expect(row.getByTestId('dataset-abnormal')).toHaveText(String(created.json.entry['异常样本数']))

  await page.getByTestId('sync-dataset').click()
  await expect(page.getByTestId('sync-message')).toContainText('口径与明细一致')
})

test('采集校验：处置包丢失读取报错并进入缺陷清单', async ({ page, request }) => {
  const result = await api(request, 'GET', `${VALIDATE}/disposal/packages/DSP-NOT-EXIST-E2E`)
  expect(result.status).toBe(404)
  const defects = await api(request, 'GET', `${VALIDATE}/disposal/defects`)
  expect(defects.json.items.some((d: any) => d['错误码'] === 'E4041')).toBe(true)

  await page.goto('/validate')
  await page.getByTestId('disposal-panel').waitFor()
  const defectTable = page.getByTestId('defect-table')
  await expect(defectTable).toContainText('处置包丢失')
})
