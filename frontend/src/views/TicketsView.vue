<template>
  <div>
    <el-space wrap style="margin-bottom: 14px">
      <el-radio-group v-model="status" @change="load">
            <el-radio-button value="">全部</el-radio-button>
            <el-radio-button value="pending_analysis">待分析</el-radio-button>
            <el-radio-button value="pending_approval">待审批</el-radio-button>
            <el-radio-button value="executing">执行中</el-radio-button>
            <el-radio-button value="recovered">已恢复</el-radio-button>
            <el-radio-button value="escalated">已升级</el-radio-button>
      </el-radio-group>
      <el-button v-perm="'tickets:operate'" type="primary" @click="openDemo">模拟告警</el-button>
      <el-tooltip :disabled="stressEnabled" content="仅服务器开启 STRESS_TOOLS_ENABLED 后可用">
        <span>
          <el-button v-perm="'tools:operate'" type="warning" plain :disabled="!stressEnabled" @click="openStress">CPU 压测</el-button>
        </span>
      </el-tooltip>
      <el-button @click="load">刷新</el-button>
    </el-space>

    <el-table :data="items" stripe style="width: 100%" empty-text="暂无任务单，可点击「模拟告警」走一遍绿灯路径">
      <el-table-column prop="number" label="编号" width="170" />
      <el-table-column label="标题" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <span :title="row.title">{{ triggerLabel(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="资产" width="170" show-overflow-tooltip>
        <template #default="{ row }">
          <span :title="row.asset_id">{{ dictLabel('asset', row.asset_id) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="owner" label="负责人" width="90" />
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="策略" width="90">
        <template #default="{ row }">
          <span v-if="row.policy_light"><i class="light-dot" :class="'light-' + row.policy_light"></i>{{ lightLabel(row.policy_light) }}</span>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="预案" width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.candidate_action_id" :title="row.candidate_action_id">{{ dictLabel('action', row.candidate_action_id) }}</span>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="190" :formatter="fmtTimeCol('created_at')" />
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="go(row)">查看详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="demoVisible" title="模拟 Zabbix 告警" width="520px">
      <el-form label-width="100px">
        <el-form-item label="演示路径">
          <el-select v-model="demo.scenario" style="width: 100%">
            <el-option label="绿灯 · CPU 飙高自动滚动重启" value="green" />
            <el-option label="黄灯 · 复制延迟需审批" value="yellow" />
            <el-option label="红灯 · 未知故障升级" value="red" />
            <el-option label="验证失败 · 停止不循环" value="verify_fail" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="demoVisible = false">取消</el-button>
        <el-button type="primary" :loading="sending" @click="sendDemo">发送</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="stressVisible" title="真实 CPU 压测（让 Zabbix 采到真数据）" width="580px">
      <el-alert type="warning" :closable="false" show-icon style="margin-bottom: 12px"
        title="压测会把服务器全部 CPU 核打满，确认当前无人依赖服务器性能" />
      <el-descriptions :column="1" border size="small" style="margin-bottom: 12px">
        <el-descriptions-item label="原理">在 API 容器内按核数拉起死循环进程，Zabbix agent 采集到真实 system.cpu.util</el-descriptions-item>
        <el-descriptions-item label="告警">CPU 持续高于阈值 5 分钟 → 触发 High CPU utilization → Webhook 自动立案</el-descriptions-item>
        <el-descriptions-item label="预计">压测启动后约 6~9 分钟出现新任务单，并自动走绿灯修复</el-descriptions-item>
      </el-descriptions>
      <el-form label-width="90px">
        <el-form-item label="压测时长">
          <el-select v-model="stressDuration" style="width: 100%" :disabled="stressRunning">
            <el-option label="5 分钟（可能不足以覆盖 5 分钟均值窗口）" :value="300" />
            <el-option label="7 分钟（推荐）" :value="420" />
            <el-option label="10 分钟" :value="600" />
            <el-option label="15 分钟" :value="900" />
          </el-select>
        </el-form-item>
      </el-form>
      <div v-if="stressRunning">
        <el-tag type="danger">压测进行中 · {{ stressCores }} 核满载 · 剩余 {{ stressRemaining }} 秒</el-tag>
      </div>
      <template #footer>
        <el-button v-if="stressRunning" v-perm="'tools:operate'" type="danger" @click="doStopStress">停止压测</el-button>
        <el-button v-else v-perm="'tools:operate'" type="warning" :loading="stressStarting" @click="doStartStress">启动压测</el-button>
        <el-button @click="stressVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { fetchTickets, fetchStatus, fetchStressStatus, fetchDict, postWebhook, startCpuStress, stopCpuStress } from '../api'
import { fmtTimeCol } from '../time'

const router = useRouter()
const items = ref([])
const status = ref('')
const demoVisible = ref(false)
const sending = ref(false)
const demo = reactive({ scenario: 'green' })
let timer

// ===== 业务字典（code → 中文名映射）=====
const dictMaps = ref({ trigger: {}, asset: {}, action: {} })

function loadDict() {
  fetchDict()
    .then(({ data }) => {
      const maps = { trigger: {}, asset: {}, action: {} }
      for (const it of data) {
        if (maps[it.dict_type]) maps[it.dict_type][it.code] = it.label
      }
      dictMaps.value = maps
    })
    .catch(() => {})
}

function dictLabel(type, code) {
  return (dictMaps.value[type] || {})[code] || code
}

function triggerLabel(row) {
  return dictLabel('trigger', row.title || row.trigger_name)
}

// ===== CPU 压测状态 =====
const stressVisible = ref(false)
const stressEnabled = ref(false)
const stressRunning = ref(false)
const stressRemaining = ref(0)
const stressCores = ref(0)
const stressDuration = ref(420)
const stressStarting = ref(false)
let stressTimer

const presets = {
  green: { asset_id: 'ast-order-app-01', trigger_name: 'CPU usage > 85% for 5 minutes' },
  yellow: { asset_id: 'ast-order-db-01', trigger_name: 'MySQL replication lag too high' },
  red: { asset_id: 'ast-order-app-02', trigger_name: 'mystery native crash' },
  verify_fail: { asset_id: 'ast-order-job-01', trigger_name: 'CPU usage too high' },
}

const statusLabel = (s) => ({
  pending_analysis: '待分析',
  pending_approval: '待审批',
  pending_execution: '待执行',
  executing: '执行中',
  verifying: '验证中',
  recovered: '已恢复',
  escalated: '已升级',
  skipped: '已跳过',
}[s] || s)

const statusType = (s) => ({
  recovered: 'success',
  escalated: 'danger',
  pending_approval: 'warning',
  executing: 'primary',
  verifying: 'primary',
}[s] || 'info')

const lightLabel = (l) => ({ green: '绿灯', yellow: '黄灯', red: '红灯' }[l] || l)

async function load() {
  const { data } = await fetchTickets(status.value)
  items.value = data.items
}

function go(row) {
  router.push(`/tickets/${row.id}`)
}

function openDemo() {
  demoVisible.value = true
}

async function sendDemo() {
  sending.value = true
  try {
    const p = presets[demo.scenario]
    const { data } = await postWebhook({
      event_id: `ui-${Date.now()}`,
      asset_id: p.asset_id,
      trigger_name: p.trigger_name,
      demo_scenario: demo.scenario,
      message: p.trigger_name,
    })
    demoVisible.value = false
    if (data.skipped) {
      ElMessage.warning('维护窗口内已跳过')
    } else {
      ElMessage.success(`已立案 ${data.ticket.number}`)
      router.push(`/tickets/${data.ticket.id}`)
    }
  } finally {
    sending.value = false
  }
}

async function loadStressState() {
  try {
    const { data } = await fetchStressStatus()
    stressEnabled.value = !!data.enabled
    stressRunning.value = !!data.running
    stressRemaining.value = data.seconds_remaining || 0
    stressCores.value = data.cores || 0
  } catch {
    stressEnabled.value = false
  }
}

function openStress() {
  stressVisible.value = true
  loadStressState()
  clearInterval(stressTimer)
  stressTimer = setInterval(loadStressState, 5000)
}

async function doStartStress() {
  stressStarting.value = true
  try {
    const { data } = await startCpuStress(stressDuration.value)
    stressRunning.value = !!data.running
    stressRemaining.value = data.seconds_remaining || 0
    stressCores.value = data.cores || 0
    stressVisible.value = false
    clearInterval(stressTimer)
    ElMessage.success(`压测已启动（${stressCores.value} 核满载 ${stressDuration.value} 秒），约 6~9 分钟后关注任务单列表`)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '启动失败')
  } finally {
    stressStarting.value = false
  }
}

async function doStopStress() {
  try {
    await stopCpuStress()
    ElMessage.success('压测已停止')
  } finally {
    loadStressState()
  }
}

onMounted(async () => {
  load()
  loadDict()
  loadStressState()
  fetchStatus().then(({ data }) => {
    stressEnabled.value = !!data.integrations?.stress_tools_enabled
  }).catch(() => {})
  timer = setInterval(load, 4000)
})
onUnmounted(() => {
  clearInterval(timer)
  clearInterval(stressTimer)
})
</script>
