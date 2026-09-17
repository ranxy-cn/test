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
      <el-button type="primary" @click="openDemo">模拟告警</el-button>
      <el-button @click="load">刷新</el-button>
    </el-space>

    <el-table :data="items" stripe @row-click="go" style="width: 100%" empty-text="暂无任务单，可点击「模拟告警」走一遍绿灯路径">
      <el-table-column prop="number" label="编号" width="170" />
      <el-table-column prop="title" label="标题" min-width="220" />
      <el-table-column prop="asset_id" label="资产" width="170" />
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
      <el-table-column prop="candidate_action_id" label="预案" width="190" />
      <el-table-column prop="created_at" label="创建时间" width="190" />
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
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { fetchTickets, postWebhook } from '../api'

const router = useRouter()
const items = ref([])
const status = ref('')
const demoVisible = ref(false)
const sending = ref(false)
const demo = reactive({ scenario: 'green' })
let timer

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

onMounted(() => {
  load()
  timer = setInterval(load, 4000)
})
onUnmounted(() => clearInterval(timer))
</script>
