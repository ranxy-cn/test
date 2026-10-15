<template>
  <el-drawer v-model="visible" size="64%" :title="`异常详情 · ${detail?.event_id || ''}`" destroy-on-close :z-index="93" @closed="stopPoll">
    <div v-if="detail" class="detail">
      <!-- 基本信息 -->
      <section class="block">
        <h4 class="block-title">告警信息</h4>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="异常项">{{ cnTrigger(detail.trigger_name) }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag v-if="detail.status === 'abnormal'" type="danger" effect="dark">异常</el-tag>
            <el-tag v-else type="success" effect="plain">恢复</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="级别">
            <el-tag size="small" :type="SEV_TAGS[detail.severity] || 'info'">{{ SEV_LABELS[detail.severity] || detail.severity }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="事件 ID">{{ detail.event_id }}</el-descriptions-item>
          <el-descriptions-item label="主机">{{ detail.hostname || detail.host || '—' }}</el-descriptions-item>
          <el-descriptions-item label="IP">{{ detail.ip || '—' }}</el-descriptions-item>
          <el-descriptions-item label="首次异常">{{ fmtTime(detail.first_seen_at) }}</el-descriptions-item>
          <el-descriptions-item label="恢复时间">{{ detail.recovered_at ? fmtTime(detail.recovered_at) : '—' }}</el-descriptions-item>
          <el-descriptions-item label="告警消息" :span="2">{{ detail.message || '—' }}</el-descriptions-item>
        </el-descriptions>
      </section>

      <!-- 告警上下文（触发时关键数据） -->
      <section v-if="hasCtx" class="block">
        <h4 class="block-title">告警上下文</h4>
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item v-for="item in ctxItems" :key="item.label" :label="item.label">{{ item.value }}</el-descriptions-item>
        </el-descriptions>
        <template v-if="(payload.oom_detail || []).length">
          <h5 class="sub-title">OOM 被杀进程明细</h5>
          <el-table :data="payload.oom_detail" size="small" border max-height="220">
            <el-table-column label="#" type="index" width="50" />
            <el-table-column label="明细（时间 / PID / 进程 / 内存 / 评分）">
              <template #default="{ row }"><span class="oom-line">{{ row }}</span></template>
            </el-table-column>
          </el-table>
        </template>
        <template v-if="(payload.top_processes || []).length">
          <h5 class="sub-title">触发时进程归因（CPU/内存 TOP，定位元凶进程）</h5>
          <el-table :data="payload.top_processes" size="small" border max-height="240">
            <el-table-column prop="pid" label="PID" width="90" />
            <el-table-column prop="comm" label="进程名" min-width="160" show-overflow-tooltip />
            <el-table-column label="CPU%" width="90">
              <template #default="{ row }">
                <span :class="{ 'p-hot': row.cpu >= 50 }">{{ row.cpu }}</span>
              </template>
            </el-table-column>
            <el-table-column label="内存%" width="90">
              <template #default="{ row }">
                <span :class="{ 'p-hot': row.mem >= 50 }">{{ row.mem }}</span>
              </template>
            </el-table-column>
          </el-table>
        </template>
        <template v-if="payload.net_rate">
          <h5 class="sub-title">触发时网络带宽（异常波动上下文）</h5>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="入口速率">{{ netRateRx }}</el-descriptions-item>
            <el-descriptions-item label="出口速率">{{ netRateTx }}</el-descriptions-item>
            <el-descriptions-item v-if="payload.net_rate.bw_rx_pct != null" label="入口带宽占比">{{ payload.net_rate.bw_rx_pct }}%</el-descriptions-item>
            <el-descriptions-item v-if="payload.net_rate.bw_tx_pct != null" label="出口带宽占比">{{ payload.net_rate.bw_tx_pct }}%</el-descriptions-item>
          </el-descriptions>
        </template>
        <template v-if="(payload.window_series || []).length">
          <h5 class="sub-title">窗口内样本序列（时间 → 数值，共 {{ payload.window_series.length }} 点）</h5>
          <pre class="snap-plain">{{ seriesText }}</pre>
        </template>
        <el-collapse v-if="ctxExtraKeys.length" class="snap-extra">
          <el-collapse-item name="extra">
            <template #title>
              <span class="snap-collapse-title">其他上下文字段（{{ ctxExtraKeys.length }}）</span>
            </template>
            <pre class="snap-plain">{{ ctxExtraText }}</pre>
          </el-collapse-item>
        </el-collapse>
      </section>

      <!-- AI 日志分析：告警产生时自动生成，打开详情即可见；支持随时重新生成 -->
      <section class="block">
        <h4 class="block-title">AI 日志分析</h4>
        <div class="ai-head">
          <el-tag :type="AI_TAGS[detail.ai_status] || 'info'" size="small">{{ AI_LABELS[detail.ai_status] || '未分析' }}</el-tag>
          <el-button
            v-if="canFeedback"
            size="small" type="primary" plain
            :disabled="detail.ai_status === 'pending' || detail.ai_status === 'running'"
            @click="triggerAi"
          >
            {{ ['none', 'skipped', 'failed'].includes(detail.ai_status) ? 'AI 分析' : '重新生成' }}
          </el-button>
          <el-button v-if="aiAnalysis" link type="primary" size="small" @click="goAiAnalyses">分析列表 →</el-button>
        </div>
        <el-alert
          v-if="detail.ai_status === 'running' || detail.ai_status === 'pending'"
          type="info" :closable="false" show-icon title="AI 正在分析该告警的日志上下文，完成后本页自动展示…"
        />
        <el-alert
          v-else-if="detail.ai_status === 'failed'"
          type="error" :closable="false" show-icon :title="'分析失败：' + (aiAnalysis?.error || '可点击重新生成重试')"
        />
        <el-alert
          v-else-if="!aiAnalysis"
          type="info" :closable="false" show-icon title="尚未生成分析（告警联动未启用或未命中），可点击上方按钮手动生成"
        />
        <template v-else>
          <el-alert
            v-if="aiAnalysis.blocked"
            type="warning" :closable="false" show-icon class="ai-blocked"
            title="AI 响应包含疑似服务器操作指令，相关内容已自动屏蔽，仅保留安全建议"
          />
          <div class="ai-meta">
            <el-tag v-if="aiAnalysis.severity" size="small" :type="SEV_TAGS[aiAnalysis.severity] || 'info'">{{ SEV_LABELS[aiAnalysis.severity] || aiAnalysis.severity }}</el-tag>
            <span class="ai-meta-item">置信度 {{ Math.round((aiAnalysis.confidence || 0) * 100) }}%</span>
            <span class="ai-meta-item">{{ aiAnalysis.model }}</span>
            <span v-if="aiAnalysis.latency_ms" class="ai-meta-item">耗时 {{ (aiAnalysis.latency_ms / 1000).toFixed(1) }}s</span>
            <span class="ai-meta-item">{{ fmtTime(aiAnalysis.created_at) }}</span>
            <el-tag v-if="aiAnalysis.handled_by" size="small" type="success" effect="plain">已处理：{{ aiAnalysis.handled_by }}</el-tag>
          </div>
          <div class="ai-summary">{{ aiAnalysis.summary || '—' }}</div>
          <template v-if="aiAnalysis.diagnosis">
            <h5 class="sub-title">诊断</h5>
            <p class="ai-para">{{ aiAnalysis.diagnosis }}</p>
          </template>
          <template v-if="(aiAnalysis.causes || []).length">
            <h5 class="sub-title">可能原因</h5>
            <ul class="ai-causes">
              <li v-for="(c, i) in aiAnalysis.causes" :key="i">{{ c }}</li>
            </ul>
          </template>
          <template v-if="(aiAnalysis.solutions || []).length">
            <h5 class="sub-title">解决方案建议（{{ aiAnalysis.solutions.length }}）</h5>
            <div v-for="(s, i) in aiAnalysis.solutions" :key="i" class="ai-sol">
              <div class="ai-sol-head">
                <span class="ai-sol-title">{{ s.title }}</span>
                <span v-if="s.tag" class="ai-sol-tag">{{ s.tag }}</span>
                <span v-if="s.severity" class="ai-sol-sev" :class="'sev-' + s.severity">{{ s.severity }}</span>
              </div>
              <div class="ai-sol-detail">{{ s.detail }}</div>
            </div>
          </template>
          <el-collapse class="ai-extra">
            <el-collapse-item name="handle">
              <template #title>
                <span class="snap-collapse-title">人工处理记录{{ aiAnalysis.handled_by ? `（${aiAnalysis.handled_by}）` : '' }}</span>
              </template>
              <div v-if="aiAnalysis.handled_by" class="ai-handle-view">
                <div>{{ aiAnalysis.handled_note || '（无备注）' }}</div>
                <div class="ai-meta-item">{{ aiAnalysis.handled_by }} · {{ fmtTime(aiAnalysis.handled_at) }}</div>
              </div>
              <div v-if="canFeedback" class="ai-handle-form">
                <el-input v-model="aiNote" type="textarea" :rows="2" maxlength="2000" show-word-limit placeholder="记录处理方式 / 结论（选填）" />
                <el-button size="small" type="primary" :loading="aiHandling" @click="submitFeedback">提交处理记录</el-button>
              </div>
            </el-collapse-item>
          </el-collapse>
          <el-collapse v-if="aiAnalysis.raw_response" class="ai-extra">
            <el-collapse-item name="raw">
              <template #title><span class="snap-collapse-title">AI 原始响应（留痕）</span></template>
              <pre class="snap-plain">{{ aiAnalysis.raw_response }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </section>

      <!-- 异常时刻快照（进程详情） -->
      <section class="block">
        <h4 class="block-title">异常时刻快照（进程详情）</h4>
        <div class="diag-head">
          <el-button size="small" type="primary" plain :loading="detail.diag_status === 'running'" @click="snapshot">
            {{ detail.diag_status === 'done' ? '重新采集' : '立即采集' }}
          </el-button>
          <span v-if="detail.diag_status === 'done' && snap.collected_at" class="snap-time">
            采集时间：{{ fmtTime(snap.collected_at) }}
          </span>
        </div>
        <el-alert
          v-if="detail.diag_status === 'running'"
          type="info" :closable="false" show-icon title="正在 SSH 上机采集异常时刻快照，约 10~30 秒，自动刷新…"
        />
        <el-alert
          v-else-if="detail.diag_status === 'failed'"
          type="error" :closable="false" show-icon :title="'采集失败：' + (detail.diag_error || '未知原因')"
        />
        <el-alert
          v-else-if="detail.diag_status !== 'done'"
          type="info" :closable="false" show-icon title="异常首次上报时自动采集当时的进程快照；也可手动触发"
        />
        <template v-else>
          <el-alert
            v-if="snap.target" type="success" :closable="false" show-icon class="diag-target"
            :title="`采集自 ${snap.target.asset || ''}（${snap.target.username}@${snap.target.ip}）`"
          />

          <!-- 系统状态摘要 -->
          <el-descriptions :column="4" border size="small" class="snap-system">
            <el-descriptions-item label="负载（1/5/15 分钟）">
              <span :class="loadClass">{{ sysText }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="运行时长">{{ snap.system?.uptime_text || '—' }}</el-descriptions-item>
            <el-descriptions-item label="内存（已用/总量）">{{ memText }}</el-descriptions-item>
            <el-descriptions-item label="进程（总/运行/僵尸）">
              {{ snap.processes?.total ?? '—' }} / <span class="p-run">{{ snap.processes?.running ?? '—' }}</span> /
              <span :class="{ 'p-zombie': (snap.processes?.zombie || 0) > 0 }">{{ snap.processes?.zombie ?? '—' }}</span>
            </el-descriptions-item>
          </el-descriptions>

          <!-- CPU 占用 TOP -->
          <h5 class="sub-title">CPU 占用 TOP{{ snap.top_cpu?.length || 0 }} 进程</h5>
          <el-table :data="snap.top_cpu || []" size="small" border max-height="320">
            <el-table-column prop="pid" label="PID" width="80" />
            <el-table-column prop="ppid" label="父PID" width="70" />
            <el-table-column prop="user" label="用户" width="100" show-overflow-tooltip />
            <el-table-column label="CPU%" width="80">
              <template #default="{ row }">
                <span :class="{ 'p-hot': row.cpu >= 50 }">{{ row.cpu }}</span>
              </template>
            </el-table-column>
            <el-table-column label="内存%" width="80">
              <template #default="{ row }">{{ row.mem }}</template>
            </el-table-column>
            <el-table-column prop="etime" label="运行时长" width="100" />
            <el-table-column prop="args" label="命令" min-width="260" show-overflow-tooltip />
          </el-table>

          <!-- 内存占用 TOP -->
          <h5 class="sub-title">内存占用 TOP{{ snap.top_mem?.length || 0 }} 进程</h5>
          <el-table :data="snap.top_mem || []" size="small" border max-height="320">
            <el-table-column prop="pid" label="PID" width="80" />
            <el-table-column prop="ppid" label="父PID" width="70" />
            <el-table-column prop="user" label="用户" width="100" show-overflow-tooltip />
            <el-table-column label="内存%" width="80">
              <template #default="{ row }">
                <span :class="{ 'p-hot': row.mem >= 50 }">{{ row.mem }}</span>
              </template>
            </el-table-column>
            <el-table-column label="CPU%" width="80">
              <template #default="{ row }">{{ row.cpu }}</template>
            </el-table-column>
            <el-table-column prop="etime" label="运行时长" width="100" />
            <el-table-column prop="args" label="命令" min-width="260" show-overflow-tooltip />
          </el-table>

          <!-- D 状态 / 监听端口 / 登录会话 -->
          <el-collapse class="snap-extra">
            <el-collapse-item v-if="(snap.d_state || []).length" name="dstate">
              <template #title>
                <span class="snap-collapse-title">D 状态进程（IO 等待，{{ snap.d_state.length }} 个）</span>
              </template>
              <el-table :data="snap.d_state" size="small" border max-height="240">
                <el-table-column prop="pid" label="PID" width="80" />
                <el-table-column prop="stat" label="状态" width="70" />
                <el-table-column prop="user" label="用户" width="100" show-overflow-tooltip />
                <el-table-column prop="etime" label="运行时长" width="100" />
                <el-table-column prop="args" label="命令" min-width="260" show-overflow-tooltip />
              </el-table>
            </el-collapse-item>
            <el-collapse-item name="listen">
              <template #title>
                <span class="snap-collapse-title">监听端口（{{ (snap.listening || []).length }}）</span>
              </template>
              <el-table :data="snap.listening || []" size="small" border max-height="240">
                <el-table-column prop="proto" label="协议" width="80" />
                <el-table-column prop="local" label="监听地址" min-width="160" />
                <el-table-column prop="process" label="进程" min-width="180" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.process || '—' }}</template>
                </el-table-column>
              </el-table>
            </el-collapse-item>
            <el-collapse-item name="users">
              <template #title>
                <span class="snap-collapse-title">登录会话（{{ (snap.users || []).length }}）</span>
              </template>
              <pre class="snap-plain">{{ (snap.users || []).join('\n') || '（无登录会话）' }}</pre>
            </el-collapse-item>
            <el-collapse-item v-if="(snap.disks || []).length" name="disk">
              <template #title>
                <span class="snap-collapse-title">磁盘（{{ snap.disks.length }} 个挂载点）</span>
              </template>
              <el-table :data="snap.disks" size="small" border max-height="240">
                <el-table-column prop="mount" label="挂载点" min-width="120" show-overflow-tooltip />
                <el-table-column prop="size" label="容量" width="90" />
                <el-table-column prop="used" label="已用" width="90" />
                <el-table-column prop="avail" label="可用" width="90" />
                <el-table-column label="使用率" width="90">
                  <template #default="{ row }">
                    <span :class="{ 'p-hot': (row.pct || 0) >= 90 }">{{ row.pct != null ? row.pct + '%' : '—' }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </el-collapse-item>
          </el-collapse>
        </template>
      </section>

      <!-- 原始记录日志 -->
      <section class="block">
        <h4 class="block-title">原始记录日志（{{ detail.logs?.length || 0 }} 条通知留痕）</h4>
        <el-collapse>
          <el-collapse-item v-for="log in detail.logs || []" :key="log.id" :name="String(log.id)">
            <template #title>
              <el-tag :type="log.action === 'problem' ? 'danger' : 'success'" size="small" effect="dark">
                {{ log.action === 'problem' ? '异常通知' : '恢复通知' }}
              </el-tag>
              <span class="log-time">{{ fmtTime(log.received_at) }}</span>
            </template>
            <pre class="snap-plain">{{ JSON.stringify(log.payload, null, 2) }}</pre>
          </el-collapse-item>
        </el-collapse>
      </section>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { fetchAnomalyDetail, runAnomalySnapshot, triggerAiAnalysis, listAiAnalyses, feedbackAiAnalysis } from '../api'
import { fmtTimeCol } from '../time'
import { TRIGGER_CN } from '../trigger-cn'
import { auth } from '../auth'

const SEV_LABELS = {
  P0: 'P0 严重故障', P1: 'P1 重要告警', P2: 'P2 一般告警', P3: 'P3 提示信息',
  disaster: '灾难', high: '严重', average: '较严重', warning: '警告', information: '提示', not_classified: '未知',
}
const SEV_TAGS = {
  P0: 'danger', P1: 'warning', P2: 'warning', P3: 'info',
  disaster: 'danger', high: 'danger', average: 'warning', warning: 'warning', information: 'info', not_classified: 'info',
}

const visible = ref(false)
const detail = ref(null)
const anomalyId = ref(null)
let pollTimer = null

// 内嵌 AI 分析结果（最新一条）：打开详情即可见，轮询自动跟进生成进度
const aiAnalysis = ref(null)
const aiNote = ref('')
const aiHandling = ref(false)

const router = useRouter()

// AI 日志分析状态（anomaly.ai_status 由后端联动引擎/手动触发更新）
const AI_LABELS = {
  none: '未分析', pending: '排队中', running: '分析中',
  done: '已完成', blocked: '已拦截', failed: '失败', skipped: '未启用',
}
const AI_TAGS = {
  none: 'info', pending: 'info', running: 'primary',
  done: 'success', blocked: 'warning', failed: 'danger', skipped: 'info',
}
const canFeedback = auth.has('ai:feedback')

function triggerAi() {
  triggerAiAnalysis(anomalyId.value)
    .then(() => {
      ElMessage.success('已派发 AI 分析任务')
      load()
      startPoll()
    })
    .catch((e) => ElMessage.error(e?.response?.data?.detail || '触发 AI 分析失败'))
}

function submitFeedback() {
  if (!aiAnalysis.value) return
  aiHandling.value = true
  feedbackAiAnalysis(aiAnalysis.value.id, { note: aiNote.value || '' })
    .then(() => {
      ElMessage.success('处理记录已登记')
      aiNote.value = ''
      load()
    })
    .catch((e) => ElMessage.error(e?.response?.data?.detail || '登记失败'))
    .finally(() => {
      aiHandling.value = false
    })
}

// 双向关联：从告警跳转到分析结果列表
function goAiAnalyses() {
  visible.value = false
  router.push({ path: '/ai-analyses', query: { anomaly_id: String(anomalyId.value) } })
}

const fmtTime = fmtTimeCol('')

// 快照数据与系统状态摘要
const snap = computed(() => detail.value?.diagnostics || {})

// 告警上下文（触发时引擎写入 payload 的关键数据）
const payload = computed(() => detail.value?.payload || {})
const CTX_LABELS = [
  ['latest', '当前值'], ['threshold', '阈值'], ['op', '比较方式'],
  ['window_seconds', '窗口（秒）'], ['samples', '样本数'], ['policy_source', '策略来源'],
]
const ctxItems = computed(() =>
  CTX_LABELS
    .filter(([k]) => payload.value[k] !== undefined && payload.value[k] !== null && payload.value[k] !== '')
    .map(([k, label]) => ({ label, value: payload.value[k] }))
)
const hasCtx = computed(() =>
  ctxItems.value.length > 0 || (payload.value.oom_detail || []).length > 0 ||
  (payload.value.window_series || []).length > 0 || (payload.value.top_processes || []).length > 0 ||
  !!payload.value.net_rate
)
const seriesText = computed(() =>
  (payload.value.window_series || []).map(([ts, v]) => `${ts}  →  ${v}`).join('\n')
)
// 速率人话化：bps → 自动选 Kbps / Mbps
function bpsText(bps) {
  if (bps == null) return '—'
  if (bps >= 1e6) return (bps / 1e6).toFixed(2) + ' Mbps'
  if (bps >= 1e3) return (bps / 1e3).toFixed(1) + ' Kbps'
  return Math.round(bps) + ' B/s'
}
const netRateRx = computed(() => bpsText(payload.value.net_rate?.rx_bps))
const netRateTx = computed(() => bpsText(payload.value.net_rate?.tx_bps))
const ctxExtraKeys = computed(() =>
  Object.keys(payload.value).filter(
    (k) => !['latest', 'threshold', 'op', 'window_seconds', 'samples', 'policy_source', 'oom_detail', 'window_series', 'top_processes', 'net_rate'].includes(k)
  )
)
const ctxExtraText = computed(() =>
  JSON.stringify(Object.fromEntries(ctxExtraKeys.map((k) => [k, payload.value[k]])), null, 2)
)
const sysText = computed(() => {
  const s = snap.value.system
  if (!s || s.load1 == null) return '—'
  return `${s.load1} / ${s.load5} / ${s.load15}`
})
const loadClass = computed(() => {
  const s = snap.value.system
  if (!s || s.load1 == null || !s.ncpu) return ''
  return s.load1 > s.ncpu ? 'p-hot' : 'p-ok'
})
const memText = computed(() => {
  const m = snap.value.memory?.mem
  if (!m || !m.total_mb) return '—'
  const swap = snap.value.memory?.swap
  let text = `${m.used_mb}MB / ${m.total_mb}MB（${m.pct ?? '—'}%）`
  if (swap && swap.total_mb) text += `，Swap ${swap.used_mb}MB`
  return text
})

function cnTrigger(name) {
  if (!name) return name
  for (const [en, cn] of TRIGGER_CN) {
    if (name.startsWith(en)) return name.replace(en, cn)
  }
  return name
}

async function load() {
  if (!anomalyId.value) return
  try {
    const { data } = await fetchAnomalyDetail(anomalyId.value)
    detail.value = data
  } catch {
    /* 静默，抽屉保留上次数据 */
  }
  try {
    const { data: ai } = await listAiAnalyses({ anomaly_id: anomalyId.value, page_size: 1 })
    aiAnalysis.value = (ai.items || [])[0] || null
  } catch {
    /* AI 列表拉取失败不影响详情 */
  }
}

function open(id) {
  anomalyId.value = id
  aiAnalysis.value = null
  aiNote.value = ''
  visible.value = true
  load()
}

function snapshot() {
  runAnomalySnapshot(anomalyId.value)
    .then(() => {
      ElMessage.success('已开始采集')
      startPoll()
    })
    .catch(() => ElMessage.error('触发采集失败'))
}

function startPoll() {
  stopPoll()
  pollTimer = setInterval(load, 4000)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(visible, (on) => {
  if (on) startPoll()
  else stopPoll()
})

onBeforeUnmount(stopPoll)

defineExpose({ open })
</script>

<style scoped>
.detail {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.block-title {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
}
.diag-head {
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.ai-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.ai-blocked {
  margin-bottom: 10px;
}
.ai-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.ai-summary {
  padding: 10px 12px;
  background: var(--el-color-primary-light-9);
  border-left: 3px solid var(--el-color-primary);
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.7;
  margin-bottom: 4px;
}
.ai-para {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
}
.ai-causes {
  margin: 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.8;
}
.ai-sol {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 8px;
}
.ai-sol-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.ai-sol-title {
  font-weight: 600;
  font-size: 13px;
}
.ai-sol-tag {
  font-size: 12px;
  padding: 0 6px;
  border-radius: 3px;
  background: var(--el-fill-color);
  color: var(--el-text-color-regular);
}
.ai-sol-sev {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
}
.ai-sol-sev.sev-high {
  color: var(--el-color-danger);
}
.ai-sol-sev.sev-medium {
  color: var(--el-color-warning);
}
.ai-sol-sev.sev-low {
  color: var(--el-color-success);
}
.ai-extra {
  margin-top: 10px;
}
.ai-handle-view {
  font-size: 13px;
  line-height: 1.7;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ai-handle-form {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.diag-target {
  margin-bottom: 10px;
}
.snap-time {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.snap-system {
  margin-bottom: 12px;
}
.sub-title {
  margin: 12px 0 6px;
  font-size: 13px;
  font-weight: 600;
}
.snap-extra {
  margin-top: 12px;
}
.snap-collapse-title {
  font-size: 13px;
  font-weight: 600;
}
.snap-plain {
  margin: 0;
  padding: 12px 14px;
  background: var(--el-fill-color-light);
  border-radius: var(--r-sm);
  font-size: 12px;
  line-height: 1.6;
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.oom-line {
  font-family: Menlo, Consolas, monospace;
  font-size: 12px;
}
.log-time {
  margin-left: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.p-hot {
  color: var(--el-color-danger);
  font-weight: 600;
}
.p-ok {
  color: var(--el-color-success);
}
.p-run {
  color: var(--el-color-primary);
  font-weight: 600;
}
.p-zombie {
  color: var(--el-color-warning);
  font-weight: 600;
}
</style>
