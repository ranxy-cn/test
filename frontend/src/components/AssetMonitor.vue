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

      <!-- 实时系统监控（类 macOS 活动监视器）：顶部选项卡切换指标视图，1 秒/次刷新 -->
      <el-card shadow="never" class="block">
        <template #header>
          <div class="row-between">
            <span>
              实时监控
              <el-tag v-if="liveNote" size="small" type="warning">{{ liveNote }}</el-tag>
              <el-tag v-else size="small" type="success">1 秒/次实时刷新</el-tag>
            </span>
            <span class="row-gap">
              <span class="live-src">
                DevOpsAgent 所在服务器<template v-if="live?.net_iface"> · 网口 {{ live.net_iface }}</template> · 自打开起实时记录
              </span>
              <el-button size="small" plain @click="openOverview">历史全揽</el-button>
            </span>
          </div>
        </template>

        <!-- 顶部选项卡：每个指标常驻显示实时值，点击切换大图 -->
        <div class="live-tabs">
          <button
            v-for="t in LIVE_TABS"
            :key="t.key"
            type="button"
            class="live-tab"
            :class="{ active: liveTab === t.key }"
            @click="switchLiveTab(t.key)"
          >
            <span class="live-tab-name">{{ t.name }}</span>
            <span class="live-tab-value" v-html="tabValue(t.key)"></span>
          </button>
        </div>

        <div ref="liveChartEl" class="chart live-chart" />
      </el-card>

      <!-- 历史全揽：落库数据（最长 2 个月）时间范围切换 + 五指标小图网格 -->
      <el-dialog
        v-model="ovVisible"
        title="历史全揽 · 系统资源落库数据"
        width="94%"
        top="4vh"
        append-to-body
        destroy-on-close
      >
        <div class="row-between ov-toolbar">
          <el-radio-group v-model="ovRange" size="small" @change="loadOverview">
            <el-radio-button v-for="r in OV_RANGES" :key="r.m" :value="r.m">{{ r.label }}</el-radio-button>
          </el-radio-group>
          <span class="live-src">
            落库粒度 5 秒 · {{ ovBucketNote }} · 保留 60 天（过期自动清理）
          </span>
        </div>

        <div class="ov-summary">
          <div v-for="s in ovSummary" :key="s.label" class="ov-kpi">
            <div class="ov-kpi-label">{{ s.label }}</div>
            <div class="ov-kpi-value" :style="{ color: s.color }">
              {{ s.value }}<span v-if="s.unit" class="ov-kpi-unit">{{ s.unit }}</span>
            </div>
            <div class="ov-kpi-sub">峰值 {{ s.max }}</div>
          </div>
        </div>

        <div class="ov-grid">
          <div v-for="c in OV_DEFS" :key="c.key" class="ov-cell">
            <div class="ov-cell-title">
              <span :style="{ color: c.color }">■</span>
              {{ c.name }}<span v-if="ovBucketNote" class="ov-cell-gran">（{{ ovBucketNote }}）</span>
            </div>
            <div :ref="(el) => setOvEl(c.key, el)" class="ov-chart" />
          </div>
        </div>
      </el-dialog>

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
                  <div class="sub-title">自研 Agent</div>
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item label="运行状态">
                      <el-tag v-if="sysinfo.agent?.running" size="small" type="success" effect="dark">运行中</el-tag>
                      <el-tag v-else size="small" type="danger" effect="plain">未检测到进程</el-tag>
                    </el-descriptions-item>
                    <el-descriptions-item label="版本">{{ sysinfo.agent?.version || '—' }}</el-descriptions-item>
                    <el-descriptions-item v-for="(p, i) in sysinfo.agent?.processes || []" :key="i" :label="`进程 ${i + 1}`">{{ p }}</el-descriptions-item>
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
              <el-tooltip content="免密 SSH 采集进程排行（3s 刷新）+ Agent 指标卡/趋势图（10s 刷新）" placement="top">
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
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { fetchAnomalies, fetchAsset, fetchAssetSysinfo, fetchSystemHistory, fetchSystemRealtime, inspectAsset } from '../api'
import { cnTrigger } from '../trigger-cn'
import { fmtTimeCol } from '../time'
import AnomalyDetailDrawer from './AnomalyDetailDrawer.vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  assetId: { type: String, default: '' },
})
defineEmits(['update:modelValue'])

const asset = ref(null)
// ===== 实时系统监控（类 macOS 活动监视器）=====
const LIVE_TABS = [
  { key: 'cpu', name: 'CPU', color: '#0a84ff', pct: true },
  { key: 'mem', name: '内存', color: '#34c759', pct: true },
  { key: 'disk', name: '磁盘', color: '#ff9500', pct: true },
  { key: 'load', name: '负载', color: '#ff3b30', pct: false },
  { key: 'net', name: '网络', color: '#5e5ce6', net: true },
]
const liveTab = ref('cpu')
const live = ref({})
const liveChartEl = ref(null)
let liveChart = null
let liveTimer = null
// 滑动窗口：最多 300 点（5 分钟），从弹窗打开起累积，不回填历史
const LIVE_MAX_POINTS = 300
const liveBufs = { cpu: [], mem: [], disk: [], load: [], netRx: [], netTx: [] }

const inspecting = ref(false)
const inspectError = ref('')
const snapshot = ref(null)
const autoRefresh = ref(true)
const topTab = ref('cpu')
let inspectTimer = null

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
const barColor = (pct) => (pct >= 90 ? '#ff3b30' : pct >= 75 ? '#ff9500' : '#34c759')

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
  const l = live.value || {}
  const pct = (v) => (v == null ? null : Math.max(0, Math.min(100, v)))
  const color = (v) => (v == null ? '#d2d2d7' : v >= 90 ? '#ff3b30' : v >= 75 ? '#ff9500' : '#34c759')
  const items = [
    { key: 'cpu', label: 'CPU 使用率', unit: '%', tip: '整机 CPU 使用率（/proc/stat 实时计算）' },
    { key: 'mem', label: '内存使用率', unit: '%', tip: '物理内存已用百分比（/proc/meminfo）' },
    { key: 'disk', label: '磁盘使用率', unit: '%', tip: '根分区已用百分比' },
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
  live.value = {}
  liveTab.value = 'cpu'
  for (const k of Object.keys(liveBufs)) liveBufs[k] = []
  stopAuto()
}

function onOpened() {
  window.addEventListener('resize', resizeChart)
  loadAll()
  // 等弹窗 DOM 布局完成后再初始化图表并启动 1 秒轮询
  nextTick(() => {
    startLive()
  })
}

function onClosed() {
  stopAuto()
  stopLive()
  window.removeEventListener('resize', resizeChart)
  if (liveChart) {
    liveChart.dispose()
    liveChart = null
  }
}

function resizeChart() {
  liveChart && liveChart.resize()
}

async function loadAll() {
  try {
    const { data } = await fetchAsset(props.assetId)
    asset.value = data
  } finally {
    // 打开弹窗即开始免密巡检 + 自动刷新
    runInspect()
  }
  loadSysinfo()
  loadAnomalies()
}

// ===== 实时监控：1 秒/次读 /proc 快照，滑动窗口渲染（自打开起累积，不回填历史） =====

function pushBuf(key, t, v) {
  if (v == null) return
  const arr = liveBufs[key]
  arr.push([t, v])
  if (arr.length > LIVE_MAX_POINTS) arr.splice(0, arr.length - LIVE_MAX_POINTS)
}

async function tickLive() {
  // 页面不可见时跳过本次轮询，避免无谓请求；恢复可见后下一秒自动续上
  if (document.hidden) return
  try {
    const { data } = await fetchSystemRealtime()
    live.value = data || {}
    if (!data?.supported) return
    const t = (data.ts || Math.floor(Date.now() / 1000)) * 1000
    pushBuf('cpu', t, data.cpu)
    pushBuf('mem', t, data.mem)
    pushBuf('disk', t, data.disk)
    pushBuf('load', t, data.load1)
    pushBuf('netRx', t, data.net_rx_bps)
    pushBuf('netTx', t, data.net_tx_bps)
    renderLiveChart()
  } catch {
    /* 单次拉取失败静默，下一秒重试 */
  }
}

function startLive() {
  stopLive()
  tickLive()
  liveTimer = setInterval(tickLive, 1000)
}

function stopLive() {
  if (liveTimer) {
    clearInterval(liveTimer)
    liveTimer = null
  }
}

function switchLiveTab(key) {
  liveTab.value = key
  renderLiveChart()
}

// 网速格式化：B/s → KB/s → MB/s → GB/s
function speedText(bps) {
  if (bps == null) return '—'
  if (bps >= 1024 ** 3) return `${(bps / 1024 ** 3).toFixed(2)} GB/s`
  if (bps >= 1024 ** 2) return `${(bps / 1024 ** 2).toFixed(2)} MB/s`
  if (bps >= 1024) return `${(bps / 1024).toFixed(1)} KB/s`
  return `${Math.round(bps)} B/s`
}

// 选项卡常驻实时值：不用单独 class（v-html 内容无 scoped 属性），单位用内联样式
function tabValue(key) {
  const l = live.value || {}
  const t = LIVE_TABS.find((x) => x.key === key)
  if (t?.net) {
    if (l.net_rx_bps == null && l.net_tx_bps == null) return '—'
    return (
      `<span style="color:#34c759">↓ ${speedText(l.net_rx_bps)}</span>` +
      ` · <span style="color:#ff9500">↑ ${speedText(l.net_tx_bps)}</span>`
    )
  }
  const v = key === 'load' ? l.load1 : l[key]
  if (v == null) return '—'
  if (key === 'load') return `<span style="color:${v >= 2 ? '#ff3b30' : '#1d1d1f'}">${v.toFixed(2)}</span>`
  const c = v >= 90 ? '#ff3b30' : v >= 75 ? '#ff9500' : '#1d1d1f'
  return `<span style="color:${c}">${v.toFixed(1)}</span><span style="font-size:11px;font-weight:400;color:#86868b;margin-left:1px">%</span>`
}

const liveNote = computed(() => live.value?.note || '')

// ===== 历史全揽：落库数据最长 2 个月；≤2h 原始粒度，更长窗口后端自动分桶（avg/max） =====
const OV_RANGES = [
  { m: 60, label: '1 小时' },
  { m: 1440, label: '24 小时' },
  { m: 10080, label: '7 天' },
  { m: 43200, label: '30 天' },
  { m: 86400, label: '60 天' },
]
const OV_DEFS = [
  { key: 'cpu', name: 'CPU 使用率', color: '#0a84ff', pct: true },
  { key: 'mem', name: '内存使用率', color: '#34c759', pct: true },
  { key: 'disk', name: '磁盘使用率', color: '#ff9500', pct: true },
  { key: 'load1', name: '系统负载', color: '#ff3b30', pct: false },
  { key: 'net', name: '网络流量', color: '#5e5ce6', net: true },
]
const ovVisible = ref(false)
const ovRange = ref(1440)
const ovLoading = ref(false)
const ovItems = ref([])
const ovBucket = ref(0)
const ovEls = {}
let ovCharts = {}

function setOvEl(key, el) {
  if (el) ovEls[key] = el
}

async function openOverview() {
  ovVisible.value = true
  await nextTick()
  loadOverview()
}

async function loadOverview() {
  if (ovLoading.value) return
  ovLoading.value = true
  try {
    const { data } = await fetchSystemHistory(ovRange.value)
    ovItems.value = data.items || []
    ovBucket.value = data.bucket_seconds || 0
    await nextTick()
    renderOverview()
  } catch {
    /* 拉取失败保留旧图 */
  } finally {
    ovLoading.value = false
  }
}

const ovBucketNote = computed(() => {
  if (!ovBucket.value) return '原始 5 秒粒度'
  if (ovBucket.value < 3600) return `按 ${ovBucket.value / 60} 分钟分桶聚合`
  return '按 1 小时分桶聚合'
})

// 顶部 KPI：窗口内均值 + 峰值一览
const ovSummary = computed(() => {
  const its = ovItems.value
  if (!its.length) return []
  const col = (f) => its.map((x) => x[f]).filter((v) => v != null)
  const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null)
  const mx = (xs) => (xs.length ? Math.max(...xs) : null)
  const f2 = (v) => (v == null ? '—' : v.toFixed(1))
  const cpuAvg = avg(col('cpu'))
  const cpuPeak = mx(col('cpu_max'))
  const memAvg = avg(col('mem'))
  const memPeak = mx(col('mem_max'))
  const diskNow = col('disk').slice(-1)[0]
  const netPeak = Math.max(mx(col('net_rx_bps_max')) || 0, mx(col('net_tx_bps_max')) || 0)
  return [
    { label: 'CPU 均值', value: f2(cpuAvg), max: `${f2(cpuPeak)}%`, unit: '%', color: '#0a84ff' },
    { label: '内存均值', value: f2(memAvg), max: `${f2(memPeak)}%`, unit: '%', color: '#34c759' },
    { label: '磁盘当前', value: f2(diskNow), max: `${f2(mx(col('disk_max')))}%`, unit: '%', color: '#ff9500' },
    { label: '负载均值', value: f2(avg(col('load1'))), max: `${f2(mx(col('load1_max')))}（load1 峰值）`, unit: '', color: '#ff3b30' },
    { label: '网络峰值', value: speedText(netPeak), max: '↓/↑ 合计', unit: '', color: '#5e5ce6' },
  ]
})

function disposeOvCharts() {
  for (const k of Object.keys(ovCharts)) {
    ovCharts[k]?.dispose()
  }
  ovCharts = {}
}

function renderOverview() {
  const its = ovItems.value
  const long = ovRange.value >= 10080
  const timeFmt = (v) =>
    long
      ? new Date(v).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
      : new Date(v).toLocaleTimeString('zh-CN', { hour12: false })
  const grad = (c) => new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: `${c}33` },
    { offset: 1, color: `${c}05` },
  ])
  for (const d of OV_DEFS) {
    const el = ovEls[d.key]
    if (!el) continue
    ovCharts[d.key] = ovCharts[d.key] || echarts.init(el)
    const chart = ovCharts[d.key]
    const mk = (name, field, color, dashed) => ({
      name,
      type: 'line',
      showSymbol: false,
      smooth: true,
      data: its.map((x) => [x.ts * 1000, x[field]]).filter(([, v]) => v != null),
      itemStyle: { color },
      lineStyle: { color, width: dashed ? 1 : 2, ...(dashed ? { type: 'dashed', opacity: 0.55 } : {}) },
      areaStyle: dashed ? undefined : { color: grad(color) },
    })
    let series
    if (d.net) {
      series = [mk('下载均值', 'net_rx_bps', '#34c759'), mk('上传均值', 'net_tx_bps', '#ff9500')]
    } else {
      series = [mk('均值', d.key, d.color), mk('峰值', `${d.key}_max`, d.color, true)]
    }
    const fmtVal = (v) => (d.net ? speedText(v) : d.pct ? `${v.toFixed(2)}%` : v.toFixed(2))
    chart.setOption(
      {
        animation: false,
        tooltip: {
          trigger: 'axis',
          confine: true,
          formatter: (params) => {
            const list = Array.isArray(params) ? params : [params]
            if (!list.length) return ''
            const rows = list
              .filter((p) => p.value?.[1] != null)
              .map((p) => `<div style="display:flex;justify-content:space-between;gap:14px"><span>${p.marker}${p.seriesName}</span><b>${fmtVal(p.value[1])}</b></div>`)
              .join('')
            return `<div style="font-weight:600;margin-bottom:4px">${timeFmt(list[0].value[0])}</div>${rows}`
          },
        },
        grid: { left: 56, right: 14, top: 24, bottom: 24 },
        xAxis: {
          type: 'time',
          axisLine: { lineStyle: { color: '#d2d2d7' } },
          axisLabel: { hideOverlap: true, formatter: (v) => (long ? fmtDay(v) : fmtTime(v)) },
        },
        yAxis: d.pct
          ? { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' }, splitLine: { lineStyle: { color: '#ececf0' } } }
          : {
              type: 'value',
              min: 0,
              scale: true,
              axisLabel: { formatter: (v) => (d.net && v >= 1024 ? speedText(v).replace(' ', '') : v) },
              splitLine: { lineStyle: { color: '#ececf0' } },
            },
        series,
      },
      true,
    )
    if (!its.length) {
      chart.setOption({
        graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: '该窗口暂无落库数据（自应用启动起记录）', fill: '#86868b', fontSize: 13 } }],
      })
    } else {
      chart.setOption({ graphic: [] })
    }
    chart.resize()
  }
}

function fmtDay(v) {
  const d = new Date(v)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function fmtTime(v) {
  const d = new Date(v)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

watch(ovVisible, (v) => {
  if (!v) disposeOvCharts()
})

// ===== 实时曲线渲染：当前选项卡对应序列，1 秒增量追加（animation 关闭保证流畅） =====

function renderLiveChart() {
  if (!liveChartEl.value) return
  liveChart = liveChart || echarts.init(liveChartEl.value)
  const t = LIVE_TABS.find((x) => x.key === liveTab.value) || LIVE_TABS[0]
  const grad = (c) => new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: `${c}33` },
    { offset: 1, color: `${c}05` },
  ])
  const mk = (name, buf, color, dashed) => ({
    name,
    type: 'line',
    showSymbol: false,
    smooth: true,
    data: buf,
    itemStyle: { color },
    lineStyle: { color, width: 2, ...(dashed ? { type: 'dashed', width: 1.5 } : {}) },
    areaStyle: dashed ? undefined : { color: grad(color) },
  })
  const series = t.net
    ? [mk('下载', liveBufs.netRx, '#34c759'), mk('上传', liveBufs.netTx, '#ff9500', true)]
    : [mk(t.name, liveBufs[t.key], t.color)]
  const fmtVal = (v) => (t.net ? speedText(v) : t.pct ? `${v.toFixed(2)}%` : v.toFixed(2))
  liveChart.setOption(
    {
      animation: false,
      tooltip: {
        trigger: 'axis',
        confine: true,
        formatter: (params) => {
          const list = Array.isArray(params) ? params : [params]
          if (!list.length) return ''
          const time = new Date(list[0].value[0]).toLocaleTimeString('zh-CN', { hour12: false })
          const rows = list
            .filter((p) => p.value?.[1] != null)
            .map((p) => `<div style="display:flex;justify-content:space-between;gap:16px"><span>${p.marker}${p.seriesName}</span><b>${fmtVal(p.value[1])}</b></div>`)
            .join('')
          return `<div style="font-weight:600;margin-bottom:4px">${time}</div>${rows}`
        },
      },
      grid: { left: 60, right: 20, top: 26, bottom: 28 },
      xAxis: {
        type: 'time',
        axisLine: { lineStyle: { color: '#d2d2d7' } },
        axisLabel: { hideOverlap: true },
      },
      yAxis: t.pct
        ? { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' }, splitLine: { lineStyle: { color: '#e8e8ed' } } }
        : {
            type: 'value',
            min: 0,
            scale: true,
            axisLabel: {
              // 网络视图纵轴压缩为 KB/M 短标签，其余直接数值
              formatter: (v) => (t.net && v >= 1024 ? speedText(v).replace(' ', '') : v),
            },
            splitLine: { lineStyle: { color: '#e8e8ed' } },
          },
      series,
    },
    true,
  )
  const hasData = t.net ? liveBufs.netRx.length + liveBufs.netTx.length : liveBufs[t.key].length
  liveChart.setOption({
    graphic: hasData
      ? []
      : [{ type: 'text', left: 'center', top: 'middle', style: { text: '等待数据…', fill: '#86868b', fontSize: 14 } }],
  })
  liveChart.resize()
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
}

watch(autoRefresh, (on) => {
  stopAuto()
  if (on) {
    inspectTimer = setInterval(() => {
      if (!inspecting.value) runInspect()
    }, 3000)
  }
})
</script>

<style scoped>
.monitor {
  max-width: 1280px;
  margin: 0 auto;
}
.metric-card {
  border-radius: var(--r-lg);
}
.metric-card :deep(.el-card__body) {
  padding: 14px 18px;
}
.metric-label {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--muted);
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
  color: var(--muted);
  margin-left: 2px;
}
.metric-value.danger {
  color: var(--danger);
}
.metric-value.warn {
  color: var(--warn);
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
  color: var(--ink);
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
  color: var(--muted);
  margin-bottom: 4px;
}
.mono-box {
  font-family: "SF Mono", ui-monospace, Menlo, monospace;
  font-size: 12px;
  background: var(--el-fill-color-light);
  border-radius: var(--r-sm);
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
  color: var(--danger);
}
.log-box {
  margin-top: 8px;
  font-family: monospace;
  font-size: 12px;
}
/* ===== 实时监控：顶部选项卡（类 macOS 活动监视器） ===== */
.live-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.live-tab {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 118px;
  padding: 8px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  background: var(--el-fill-color-lighter);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.live-tab:hover {
  border-color: var(--el-border-color);
  background: var(--el-fill-color);
}
.live-tab.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  box-shadow: 0 0 0 1px var(--el-color-primary) inset;
}
.live-tab-name {
  font-size: 12px;
  color: var(--muted);
}
.live-tab-value {
  font-size: 15px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.live-src {
  font-size: 12px;
  color: var(--muted);
}
.live-chart {
  height: 320px;
}
/* ===== 历史全揽弹窗 ===== */
.ov-toolbar {
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.ov-summary {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 10px;
  margin-bottom: 12px;
}
.ov-kpi {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 10px 14px;
  background: var(--el-fill-color-lighter);
}
.ov-kpi-label {
  font-size: 12px;
  color: var(--muted);
}
.ov-kpi-value {
  font-size: 22px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  margin: 2px 0;
}
.ov-kpi-unit {
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
  margin-left: 2px;
}
.ov-kpi-sub {
  font-size: 11px;
  color: var(--muted);
}
.ov-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.ov-cell {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 10px 12px 4px;
}
.ov-cell:last-child {
  grid-column: span 2;
}
.ov-cell-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 2px;
}
.ov-cell-gran {
  font-size: 11px;
  font-weight: 400;
  color: var(--muted);
}
.ov-chart {
  height: 190px;
}
@media (max-width: 900px) {
  .ov-summary {
    grid-template-columns: repeat(2, 1fr);
  }
  .ov-grid {
    grid-template-columns: 1fr;
  }
  .ov-cell:last-child {
    grid-column: auto;
  }
}
</style>
