<template>
  <el-dialog
    :model-value="modelValue"
    fullscreen
    :title="title"
    destroy-on-close
    @update:model-value="$emit('update:modelValue', $event)"
    @opened="onOpened"
    @closed="onClosed"
  >
    <div v-if="asset" class="monitor">
      <!-- 健康总览 -->
      <el-row :gutter="12">
        <el-col v-for="card in healthCards" :key="card.label" :xs="12" :sm="6">
          <el-card shadow="never" class="metric-card">
            <div class="metric-label">
              {{ card.label }}
              <el-tooltip :content="card.tip" placement="top"><el-icon><QuestionFilled /></el-icon></el-tooltip>
            </div>
            <div class="metric-value" :class="card.cls">
              {{ card.value }}<span class="metric-unit">{{ card.unit }}</span>
            </div>
            <el-progress :percentage="card.pct" :color="card.color" :show-text="false" :stroke-width="6" />
          </el-card>
        </el-col>
      </el-row>

      <!-- 折线图 -->
      <el-card shadow="never" class="block">
        <template #header>
          <div class="row-between">
            <span>
              趋势（{{ metricsNote || '近 60 分钟' }}）
              <el-tag v-if="!metricsReal" size="small" type="warning">演示数据</el-tag>
              <el-tag v-else size="small" type="success">Zabbix 真实数据</el-tag>
            </span>
            <div class="row-gap">
              <el-radio-group v-model="minutes" size="small" @change="loadMetrics">
                <el-radio-button :value="30">30 分钟</el-radio-button>
                <el-radio-button :value="60">1 小时</el-radio-button>
                <el-radio-button :value="180">3 小时</el-radio-button>
                <el-radio-button :value="360">6 小时</el-radio-button>
              </el-radio-group>
              <el-button size="small" :loading="metricsLoading" @click="loadMetrics">刷新</el-button>
            </div>
          </div>
        </template>
        <div ref="chartEl" class="chart" />
      </el-card>

      <!-- 服务器详情：SSH 全景采集（系统 / 硬件 / 内存 / 磁盘 / 网络 / 进程） -->
      <el-card shadow="never" class="block">
        <template #header>
          <div class="row-between">
            <span>
              服务器详情
              <el-tag v-if="sysinfo" size="small" type="success" effect="plain">
                {{ sysinfo.basic.hostname || asset?.hostname }} · {{ sysinfo.ip }}
              </el-tag>
              <el-tag v-if="sysinfoCollectedAt" size="small" type="info" effect="plain">采集于 {{ sysinfoCollectedAt }}</el-tag>
              <el-tooltip content="SSH 上机执行只读命令，一次性采集系统/硬件/内存/磁盘/网络/进程全景信息" placement="top">
                <el-icon><QuestionFilled /></el-icon>
              </el-tooltip>
            </span>
            <el-button size="small" :loading="sysinfoLoading" @click="loadSysinfo">重新采集</el-button>
          </div>
        </template>

        <el-alert
          v-if="sysinfoError"
          :title="sysinfoError"
          type="warning"
          :closable="false"
          show-icon
          class="inspect-alert"
        />

        <template v-if="sysinfo">
          <el-tabs v-model="detailTab">
            <!-- 系统概览 -->
            <el-tab-pane label="系统概览" name="overview">
              <el-row :gutter="16">
                <el-col :xs="24" :md="8">
                  <div class="sub-title">系统信息</div>
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item label="主机名">{{ sysinfo.basic.hostname || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="IP 地址">{{ sysinfo.ip }}</el-descriptions-item>
                    <el-descriptions-item label="操作系统">{{ sysinfo.basic.os || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="内核版本">{{ sysinfo.basic.kernel || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="系统架构">{{ sysinfo.basic.arch || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="虚拟化">
                      {{ sysinfo.virt.label }}
                      <span v-if="sysinfo.virt.product" class="muted">（{{ sysinfo.virt.product }}）</span>
                    </el-descriptions-item>
                    <el-descriptions-item label="时区 / 本机时间">{{ sysinfo.basic.timezone || '—' }} · {{ sysinfo.basic.local_time || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="最近启动时间">{{ sysinfo.basic.boot_at || '—' }}</el-descriptions-item>
                  </el-descriptions>
                </el-col>
                <el-col :xs="24" :md="8">
                  <div class="sub-title">CPU（{{ sysinfo.cpu.cores_logical || '?' }} 逻辑核）</div>
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item label="处理器型号">{{ sysinfo.cpu.model || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="物理核 / 逻辑核">{{ cpuPhysical }} / {{ sysinfo.cpu.cores_logical || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="拓扑（插槽×核×线程）">{{ cpuTopology }}</el-descriptions-item>
                    <el-descriptions-item label="基准 / 最大频率">{{ cpuFreq }}</el-descriptions-item>
                    <el-descriptions-item label="L3 缓存 / NUMA 节点">{{ sysinfo.cpu.l3_cache || '—' }} / {{ sysinfo.cpu.numa_nodes || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="当前负载（1/5/15min）">{{ loadText }} <span v-if="loadPerCore !== '—'" class="muted">（{{ loadPerCore }}/核）</span></el-descriptions-item>
                  </el-descriptions>
                </el-col>
                <el-col :xs="24" :md="8">
                  <div class="sub-title">内存 / 交换分区</div>
                  <div class="mem-bar">
                    <div class="bar-label"><span>物理内存</span><span>{{ memUsed }} / {{ memTotal }} MB（{{ memPct }}%）</span></div>
                    <el-progress :percentage="memPctNum" :color="barColor" :stroke-width="10" />
                  </div>
                  <div class="mem-bar">
                    <div class="bar-label"><span>交换分区（Swap）</span><span>{{ swapText }}</span></div>
                    <el-progress :percentage="swapPctNum" :color="barColor" :stroke-width="10" />
                  </div>
                  <el-descriptions :column="1" border size="small" style="margin-top: 10px">
                    <el-descriptions-item label="可用内存">{{ fmtKB(memDetail.MemAvailable) }}</el-descriptions-item>
                    <el-descriptions-item label="Buffers / Cached">{{ fmtKB(memDetail.Buffers) }} / {{ fmtKB(memDetail.Cached) }}</el-descriptions-item>
                    <el-descriptions-item label="Active / Inactive / Slab">{{ fmtKB(memDetail.Active) }} / {{ fmtKB(memDetail.Inactive) }} / {{ fmtKB(memDetail.Slab) }}</el-descriptions-item>
                    <el-descriptions-item label="进程总数">{{ sysinfo.processes.total }}（运行 {{ sysinfo.processes.running }} · 僵尸 {{ sysinfo.processes.zombie }}）</el-descriptions-item>
                  </el-descriptions>
                </el-col>
              </el-row>
            </el-tab-pane>

            <!-- 磁盘与分区 -->
            <el-tab-pane :label="`磁盘与分区（${(sysinfo.disks || []).length}）`" name="disk">
              <div class="sub-title">块设备</div>
              <el-table :data="sysinfo.blocks" size="small" border>
                <el-table-column prop="name" label="设备" width="120" />
                <el-table-column prop="size" label="容量" width="100" />
                <el-table-column prop="type" label="类型" width="100" />
                <el-table-column label="文件系统" width="110">
                  <template #default="{ row }">{{ row.fstype || '—' }}</template>
                </el-table-column>
                <el-table-column label="挂载点" min-width="140">
                  <template #default="{ row }">{{ row.mount || '—' }}</template>
                </el-table-column>
                <el-table-column label="硬件型号" min-width="180" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.model || '—' }}</template>
                </el-table-column>
              </el-table>
              <div class="sub-title">挂载点 / 空间 / inode</div>
              <el-table :data="sysinfo.disks" size="small" border>
                <el-table-column prop="fs" label="文件系统" min-width="130" show-overflow-tooltip />
                <el-table-column prop="mount" label="挂载点" min-width="120" show-overflow-tooltip />
                <el-table-column prop="size" label="总容量" width="90" />
                <el-table-column prop="used" label="已用" width="90" />
                <el-table-column prop="avail" label="可用" width="90" />
                <el-table-column label="空间使用率" width="170">
                  <template #default="{ row }">
                    <el-progress v-if="row.pct != null" :percentage="row.pct" :color="barColor" :stroke-width="8" />
                    <span v-else>—</span>
                  </template>
                </el-table-column>
                <el-table-column label="inode 使用率" width="120">
                  <template #default="{ row }">{{ row.inode_pct != null ? row.inode_pct + '%' : '—' }}</template>
                </el-table-column>
              </el-table>
            </el-tab-pane>

            <!-- 网络 -->
            <el-tab-pane :label="`网络（${(sysinfo.network.interfaces || []).length} 网卡 · ${(sysinfo.network.listening || []).length} 监听）`" name="net">
              <el-row :gutter="16">
                <el-col :xs="24" :md="12">
                  <div class="sub-title">网卡与地址</div>
                  <el-table :data="sysinfo.network.interfaces" size="small" border>
                    <el-table-column prop="iface" label="网卡" width="110" />
                    <el-table-column label="状态" width="100">
                      <template #default="{ row }">
                        <el-tag v-if="row.state === 'UP'" size="small" type="success" effect="plain">UP</el-tag>
                        <el-tag v-else-if="row.state" size="small" type="info" effect="plain">{{ row.state }}</el-tag>
                        <span v-else>—</span>
                      </template>
                    </el-table-column>
                    <el-table-column label="IP 地址" min-width="200">
                      <template #default="{ row }">
                        <div v-for="a in row.addresses" :key="a" class="addr-line">{{ a }}</div>
                        <span v-if="!(row.addresses || []).length">—</span>
                      </template>
                    </el-table-column>
                  </el-table>
                  <div class="sub-title">连接状态（ss 摘要）</div>
                  <el-descriptions :column="3" border size="small">
                    <el-descriptions-item label="总连接">{{ tcp.total ?? '—' }}</el-descriptions-item>
                    <el-descriptions-item label="ESTAB">{{ tcp.estab ?? '—' }}</el-descriptions-item>
                    <el-descriptions-item label="TIME_WAIT">{{ tcp.timewait ?? '—' }}</el-descriptions-item>
                  </el-descriptions>
                  <div class="sub-title">路由表（默认网关：{{ sysinfo.network.gateway || '—' }}）</div>
                  <div class="mono-box">
                    <div v-for="r in sysinfo.network.routes" :key="r">{{ r }}</div>
                    <span v-if="!(sysinfo.network.routes || []).length">—</span>
                  </div>
                </el-col>
                <el-col :xs="24" :md="12">
                  <div class="sub-title">监听端口</div>
                  <el-table :data="sysinfo.network.listening" size="small" border max-height="420">
                    <el-table-column prop="proto" label="协议" width="80" />
                    <el-table-column prop="local" label="监听地址:端口" min-width="160" show-overflow-tooltip />
                    <el-table-column label="进程" min-width="140" show-overflow-tooltip>
                      <template #default="{ row }">{{ row.process || '—' }}</template>
                    </el-table-column>
                  </el-table>
                </el-col>
              </el-row>
            </el-tab-pane>

            <!-- 进程与会话 -->
            <el-tab-pane label="进程与会话" name="proc">
              <el-row :gutter="16">
                <el-col :xs="24" :md="12">
                  <div class="sub-title">登录会话（who）</div>
                  <div class="mono-box">
                    <div v-for="(u, i) in sysinfo.users" :key="i">{{ u }}</div>
                    <span v-if="!(sysinfo.users || []).length">当前无交互登录会话</span>
                  </div>
                </el-col>
                <el-col :xs="24" :md="12">
                  <div class="sub-title">Zabbix Agent</div>
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item label="运行状态">
                      <el-tag v-if="sysinfo.zabbix_agent.running" size="small" type="success" effect="dark">运行中</el-tag>
                      <el-tag v-else size="small" type="danger" effect="plain">未检测到进程</el-tag>
                    </el-descriptions-item>
                    <el-descriptions-item label="版本">{{ sysinfo.zabbix_agent.version || '—' }}</el-descriptions-item>
                    <el-descriptions-item v-for="(p, i) in sysinfo.zabbix_agent.processes" :key="i" :label="`进程 ${i + 1}`">{{ p }}</el-descriptions-item>
                  </el-descriptions>
                </el-col>
              </el-row>
            </el-tab-pane>
          </el-tabs>
        </template>
      </el-card>

      <!-- 异常告警：该资产名下的全部异常/恢复记录（与异常警告页同源，逐条展示） -->
      <el-card shadow="never" class="block">
        <template #header>
          <div class="row-between">
            <span>
              异常告警
              <el-tag v-if="anomalyTotal" size="small" type="danger" effect="plain">{{ anomalyTotal }} 条</el-tag>
              <el-tooltip content="与「异常警告」页面同源：该资产名下的全部异常/恢复记录，逐条展示" placement="top">
                <el-icon><QuestionFilled /></el-icon>
              </el-tooltip>
            </span>
            <el-button size="small" :loading="anomaliesLoading" @click="loadAnomalies">刷新</el-button>
          </div>
        </template>
        <el-table :data="anomalies" size="small" v-loading="anomaliesLoading">
          <el-table-column label="异常项" min-width="240" show-overflow-tooltip>
            <template #default="{ row }">{{ cnTrigger(row.trigger_name) }}</template>
          </el-table-column>
          <el-table-column label="级别" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="SEV_TAGS[row.severity] || 'info'">{{ SEV_LABELS[row.severity] || row.severity }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag v-if="row.status === 'abnormal'" type="danger" effect="dark">异常</el-tag>
              <el-tag v-else type="success" effect="plain">恢复</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="首次异常" width="160" :formatter="fmtTimeCol('first_seen_at')" />
          <el-table-column label="恢复时间" width="160" :formatter="fmtTimeCol('recovered_at')" />
          <el-table-column label="操作" width="70" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openAnomalyDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="anomalyTotal > anomalyPageSize" class="pager">
          <el-pagination
            small
            background
            :current-page="anomalyPage"
            :page-size="anomalyPageSize"
            :total="anomalyTotal"
            layout="total, prev, pager, next"
            @current-change="onAnomalyPage"
          />
        </div>
      </el-card>
      <AnomalyDetailDrawer ref="anomalyDrawer" />

      <!-- 实时巡检 -->
      <el-card shadow="never" class="block">
        <template #header>
          <div class="row-between">
            <span>实时巡检（类似 top）
              <el-tooltip content="免密 SSH 采集进程排行（3s 刷新）+ Zabbix 趋势图/指标卡（10s 刷新）" placement="top">
                <el-icon><QuestionFilled /></el-icon>
              </el-tooltip>
            </span>
            <div class="row-gap">
              <el-switch v-model="autoRefresh" active-text="自动刷新（巡检 3s · 图表 10s）" />
              <el-button size="small" :loading="inspecting" @click="runInspect">立即刷新</el-button>
            </div>
          </div>
        </template>

        <el-alert
          v-if="inspectError"
          :title="inspectError"
          type="error"
          :closable="false"
          show-icon
          class="inspect-alert"
        />

        <template v-if="snapshot">
          <el-row :gutter="12" class="summary">
            <el-col :span="6"><el-statistic title="负载 (1/5/15min)" :value="loadText" /></el-col>
            <el-col :span="6"><el-statistic title="内存已用" :value="memText" /></el-col>
            <el-col :span="6"><el-statistic title="根分区使用" :value="diskText" /></el-col>
            <el-col :span="6"><el-statistic title="运行时长" :value="uptimeText" /></el-col>
          </el-row>

          <el-tabs v-model="topTab" class="top-tabs">
            <el-tab-pane label="CPU 占用排行" name="cpu" />
            <el-tab-pane label="内存占用排行" name="mem" />
          </el-tabs>
          <el-table :data="topRows" size="small" max-height="360" v-loading="inspecting">
            <el-table-column prop="pid" label="PID" width="80" />
            <el-table-column prop="user" label="用户" width="110" />
            <el-table-column prop="cpu" label="CPU%" width="80" sortable>
              <template #default="{ row }">{{ fmtNum(row.cpu) }}</template>
            </el-table-column>
            <el-table-column prop="mem" label="内存%" width="80" sortable>
              <template #default="{ row }">{{ fmtNum(row.mem) }}</template>
            </el-table-column>
            <el-table-column prop="comm" label="进程" width="150" show-overflow-tooltip />
            <el-table-column prop="args" label="启动命令" min-width="260" show-overflow-tooltip />
          </el-table>
        </template>
        <el-empty v-else-if="!inspecting && !inspectError" description="正在采集…" :image-size="60" />
      </el-card>

      <!-- 纳管记录 -->
      <el-card v-if="asset?.extra?.provision" shadow="never" class="block">
        <template #header>纳管记录</template>
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="状态">
            {{ PROV_LABELS[asset.extra.provision.status] || asset.extra.provision.status }}
          </el-descriptions-item>
          <el-descriptions-item label="目标">
            {{ asset.extra.provision.ip }}:{{ asset.extra.provision.port }}（{{ asset.extra.provision.username }}）
          </el-descriptions-item>
          <el-descriptions-item label="开始 / 结束">
            {{ (asset.extra.provision.started_at || '').slice(0, 19) }} /
            {{ (asset.extra.provision.finished_at || '').slice(0, 19) || '—' }}
          </el-descriptions-item>
          <el-descriptions-item v-if="asset.extra.provision.error" label="错误" :span="3">
            <span class="err">{{ asset.extra.provision.error }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <el-input
          v-if="(asset.extra.provision.logs || []).length"
          type="textarea"
          :rows="6"
          readonly
          :model-value="(asset.extra.provision.logs || []).join('\n')"
          class="log-box"
        />
      </el-card>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { fetchAnomalies, fetchAsset, fetchAssetMetrics, fetchAssetSysinfo, inspectAsset } from '../api'
import { cnTrigger } from '../trigger-cn'
import { fmtTimeCol } from '../time'
import AnomalyDetailDrawer from './AnomalyDetailDrawer.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  assetId: { type: String, default: '' },
})
defineEmits(['update:modelValue'])

const asset = ref(null)
const metricsNote = ref('')
const metricsReal = ref(false)
const metricsLoading = ref(false)
const minutes = ref(60)
const chartEl = ref(null)
const metricsLatest = ref({})
let chart = null

const inspecting = ref(false)
const inspectError = ref('')
const snapshot = ref(null)
const autoRefresh = ref(true)
const topTab = ref('cpu')
let inspectTimer = null
let metricsTimer = null

// ===== 服务器详情（SSH 全景采集） =====
const sysinfo = ref(null)
const sysinfoLoading = ref(false)
const sysinfoError = ref('')
const detailTab = ref('overview')

const fmtKB = (kb) => {
  if (kb == null) return '—'
  if (kb >= 1024 * 1024) return `${(kb / 1024 / 1024).toFixed(2)} GB`
  return `${Math.round(kb / 1024).toLocaleString()} MB`
}
const barColor = (pct) => (pct >= 90 ? '#f56c6c' : pct >= 75 ? '#e6a23c' : '#67c23a')

const sysinfoCollectedAt = computed(() => {
  const t = sysinfo.value?.collected_at
  return t ? new Date(t).toLocaleTimeString('zh-CN', { hour12: false }) : ''
})
const tcp = computed(() => sysinfo.value?.network?.tcp || {})
const memDetail = computed(() => sysinfo.value?.memory?.detail_kb || {})
const cpuPhysical = computed(() => sysinfo.value?.cpu?.cores_physical ?? '—')
const cpuTopology = computed(() => {
  const c = sysinfo.value?.cpu || {}
  if (!c.sockets || !c.cores_per_socket) return '—'
  return `${c.sockets} 插槽 × ${c.cores_per_socket} 核 × ${c.threads_per_core || 1} 线程`
})
const cpuFreq = computed(() => {
  const c = sysinfo.value?.cpu || {}
  if (!c.mhz && !c.max_mhz) return '—'
  const ghz = (v) => (v ? `${(parseFloat(v) / 1000).toFixed(2)} GHz` : '—')
  return `${ghz(c.mhz)} / ${ghz(c.max_mhz)}`
})
const memPctNum = computed(() => Math.min(100, Number(sysinfo.value?.memory?.mem?.pct ?? 0)))
const memPct = computed(() => (sysinfo.value?.memory?.mem?.pct != null ? sysinfo.value.memory.mem.pct.toFixed(1) : '—'))
const memUsed = computed(() => sysinfo.value?.memory?.mem?.used_mb ?? '—')
const memTotal = computed(() => sysinfo.value?.memory?.mem?.total_mb ?? '—')
const swapPctNum = computed(() => Math.min(100, Number(sysinfo.value?.memory?.swap?.pct ?? 0)))
const swapText = computed(() => {
  const s = sysinfo.value?.memory?.swap
  if (!s || !s.total_mb) return '未启用'
  return `${s.used_mb} / ${s.total_mb} MB（${s.pct ?? 0}%）`
})
const loadPerCore = computed(() => {
  const l = snapshot.value?.load
  const cores = sysinfo.value?.cpu?.cores_logical
  if (!l || l.load1 == null || !cores) return '—'
  return (l.load1 / cores).toFixed(2)
})

async function loadSysinfo() {
  if (!props.assetId) return
  sysinfoLoading.value = true
  sysinfoError.value = ''
  try {
    const { data } = await fetchAssetSysinfo(props.assetId, {})
    sysinfo.value = data
  } catch (err) {
    sysinfoError.value = err.response?.data?.detail || '服务器详情采集失败'
  } finally {
    sysinfoLoading.value = false
  }
}

const title = computed(() => (asset.value ? `资产监控 · ${asset.value.id}（${asset.value.hostname}）` : '资产监控'))

const fmtNum = (v) => (v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(2))

const healthCards = computed(() => {
  const l = metricsLatest.value || {}
  const pct = (v) => (v == null ? null : Math.max(0, Math.min(100, v)))
  const color = (v) => (v == null ? '#dcdfe6' : v >= 90 ? '#f56c6c' : v >= 75 ? '#e6a23c' : '#67c23a')
  const items = [
    { key: 'cpu', label: 'CPU 使用率', unit: '%', tip: '整机 CPU 使用率（Zabbix system.cpu.util）' },
    { key: 'mem', label: '内存使用率', unit: '%', tip: '物理内存已用百分比' },
    { key: 'disk', label: '磁盘使用率', unit: '%', tip: '根分区已用百分比（Zabbix vfs.fs.size[/,pused]）' },
    { key: 'load1', label: '负载 load1', unit: '', tip: '1 分钟平均负载，超过 CPU 核数说明过载', raw: true },
  ]
  return items.map((it) => {
    const v = l[it.key]
    const show = v == null ? '—' : it.raw ? fmtNum(v) : `${fmtNum(v)}`
    const p = it.raw ? null : pct(v)
    return {
      label: it.label,
      tip: it.tip,
      unit: it.unit,
      value: show,
      pct: p ?? 0,
      color: color(v),
      cls: v != null && v >= 90 ? 'danger' : v != null && v >= 75 ? 'warn' : '',
    }
  })
})

const loadText = computed(() => {
  const ld = snapshot.value?.load
  if (!ld || ld.load1 == null) return '—'
  return `${fmtNum(ld.load1)} / ${fmtNum(ld.load5)} / ${fmtNum(ld.load15)}`
})

const memText = computed(() => {
  const m = snapshot.value?.mem
  if (!m || m.total_mb == null) return '—'
  return `${m.used_mb ?? '—'} / ${m.total_mb} MB`
})

const diskText = computed(() => (snapshot.value?.disk?.pct != null ? `${fmtNum(snapshot.value.disk.pct)}%` : '—'))

const uptimeText = computed(() => {
  const s = snapshot.value?.uptime_seconds
  if (!s) return '—'
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return d > 0 ? `${d} 天 ${h} 时` : h > 0 ? `${h} 时 ${m} 分` : `${m} 分`
})

const topRows = computed(() => {
  const s = snapshot.value
  if (!s) return []
  return (topTab.value === 'cpu' ? s.top_cpu : s.top_mem) || []
})

const PROV_LABELS = {
  registered: '已纳管',
  installed: '已装待注册',
  running: '安装中…',
  failed: '纳管失败',
  '': '已登记',
}

// ===== 异常告警（与异常警告页同源，按资产精确过滤） =====
const SEV_LABELS = {
  disaster: '灾难',
  high: '严重',
  average: '较严重',
  warning: '警告',
  information: '提示',
  not_classified: '未知',
}
const SEV_TAGS = {
  disaster: 'danger',
  high: 'danger',
  average: 'warning',
  warning: 'warning',
  information: 'info',
  not_classified: 'info',
}
const anomalies = ref([])
const anomalyTotal = ref(0)
const anomalyPage = ref(1)
const anomalyPageSize = ref(10)
const anomaliesLoading = ref(false)
const anomalyDrawer = ref(null)

async function loadAnomalies() {
  if (!props.assetId) return
  anomaliesLoading.value = true
  try {
    const { data } = await fetchAnomalies({
      asset_id: props.assetId,
      page: anomalyPage.value,
      page_size: anomalyPageSize.value,
    })
    anomalies.value = data.items || []
    anomalyTotal.value = data.total || 0
  } catch {
    /* 告警加载失败不影响监控详情主功能 */
  } finally {
    anomaliesLoading.value = false
  }
}

function onAnomalyPage(p) {
  anomalyPage.value = p
  loadAnomalies()
}

function openAnomalyDetail(row) {
  anomalyDrawer.value?.open(row.id)
}

watch(() => props.modelValue, (open) => {
  if (open && props.assetId) reset()
})

onBeforeUnmount(stopAuto)

function reset() {
  asset.value = null
  snapshot.value = null
  inspectError.value = ''
  topTab.value = 'cpu'
  sysinfo.value = null
  sysinfoError.value = ''
  detailTab.value = 'overview'
  anomalies.value = []
  anomalyTotal.value = 0
  anomalyPage.value = 1
  stopAuto()
}

function onOpened() {
  window.addEventListener('resize', resizeChart)
  loadAll()
}

function onClosed() {
  stopAuto()
  window.removeEventListener('resize', resizeChart)
  if (chart) {
    chart.dispose()
    chart = null
  }
}

function resizeChart() {
  chart && chart.resize()
}

async function loadAll() {
  try {
    const { data } = await fetchAsset(props.assetId)
    asset.value = data
    await loadMetrics()
  } finally {
    // 打开弹窗即开始免密巡检 + 自动刷新
    runInspect()
  }
  loadSysinfo()
  loadAnomalies()
}

async function loadMetrics() {
  metricsLoading.value = true
  try {
    const { data } = await fetchAssetMetrics(props.assetId, minutes.value)
    metricsReal.value = !!data.real
    metricsNote.value = data.note || ''
    metricsLatest.value = data.latest || {}
    renderChart(data.series || {})
  } finally {
    metricsLoading.value = false
  }
}

function renderChart(series) {
  if (!chartEl.value) return
  chart = chart || echarts.init(chartEl.value)
  const fmt = (arr, key) =>
    (arr || []).map((p) => [Number(p.t) * 1000, p[key] != null ? p[key] : p.v]).filter(([, v]) => v != null)

  const defs = [
    { name: 'CPU %', arr: series.cpu, key: 'cpu', color: '#409eff', pct: true },
    { name: '内存 %', arr: series.mem, key: 'mem', color: '#67c23a', pct: true },
    { name: '磁盘 %', arr: series.disk, key: 'disk', color: '#e6a23c', pct: true },
    { name: '负载', arr: series.load, key: 'load', color: '#f56c6c', pct: false },
  ]
  // 只展示有数据的系列，全部画进同一坐标系（负载走右侧独立轴）
  const hasData = defs.filter((d) => fmt(d.arr, d.key).length > 0)
  const grad = (c) => new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: `${c}33` },
    { offset: 1, color: `${c}05` },
  ])
  const mkSeries = (d) => ({
    name: d.name,
    type: 'line',
    showSymbol: false,
    smooth: true,
    lineWidth: 2,
    data: fmt(d.arr, d.key),
    itemStyle: { color: d.color },
    lineStyle: { color: d.color, width: 2 },
    areaStyle: d.pct ? { color: grad(d.color) } : undefined,
    yAxisIndex: d.pct ? 0 : 1,
  })
  const hasPct = hasData.some((d) => d.pct)
  chart.setOption(
    {
      animation: false,
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v) => (v == null ? '—' : Number(v).toFixed(2)),
      },
      legend: { top: 0, icon: 'roundRect', itemWidth: 14, itemHeight: 8 },
      grid: { left: 52, right: hasData.some((d) => !d.pct) ? 52 : 28, top: 36, bottom: 30 },
      xAxis: { type: 'time', axisLine: { lineStyle: { color: '#dcdfe6' } } },
      yAxis: [
        {
          type: 'value',
          name: hasPct ? '%' : '',
          min: 0,
          max: hasPct ? 100 : null,
          splitLine: { lineStyle: { color: '#f0f2f5' } },
        },
        {
          type: 'value',
          name: 'load',
          splitLine: { show: false },
          axisLabel: { color: '#909399' },
        },
      ],
      series: hasData.map(mkSeries),
    },
    true,
  )
  if (!hasData.length) {
    chart.setOption({ graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: '暂无监控数据', fill: '#909399', fontSize: 14 } }] })
  } else {
    chart.setOption({ graphic: [] })
  }
  chart.resize()
}

async function runInspect() {
  inspecting.value = true
  inspectError.value = ''
  try {
    // 免密巡检：后端自动使用内置巡检密钥与资产登记用户，无需前端输入
    const { data } = await inspectAsset(props.assetId, {})
    snapshot.value = data
  } catch (err) {
    inspectError.value = err.response?.data?.detail || '巡检失败'
  } finally {
    inspecting.value = false
  }
}

function stopAuto() {
  if (inspectTimer) {
    clearInterval(inspectTimer)
    inspectTimer = null
  }
  if (metricsTimer) {
    clearInterval(metricsTimer)
    metricsTimer = null
  }
}

watch(autoRefresh, (on) => {
  stopAuto()
  if (on) {
    inspectTimer = setInterval(() => {
      if (!inspecting.value) runInspect()
    }, 3000)
    // 趋势图与指标卡跟随自动刷新（Zabbix 历史查询，10s 一次）
    metricsTimer = setInterval(() => {
      if (!metricsLoading.value) loadMetrics()
    }, 10000)
  }
})
</script>

<style scoped>
.monitor {
  max-width: 1280px;
  margin: 0 auto;
}
.metric-card {
  border-radius: 8px;
}
.metric-card :deep(.el-card__body) {
  padding: 14px 18px;
}
.metric-label {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #909399;
  font-size: 13px;
}
.metric-value {
  font-size: 28px;
  font-weight: 600;
  margin: 4px 0 8px;
  font-variant-numeric: tabular-nums;
}
.metric-value .metric-unit {
  font-size: 13px;
  color: #909399;
  margin-left: 2px;
}
.metric-value.danger {
  color: #f56c6c;
}
.metric-value.warn {
  color: #e6a23c;
}
.block {
  margin-top: 12px;
  border-radius: 8px;
}
.chart {
  width: 100%;
  height: 380px;
}
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
.summary {
  margin-bottom: 8px;
}
.top-tabs {
  margin-top: 8px;
}
.inspect-alert {
  margin-bottom: 12px;
}
.sub-title {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin: 10px 0 6px;
}
.sub-title:first-child {
  margin-top: 0;
}
.mem-bar {
  margin-bottom: 10px;
}
.bar-label {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
.mono-box {
  font-family: monospace;
  font-size: 12px;
  background: #f5f7fa;
  border-radius: 4px;
  padding: 8px 10px;
  line-height: 1.7;
  word-break: break-all;
  max-height: 220px;
  overflow: auto;
}
.addr-line {
  line-height: 1.6;
}
.pager {
  margin-top: 8px;
  display: flex;
  justify-content: flex-end;
}
.err {
  color: #f56c6c;
}
.log-box {
  margin-top: 8px;
  font-family: monospace;
  font-size: 12px;
}
</style>
