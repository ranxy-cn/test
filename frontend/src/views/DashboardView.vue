<template>
  <div class="dashboard-page">
    <div class="dashboard-topbar">
      <div>
        <div class="eyebrow">DEVOPS COMMAND CENTER · {{ clock }}</div>
        <h1>智慧运维运营大屏</h1>
        <p>实时掌握资产健康、告警态势与数字员工闭环效率</p>
      </div>
      <div class="dashboard-actions">
        <span class="live-dot" /> 数据实时刷新
        <el-button class="screen-btn" plain @click="toggleFullscreen">{{ fullscreen ? '退出全屏' : '全屏展示' }}</el-button>
      </div>
    </div>

    <div class="kpi-grid">
      <div v-for="item in kpis" :key="item.label" class="kpi-card" :class="`kpi-${item.tone}`">
        <div class="kpi-icon">{{ item.icon }}</div>
        <div class="kpi-body">
          <div class="kpi-label">{{ item.label }}</div>
          <div class="kpi-value">{{ item.value }}<small>{{ item.unit }}</small></div>
          <div class="kpi-foot"><span :class="item.tone">{{ item.note }}</span><span>{{ item.caption }}</span></div>
        </div>
      </div>
    </div>

    <div class="dashboard-grid">
      <section class="screen-panel trend-panel">
        <div class="panel-title"><span>近 7 日告警趋势</span><em>EVENT TREND</em></div>
        <div ref="trendRef" class="chart chart-trend" />
      </section>
      <section class="screen-panel health-panel">
        <div class="panel-title"><span>基础设施健康度</span><em>HEALTH INDEX</em></div>
        <div class="health-ring" :style="{ '--pct': `${overview?.kpis?.availability ?? 100}%` }"><div class="ring-inner"><strong>{{ overview?.kpis?.availability ?? 100 }}%</strong><span>可达率</span></div></div>
        <div class="health-legend"><span><i class="ok" />在线 {{ overview?.kpis?.asset_reachable ?? 0 }}</span><span><i class="bad" />异常 {{ (overview?.kpis?.asset_total ?? 0) - (overview?.kpis?.asset_reachable ?? 0) }}</span></div>
      </section>
      <section class="screen-panel asset-panel">
        <div class="panel-title"><span>资产健康矩阵</span><em>ASSET MATRIX</em></div>
        <div class="asset-list">
          <div v-for="asset in overview?.assets || []" :key="asset.hostname" class="asset-row">
            <span class="asset-dot" :class="asset.reachable ? 'ok' : 'bad'" />
            <span class="asset-name">{{ asset.hostname }}</span><span class="asset-app">{{ asset.app || '—' }}</span>
            <b :class="asset.reachable && asset.db_ok ? 'healthy' : 'warning'">{{ asset.reachable && asset.db_ok ? '健康' : '关注' }}</b>
          </div>
          <el-empty v-if="!overview?.assets?.length" description="暂无资产数据" :image-size="60" />
        </div>
      </section>
      <section class="screen-panel alert-panel">
        <div class="panel-title"><span>实时告警态势</span><em>ACTIVE ALERTS</em></div>
        <div v-if="overview?.anomalies?.length" class="alert-list">
          <div v-for="alert in overview.anomalies" :key="`${alert.hostname}-${alert.trigger}`" class="alert-row" :class="severityClass(alert.severity)" @click="router.push('/anomalies')">
            <span class="severity" :class="severityClass(alert.severity)">{{ alert.severity }}</span>
            <div><strong>{{ alert.hostname || '未知主机' }}</strong><p>{{ alert.trigger || alert.message || '告警事件' }}</p></div>
            <el-icon><ArrowRight /></el-icon>
          </div>
        </div>
        <div v-else class="empty-alert"><span>✓</span><strong>当前没有活动告警</strong><small>系统运行平稳，数字员工持续守护中</small></div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { fetchDashboardOverview } from '../api'

const overview = ref(null)
const trendRef = ref(null)
const clock = ref('')
const fullscreen = ref(false)
const router = useRouter()
let chart
let timer
let clockTimer

const kpis = computed(() => {
  const data = overview.value?.kpis || {}
  return [
    { icon: '▣', label: '纳管资产', value: data.asset_total ?? 0, unit: ' 台', note: '资产台账', tone: 'blue', caption: '统一纳管' },
    { icon: '◉', label: '基础设施可达', value: data.asset_reachable ?? 0, unit: ' 台', note: `${data.availability ?? 100}%`, tone: 'green', caption: '当前可达' },
    { icon: '⚠', label: '活动告警', value: data.active_anomalies ?? 0, unit: ' 条', note: data.active_anomalies ? '需关注' : '运行平稳', tone: data.active_anomalies ? 'orange' : 'green', caption: '未恢复事件' },
    { icon: '⟳', label: '进行中任务单', value: data.open_tickets ?? 0, unit: ' 单', note: '闭环处理中', tone: 'violet', caption: '自动化流水线' },
    { icon: '✓', label: '已恢复任务单', value: data.recovered_tickets ?? 0, unit: ' 单', note: '累计恢复', tone: 'green', caption: '数字员工成果' },
  ]
})

function severityClass(value) { return String(value || '').toLowerCase().includes('high') || value === '高' ? 'high' : 'medium' }
function updateClock() { clock.value = new Date().toLocaleString('zh-CN', { hour12: false }) }
function renderChart() {
  if (!trendRef.value) return
  chart?.dispose()
  chart = echarts.init(trendRef.value)
  const rows = overview.value?.trend || []
  chart.setOption({
    grid: { left: 36, right: 20, top: 24, bottom: 26 },
    xAxis: { type: 'category', data: rows.map((row) => row.date.slice(5)), axisLabel: { color: '#93a7c6' }, axisLine: { lineStyle: { color: '#243756' } } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#1c2d49' } }, axisLabel: { color: '#7185a6' } },
    series: [{ type: 'line', smooth: true, data: rows.map((row) => row.count), symbolSize: 8, lineStyle: { width: 3, color: '#39d5ff' }, itemStyle: { color: '#39d5ff', borderColor: '#b4f4ff', borderWidth: 2 }, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(57,213,255,.38)' }, { offset: 1, color: 'rgba(57,213,255,0)' }]) } }],
  })
}
async function load() { try { overview.value = (await fetchDashboardOverview()).data; await nextTick(); renderChart() } catch (error) { ElMessage.error(error.response?.data?.detail || '大屏数据加载失败') } }
async function toggleFullscreen() { try { if (!document.fullscreenElement) await document.documentElement.requestFullscreen(); else await document.exitFullscreen(); fullscreen.value = !!document.fullscreenElement } catch { ElMessage.info('当前浏览器不支持全屏') } }
function resize() { chart?.resize() }
onMounted(() => { updateClock(); clockTimer = setInterval(updateClock, 1000); load(); timer = setInterval(load, 30000); window.addEventListener('resize', resize) })
onBeforeUnmount(() => { clearInterval(timer); clearInterval(clockTimer); chart?.dispose(); window.removeEventListener('resize', resize) })
</script>

<style scoped>
/* ===== 大屏布局 ===== */
.dashboard-page { min-height: calc(100vh - 50px); margin: -54px -22px -100px; padding: 72px 36px 128px; color: #dfeeff; background: radial-gradient(ellipse at 50% -12%, #122d50 0, #081526 44%, #040c18 100%); }
.dashboard-topbar { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 28px; }
.eyebrow, .panel-title em { color: #5b789f; font-size: 11px; letter-spacing: .18em; font-style: normal; }
.dashboard-topbar h1 { margin: 6px 0 4px; font-size: 32px; letter-spacing: .04em; background: linear-gradient(90deg, #ffffff, #62d5ff, #3aa8ff); -webkit-background-clip: text; background-clip: text; color: transparent; }
.dashboard-topbar p { margin: 0; color: #6b8ba8; font-size: 13px; }
.dashboard-actions { display: flex; align-items: center; gap: 10px; color: #87a0c3; font-size: 13px; white-space: nowrap; }
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: #38e8a3; box-shadow: 0 0 10px #38e8a3; animation: pulse 2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .5; } }
.screen-btn { color: #a6dfff; border-color: #285078; background: #0b2038; }

/* ===== KPI 卡片 ===== */
.kpi-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; }
.kpi-card { display: flex; align-items: center; gap: 14px; padding: 20px 18px; border: 1px solid #1b3556; border-radius: 12px; background: linear-gradient(135deg, rgba(16,40,70,.92), rgba(8,20,37,.94)); box-shadow: inset 0 1px 0 rgba(135,208,255,.06), 0 10px 26px rgba(0,0,0,.2); transition: transform .2s, border-color .2s, box-shadow .2s; }
.kpi-card:hover { transform: translateY(-2px); border-color: #2d5c90; box-shadow: inset 0 1px 0 rgba(135,208,255,.1), 0 16px 34px rgba(0,0,0,.3); }
.kpi-icon { display: grid; flex-shrink: 0; width: 42px; height: 42px; place-items: center; border-radius: 10px; font-size: 20px; }
.kpi-blue .kpi-icon { color: #51c9ff; background: rgba(81,201,255,.1); border: 1px solid rgba(81,201,255,.22); }
.kpi-green .kpi-icon { color: #36e2a0; background: rgba(54,226,160,.1); border: 1px solid rgba(54,226,160,.22); }
.kpi-orange .kpi-icon { color: #ffc15d; background: rgba(255,193,93,.1); border: 1px solid rgba(255,193,93,.22); }
.kpi-violet .kpi-icon { color: #b693ff; background: rgba(182,147,255,.1); border: 1px solid rgba(182,147,255,.22); }
.kpi-label { color: #7d99b8; font-size: 12px; }
.kpi-value { margin: 6px 0; font-size: 30px; font-weight: 700; color: #fff; letter-spacing: -.02em; }
.kpi-value small { font-size: 13px; font-weight: 400; color: #7d99b8; margin-left: 2px; }
.kpi-foot { display: flex; justify-content: space-between; color: #526a8a; font-size: 11px; }
.blue { color: #51c9ff; }.green { color: #36e2a0; }.orange { color: #ffc15d; }.violet { color: #b693ff; }

/* ===== 面板通用 ===== */
.dashboard-grid { display: grid; grid-template-columns: 1.45fr .75fr 1fr; gap: 16px; margin-top: 16px; }
.screen-panel { padding: 20px; min-height: 270px; border: 1px solid #1b3556; background: linear-gradient(135deg, rgba(16,38,67,.92), rgba(8,20,37,.94)); box-shadow: inset 0 1px 0 rgba(135,208,255,.06), 0 10px 26px rgba(0,0,0,.2); border-radius: 12px; }
.trend-panel { grid-column: span 2; }
.panel-title { display: flex; justify-content: space-between; align-items: center; font-weight: 600; color: #dcecff; font-size: 14px; }
.panel-title span { padding-left: 10px; border-left: 3px solid #3aa8ff; }
.chart { width: 100%; height: 220px; }

/* ===== 健康度圆环 ===== */
.health-panel { display: flex; flex-direction: column; align-items: center; }
.health-ring { width: 170px; height: 170px; display: grid; place-items: center; margin: 20px 0 14px; border-radius: 50%; background: conic-gradient(#37e1aa var(--pct, 100%), #152d4a var(--pct, 100%)); box-shadow: 0 0 30px rgba(55,225,170,.14); position: relative; }
.health-ring::before { content: ''; position: absolute; inset: 12px; border-radius: 50%; background: #0a1e38; }
.ring-inner { position: relative; z-index: 1; text-align: center; }
.ring-inner strong { display: block; font-size: 32px; color: #fff; }
.ring-inner span { display: block; margin-top: 4px; color: #6b8ba8; font-size: 12px; }
.health-legend { display: flex; gap: 18px; color: #8097b8; font-size: 12px; }
.health-legend i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; }
.health-legend .ok { background: #36e2a0; }
.health-legend .bad { background: #ff7c70; }

/* ===== 资产矩阵 ===== */
.asset-panel { grid-column: span 2; }
.asset-list { margin-top: 16px; max-height: 235px; overflow: auto; }
.asset-row { display: flex; align-items: center; gap: 10px; padding: 11px 4px; border-bottom: 1px solid #14304f; transition: background .15s; }
.asset-row:hover { background: rgba(42,90,148,.08); border-radius: 6px; }
.asset-dot { flex-shrink: 0; width: 7px; height: 7px; border-radius: 50%; }
.asset-dot.ok { background: #36e2a0; box-shadow: 0 0 6px rgba(54,226,160,.6); }
.asset-dot.bad { background: #ff7c70; box-shadow: 0 0 6px rgba(255,124,112,.6); }
.asset-name { flex: 1; color: #d7eaff; font-family: ui-monospace, monospace; font-size: 12px; }
.asset-app { width: 120px; color: #6f87aa; font-size: 12px; }
.asset-row b { width: 42px; text-align: right; font-size: 12px; }
.healthy { color: #3be0a4; }.warning { color: #ffc15d; }

/* ===== 告警列表 ===== */
.alert-panel { grid-column: 3; grid-row: 2; }
.alert-list { margin-top: 16px; max-height: 235px; overflow: auto; }
.alert-row { display: flex; align-items: center; gap: 10px; padding: 11px 8px 11px 12px; border-left: 3px solid transparent; border-bottom: 1px solid #14304f; border-radius: 6px; transition: background .15s; cursor: pointer; }
.alert-row:hover { background: rgba(42,90,148,.08); }
.alert-row.high { border-left-color: #ff7c70; }
.alert-row.medium { border-left-color: #ffc15d; }
.alert-row strong { color: #e9f4ff; font-size: 13px; }
.alert-row p { margin: 5px 0 0; color: #7890b2; font-size: 12px; }
.alert-row .el-icon { margin-left: auto; color: #527096; }
.severity { min-width: 38px; text-align: center; padding: 4px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; color: #fff; background: #df625a; }
.severity.medium { background: #b97934; }
.empty-alert { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 210px; color: #89a6c8; }
.empty-alert span { display: grid; place-items: center; width: 48px; height: 48px; border-radius: 50%; background: rgba(54,226,160,.14); color: #39e1a2; font-size: 26px; margin-bottom: 12px; }
.empty-alert small { margin-top: 7px; color: #587292; }

/* ===== 响应式 ===== */
@media (max-width: 1100px) { .kpi-grid { grid-template-columns: repeat(3, 1fr); }.dashboard-grid { grid-template-columns: 1fr 1fr; }.trend-panel, .asset-panel { grid-column: span 2; }.alert-panel { grid-column: span 2; grid-row: auto; } }
@media (max-width: 1024px) { .dashboard-page { margin: -54px -14px -96px; padding: 70px 22px 120px; } }
@media (max-width: 768px) { .dashboard-page { margin: -52px -10px -92px; padding: 66px 16px 116px; }.dashboard-topbar { align-items: flex-start; flex-direction: column; }.kpi-grid, .dashboard-grid { grid-template-columns: 1fr; }.trend-panel, .asset-panel, .alert-panel { grid-column: auto; }.kpi-value { font-size: 28px; } }
</style>
