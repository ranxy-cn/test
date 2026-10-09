<template>
  <div>
    <!-- 告警恢复任务：告警触发自动生成，恢复自动关闭（默认只看未结） -->
    <el-card shadow="never" class="recovery-block">
      <div class="recovery-head">
        <div class="recovery-title">
          <span class="t">告警恢复任务</span>
          <span class="d">异常告警触发时自动生成；告警恢复后任务自动关闭并从列表消失</span>
        </div>
        <el-radio-group v-model="recStatus" size="small" @change="loadRecovery">
          <el-radio-button value="open">待处理</el-radio-button>
          <el-radio-button value="executing">执行中</el-radio-button>
          <el-radio-button value="done">已完成</el-radio-button>
          <el-radio-button value="cancelled">已取消</el-radio-button>
          <el-radio-button value="all">全部</el-radio-button>
        </el-radio-group>
      </div>
      <el-table :data="recTasks" size="small" border empty-text="当前没有恢复任务">
        <el-table-column label="优先级" width="90" sortable>
          <template #default="{ row }">
            <el-tag size="small" :type="prioType(row.priority)" effect="dark">{{ row.priority }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="级别" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="SEV_TAGS[row.severity] || 'info'">{{ SEV_LABELS[row.severity] || row.severity || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="告警规则" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">{{ cnRule(row.rule_key) || '—' }}</template>
        </el-table-column>
        <el-table-column prop="asset_id" label="资产" min-width="130" show-overflow-tooltip />
        <el-table-column label="恢复脚本" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.script_name">{{ row.script_name }}</span>
            <span v-else class="muted">纯人工</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="REC_TAGS[row.status] || 'info'">{{ REC_LABELS[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="执行" width="110">
          <template #default="{ row }">
            <span v-if="row.execute_ok === true" class="ok">成功 · {{ row.executed_by }}</span>
            <span v-else-if="row.execute_ok === false" class="fail">失败 · {{ row.executed_by }}</span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="165" :formatter="fmtTimeCol('created_at')" />
        <el-table-column v-if="canOperate" label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.script_id && ['open', 'executing'].includes(row.status)"
              link type="primary" size="small" @click="execTask(row)"
            >执行脚本</el-button>
            <el-button v-if="['open', 'executing'].includes(row.status)" link type="danger" size="small" @click="cancelTask(row)">取消</el-button>
            <el-button
              v-if="row.execute_output"
              link type="info" size="small" @click="showOutput(row)"
            >输出</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

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
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  fetchTickets,
  fetchDict,
  fetchMothers,
  fetchMotherGroups,
  listRecoveryTasks,
  executeRecoveryTask,
  cancelRecoveryTask,
} from '../api'
import { fmtTimeCol } from '../time'
import { auth } from '../auth'

// ===== 告警恢复任务 =====
const SEV_LABELS = { P0: 'P0 严重', P1: 'P1 重要', P2: 'P2 一般', P3: 'P3 提示' }
const SEV_TAGS = { P0: 'danger', P1: 'warning', P2: 'warning', P3: 'info' }
const REC_LABELS = { open: '待处理', executing: '执行中', done: '已完成', cancelled: '已取消' }
const REC_TAGS = { open: 'danger', executing: 'primary', done: 'success', cancelled: 'info' }
const RULE_CN = {
  cpu: 'CPU 使用率', mem: '内存使用率', load1: '1分钟负载', swap: 'Swap 使用率',
  inode: 'Inode 使用率', await_ms: '磁盘 IO 延迟', loss_pct: '网络丢包率', latency_ms: '网络延迟',
  bw_rx_pct: '入口带宽使用率', bw_tx_pct: '出口带宽使用率', tcp_tw: 'TIME_WAIT 连接数',
  tcp_conn_pct: 'TCP 连接数/上限', oom: 'OOM kill 事件', process: '关键进程消失', port: '关键端口探活失败',
}
const cnRule = (k) => RULE_CN[k] || k
const canOperate = auth.has('recovery:execute')

const recTasks = ref([])
const recStatus = ref('open')

const prioType = (p) => (p >= 80 ? 'danger' : p >= 60 ? 'warning' : 'info')

function loadRecovery(silent = false) {
  const params = { status: recStatus.value, page: 1, page_size: 50 }
  return listRecoveryTasks(params)
    .then(({ data }) => {
      recTasks.value = data.items || []
    })
    .catch(() => {
      if (!silent) recTasks.value = []
    })
}

async function execTask(row) {
  try {
    await ElMessageBox.confirm(
      `将在资产「${row.asset_id}」上以 root 执行恢复脚本「${row.script_name}」，确认执行？`,
      '执行确认',
      { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const { data } = await executeRecoveryTask(row.id)
    if (data.ok) ElMessage.success('脚本执行成功')
    else ElMessage.error('脚本执行失败，详情见输出')
    loadRecovery(true)
    if (data.output) showOutput({ ...row, execute_output: data.output, execute_ok: data.ok })
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '执行失败')
    loadRecovery(true)
  }
}

async function cancelTask(row) {
  try {
    await ElMessageBox.confirm(`确认取消该恢复任务？（告警恢复后不会再自动关闭）`, '取消确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await cancelRecoveryTask(row.id)
    ElMessage.success('已取消')
    loadRecovery(true)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '取消失败')
  }
}

function showOutput(row) {
  ElMessageBox.alert(
    `<pre style="margin:0;max-height:360px;overflow:auto;font-size:12px;white-space:pre-wrap;word-break:break-all;">${String(row.execute_output || '').replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]))}</pre>`,
    `执行输出 · ${row.script_name || '任务 #' + row.id}`,
    { dangerouslyUseHTMLString: true, confirmButtonText: '关闭' },
  )
}

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

async function load(silent = false) {
  // silent：4 秒自动刷新不展示 loading 遮罩（避免遮罩盖住页面底部 Dock）
  if (!silent) loading.value = true
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
    if (!silent) loading.value = false
  }
}

function go(row) {
  router.push(`/tickets/${row.id}`)
}

onMounted(() => {
  load()
  loadRecovery()
  loadDict()
  loadMothers()
  timer = setInterval(() => {
    load(true)
    loadRecovery(true)
  }, 4000)
})
onUnmounted(() => {
  clearInterval(timer)
})
</script>

<style scoped>
.recovery-block {
  margin-bottom: 16px;
}
.recovery-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}
.recovery-title .t {
  font-size: 15px;
  font-weight: 600;
  margin-right: 10px;
}
.recovery-title .d {
  font-size: 12px;
  color: var(--muted);
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.ok {
  color: var(--el-color-success);
  font-size: 12px;
}
.fail {
  color: var(--el-color-danger);
  font-size: 12px;
}
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
  color: var(--muted);
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
