<template>
  <div
    class="screen-page"
    :class="{ 'is-standalone': standalone, 'is-fullscreen': isFullscreen, 'is-alarm': alertLevel !== 'normal', 'is-flash': alertFlash }"
  >
    <div class="screen-stage">
      <!-- ===== 顶部标题栏 ===== -->
      <header class="screen-header">
        <div class="header-wing left"><i /><i /><i /></div>
        <div class="header-core">
          <span class="title-mark" />
          <h1 class="header-title">智慧运维综合态势大屏</h1>
          <span class="title-mark right" />
        </div>
        <div class="header-wing right"><i /><i /><i /></div>
        <div class="header-meta">
          <span class="live-dot" />实时监测 <b>{{ clock }}</b>
        </div>
        <button class="screen-control" type="button" @click="toggleFullscreen">
          {{ isFullscreen ? '退出全屏' : '全屏展示' }}
        </button>
      </header>

      <!-- ===== 全局告警带（仅在异常时出现） ===== -->
      <div v-if="alertLevel !== 'normal'" class="alert-banner" :class="alertLevel">
        <span class="alert-badge">{{ alertLevel === 'critical' ? '严重' : '告警' }}</span>
        <div class="alert-track"><span class="alert-text">{{ alertText }}</span></div>
        <span class="alert-extra">离线 {{ offlineCount }} 台 · 告警 {{ kpis.active_anomalies ?? 0 }} 条</span>
      </div>

      <!-- ===== 顶部 KPI 条带：与主体同一视觉带，避免中间断层 ===== -->
      <section class="kpi-band">
        <div
          v-for="card in kpiCards"
          :key="card.label"
          class="kpi-card"
          :class="[card.tone, { 'is-alarm': card.alarm && Number(card.value) > 0 }]"
          :title="card.hint"
        >
          <span class="kpi-label">{{ card.label }}</span>
          <div class="kpi-value">
            <b>{{ card.shown }}</b><small>{{ card.unit }}</small>
          </div>
          <span class="kpi-foot">{{ card.foot }}</span>
        </div>
      </section>

      <!-- ===== 三栏主体 ===== -->
      <main class="screen-body">
        <!-- 左栏 -->
        <section class="screen-column">
          <ScreenPanel title="资产健康分布" subtitle="ASSET HEALTH">
            <div class="donut-wrap">
              <div ref="healthDonutRef" class="donut-chart" />
              <div class="donut-center">
                <b>{{ reachPercent }}<small>%</small></b>
                <span class="dc-label">综合健康度</span>
                <span class="dc-sub">纳管 {{ kpis.asset_total ?? 0 }} 台</span>
              </div>
              <ul class="donut-legend">
                <li :title="`在线资产 ${kpis.asset_reachable ?? 0} 台`"><i class="dot green" />在线资产<b>{{ kpis.asset_reachable ?? 0 }}</b></li>
                <li :title="`异常/离线资产 ${offlineCount} 台`"><i class="dot amber" />异常资产<b>{{ offlineCount }}</b></li>
                <li :title="`综合健康度 = 在线资产 / 纳管资产`"><i class="dot cyan" />健康度<b>{{ reachPercent }}%</b></li>
              </ul>
            </div>
          </ScreenPanel>

          <ScreenPanel title="近 7 日告警趋势" subtitle="ALERT TREND · 7D">
            <div ref="trendChartRef" class="trend-chart" />
          </ScreenPanel>

          <ScreenPanel title="运维关键指标" subtitle="OPS METRICS">
            <div class="metric-cards">
              <div v-for="card in metricCards" :key="card.label" class="metric-card" :class="card.tone" :title="card.hint">
                <span class="metric-icon">{{ card.icon }}</span>
                <div class="metric-main">
                  <b>{{ card.value }}<small>{{ card.unit }}</small></b>
                  <span>{{ card.label }}</span>
                </div>
              </div>
            </div>
          </ScreenPanel>
        </section>

        <!-- 中栏 -->
        <section class="screen-column center-column">
          <div class="center-stage">
            <div class="stage-glow" />
            <div class="core-node">
              <span class="core-avatar">AI</span>
              <strong>数字员工</strong>
              <small>DIGITAL EMPLOYEE</small>
            </div>
            <div class="stage-gauges">
              <div class="gauge">
                <div class="ring" :style="ringStyle(kpis.availability ?? 100, '#3fd2ff')">
                  <div class="ring-core"><b>{{ kpis.availability ?? 100 }}%</b></div>
                </div>
                <span>综合可达率</span>
              </div>
              <div class="stage-brief">
                <span class="brief-line"><i class="dot green" />在线 {{ kpis.asset_reachable ?? 0 }} 台</span>
                <span class="brief-line"><i class="dot amber" />离线 {{ offlineCount }} 台</span>
                <span class="brief-line"><i class="dot red" />高等级告警 {{ severityRows[0]?.count ?? 0 }} 条</span>
              </div>
              <div class="gauge">
                <div class="ring" :style="ringStyle(closeRate, '#2ee6a0')">
                  <div class="ring-core"><b>{{ closeRate }}%</b></div>
                </div>
                <span>工单闭环率</span>
              </div>
            </div>
          </div>

          <ScreenPanel title="资产状态清单" subtitle="ASSET STATUS">
            <ul class="asset-list">
              <li v-for="item in rankedAssets" :key="item.hostname" :title="item.tip">
                <i class="dot" :class="item.tone" />
                <span class="al-name">{{ item.short }}</span>
                <span class="al-meta">{{ item.app || item.group || '—' }}</span>
                <span class="al-score" :class="item.tone">{{ item.score }}</span>
              </li>
            </ul>
          </ScreenPanel>

          <ScreenPanel title="服务器实时监控" subtitle="REALTIME METRICS">
            <div class="realtime-head">
              <span class="rt-host" :title="featuredAsset?.hostname || '未接入监控'">
                <i :class="featuredOnline ? 'ok' : 'off'" />
                {{ featuredAsset ? hostLabel(featuredAsset.hostname) : '暂无可监控资产' }}
              </span>
              <span class="rt-count">{{ netdata?.online_count ?? 0 }}/{{ netdata?.configured_count ?? 0 }} 台在线</span>
              <div class="rt-tabs">
                <button
                  v-for="item in metricTabs"
                  :key="item.key"
                  type="button"
                  :class="{ active: activeMetric === item.key }"
                  @click="switchMetric(item.key)"
                >
                  {{ item.name }}
                </button>
              </div>
            </div>
            <div v-if="hasRealtime" ref="realtimeChartRef" class="realtime-chart" />
            <div v-else class="rt-offline">
              <span class="rt-offline-icon">⏻</span>
              <b>暂无实时监控数据</b>
              <small :title="realtimeReason">{{ realtimeReason }}</small>
            </div>
            <div class="realtime-foot">
              <span>CPU <b>{{ hasRealtime ? formatPercent(featuredAsset.metrics?.cpu) : '—' }}</b></span>
              <span>内存 <b>{{ hasRealtime ? formatPercent(featuredAsset.metrics?.memory) : '—' }}</b></span>
              <span>磁盘 <b>{{ hasRealtime ? formatPercent(featuredAsset.metrics?.disk) : '—' }}</b></span>
            </div>
          </ScreenPanel>
        </section>

        <!-- 右栏 -->
        <section class="screen-column">
          <ScreenPanel title="资产健康排行" subtitle="HEALTH RANKING">
            <div ref="rankChartRef" class="rank-chart" />
            <p class="panel-note">健康分 = 可达性 40 + 数据库 20 + 告警影响 20 + 监控接入 20（悬浮查看明细）</p>
          </ScreenPanel>

          <ScreenPanel title="告警等级分布" subtitle="SEVERITY LEVELS">
            <div class="sev-summary">
              <b>{{ severityTotal }}</b>
              <span>条活动告警</span>
            </div>
            <div class="sev-list">
              <div v-for="row in severityRows" :key="row.label" class="sev-row" :class="row.tone" :title="`${row.label}等级 ${row.count} 条，占比 ${row.pct}%`">
                <span class="sev-name"><i class="dot" />{{ row.label }}</span>
                <b class="sev-count">{{ row.count }}</b>
                <span class="sev-pct">{{ row.pct }}%</span>
                <div class="sev-bar"><i :style="{ width: `${row.pct}%` }" /></div>
              </div>
            </div>
          </ScreenPanel>

          <ScreenPanel title="运维能力情况" subtitle="CAPABILITIES">
            <div class="capability-grid">
              <div v-for="chip in capabilityChips" :key="chip.label" class="capability-chip" :class="chip.tone" :title="chip.hint">
                <span>{{ chip.label }}</span>
                <b>{{ chip.value }}</b>
              </div>
              <div class="ai-badge">
                <span class="ai-pulse">AI</span>
                <div>
                  <b>DevOpsAgent 在线守护</b>
                  <small>基于企业规则持续分析与闭环</small>
                </div>
              </div>
            </div>
          </ScreenPanel>
        </section>
      </main>

      <footer class="screen-footer">
        <span>DEVOPS AGENT · 智能运维数字员工</span>
        <span class="foot-legend">
          <i class="dot green" />正常
          <i class="dot cyan" />监控中
          <i class="dot amber" />异常
          <i class="dot red" />高等级告警
        </span>
        <span>数据更新时间：{{ updatedAt }} · 自动刷新 30 秒</span>
      </footer>
    </div>
  </div>
</template>

<script setup>
// 注意：生产构建的 Vue 是 runtime-only（不含模板编译器），
// 字符串 template 在线上会渲染为空，这里必须用 render 函数
import { computed, h, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { fetchDashboardNetdata, fetchDashboardOverview, fetchKnowledgeDocuments } from '../api'

// standalone=true 用于独立大屏路由（/screen，脱离应用外壳全屏展示）；
// 默认 false 用于 /dashboard（嵌在应用外壳主内容区内，负边距撑满可视区）
const props = defineProps({
  standalone: { type: Boolean, default: false },
})

const overview = ref({ kpis: {}, trend: [], assets: [], anomalies: [], severity: null })
const netdata = ref({ online_count: 0, configured_count: 0, items: [], featured: null })
const knowledgeCount = ref(0)
const clock = ref('')
const updatedAt = ref('—')
const isFullscreen = ref(false)
const activeMetric = ref('cpu')
const alertFlash = ref(false)

const healthDonutRef = ref(null)
const trendChartRef = ref(null)
const rankChartRef = ref(null)
const realtimeChartRef = ref(null)

let healthDonutChart
let trendChart
let rankChart
let realtimeChart
let refreshTimer
let clockTimer
let flashTimer

/* ===== 配色：异常=琥珀（不刺眼），高等级告警=红色（最醒目） ===== */
const PALETTE = {
  ok: '#2ee6a0',
  cyan: '#3fd2ff',
  amber: '#ffb020',
  red: '#ff4d5e',
  violet: '#b58cff',
}
const textColor = '#a8ccec'
const gridColor = 'rgba(90, 156, 220, .22)'

/* ===== 派生数据 ===== */
const kpis = computed(() => overview.value.kpis || {})
const assets = computed(() => overview.value.assets || [])
const anomalies = computed(() => overview.value.anomalies || [])
const netdataItems = computed(() => netdata.value.items || [])
const offlineCount = computed(() => Math.max((kpis.value.asset_total ?? 0) - (kpis.value.asset_reachable ?? 0), 0))
const reachPercent = computed(() => Number(kpis.value.availability ?? 100).toFixed(1))
const closeRate = computed(() => {
  const open = Number(kpis.value.open_tickets ?? 0)
  const recovered = Number(kpis.value.recovered_tickets ?? 0)
  const total = open + recovered
  return total ? Math.round((recovered / total) * 100) : 100
})
const featuredAsset = computed(() => netdata.value.featured || null)
const featuredOnline = computed(() => !!featuredAsset.value?.online)
const hasRealtime = computed(() => featuredOnline.value && (featuredAsset.value?.series?.[activeMetric.value] || []).length > 0)
const realtimeReason = computed(() => {
  if (!featuredAsset.value) return '资产未登记 IP / 未接入 Netdata Agent'
  return featuredAsset.value.error || `监控未接入：${hostLabel(featuredAsset.value.hostname)}`
})

const metricTabs = [
  { key: 'cpu', name: 'CPU', color: '#3fd2ff' },
  { key: 'memory', name: '内存', color: '#2ee6a0' },
  { key: 'disk', name: '磁盘', color: '#ffb020' },
]

/* ===== 告警等级：优先用后端全量统计，缺失时按列表兜底 ===== */
const severityRows = computed(() => {
  const raw = overview.value.severity
  let high
  let medium
  let low
  if (raw) {
    high = Number(raw.high || 0)
    medium = Number(raw.medium || 0)
    low = Number(raw.low || 0)
  } else {
    const hit = (keywords) =>
      anomalies.value.filter((item) => {
        const text = String(item.severity || '').toLowerCase()
        return keywords.some((word) => text.includes(word))
      }).length
    high = hit(['high', 'critical', '严重', '高'])
    medium = hit(['medium', 'warning', 'warn', '警告', '中'])
    low = Math.max(anomalies.value.length - high - medium, 0)
  }
  const total = high + medium + low
  const pct = (value) => (total ? Math.round((value / total) * 100) : 0)
  return [
    { label: '高', count: high, pct: pct(high), tone: 'red' },
    { label: '中', count: medium, pct: pct(medium), tone: 'amber' },
    { label: '低', count: low, pct: pct(low), tone: 'cyan' },
  ]
})
const severityTotal = computed(() => severityRows.value.reduce((sum, row) => sum + row.count, 0))

/* ===== 全局告警态 ===== */
const alertLevel = computed(() => {
  const alarms = Number(kpis.value.active_anomalies ?? 0)
  const offline = offlineCount.value
  if (alarms >= 5 || offline >= 3 || severityRows.value[0].count >= 3) return 'critical'
  if (alarms > 0 || offline > 0) return 'warning'
  return 'normal'
})
const alertText = computed(() => {
  const parts = []
  if (Number(kpis.value.active_anomalies ?? 0)) parts.push(`活动告警 ${kpis.value.active_anomalies} 条（高等级 ${severityRows.value[0].count} 条）`)
  if (offlineCount.value) parts.push(`资产离线 ${offlineCount.value} 台`)
  if (!parts.length) return ''
  return `${parts.join(' · ')} · 综合健康度 ${reachPercent.value}% · 建议优先处理高等级告警与离线资产`
})

/* ===== 资产健康分（可解释，避免所有柱子一样长） ===== */
const rankedAssets = computed(() => {
  const alarmByHost = new Map()
  for (const item of anomalies.value) {
    const host = String(item.hostname || '')
    alarmByHost.set(host, (alarmByHost.get(host) || 0) + 1)
  }
  const monitorByHost = new Map(netdataItems.value.map((item) => [String(item.hostname), item]))
  return assets.value
    .map((asset) => {
      const hostname = String(asset.hostname || '未知')
      const alarms = alarmByHost.get(hostname) || 0
      const monitor = monitorByHost.get(hostname)
      const reachableScore = asset.reachable ? 40 : 0
      const dbScore = asset.db_ok ? 20 : 0
      const alarmScore = Math.max(0, 20 - alarms * 8)
      const monitorScore = monitor?.online ? 20 : monitor?.configured ? 10 : 0
      const score = reachableScore + dbScore + alarmScore + monitorScore
      const tone = score >= 80 ? 'green' : score >= 60 ? 'cyan' : score >= 40 ? 'amber' : 'red'
      return {
        hostname,
        short: shortLabel(hostname, 12),
        app: asset.app && asset.app !== '暂无' ? asset.app : '',
        group: asset.group || '',
        score,
        tone,
        tip: `${hostname}\n健康分 ${score}：可达性 ${reachableScore}/40 · 数据库 ${dbScore}/20 · 告警影响 ${alarmScore}/20（${alarms} 条）· 监控接入 ${monitorScore}/20`,
      }
    })
    .sort((a, b) => b.score - a.score)
})

/* ===== KPI 条带（数字滚动） ===== */
const animated = reactive({ total: 0, health: 0, alarms: 0, tickets: 0 })
function tween(key, target) {
  const from = Number(animated[key] || 0)
  const to = Number(target || 0)
  if (from === to) {
    animated[key] = to
    return
  }
  const duration = 700
  const start = performance.now()
  const step = (now) => {
    const progress = Math.min(1, (now - start) / duration)
    const eased = 1 - Math.pow(1 - progress, 3)
    animated[key] = key === 'health' ? Number((from + (to - from) * eased).toFixed(1)) : Math.round(from + (to - from) * eased)
    if (progress < 1) requestAnimationFrame(step)
  }
  requestAnimationFrame(step)
}
const kpiCards = computed(() => [
  {
    label: '纳管资产',
    value: kpis.value.asset_total ?? 0,
    shown: animated.total,
    unit: '台',
    tone: 'cyan',
    foot: `在线 ${kpis.value.asset_reachable ?? 0} · 离线 ${offlineCount.value}`,
    hint: '纳管资产总数 = 母机 + 已登记子机',
  },
  {
    label: '综合健康度',
    value: Number(kpis.value.availability ?? 100),
    shown: animated.health,
    unit: '%',
    tone: Number(kpis.value.availability ?? 100) >= 90 ? 'green' : Number(kpis.value.availability ?? 100) >= 60 ? 'amber' : 'red',
    foot: `在线占比 ${kpis.value.asset_reachable ?? 0}/${kpis.value.asset_total ?? 0}`,
    hint: '综合健康度 = 在线资产 / 纳管资产',
    alarm: true,
  },
  {
    label: '活动告警',
    value: kpis.value.active_anomalies ?? 0,
    shown: animated.alarms,
    unit: '条',
    tone: 'red',
    foot: `高等级 ${severityRows.value[0].count} · 中 ${severityRows.value[1].count} · 低 ${severityRows.value[2].count}`,
    hint: '状态为 abnormal 的异常事件数',
    alarm: true,
  },
  {
    label: '进行中任务',
    value: kpis.value.open_tickets ?? 0,
    shown: animated.tickets,
    unit: '单',
    tone: 'violet',
    foot: `已闭环 ${kpis.value.recovered_tickets ?? 0} 单 · 闭环率 ${closeRate.value}%`,
    hint: '未恢复、未跳过的任务单数量',
  },
])

const metricCards = computed(() => [
  { icon: '▣', label: '纳管资产', value: kpis.value.asset_total ?? 0, unit: '台', tone: 'cyan', hint: '纳管资产总数' },
  { icon: '◉', label: '基础设施可达', value: kpis.value.asset_reachable ?? 0, unit: '台', tone: 'green', hint: '最近一次巡检可达的资产数' },
  { icon: '⚠', label: '活动告警', value: kpis.value.active_anomalies ?? 0, unit: '条', tone: 'amber', hint: '当前未恢复的异常事件' },
  { icon: '✓', label: '已恢复任务单', value: kpis.value.recovered_tickets ?? 0, unit: '单', tone: 'violet', hint: '已闭环的任务单' },
])

const capabilityChips = computed(() => [
  { label: '工单闭环率', value: `${closeRate.value}%`, tone: 'green', hint: '已恢复任务单 / (已恢复 + 进行中)' },
  { label: '已恢复任务', value: kpis.value.recovered_tickets ?? 0, tone: 'cyan', hint: '累计已闭环任务单数量' },
  { label: '知识规则', value: knowledgeCount.value || 0, tone: 'violet', hint: '知识库中的规则/文档数量' },
  { label: '活动告警', value: kpis.value.active_anomalies ?? 0, tone: 'red', hint: '当前未恢复的异常事件数' },
  { label: '进行中任务', value: kpis.value.open_tickets ?? 0, tone: 'amber', hint: '待处理的任务单数量' },
  { label: '监控接入', value: `${netdata.value.online_count ?? 0}/${netdata.value.configured_count ?? 0}`, tone: 'cyan', hint: '已接入 Netdata 并在线的资产 / 已登记监控的资产' },
])

/* ===== 工具 ===== */
// 资产表里存在 "IP:SSH端口" 这类录入（如 124.221.251.186:22），展示时剥掉端口，悬浮看全称
function hostLabel(value) {
  const text = String(value || '未接入')
  const matched = text.match(/^(\d{1,3}(?:\.\d{1,3}){3}):(\d+)$/)
  return matched ? matched[1] : text
}
function shortLabel(value, max = 12) {
  const text = String(value || '未知')
  return text.length > max ? `${text.slice(0, max)}…` : text
}
function formatPercent(value) {
  return value === null || value === undefined ? '—' : `${Number(value).toFixed(1)}%`
}
function ringStyle(percent, color) {
  const value = Math.max(0, Math.min(100, Number(percent) || 0))
  return { background: `conic-gradient(${color} ${value}%, rgba(20, 60, 110, .55) 0)` }
}

/* ===== 图表 ===== */
function renderHealthDonut() {
  if (!healthDonutRef.value) return
  healthDonutChart?.dispose()
  healthDonutChart = echarts.init(healthDonutRef.value)
  healthDonutChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}：{c} 台（{d}%）' },
    series: [{
      type: 'pie',
      radius: ['64%', '88%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: true,
      label: { show: false },
      itemStyle: { borderColor: '#041028', borderWidth: 3 },
      data: [
        { value: kpis.value.asset_reachable ?? 0, name: '在线', itemStyle: { color: PALETTE.ok } },
        { value: offlineCount.value, name: '异常', itemStyle: { color: PALETTE.amber } },
      ],
    }],
  })
}

function renderTrend() {
  if (!trendChartRef.value) return
  trendChart?.dispose()
  trendChart = echarts.init(trendChartRef.value)
  const rows = trend7d.value
  trendChart.setOption({
    grid: { left: 38, right: 16, top: 22, bottom: 28 },
    tooltip: { trigger: 'axis', formatter: (params) => `${params[0].axisValue}　新增告警 ${params[0].data} 条` },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: rows.map((row) => row.date.slice(5)),
      axisLabel: { color: textColor, fontSize: 12 },
      axisLine: { lineStyle: { color: gridColor } },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: gridColor } },
      axisLabel: { color: textColor, fontSize: 12 },
    },
    series: [{
      type: 'line',
      smooth: true,
      symbolSize: 7,
      data: rows.map((row) => row.count),
      lineStyle: { width: 2.5, color: PALETTE.red },
      itemStyle: { color: PALETTE.red, borderColor: '#ffd9dd', borderWidth: 1.5 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(255, 77, 94, .32)' },
          { offset: 1, color: 'rgba(255, 77, 94, 0)' },
        ]),
      },
    }],
  })
}

function renderRank() {
  if (!rankChartRef.value) return
  rankChart?.dispose()
  rankChart = echarts.init(rankChartRef.value)
  const rows = rankedAssets.value.slice(0, 8).reverse()
  const colorOf = (tone) => (tone === 'green' ? PALETTE.ok : tone === 'cyan' ? PALETTE.cyan : tone === 'amber' ? PALETTE.amber : PALETTE.red)
  rankChart.setOption({
    grid: { left: 96, right: 44, top: 10, bottom: 10 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'none' },
      formatter: (params) => {
        const item = rows[params[0].dataIndex]
        return item ? item.tip.replace('\n', '<br/>') : ''
      },
    },
    xAxis: { type: 'value', max: 100, show: false },
    yAxis: {
      type: 'category',
      data: rows.map((row) => row.short),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: textColor, fontSize: 12 },
    },
    series: [{
      type: 'bar',
      barWidth: 11,
      data: rows.map((row) => ({ value: row.score, itemStyle: { color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{ offset: 0, color: colorOf(row.tone) }, { offset: 1, color: 'rgba(27, 95, 208, .55)' }]) } })),
      itemStyle: { borderRadius: 6 },
      showBackground: true,
      backgroundStyle: { color: 'rgba(38, 92, 160, .16)', borderRadius: 6 },
      label: { show: true, position: 'right', color: '#e6f6ff', fontSize: 12, fontWeight: 600 },
    }],
  })
}

function renderRealtime() {
  if (!realtimeChartRef.value || !hasRealtime.value) return
  realtimeChart?.dispose()
  realtimeChart = echarts.init(realtimeChartRef.value)
  const tab = metricTabs.find((item) => item.key === activeMetric.value) || metricTabs[0]
  const series = featuredAsset.value.series?.[tab.key] || []
  realtimeChart.setOption({
    grid: { left: 40, right: 14, top: 18, bottom: 24 },
    tooltip: { trigger: 'axis', valueFormatter: (value) => `${Number(value).toFixed(1)}%` },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: series.map((point) => new Date(Number(point.t) * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })),
      axisLabel: { color: textColor, fontSize: 11 },
      axisLine: { lineStyle: { color: gridColor } },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      splitLine: { lineStyle: { color: gridColor } },
      axisLabel: { color: textColor, fontSize: 11, formatter: '{value}%' },
    },
    series: [{
      type: 'line',
      smooth: true,
      showSymbol: false,
      data: series.map((point) => point.v),
      lineStyle: { width: 2.4, color: tab.color },
      itemStyle: { color: tab.color },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: `${tab.color}55` },
          { offset: 1, color: `${tab.color}00` },
        ]),
      },
    }],
  })
}

function renderCharts() {
  renderHealthDonut()
  renderTrend()
  renderRank()
  renderRealtime()
}

function switchMetric(key) {
  activeMetric.value = key
  nextTick(renderRealtime)
}

/* ===== 趋势：前端兜底补齐 7 天（后端已补零，这里防止旧接口未更新） ===== */
const trend7d = computed(() => {
  const map = new Map((overview.value.trend || []).map((row) => [String(row.date).slice(0, 10), Number(row.count) || 0]))
  const out = []
  const today = new Date()
  for (let offset = 6; offset >= 0; offset -= 1) {
    const day = new Date(today)
    day.setDate(today.getDate() - offset)
    const key = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`
    out.push({ date: key, count: map.get(key) || 0 })
  }
  return out
})

/* ===== 数据加载 ===== */
async function load() {
  const [overviewResult, netdataResult, docsResult] = await Promise.allSettled([
    fetchDashboardOverview(),
    fetchDashboardNetdata(),
    fetchKnowledgeDocuments(),
  ])
  if (overviewResult.status === 'fulfilled') overview.value = overviewResult.value.data
  if (netdataResult.status === 'fulfilled') netdata.value = netdataResult.value.data
  if (docsResult.status === 'fulfilled') knowledgeCount.value = docsResult.value.data.total || 0
  if (overviewResult.status === 'rejected') {
    ElMessage.error(overviewResult.reason?.response?.data?.detail || '大屏数据加载失败')
  }
  updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  await nextTick()
  renderCharts()
}

function syncKpiAnimation() {
  tween('total', kpis.value.asset_total ?? 0)
  tween('health', Number(kpis.value.availability ?? 100))
  tween('alarms', kpis.value.active_anomalies ?? 0)
  tween('tickets', kpis.value.open_tickets ?? 0)
}

let prevAlarmCount = 0
watch(
  () => Number(kpis.value.active_anomalies ?? 0),
  (value) => {
    syncKpiAnimation()
    // 新增告警：全局告警带闪烁提示 8 秒
    if (value > prevAlarmCount && prevAlarmCount >= 0) {
      alertFlash.value = true
      clearTimeout(flashTimer)
      flashTimer = setTimeout(() => { alertFlash.value = false }, 8000)
    }
    prevAlarmCount = value
  },
)

function updateClock() {
  clock.value = new Date().toLocaleString('zh-CN', { hour12: false })
}

async function toggleFullscreen() {
  try {
    if (!document.fullscreenElement) await document.documentElement.requestFullscreen()
    else await document.exitFullscreen()
    isFullscreen.value = !!document.fullscreenElement
  } catch {
    ElMessage.info('当前浏览器不支持全屏')
  }
}

function resizeAll() {
  healthDonutChart?.resize()
  trendChart?.resize()
  rankChart?.resize()
  realtimeChart?.resize()
}

onMounted(() => {
  updateClock()
  clockTimer = setInterval(updateClock, 1000)
  load()
  refreshTimer = setInterval(load, 30000)
  window.addEventListener('resize', resizeAll)
})

onBeforeUnmount(() => {
  clearInterval(refreshTimer)
  clearInterval(clockTimer)
  clearTimeout(flashTimer)
  healthDonutChart?.dispose()
  trendChart?.dispose()
  rankChart?.dispose()
  realtimeChart?.dispose()
  window.removeEventListener('resize', resizeAll)
})

const ScreenPanel = {
  props: { title: String, subtitle: String },
  setup(panelProps, { slots }) {
    return () =>
      h('section', { class: 'screen-panel' }, [
        h('div', { class: 'panel-title' }, [h('span', panelProps.title), h('small', panelProps.subtitle)]),
        h('div', { class: 'panel-body' }, slots.default?.()),
      ])
  },
}
</script>

<style scoped>
/* ===== 整体舞台 ===== */
.screen-page {
  position: relative;
  overflow: auto;
  color: #d8efff;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  background:
    radial-gradient(circle at 50% 34%, rgba(16, 96, 190, .3), transparent 46%),
    linear-gradient(150deg, #020918, #04163a 46%, #020b1e);
}
/* 独立大屏路由：脱离应用外壳，铺满视口 */
.screen-page.is-standalone { position: fixed; inset: 0; }

.screen-stage {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  min-height: calc(100vh - 120px);
  min-width: 1120px;
  padding: 14px 22px 10px;
  background-image:
    linear-gradient(rgba(48, 128, 220, .05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(48, 128, 220, .05) 1px, transparent 1px);
  background-size: 34px 34px;
}
.is-standalone .screen-stage { min-height: 100%; }
/* 嵌入模式：负边距抵消外壳内边距后，顶部需给固定顶栏留出空间，避免标题被裁 */
.screen-page:not(.is-standalone) .screen-stage { padding-top: 64px; }

/* 全局告警态：整屏边缘呼吸提示 */
.screen-page.is-alarm .screen-stage {
  box-shadow: inset 0 0 0 2px rgba(255, 77, 94, .55);
  animation: edge-breathe 2.8s ease-in-out infinite;
}
@keyframes edge-breathe {
  50% { box-shadow: inset 0 0 70px rgba(255, 77, 94, .3), inset 0 0 0 2px rgba(255, 77, 94, .18); }
}

/* ===== 顶部标题 ===== */
.screen-header {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 74px;
  flex-shrink: 0;
}
.header-core {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 10px 64px;
  background: linear-gradient(180deg, rgba(18, 108, 204, .88), rgba(5, 44, 102, .72));
  clip-path: polygon(6% 0, 94% 0, 100% 100%, 0 100%);
  box-shadow: 0 0 26px rgba(24, 128, 228, .4);
}
.header-title {
  margin: 0;
  color: #eaf7ff;
  font-size: clamp(24px, 2.2vw, 36px);
  font-weight: 700;
  letter-spacing: .16em;
  text-shadow: 0 0 18px rgba(63, 210, 255, .8);
  white-space: nowrap;
}
.title-mark { width: 34px; height: 3px; background: linear-gradient(90deg, transparent, #4fe3ff); box-shadow: 0 0 10px #4fe3ff; }
.title-mark.right { background: linear-gradient(90deg, #4fe3ff, transparent); }
.header-wing { position: absolute; top: 24px; display: flex; gap: 8px; width: 30%; height: 26px; }
.header-wing.left { left: 1%; justify-content: flex-end; }
.header-wing.right { right: 1%; justify-content: flex-start; }
.header-wing i { flex: 1; height: 3px; background: linear-gradient(90deg, rgba(63, 210, 255, .9), transparent); }
.header-wing.right i { background: linear-gradient(90deg, transparent, rgba(63, 210, 255, .9)); }
.header-wing i:nth-child(2) { height: 2px; opacity: .65; }
.header-wing i:nth-child(3) { height: 1px; opacity: .35; }
.header-meta { position: absolute; right: 24px; top: 26px; z-index: 2; display: flex; align-items: center; gap: 7px; color: #8fc0e8; font-size: 14px; }
.header-meta b { margin-left: 4px; color: #d9f1ff; font-size: 16px; font-variant-numeric: tabular-nums; }
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: #38e8a3; box-shadow: 0 0 10px #38e8a3; animation: pulse 2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .45; } }
.screen-control {
  position: absolute;
  right: 24px;
  bottom: 4px;
  padding: 5px 14px;
  border: 1px solid #2b6cb0;
  border-radius: 3px;
  color: #b4ddff;
  font-size: 13px;
  background: rgba(6, 44, 92, .8);
  cursor: pointer;
  transition: all .2s;
}
.screen-control:hover { border-color: #3fd2ff; color: #eaf7ff; box-shadow: 0 0 12px rgba(63, 210, 255, .4); }

/* ===== 全局告警带 ===== */
.alert-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 40px;
  flex-shrink: 0;
  padding: 0 12px;
  border: 1px solid rgba(255, 77, 94, .65);
  background: linear-gradient(90deg, rgba(120, 12, 28, .85), rgba(60, 10, 22, .6));
}
.alert-banner.warning { border-color: rgba(255, 176, 32, .6); background: linear-gradient(90deg, rgba(104, 62, 6, .8), rgba(48, 32, 6, .55)); }
.alert-badge {
  flex-shrink: 0;
  padding: 3px 10px;
  border-radius: 2px;
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: .1em;
  background: #ff4d5e;
  box-shadow: 0 0 14px rgba(255, 77, 94, .7);
}
.alert-banner.warning .alert-badge { background: #ffb020; box-shadow: 0 0 14px rgba(255, 176, 32, .6); }
.alert-track { flex: 1; overflow: hidden; }
.alert-text {
  display: inline-block;
  padding-left: 100%;
  color: #ffe3e7;
  font-size: 15px;
  letter-spacing: .04em;
  white-space: nowrap;
  animation: marquee 26s linear infinite;
}
.alert-banner.warning .alert-text { color: #ffe8c4; }
@keyframes marquee { to { transform: translateX(-100%); } }
.alert-extra { flex-shrink: 0; color: #ffd9dd; font-size: 14px; font-variant-numeric: tabular-nums; }
.screen-page.is-flash .alert-banner { animation: alert-blink 1s steps(2, jump-none) 8; }
@keyframes alert-blink { 50% { background: rgba(255, 77, 94, .35); } }

/* ===== KPI 条带 ===== */
.kpi-band { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; flex-shrink: 0; }
.kpi-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  min-height: 92px;
  padding: 10px 16px;
  border: 1px solid rgba(56, 138, 224, .55);
  border-left: 3px solid var(--tone, #3fd2ff);
  background: linear-gradient(140deg, rgba(14, 76, 148, .62), rgba(4, 28, 66, .78));
}
.kpi-card.cyan { --tone: #3fd2ff; }
.kpi-card.green { --tone: #2ee6a0; }
.kpi-card.amber { --tone: #ffb020; }
.kpi-card.red { --tone: #ff4d5e; }
.kpi-card.violet { --tone: #b58cff; }
.kpi-label { color: #a8ccec; font-size: 15px; letter-spacing: .04em; }
.kpi-value { display: flex; align-items: baseline; gap: 4px; }
.kpi-value b { color: #fff; font-size: 40px; font-weight: 700; line-height: 1.05; font-variant-numeric: tabular-nums; text-shadow: 0 0 18px rgba(120, 200, 255, .35); }
.kpi-value small { color: #9dc3e6; font-size: 15px; }
.kpi-card.cyan .kpi-value b { color: #bff0ff; }
.kpi-card.green .kpi-value b { color: #b6ffe4; }
.kpi-card.amber .kpi-value b { color: #ffdfa8; }
.kpi-card.red .kpi-value b { color: #ffc3ca; }
.kpi-card.violet .kpi-value b { color: #e2d4ff; }
.kpi-foot { color: #8fb6d7; font-size: 12px; }
/* 告警类 KPI：数字脉冲，强化警觉 */
.kpi-card.is-alarm { border-color: rgba(255, 77, 94, .7); box-shadow: 0 0 18px rgba(255, 77, 94, .28); }
.kpi-card.is-alarm .kpi-value b { animation: alarm-pulse 1.8s ease-in-out infinite; }
@keyframes alarm-pulse { 50% { opacity: .55; text-shadow: 0 0 26px rgba(255, 77, 94, .8); } }

/* ===== 三栏布局 ===== */
.screen-body {
  display: grid;
  flex: 1;
  grid-template-columns: minmax(300px, 1fr) minmax(430px, 1.35fr) minmax(300px, 1fr);
  gap: 14px;
  align-content: start;
}
.screen-column { display: flex; flex-direction: column; gap: 12px; min-width: 0; }

/* ===== 科技风面板 ===== */
.screen-panel {
  position: relative;
  min-height: 150px;
  border: 1px solid rgba(56, 138, 224, .55);
  background: linear-gradient(155deg, rgba(10, 52, 108, .72), rgba(3, 24, 58, .8));
  box-shadow: inset 0 0 26px rgba(16, 110, 205, .1), 0 0 16px rgba(0, 68, 150, .18);
}
.screen-panel::before,
.screen-panel::after { position: absolute; width: 30px; height: 14px; content: ''; border-color: #3fd2ff; border-style: solid; }
.screen-panel::before { top: -1px; left: -1px; border-width: 2px 0 0 2px; }
.screen-panel::after { right: -1px; bottom: -1px; border-width: 0 2px 2px 0; }
.panel-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  height: 38px;
  padding: 10px 14px 0;
  font-size: 17px;
  font-weight: 700;
  color: #eaf7ff;
  background: linear-gradient(90deg, rgba(20, 108, 204, .8), transparent);
}
/* 英文副标题弱化，避免中英混排拥挤 */
.panel-title small { color: #7fa6cc; font-size: 10px; font-weight: 400; letter-spacing: .1em; opacity: .6; }
.panel-body { padding: 8px 14px 12px; }
.panel-note { margin: 4px 0 0; color: #7fa6cc; font-size: 11px; line-height: 1.5; }

/* ===== 环形图 ===== */
.donut-wrap { display: flex; align-items: center; gap: 8px; }
.donut-chart { position: relative; width: 152px; height: 152px; flex-shrink: 0; }
.donut-center {
  position: absolute;
  top: 50%;
  left: 76px;
  display: flex;
  width: 96px;
  flex-direction: column;
  align-items: center;
  transform: translate(-50%, -50%);
  pointer-events: none;
}
.donut-center b { color: #fff; font-size: 30px; line-height: 1; font-variant-numeric: tabular-nums; text-shadow: 0 0 16px rgba(63, 210, 255, .5); }
.donut-center b small { margin-left: 1px; font-size: 15px; color: #9dc3e6; }
.dc-label { margin-top: 4px; color: #a8ccec; font-size: 12px; }
.dc-sub { color: #7fa6cc; font-size: 11px; }
.donut-legend { display: flex; flex: 1; flex-direction: column; gap: 12px; margin: 0; padding: 0; list-style: none; }
.donut-legend li { display: flex; align-items: center; gap: 8px; color: #b6d6f0; font-size: 14px; }
.donut-legend b { margin-left: auto; color: #fff; font-size: 19px; font-variant-numeric: tabular-nums; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot.green { background: #2ee6a0; box-shadow: 0 0 7px rgba(46, 230, 160, .85); }
.dot.cyan { background: #3fd2ff; box-shadow: 0 0 7px rgba(63, 210, 255, .85); }
.dot.amber { background: #ffb020; box-shadow: 0 0 7px rgba(255, 176, 32, .85); }
.dot.red { background: #ff4d5e; box-shadow: 0 0 8px rgba(255, 77, 94, .9); }
.dot.violet { background: #b58cff; box-shadow: 0 0 7px rgba(181, 140, 255, .85); }

/* ===== 趋势 / 排行 ===== */
.trend-chart { width: 100%; height: 176px; }
.rank-chart { width: 100%; height: 208px; }

/* ===== 指标卡 ===== */
.metric-cards { display: grid; grid-template-columns: repeat(2, 1fr); gap: 9px; }
.metric-card {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 66px;
  padding: 8px 12px;
  border: 1px solid rgba(56, 138, 224, .45);
  background: linear-gradient(140deg, rgba(14, 76, 148, .6), rgba(4, 28, 66, .7));
}
.metric-icon {
  display: grid;
  flex-shrink: 0;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid currentColor;
  border-radius: 6px;
  font-size: 16px;
}
.metric-card.cyan .metric-icon { color: #3fd2ff; }
.metric-card.green .metric-icon { color: #2ee6a0; }
.metric-card.amber .metric-icon { color: #ffb020; }
.metric-card.violet .metric-icon { color: #b58cff; }
.metric-main b { display: block; color: #fff; font-size: 26px; line-height: 1.1; font-variant-numeric: tabular-nums; }
.metric-main small { margin-left: 3px; color: #9dc3e6; font-size: 13px; font-weight: 400; }
.metric-main span { display: block; margin-top: 2px; color: #a8ccec; font-size: 13px; }

/* ===== 中栏：简化后的数字员工核心 ===== */
.center-stage {
  position: relative;
  height: 224px;
  overflow: hidden;
  border: 1px solid rgba(56, 138, 224, .5);
  background: radial-gradient(circle at 50% 45%, rgba(14, 92, 180, .42), rgba(3, 20, 52, .88) 70%);
}
.stage-glow {
  position: absolute;
  top: 46%;
  left: 50%;
  width: 360px;
  height: 120px;
  border-radius: 50%;
  background: radial-gradient(ellipse, rgba(35, 170, 255, .28), transparent 68%);
  transform: translate(-50%, -50%);
  filter: blur(8px);
}
.core-node {
  position: absolute;
  top: 30%;
  left: 50%;
  z-index: 3;
  display: flex;
  width: 118px;
  height: 118px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border: 2px solid #3fd2ff;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(16, 120, 210, .9), rgba(5, 40, 92, .92));
  box-shadow: 0 0 34px rgba(63, 210, 255, .5);
  transform: translate(-50%, -50%);
}
.core-avatar {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  font-size: 19px;
  font-weight: 700;
  background: linear-gradient(135deg, #2ea8ff, #1b5fd0);
  box-shadow: 0 0 16px rgba(63, 210, 255, .7);
}
.core-node strong { margin-top: 6px; color: #fff; font-size: 15px; }
.core-node small { margin-top: 2px; color: #9ed2f5; font-size: 9px; letter-spacing: .12em; }
.stage-gauges {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 16px;
  display: flex;
  align-items: center;
  justify-content: space-around;
  gap: 12px;
  padding: 0 18px;
}
.gauge { display: flex; flex-direction: column; align-items: center; gap: 6px; color: #a8ccec; font-size: 13px; }
.ring { display: grid; width: 84px; height: 84px; place-items: center; border-radius: 50%; box-shadow: 0 0 20px rgba(63, 210, 255, .25); }
.ring-core {
  display: grid;
  width: 62px;
  height: 62px;
  place-items: center;
  border-radius: 50%;
  background: #062244;
}
.ring-core b { color: #fff; font-size: 18px; font-variant-numeric: tabular-nums; }
.stage-brief { display: flex; flex-direction: column; gap: 6px; }
.brief-line { display: flex; align-items: center; gap: 7px; color: #b6d6f0; font-size: 13px; }

/* ===== 资产状态清单 ===== */
.asset-list { max-height: 186px; margin: 0; padding: 0; overflow: auto; list-style: none; }
.asset-list li {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 6px 4px;
  border-bottom: 1px solid rgba(56, 138, 224, .16);
  font-size: 13px;
}
.asset-list li:last-child { border-bottom: 0; }
.al-name { flex: 1; overflow: hidden; color: #dcf2ff; text-overflow: ellipsis; white-space: nowrap; }
.al-meta { width: 82px; overflow: hidden; color: #7fa6cc; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.al-score { width: 40px; text-align: right; color: #fff; font-size: 17px; font-variant-numeric: tabular-nums; }
.al-score.green { color: #7dffcd; }
.al-score.cyan { color: #9fe6ff; }
.al-score.amber { color: #ffd58a; }
.al-score.red { color: #ffa8b1; }

/* ===== 实时监控 ===== */
.realtime-head { display: flex; align-items: center; gap: 10px; }
.rt-host { display: flex; align-items: center; gap: 6px; max-width: 180px; overflow: hidden; color: #d9f1ff; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.rt-host i { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.rt-host i.ok { background: #2ee6a0; box-shadow: 0 0 8px rgba(46, 230, 160, .85); }
.rt-host i.off { background: #ff4d5e; box-shadow: 0 0 8px rgba(255, 77, 94, .85); }
.rt-count { color: #a8ccec; font-size: 12px; }
.rt-tabs { display: flex; gap: 6px; margin-left: auto; }
.rt-tabs button {
  min-width: 50px;
  padding: 4px 11px;
  border: 1px solid rgba(63, 150, 230, .5);
  color: #a8ccec;
  font-size: 12px;
  background: rgba(8, 46, 96, .7);
  cursor: pointer;
}
.rt-tabs button.active { border-color: #3fd2ff; color: #04101f; font-weight: 600; background: linear-gradient(90deg, #2ea8ff, #3fd2ff); }
.realtime-chart { width: 100%; height: 142px; margin-top: 4px; }
.rt-offline {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 142px;
  margin-top: 4px;
  border: 1px dashed rgba(120, 160, 200, .35);
  background: repeating-linear-gradient(45deg, rgba(255, 255, 255, .02) 0 8px, transparent 8px 16px);
}
.rt-offline-icon { color: #6c9cc8; font-size: 22px; }
.rt-offline b { color: #c3def5; font-size: 15px; }
.rt-offline small { max-width: 92%; overflow: hidden; color: #7fa6cc; font-size: 12px; text-align: center; text-overflow: ellipsis; white-space: nowrap; }
.realtime-foot { display: flex; justify-content: space-around; padding: 5px 0 0; color: #a8ccec; font-size: 13px; }
.realtime-foot span { padding: 0 8px; border-right: 1px solid rgba(90, 156, 220, .2); }
.realtime-foot span:last-child { border-right: 0; }
.realtime-foot b { color: #fff; font-size: 15px; font-variant-numeric: tabular-nums; }

/* ===== 告警等级分布 ===== */
.sev-summary { display: flex; align-items: baseline; gap: 8px; padding: 2px 0 8px; }
.sev-summary b { color: #fff; font-size: 30px; font-variant-numeric: tabular-nums; }
.sev-summary span { color: #a8ccec; font-size: 13px; }
.sev-list { display: flex; flex-direction: column; gap: 10px; }
.sev-row { display: grid; grid-template-columns: 62px 52px 52px 1fr; align-items: center; gap: 8px; }
.sev-name { display: flex; align-items: center; gap: 7px; color: #c3def5; font-size: 14px; }
.sev-count { color: #fff; font-size: 22px; text-align: right; font-variant-numeric: tabular-nums; }
.sev-pct { color: #a8ccec; font-size: 13px; text-align: right; }
.sev-bar { height: 10px; border-radius: 5px; background: rgba(38, 92, 160, .22); overflow: hidden; }
.sev-bar i { display: block; height: 100%; border-radius: 5px; transition: width .6s ease; }
.sev-row.red .sev-bar i { background: linear-gradient(90deg, #ff4d5e, #ff8a94); box-shadow: 0 0 10px rgba(255, 77, 94, .6); }
.sev-row.amber .sev-bar i { background: linear-gradient(90deg, #ffb020, #ffd27a); }
.sev-row.cyan .sev-bar i { background: linear-gradient(90deg, #1b5fd0, #3fd2ff); }
.sev-row.red .sev-count { color: #ffb3ba; }
.sev-row.red .sev-name .dot { background: #ff4d5e; box-shadow: 0 0 8px rgba(255, 77, 94, .9); animation: pulse 1.6s ease-in-out infinite; }
.sev-row.amber .sev-name .dot { background: #ffb020; box-shadow: 0 0 7px rgba(255, 176, 32, .85); }
.sev-row.cyan .sev-name .dot { background: #3fd2ff; box-shadow: 0 0 7px rgba(63, 210, 255, .85); }

/* ===== 能力矩阵 ===== */
.capability-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px; }
.capability-chip {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-height: 62px;
  justify-content: center;
  padding: 6px 11px;
  border: 1px solid rgba(56, 138, 224, .45);
  background: rgba(8, 42, 90, .7);
}
.capability-chip span { color: #a8ccec; font-size: 13px; }
.capability-chip b { color: #fff; font-size: 24px; line-height: 1.15; font-variant-numeric: tabular-nums; }
.capability-chip.green { border-color: rgba(46, 230, 160, .6); background: linear-gradient(140deg, rgba(8, 62, 48, .8), rgba(4, 32, 30, .75)); }
.capability-chip.green b { color: #6dffcb; }
.capability-chip.cyan { border-color: rgba(63, 210, 255, .55); background: linear-gradient(140deg, rgba(8, 52, 100, .8), rgba(4, 26, 58, .75)); }
.capability-chip.cyan b { color: #8fe3ff; }
.capability-chip.violet { border-color: rgba(181, 140, 255, .55); background: linear-gradient(140deg, rgba(44, 28, 88, .8), rgba(20, 14, 46, .75)); }
.capability-chip.violet b { color: #d6c2ff; }
.capability-chip.amber { border-color: rgba(255, 176, 32, .55); background: linear-gradient(140deg, rgba(78, 48, 8, .8), rgba(38, 24, 6, .75)); }
.capability-chip.amber b { color: #ffd58a; }
.capability-chip.red { border-color: rgba(255, 77, 94, .6); background: linear-gradient(140deg, rgba(84, 16, 28, .85), rgba(40, 8, 16, .8)); }
.capability-chip.red b { color: #ffa8b1; }
.ai-badge {
  grid-column: span 3;
  display: flex;
  align-items: center;
  gap: 11px;
  min-height: 58px;
  padding: 7px 13px;
  border: 1px solid rgba(63, 190, 255, .5);
  background: linear-gradient(120deg, rgba(14, 76, 148, .7), rgba(4, 28, 66, .75));
}
.ai-pulse {
  display: grid;
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  background: linear-gradient(135deg, #2ea8ff, #7a4bd8);
  box-shadow: 0 0 14px rgba(63, 210, 255, .55);
  animation: pulse 2.4s ease-in-out infinite;
}
.ai-badge b { display: block; color: #eaf7ff; font-size: 15px; }
.ai-badge small { color: #a8ccec; font-size: 12px; }

/* ===== 底栏 ===== */
.screen-footer {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  height: 36px;
  padding: 0 6px;
  border-top: 1px solid rgba(56, 138, 224, .45);
  color: #8fb6d7;
  font-size: 12px;
  letter-spacing: .06em;
}
.foot-legend { display: flex; align-items: center; gap: 12px; }
.foot-legend .dot { margin-right: 4px; }

/* ===== 响应式 ===== */
@media (max-width: 1250px) {
  .screen-stage { min-width: 1120px; transform-origin: top left; }
  .is-standalone .screen-stage { min-width: 0; }
}
</style>
