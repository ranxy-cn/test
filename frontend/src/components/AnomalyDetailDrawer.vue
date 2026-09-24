<template>
  <el-drawer v-model="visible" size="64%" :title="`异常详情 · ${detail?.event_id || ''}`" destroy-on-close @closed="stopPoll">
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
import { ElMessage } from 'element-plus'
import { fetchAnomalyDetail, runAnomalySnapshot } from '../api'
import { fmtTimeCol } from '../time'
import { TRIGGER_CN } from '../trigger-cn'

const SEV_LABELS = {
  disaster: '灾难', high: '严重', average: '较严重', warning: '警告', information: '提示', not_classified: '未知',
}
const SEV_TAGS = { disaster: 'danger', high: 'danger', average: 'warning', warning: 'warning', information: 'info', not_classified: 'info' }

const visible = ref(false)
const detail = ref(null)
const anomalyId = ref(null)
let pollTimer = null

const fmtTime = fmtTimeCol('')

// 快照数据与系统状态摘要
const snap = computed(() => detail.value?.diagnostics || {})
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
}

function open(id) {
  anomalyId.value = id
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
  padding: 10px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
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
