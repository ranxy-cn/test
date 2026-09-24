<template>
  <div>
    <!-- 资产台账联动：母机 → 分组 按钮切换筛选 -->
    <div class="filter-block">
      <span class="filter-label">母机</span>
      <el-radio-group v-model="motherId" size="default" @change="onMotherChange">
        <el-radio-button value="">全部母机</el-radio-button>
        <el-radio-button v-for="m in mothers" :key="m.id" :value="m.id">{{ m.hostname }}</el-radio-button>
      </el-radio-group>
    </div>
    <div class="filter-block">
      <span class="filter-label">分组</span>
      <el-radio-group v-model="group" size="default" @change="search">
        <el-radio-button value="">全部分组</el-radio-button>
        <el-radio-button v-for="g in groups" :key="g.name" :value="g.name">
          {{ g.name || '未分组' }}<span class="opt-count">({{ g.total }})</span>
        </el-radio-button>
      </el-radio-group>
    </div>

    <!-- 筛选框 + 操作区 -->
    <div class="filter-block row">
      <el-select v-model="status" placeholder="全部状态" clearable style="width: 140px" @change="search">
        <el-option v-for="(label, val) in STATUS_OPTIONS" :key="val" :label="label" :value="val" />
      </el-select>
      <el-input
        v-model="keyword"
        placeholder="搜索编号 / 标题 / 资产"
        clearable
        style="width: 240px"
        @keyup.enter="search"
        @clear="search"
      />
      <el-button type="primary" plain @click="search">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
      <span class="spacer"></span>
      <el-tooltip :disabled="stressEnabled" content="仅服务器开启 STRESS_TOOLS_ENABLED 后可用">
        <span>
          <el-button v-perm="'tools:operate'" type="warning" plain :disabled="!stressEnabled" @click="openStress">CPU 压测</el-button>
        </span>
      </el-tooltip>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table :data="items" stripe style="width: 100%" empty-text="暂无任务单" v-loading="loading">
      <el-table-column prop="number" label="编号" width="170" />
      <el-table-column label="标题" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">
          <span :title="row.title">{{ triggerLabel(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="母机" width="130" show-overflow-tooltip>
        <template #default="{ row }">{{ row.asset_info?.mother_hostname || '-' }}</template>
      </el-table-column>
      <el-table-column label="分组" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.asset_info?.group" size="small" effect="plain">{{ row.asset_info.group }}</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="子机" width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <template v-if="row.asset_info?.hostname">{{ row.asset_info.hostname }}</template>
          <span v-else :title="row.asset_id">{{ row.asset_id }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="owner" label="负责人" width="90" />
      <el-table-column label="状态" width="110">
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
      <el-table-column prop="created_at" label="创建时间" width="170" :formatter="fmtTimeCol('created_at')" />
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="go(row)">查看详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="search"
      />
    </div>

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
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  fetchTickets,
  fetchStatus,
  fetchStressStatus,
  fetchDict,
  fetchMothers,
  fetchMotherGroups,
  startCpuStress,
  stopCpuStress,
} from '../api'
import { fmtTimeCol } from '../time'

const STATUS_OPTIONS = {
  pending_analysis: '待分析',
  pending_approval: '待审批',
  pending_execution: '待执行',
  executing: '执行中',
  verifying: '验证中',
  recovered: '已恢复',
  escalated: '已升级',
  skipped: '已跳过',
}

const router = useRouter()
const items = ref([])
const loading = ref(false)
// 资产台账联动筛选
const mothers = ref([])
const groups = ref([])
const motherId = ref('')
const group = ref('')
// 筛选与分页
const status = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
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

function triggerLabel(row) {
  return (dictMaps.value.trigger || {})[row.title || row.trigger_name] || row.title || row.trigger_name
}

// ===== 资产台账联动 =====
async function loadMothers() {
  try {
    const { data } = await fetchMothers()
    mothers.value = data.items || []
  } catch {
    mothers.value = []
  }
}

async function loadGroups() {
  if (!motherId.value) {
    groups.value = []
    return
  }
  try {
    const { data } = await fetchMotherGroups(motherId.value)
    groups.value = data.items || []
  } catch {
    groups.value = []
  }
}

function onMotherChange() {
  group.value = ''
  loadGroups()
  search()
}

function search() {
  page.value = 1
  load()
}

function resetFilters() {
  motherId.value = ''
  group.value = ''
  status.value = ''
  keyword.value = ''
  groups.value = []
  search()
}

const statusLabel = (s) => STATUS_OPTIONS[s] || s

const statusType = (s) => ({
  recovered: 'success',
  escalated: 'danger',
  pending_approval: 'warning',
  executing: 'primary',
  verifying: 'primary',
}[s] || 'info')

const lightLabel = (l) => ({ green: '绿灯', yellow: '黄灯', red: '红灯' }[l] || l)

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (motherId.value) params.mother_id = motherId.value
    if (group.value) params.group = group.value
    if (status.value) params.status = status.value
    if (keyword.value && keyword.value.trim()) params.keyword = keyword.value.trim()
    const { data } = await fetchTickets(params)
    items.value = data.items
    total.value = data.total ?? data.items.length
  } finally {
    loading.value = false
  }
}

function go(row) {
  router.push(`/tickets/${row.id}`)
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

onMounted(() => {
  load()
  loadDict()
  loadMothers()
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

<style scoped>
.filter-block {
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.filter-block.row {
  gap: 10px;
}
.filter-label {
  font-size: 13px;
  color: #606266;
  width: 36px;
  text-align: right;
  flex-shrink: 0;
}
.opt-count {
  font-size: 12px;
  opacity: 0.65;
  margin-left: 2px;
}
.spacer {
  flex: 1;
}
.pager {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
}
</style>
