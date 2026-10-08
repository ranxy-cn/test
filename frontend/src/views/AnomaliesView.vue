<template>
  <div class="anomalies">
    <!-- 顶部统计指标 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :xs="12" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">当前未恢复</div>
          <div class="stat-value" :class="{ danger: stats.current_abnormal > 0 }">{{ stats.current_abnormal ?? '-' }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">今日异常</div>
          <div class="stat-value">{{ stats.today_abnormal ?? '-' }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">本月异常</div>
          <div class="stat-value">{{ stats.month_abnormal ?? '-' }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">平均恢复时长</div>
          <div class="stat-value unit">{{ stats.avg_recover_minutes ?? '-' }}<span v-if="stats.avg_recover_minutes != null"> 分钟</span></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 统计图形 -->
    <el-row :gutter="16" class="chart-row">
      <el-col :xs="24" :md="8">
        <el-card shadow="never" class="chart-card">
          <template #header><span class="chart-title">严重级别分布</span></template>
          <div ref="sevEl" class="chart-box"></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never" class="chart-card">
          <template #header><span class="chart-title">母机异常分布</span></template>
          <div ref="motherEl" class="chart-box"></div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="8">
        <el-card shadow="never" class="chart-card">
          <template #header><span class="chart-title">近 7 天趋势</span></template>
          <div ref="trendEl" class="chart-box"></div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 异常列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="row-between">
          <span>
            异常条目
            <el-badge v-if="abnormalCount > 0" :value="abnormalCount" type="danger" class="count-badge" />
          </span>
          <div class="row-gap">
            <el-radio-group v-model="statusFilter" size="small" @change="onFilterChange">
              <el-radio-button label="">全部</el-radio-button>
              <el-radio-button label="abnormal">异常</el-radio-button>
              <el-radio-button label="recovered">恢复</el-radio-button>
            </el-radio-group>
            <el-select v-model="severityFilter" placeholder="级别" clearable size="small" style="width: 120px" @change="onFilterChange">
              <el-option v-for="(label, val) in SEV_LABELS" :key="val" :label="label" :value="val" />
            </el-select>
            <el-switch v-model="autoRefresh" active-text="5 秒自动刷新" />
            <el-button size="small" :loading="loading" @click="reload">立即刷新</el-button>
          </div>
        </div>
      </template>

      <el-table :data="items" v-loading="loading" empty-text="当前没有异常">
        <el-table-column prop="mother_name" label="母机" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.mother_name && row.mother_name !== '未归属'" size="small" type="primary" effect="plain">{{ row.mother_name }}</el-tag>
            <span v-else class="muted">{{ row.mother_name || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="group" label="组" width="130">
          <template #default="{ row }">{{ row.group || '—' }}</template>
        </el-table-column>
        <el-table-column prop="child" label="子机" width="190" show-overflow-tooltip>
          <template #default="{ row }">
            <span>{{ row.child || '—' }}</span>
            <el-tag v-if="row.is_mother_self" size="small" type="primary" effect="dark" style="margin-left: 6px">本机·母机</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="trigger_name" label="异常项" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ cnTrigger(row.trigger_name) }}</template>
        </el-table-column>
        <el-table-column prop="message" label="告警消息" min-width="280" show-overflow-tooltip>
          <template #default="{ row }">{{ row.message || '—' }}</template>
        </el-table-column>
        <el-table-column label="级别" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="SEV_TAGS[row.severity] || 'info'">{{ SEV_LABELS[row.severity] || row.severity }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.status === 'abnormal'" type="danger" effect="dark">异常</el-tag>
            <el-tag v-else type="success" effect="plain">恢复</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="异常时间" width="170" :formatter="fmtTimeCol('first_seen_at')" />
        <el-table-column label="恢复时间" width="170" :formatter="fmtTimeCol('recovered_at')" />
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager-row">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </el-card>

    <AnomalyDetailDrawer ref="detailDrawer" />
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts'
import {
  fetchAnomalies,
  fetchAnomalyStats,
} from '../api'
import { fmtTimeCol } from '../time'
import { cnTrigger } from '../trigger-cn'
import AnomalyDetailDrawer from '../components/AnomalyDetailDrawer.vue'

const items = ref([])
const abnormalCount = ref(0)
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const statusFilter = ref('')
const severityFilter = ref('')

const stats = ref({})

const sevEl = ref(null)
const motherEl = ref(null)
const trendEl = ref(null)
let sevChart = null
let motherChart = null
let trendChart = null

const autoRefresh = ref(true)
let timer = null

const SEV_LABELS = {
  P0: 'P0 严重故障',
  P1: 'P1 重要告警',
  P2: 'P2 一般告警',
  P3: 'P3 提示信息',
  disaster: '灾难',
  high: '严重',
  average: '较严重',
  warning: '警告',
  information: '提示',
  not_classified: '未知',
}
const SEV_TAGS = {
  P0: 'danger',
  P1: 'warning',
  P2: 'warning',
  P3: 'info',
  disaster: 'danger',
  high: 'danger',
  average: 'warning',
  warning: 'warning',
  information: 'info',
  not_classified: 'info',
}
// P0-P3 与后端 LEVEL_COLORS 一致；历史 Zabbix 风格 key 保留兼容
const SEV_COLORS = {
  P0: '#ff3b30',
  P1: '#ff9500',
  P2: '#f7ba2a',
  P3: '#909399',
  disaster: '#d70015',
  high: '#ff3b30',
  average: '#ff9500',
  warning: '#ffb340',
  information: '#0a84ff',
  not_classified: '#8e8e93',
}

const detailDrawer = ref(null)

const route = useRoute()
const router = useRouter()

function openDetail(row) {
  detailDrawer.value?.open(row.id)
}

async function load() {
  loading.value = true
  try {
    const { data } = await fetchAnomalies({
      page: page.value,
      page_size: pageSize.value,
      status: statusFilter.value || undefined,
      severity: severityFilter.value || undefined,
    })
    items.value = data.items || []
    abnormalCount.value = data.abnormal_count || 0
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

function reload() {
  load()
  loadStats()
}

function onFilterChange() {
  page.value = 1
  load()
}

function onPageChange(p) {
  page.value = p
  load()
}

function onSizeChange(s) {
  pageSize.value = s
  page.value = 1
  load()
}

async function loadStats() {
  try {
    const { data } = await fetchAnomalyStats()
    stats.value = data
    renderCharts(data)
  } catch {
    /* 统计获取失败不影响主列表 */
  }
}

function renderCharts(data) {
  renderSevChart(data.severity_dist || [])
  renderMotherChart(data.mother_dist || [])
  renderTrendChart(data.trend_7d || [])
}

function renderSevChart(dist) {
  if (!sevChart) return
  if (!dist.length) {
    sevChart.setOption({ title: { text: '暂无数据', left: 'center', top: 'middle', textStyle: { color: '#86868b', fontSize: 14 } } })
    return
  }
  sevChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 8 },
    series: [
      {
        type: 'pie',
        radius: ['45%', '68%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        label: { formatter: '{b}: {c}', color: '#48484a' },
        data: dist.map((d) => ({ name: d.label, value: d.count, itemStyle: { color: SEV_COLORS[d.severity] || '#8e8e93' } })),
      },
    ],
  })
}

function renderMotherChart(dist) {
  if (!motherChart) return
  if (!dist.length) {
    motherChart.setOption({ title: { text: '暂无数据', left: 'center', top: 'middle', textStyle: { color: '#86868b', fontSize: 14 } } })
    return
  }
  motherChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 8, right: 24, top: 24, bottom: 8, containLabel: true },
    xAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#e8e8ed' } } },
    yAxis: {
      type: 'category',
      data: dist.map((d) => d.mother),
      axisLabel: { color: '#6e6e73' },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 20,
        itemStyle: { color: '#0a84ff', borderRadius: [0, 6, 6, 0] },
        data: dist.map((d) => d.count),
      },
    ],
  })
}

function renderTrendChart(trend) {
  if (!trendChart) return
  if (!trend.length) {
    trendChart.setOption({ title: { text: '暂无数据', left: 'center', top: 'middle', textStyle: { color: '#86868b', fontSize: 14 } } })
    return
  }
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0, icon: 'roundRect', itemWidth: 14, itemHeight: 8 },
    grid: { left: 8, right: 24, top: 32, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: trend.map((d) => d.date.slice(5)),
      axisLine: { lineStyle: { color: '#d2d2d7' } },
    },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#e8e8ed' } } },
    series: [
      {
        name: '异常',
        type: 'line',
        smooth: true,
        symbolSize: 6,
        lineStyle: { color: '#ff3b30', width: 2 },
        itemStyle: { color: '#ff3b30' },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: '#ff3b3022' }, { offset: 1, color: '#ff3b3000' }]) },
        data: trend.map((d) => d.abnormal),
      },
      {
        name: '恢复',
        type: 'line',
        smooth: true,
        symbolSize: 6,
        lineStyle: { color: '#34c759', width: 2 },
        itemStyle: { color: '#34c759' },
        data: trend.map((d) => d.recovered),
      },
    ],
  })
}

function handleResize() {
  sevChart?.resize()
  motherChart?.resize()
  trendChart?.resize()
}

function tick() {
  if (!loading.value) load()
  loadStats()
}

function startAuto() {
  stopAuto()
  if (autoRefresh.value) timer = setInterval(tick, 5000)
}

function stopAuto() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(autoRefresh, (on) => (on ? startAuto() : stopAuto()))

onMounted(() => {
  nextTick(() => {
    sevChart = echarts.init(sevEl.value)
    motherChart = echarts.init(motherEl.value)
    trendChart = echarts.init(trendEl.value)
    window.addEventListener('resize', handleResize)
  })
  load()
  loadStats()
  startAuto()
  // 从 AI 分析页等外部跳转：?anomaly_id=xx 自动打开对应告警详情
  const fromQuery = route.query.anomaly_id
  if (fromQuery) {
    openDetail({ id: Number(fromQuery) })
    router.replace({ query: { ...route.query, anomaly_id: undefined } })
  }
})

onBeforeUnmount(() => {
  stopAuto()
  window.removeEventListener('resize', handleResize)
  sevChart?.dispose()
  motherChart?.dispose()
  trendChart?.dispose()
})
</script>

<style scoped>
.row-between {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.row-gap {
  display: flex;
  align-items: center;
  gap: 10px;
}
.count-badge {
  margin-left: 8px;
  vertical-align: middle;
}
.muted {
  color: var(--el-text-color-secondary);
}
.stat-row {
  margin-bottom: 16px;
}
.stat-card {
  text-align: center;
}
/* 统计卡入场：轻浮动浮现，节奏错落 */
.stat-row .el-card { animation: rise-in 0.5s var(--ease-out, ease) both; }
.stat-row .el-col:nth-child(2) .el-card { animation-delay: 0.06s; }
.stat-row .el-col:nth-child(3) .el-card { animation-delay: 0.12s; }
.stat-row .el-col:nth-child(4) .el-card { animation-delay: 0.18s; }
.stat-label {
  font-size: 13px;
  letter-spacing: 0.02em;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.stat-value {
  font-size: 30px;
  font-weight: 700;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
  line-height: 1.2;
}
.stat-value.danger {
  color: var(--el-color-danger);
}
.stat-value.unit {
  font-size: 22px;
}
.stat-value.unit span {
  font-size: 13px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}
.chart-row {
  margin-bottom: 16px;
}
.chart-title {
  font-size: 14px;
  font-weight: 600;
}
.chart-box {
  height: 260px;
}
.pager-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}
.stress-box {
  margin-bottom: 12px;
  border: 1px dashed var(--el-border-color);
  border-radius: 4px;
}
.stress-title {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
