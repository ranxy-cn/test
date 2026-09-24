<template>
  <div v-if="detail">
    <el-page-header @back="$router.push('/tickets')" :content="ticket.number" />
    <div style="margin-top: 16px" class="card-grid">
      <el-card shadow="never"><b>状态</b><div style="margin-top:8px"><el-tag :type="statusType(ticket.status)">{{ statusLabel(ticket.status) }}</el-tag></div></el-card>
      <el-card shadow="never">
        <b>事件级别</b>
        <el-tooltip placement="top" effect="dark">
          <template #content>
            策略引擎根据诊断结果给出的处置级别：<br />
            绿灯：低风险、命中白名单预案且前置条件满足，自动执行修复<br />
            黄灯：高风险操作（如数据库主备切换），挂起等待人工审批<br />
            红灯：未命中预案 / 前置失败 / 维护窗口 / 失败冷却，升级人工处理
          </template>
          <el-icon style="margin-left: 4px; vertical-align: middle; color: #86868b; cursor: help"><QuestionFilled /></el-icon>
        </el-tooltip>
        <div style="margin-top:8px"><i class="light-dot" :class="'light-' + (ticket.policy_light || 'red')"></i>{{ lightLabel(ticket.policy_light) }}</div>
      </el-card>
      <el-card shadow="never"><b>预案</b><div class="mono" style="margin-top:8px">{{ ticket.candidate_action_id || '无' }} @ {{ ticket.playbook_version || '-' }}</div></el-card>
      <el-card shadow="never"><b>负责人</b><div style="margin-top:8px">{{ ticket.owner }} / {{ ticket.asset_id }}</div></el-card>
    </div>

    <el-steps :active="stepIndex" finish-status="success" align-center style="margin: 12px 0 22px">
      <el-step title="发现" />
      <el-step title="立案" />
      <el-step title="排查" />
      <el-step title="策略" />
      <el-step title="执行" />
      <el-step title="验证" />
      <el-step title="闭环" />
    </el-steps>

    <el-row :gutter="16">
      <el-col :md="14">
        <el-card header="诊断与证据">
          <p v-if="ticket.diagnosis"><b>根因：</b>{{ ticket.diagnosis.root_cause }}</p>
          <p v-if="ticket.diagnosis"><b>置信度：</b>{{ ticket.diagnosis.confidence }}</p>
          <p v-if="ticket.diagnosis"><b>证据引用：</b>{{ (ticket.diagnosis.evidence_refs || []).join('、') }}</p>
          <p v-if="ticket.escalate_reason" class="status-red"><b>升级原因：</b>{{ ticket.escalate_reason }}</p>
          <el-tabs v-if="ticket.evidence">
            <el-tab-pane label="指标">
              <pre>{{ pretty(ticket.evidence.metrics) }}</pre>
            </el-tab-pane>
            <el-tab-pane label="事件">
              <pre>{{ pretty(ticket.evidence.events) }}</pre>
            </el-tab-pane>
            <el-tab-pane label="脱敏日志">
              <div class="evidence"><pre>{{ (ticket.evidence.logs?.lines || []).join('\n') }}</pre></div>
            </el-tab-pane>
            <el-tab-pane label="发版">
              <pre>{{ pretty(ticket.evidence.deploys) }}</pre>
            </el-tab-pane>
            <el-tab-pane label="依赖">
              <pre>{{ pretty(ticket.evidence.deps) }}</pre>
            </el-tab-pane>
            <el-tab-pane label="手册 RAG">
              <pre>{{ pretty(ticket.evidence.rag) }}</pre>
            </el-tab-pane>
            <el-tab-pane v-if="ticket.evidence.execution" label="执行">
              <pre>{{ pretty(ticket.evidence.execution) }}</pre>
            </el-tab-pane>
          </el-tabs>
        </el-card>

        <el-card header="时间线" style="margin-top: 16px">
          <el-timeline>
            <el-timeline-item v-for="ev in detail.events" :key="ev.id" :timestamp="fmtTime(ev.created_at)">
              <b>{{ ev.kind }}</b> · {{ ev.actor }}<br />{{ ev.message }}
            </el-timeline-item>
          </el-timeline>
        </el-card>
      </el-col>
      <el-col :md="10">
        <el-card v-if="ticket.status === 'pending_approval'" header="人工审批">
          <p>审批绑定：资产 <code>{{ ticket.asset_id }}</code></p>
          <p>作业版本 <code>{{ ticket.playbook_version }}</code></p>
          <p>参数摘要 <code class="mono">{{ ticket.params_digest }}</code></p>
          <el-input v-model="approver" placeholder="审批人" style="margin: 8px 0" />
          <el-input v-model="comment" type="textarea" placeholder="意见" />
          <el-space style="margin-top: 12px">
            <el-button v-perm="'tickets:operate'" type="primary" @click="doApprove">批准并执行</el-button>
            <el-button v-perm="'tickets:operate'" type="danger" @click="doReject">驳回升级</el-button>
          </el-space>
        </el-card>
        <el-card header="审计" style="margin-top: 16px">
          <el-table :data="detail.audit" size="small">
            <el-table-column prop="event_type" label="事件" width="110" />
            <el-table-column prop="actor" label="执行人" width="90" />
            <el-table-column prop="model_version" label="模型" />
            <el-table-column prop="policy_version" label="策略" />
          </el-table>
          <p class="mono" style="margin-top: 8px; color: #64748b">params_digest: {{ ticket.params_digest || '-' }}</p>
        </el-card>
        <el-card header="资源锁" style="margin-top: 16px">
          <p v-if="detail.lock">资产 {{ detail.lock.asset_id }} 由任务 {{ detail.lock.ticket_id }} 持有，过期 {{ fmtTime(detail.lock.expires_at) }}</p>
          <p v-else>当前无锁</p>
          <el-button
            v-if="ticket.status === 'pending_execution'"
            size="small"
            type="primary"
            style="margin-top: 8px"
            @click="doRetry"
          >重试执行（锁释放后）</el-button>
        </el-card>
        <el-card header="通知记录" style="margin-top: 16px">
          <el-table :data="detail.notifications || []" size="small">
            <el-table-column prop="kind" label="类型" width="130" />
            <el-table-column prop="channel" label="通道" width="90" />
            <el-table-column prop="title" label="标题" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { approveTicket, fetchTicket, rejectTicket, retryExecution } from '../api'
import { fmtTime } from '../time'

const props = defineProps({ id: { type: String, required: true } })
const detail = ref(null)
const approver = ref('王五')
const comment = ref('')
let timer

const ticket = computed(() => detail.value?.ticket || {})
const statusLabel = (s) => ({
  pending_analysis: '待分析', pending_approval: '待审批', pending_execution: '待执行',
  executing: '执行中', verifying: '验证中', recovered: '已恢复', escalated: '已升级', skipped: '已跳过',
}[s] || s)
const statusType = (s) => ({ recovered: 'success', escalated: 'danger', pending_approval: 'warning' }[s] || 'info')
const lightLabel = (l) => ({ green: '绿灯', yellow: '黄灯', red: '红灯' }[l] || (l || '未知'))
const pretty = (x) => JSON.stringify(x, null, 2)

const stepIndex = computed(() => {
  const s = ticket.value.status
  const map = {
    pending_analysis: 2, pending_approval: 3, pending_execution: 4,
    executing: 4, verifying: 5, recovered: 6, escalated: 6, skipped: 1,
  }
  return map[s] ?? 1
})

async function load() {
  const { data } = await fetchTicket(props.id)
  detail.value = data
}
async function doApprove() {
  await approveTicket(props.id, { approver: approver.value, comment: comment.value })
  ElMessage.success('已批准')
  await load()
}
async function doReject() {
  await rejectTicket(props.id, { approver: approver.value, comment: comment.value || '驳回' })
  ElMessage.warning('已驳回并升级')
  await load()
}
async function doRetry() {
  await retryExecution(props.id)
  ElMessage.success('已触发重试')
  await load()
}

onMounted(() => {
  load()
  timer = setInterval(() => {
    if (!['recovered', 'escalated', 'skipped'].includes(ticket.value.status)) load()
  }, 2000)
})
onUnmounted(() => clearInterval(timer))
</script>
