<template>
  <el-card>
    <template #header>
      <div class="assets-header">
        <div>
          <div>订单系统 · CMDB 试点资产</div>
          <p class="assets-sub">
            运行状态来自 Zabbix（mock 为演示值）· 约 30 秒刷新
            <span v-if="updatedAt"> · 更新于 {{ formatClock(updatedAt) }}</span>
          </p>
        </div>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </template>
    <el-table :data="rows" stripe empty-text="暂无资产">
      <el-table-column prop="id" label="asset_id" width="200" />
      <el-table-column prop="hostname" label="主机" min-width="140" />
      <el-table-column prop="zabbix_host" label="Zabbix host" width="150" />
      <el-table-column prop="external_id" label="hostid" width="90" />
      <el-table-column prop="role" label="角色" width="90" />
      <el-table-column prop="owner" label="负责人" width="90" />
      <el-table-column prop="env" label="环境" width="80" />
      <el-table-column label="可达" width="70">
        <template #default="{ row }">{{ row.reachable ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column label="DB" width="70">
        <template #default="{ row }">{{ row.db_ok ? '正常' : '异常' }}</template>
      </el-table-column>
      <el-table-column label="CPU" width="150">
        <template #default="{ row }">
          <el-tag v-if="unmapped(row)" type="info" size="small" effect="plain">未映射</el-tag>
          <el-progress
            v-else-if="hasMetric(row, 'cpu_pct')"
            :percentage="barPercent(row, 'cpu_pct')"
            :status="metricTone(row, 'cpu_pct')"
            :stroke-width="10"
            :format="() => metricLabel(row, 'cpu_pct')"
          />
          <span v-else class="metric-empty">—</span>
        </template>
      </el-table-column>
      <el-table-column label="内存" width="150">
        <template #default="{ row }">
          <el-tag v-if="unmapped(row)" type="info" size="small" effect="plain">未映射</el-tag>
          <el-progress
            v-else-if="hasMetric(row, 'mem_pct')"
            :percentage="barPercent(row, 'mem_pct')"
            :status="metricTone(row, 'mem_pct')"
            :stroke-width="10"
            :format="() => metricLabel(row, 'mem_pct')"
          />
          <span v-else class="metric-empty">—</span>
        </template>
      </el-table-column>
      <el-table-column label="磁盘" width="150">
        <template #default="{ row }">
          <el-tag v-if="unmapped(row)" type="info" size="small" effect="plain">未映射</el-tag>
          <el-progress
            v-else-if="hasMetric(row, 'disk_pct')"
            :percentage="barPercent(row, 'disk_pct')"
            :status="metricTone(row, 'disk_pct')"
            :stroke-width="10"
            :format="() => metricLabel(row, 'disk_pct')"
          />
          <span v-else class="metric-empty">—</span>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { fetchAssetStatus, fetchAssets } from '../api'

const assets = ref([])
const metricsById = ref({})
const updatedAt = ref('')
const loading = ref(false)
let timer

const rows = computed(() =>
  assets.value.map((row) => ({
    ...row,
    metrics: metricsById.value[row.id] || null,
  })),
)

function unmapped(row) {
  return Boolean(row.metrics) && row.metrics.host_mapped === false
}

function metricNumber(row, field) {
  const raw = row.metrics?.[field]
  if (raw == null || raw === '') return null
  const value = Number(raw)
  return Number.isNaN(value) ? null : value
}

function hasMetric(row, field) {
  return row.metrics?.host_mapped && metricNumber(row, field) != null
}

function barPercent(row, field) {
  const value = metricNumber(row, field)
  if (value == null) return 0
  return Math.min(100, Math.max(0, Math.round(value * 10) / 10))
}

function metricLabel(row, field) {
  const value = metricNumber(row, field)
  if (value == null) return '—'
  return `${value.toFixed(1)}%`
}

function metricTone(row, field) {
  const value = metricNumber(row, field)
  if (value == null) return undefined
  if (value >= 90) return 'exception'
  if (value >= 75) return 'warning'
  return 'success'
}

function formatClock(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  try {
    const [assetRes, statusRes] = await Promise.all([fetchAssets(), fetchAssetStatus()])
    assets.value = assetRes.data.items || []
    const map = {}
    for (const row of statusRes.data.items || []) {
      map[row.id] = row.metrics
    }
    metricsById.value = map
    updatedAt.value = statusRes.data.updated_at || ''
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 30000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.assets-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.assets-sub {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--muted);
  font-weight: 400;
}
.metric-empty {
  color: var(--muted);
}
</style>
