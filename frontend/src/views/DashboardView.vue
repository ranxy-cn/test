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
      <div v-for="item in kpis" :key="item.label" class="kpi-card">
        <div class="kpi-label">{{ item.label }}</div>
        <div class="kpi-value">{{ item.value }}<small>{{ item.unit }}</small></div>
        <div class="kpi-foot"><span :class="item.tone">{{ item.note }}</span><span>{{ item.caption }}</span></div>
      </div>
    </div>

    <div class="dashboard-grid">
      <section class="screen-panel trend-panel">
        <div class="panel-title"><span>近 7 日告警趋势</span><em>EVENT TREND</em></div>
        <div ref="trendRef" class="chart chart-trend" />
      </section>
      <section class="screen-panel health-panel">
        <div class="panel-title"><span>基础设施健康度</span><em>HEALTH INDEX</em></div>
        <div class="health-ring" :style="{ background: `radial-gradient(circle, #0b1b31 58%, transparent 60%), conic-gradient(#37e1aa 0 ${overview?.kpis?.availability ?? 100}%, #173653 ${overview?.kpis?.availability ?? 100}% 100%)` }"><div><strong>{{ overview?.kpis?.availability ?? 100 }}%</strong><span>可达率</span></div></div>
        <div class="health-legend"><span><i class="ok" />在线 {{ overview?.kpis?.asset_reachable ?? 0 }}</span><span><i class="bad" />异常 {{ (overview?.kpis?.asset_total ?? 0) - (overview?.kpis?.asset_reachable ?? 0) }}</span></div>
      </section>
      <section class="screen-panel asset-panel">
        <div class="panel-title"><span>资产健康矩阵</span><em>ASSET MATRIX</em></div>
        <div class="asset-list">
          <div v-for="asset in overview?.assets || []" :key="asset.hostname" class="asset-row">
            <span class="asset-name">{{ asset.hostname }}</span><span class="asset-app">{{ asset.app || '—' }}</span>
            <b :class="asset.reachable && asset.db_ok ? 'healthy' : 'warning'">{{ asset.reachable && asset.db_ok ? '健康' : '关注' }}</b>
          </div>
          <el-empty v-if="!overview?.assets?.length" description="暂无资产数据" :image-size="60" />
        </div>
      </section>
      <section class="screen-panel alert-panel">
        <div class="panel-title"><span>实时告警态势</span><em>ACTIVE ALERTS</em></div>
        <div v-if="overview?.anomalies?.length" class="alert-list">
          <div v-for="alert in overview.anomalies" :key="`${alert.hostname}-${alert.trigger}`" class="alert-row">
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
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { fetchDashboardOverview } from '../api'

const overview = ref(null)
const trendRef = ref(null)
const clock = ref('')
const fullscreen = ref(false)
let chart
let timer
let clockTimer

const kpis = computed(() => {
  const data = overview.value?.kpis || {}
  return [
    { label: '纳管资产', value: data.asset_total ?? 0, unit: ' 台', note: '资产台账', tone: 'blue', caption: '统一纳管' },
    { label: '基础设施可达', value: data.asset_reachable ?? 0, unit: ' 台', note: `${data.availability ?? 100}%`, tone: 'green', caption: '当前可达' },
    { label: '活动告警', value: data.active_anomalies ?? 0, unit: ' 条', note: data.active_anomalies ? '需关注' : '运行平稳', tone: data.active_anomalies ? 'orange' : 'green', caption: '未恢复事件' },
    { label: '进行中任务单', value: data.open_tickets ?? 0, unit: ' 单', note: '闭环处理中', tone: 'violet', caption: '自动化流水线' },
    { label: '已恢复任务单', value: data.recovered_tickets ?? 0, unit: ' 单', note: '累计恢复', tone: 'green', caption: '数字员工成果' },
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
/* 负 margin 抵消 App 布局 main 的 padding，使深色大屏铺满视口（顶到固定顶栏之后；底部 -100px 延伸至 Dock 之后） */
.dashboard-page { min-height: calc(100vh - 50px); margin: -54px -22px -100px; padding: 72px 32px 128px; color: #e9f4ff; background: radial-gradient(circle at 50% -20%, #17325c 0, #071425 46%, #040b16 100%); }
.dashboard-topbar { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 26px; }
.eyebrow, .panel-title em { color: #6d8ab6; font-size: 11px; letter-spacing: .16em; font-style: normal; }
.dashboard-topbar h1 { margin: 6px 0 4px; font-size: 30px; letter-spacing: .04em; background: linear-gradient(90deg, #fff, #59dcff); -webkit-background-clip: text; color: transparent; }
.dashboard-topbar p { margin: 0; color: #7890b2; }
.dashboard-actions { display: flex; align-items: center; gap: 10px; color: #87a0c3; font-size: 13px; white-space: nowrap; }.live-dot { width: 8px; height: 8px; border-radius: 50%; background: #38e8a3; box-shadow: 0 0 12px #38e8a3; }.screen-btn { color: #a6dfff; border-color: #285078; background: #0b2038; }
.kpi-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; }.kpi-card, .screen-panel { border: 1px solid #1b3556; background: linear-gradient(135deg, rgba(16,38,67,.92), rgba(8,20,37,.92)); box-shadow: inset 0 1px 0 rgba(135,208,255,.08), 0 12px 28px rgba(0,0,0,.2); border-radius: 10px; }.kpi-card { padding: 18px 20px; }.kpi-label { color: #8ca6c8; font-size: 13px; }.kpi-value { margin: 8px 0 8px; font-size: 32px; font-weight: 700; color: #fff; }.kpi-value small { font-size: 14px; color: #8ca6c8; }.kpi-foot { display: flex; justify-content: space-between; color: #5d7395; font-size: 11px; }.blue { color: #51c9ff; }.green { color: #36e2a0; }.orange { color: #ffc15d; }.violet { color: #b693ff; }
.dashboard-grid { display: grid; grid-template-columns: 1.45fr .75fr 1fr; gap: 16px; margin-top: 16px; }.screen-panel { padding: 18px; min-height: 270px; }.trend-panel { grid-column: span 2; }.panel-title { display: flex; justify-content: space-between; align-items: center; font-weight: 600; color: #dcecff; }.chart { width: 100%; height: 220px; }.health-panel { display: flex; flex-direction: column; align-items: center; }.health-ring { width: 160px; height: 160px; display: grid; place-items: center; margin: 18px 0 12px; border-radius: 50%; background: radial-gradient(circle, #0b1b31 58%, transparent 60%), conic-gradient(#37e1aa 0 83%, #173653 83% 100%); box-shadow: 0 0 26px rgba(55,225,170,.18); }.health-ring strong { display: block; text-align: center; font-size: 30px; color: #fff; }.health-ring span { display: block; color: #7187aa; text-align: center; font-size: 12px; margin-top: 4px; }.health-legend { display: flex; gap: 18px; color: #8097b8; font-size: 12px; }.health-legend i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; }.health-legend .ok { background: #36e2a0; }.health-legend .bad { background: #ff7c70; }.asset-panel { grid-column: span 2; }.asset-list, .alert-list { margin-top: 16px; max-height: 235px; overflow: auto; }.asset-row, .alert-row { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px solid #17304f; }.asset-name { flex: 1; color: #d7eaff; font-family: ui-monospace, monospace; font-size: 12px; }.asset-app { width: 120px; color: #6f87aa; font-size: 12px; }.asset-row b { width: 42px; text-align: right; font-size: 12px; }.healthy { color: #3be0a4; }.warning { color: #ffc15d; }.alert-panel { grid-column: 3; grid-row: 2; }.alert-row strong { color: #e9f4ff; font-size: 13px; }.alert-row p { margin: 5px 0 0; color: #7890b2; font-size: 12px; }.alert-row .el-icon { margin-left: auto; color: #527096; }.severity { min-width: 38px; text-align: center; padding: 4px 6px; border-radius: 4px; font-size: 10px; color: #fff; background: #df625a; }.severity.medium { background: #b97934; }.empty-alert { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 210px; color: #89a6c8; }.empty-alert span { display: grid; place-items: center; width: 48px; height: 48px; border-radius: 50%; background: rgba(54,226,160,.14); color: #39e1a2; font-size: 26px; margin-bottom: 12px; }.empty-alert small { margin-top: 7px; color: #587292; }
@media (max-width: 1100px) { .kpi-grid { grid-template-columns: repeat(3, 1fr); }.dashboard-grid { grid-template-columns: 1fr 1fr; }.trend-panel, .asset-panel { grid-column: span 2; }.alert-panel { grid-column: span 2; grid-row: auto; } }
@media (max-width: 1024px) { .dashboard-page { margin: -54px -14px -96px; padding: 70px 22px 120px; } }
@media (max-width: 768px) { .dashboard-page { margin: -52px -10px -92px; padding: 66px 16px 116px; }.dashboard-topbar { align-items: flex-start; flex-direction: column; }.kpi-grid, .dashboard-grid { grid-template-columns: 1fr; }.trend-panel, .asset-panel, .alert-panel { grid-column: auto; }.kpi-value { font-size: 28px; } }
</style>
