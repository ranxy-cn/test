<template>
  <div class="ai-analyses">
    <el-card shadow="never">
      <template #header>
        <div class="row-between">
          <span class="card-title">AI 日志分析</span>
          <div class="row-gap">
            <el-radio-group v-model="filters.order" size="small" @change="onFilterChange">
              <el-radio-button value="time">按时间</el-radio-button>
              <el-radio-button value="severity">按严重程度</el-radio-button>
            </el-radio-group>
            <el-button size="small" :loading="loading" @click="load">刷新</el-button>
          </div>
        </div>
      </template>

      <div class="row-gap filters">
        <el-select v-model="filters.status" placeholder="状态" clearable size="small" style="width: 130px" @change="onFilterChange">
          <el-option v-for="(label, key) in STATUS_LABELS" :key="key" :label="label" :value="key" />
        </el-select>
        <el-select v-model="filters.severity" placeholder="AI 严重程度" clearable size="small" style="width: 130px" @change="onFilterChange">
          <el-option v-for="(v, k) in SEV_LABELS" :key="k" :label="v" :value="k" />
        </el-select>
        <el-input
          v-model="filters.q"
          placeholder="搜索摘要 / 诊断内容"
          clearable
          size="small"
          style="width: 220px"
          @keyup.enter="onFilterChange"
          @clear="onFilterChange"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-tag v-if="filters.anomaly_id" closable size="small" type="warning" @close="clearAnomalyFilter">
          告警 #{{ filters.anomaly_id }} 的分析
        </el-tag>
      </div>

      <el-table :data="rows" v-loading="loading" size="small" stripe>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="主机" width="150">
          <template #default="{ row }">
            <div>{{ anomalyOf(row).hostname || '-' }}</div>
            <div class="muted">{{ anomalyOf(row).ip || '' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="触发器" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ anomalyOf(row).trigger_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="AI 严重程度" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.severity" :type="SEV_TAGS[row.severity] || 'info'" size="small" effect="dark">
              {{ SEV_LABELS[row.severity] || row.severity }}
            </el-tag>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="92">
          <template #default="{ row }">
            <el-tag :type="STATUS_TAGS[row.status] || 'info'" size="small">{{ STATUS_LABELS[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="摘要" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ row.summary || row.error || '-' }}</template>
        </el-table-column>
        <el-table-column label="置信度" width="80">
          <template #default="{ row }">
            <span v-if="row.confidence">{{ Math.round(row.confidence * 100) }}%</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="模型" width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.model || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
            <el-button link type="default" size="small" @click="gotoAnomaly(row)" :disabled="!row.anomaly_id">告警</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager-row">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @current-change="load"
          @size-change="onSizeChange"
        />
      </div>
    </el-card>

    <!-- 详情弹窗 -->
    <el-dialog v-model="detailVisible" title="AI 分析详情" width="720px" destroy-on-close>
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-alert
            v-if="detail.blocked"
            type="warning"
            show-icon
            :closable="false"
            title="该响应包含疑似服务器操作指令，已被内容过滤器拦截，以下为清洗后内容"
            class="blocked-alert"
          />
          <el-alert
            v-if="detail.status === 'failed'"
            type="error"
            show-icon
            :closable="false"
            :title="'分析失败：' + (detail.error || '未知错误')"
            class="blocked-alert"
          />

          <div class="detail-meta">
            <el-tag :type="STATUS_TAGS[detail.status] || 'info'" size="small">{{ STATUS_LABELS[detail.status] || detail.status }}</el-tag>
            <el-tag v-if="detail.severity" :type="SEV_TAGS[detail.severity] || 'info'" size="small" effect="dark">
              {{ SEV_LABELS[detail.severity] || detail.severity }}
            </el-tag>
            <span class="muted">{{ detail.model || '-' }}</span>
            <span class="muted" v-if="detail.latency_ms">· {{ detail.latency_ms }}ms</span>
            <span class="muted" v-if="detail.confidence">· 置信度 {{ Math.round(detail.confidence * 100) }}%</span>
            <span class="muted" v-if="detail.handled_by">· 已处理：{{ detail.handled_by }}（{{ fmtTime(detail.handled_at) }}）</span>
          </div>

          <div v-if="anomaly" class="anomaly-box">
            <div class="box-title">关联告警
              <el-button link type="primary" size="small" @click="gotoAnomaly(detail)">查看告警 →</el-button>
            </div>
            <div class="anomaly-line">
              #{{ anomaly.id }} {{ anomaly.hostname }}（{{ anomaly.ip }}）· {{ anomaly.trigger_name }}
              <el-tag size="small" :type="SEV_TAGS[anomaly.severity] || 'info'" class="anomaly-sev">{{ SEV_LABELS[anomaly.severity] || anomaly.severity }}</el-tag>
            </div>
            <div class="muted small">{{ anomaly.message }}</div>
          </div>

          <div class="section">
            <div class="box-title">诊断摘要</div>
            <div class="section-body">{{ detail.summary || '-' }}</div>
          </div>
          <div class="section" v-if="detail.diagnosis">
            <div class="box-title">诊断分析</div>
            <div class="section-body pre-wrap">{{ detail.diagnosis }}</div>
          </div>
          <div class="section" v-if="(detail.causes || []).length">
            <div class="box-title">可能原因</div>
            <ul class="plain-list">
              <li v-for="(c, i) in detail.causes" :key="i">{{ c }}</li>
            </ul>
          </div>
          <div class="section" v-if="(detail.solutions || []).length">
            <div class="box-title">解决方案建议</div>
            <div v-for="(s, i) in detail.solutions" :key="i" class="solution-card">
              <div class="solution-head">
                <span class="solution-title">{{ s.title || `建议 ${i + 1}` }}</span>
                <span class="solution-tags">
                  <el-tag v-if="s.tag" size="small" type="info">{{ s.tag }}</el-tag>
                  <el-tag v-if="s.severity" size="small" :type="SEV_TAGS[s.severity] || 'info'">
                    {{ SEV_LABELS[s.severity] || s.severity }}
                  </el-tag>
                </span>
              </div>
              <div class="solution-detail pre-wrap">{{ s.detail }}</div>
            </div>
          </div>

          <el-collapse class="raw-collapse" v-if="detail.raw_response">
            <el-collapse-item title="AI 原始响应（留痕 / 供审计核对）">
              <pre class="raw-pre">{{ detail.raw_response }}</pre>
            </el-collapse-item>
          </el-collapse>

          <el-divider />
          <div class="feedback-row">
            <template v-if="detail.handled_note">
              <div class="section-body">处理备注：{{ detail.handled_note }}</div>
            </template>
            <el-button v-if="canHandle" type="warning" size="small" plain @click="reanalyze">重新分析</el-button>
            <el-input
              v-if="canHandle"
              v-model="feedbackNote"
              type="textarea"
              :rows="2"
              maxlength="2000"
              show-word-limit
              placeholder="记录人工处理情况（如已按建议处理、误报忽略等）"
              class="feedback-input"
            />
            <el-button v-if="canHandle" type="primary" size="small" :loading="submitting" :disabled="!feedbackNote.trim()" @click="submitFeedback">
              提交处理记录
            </el-button>
          </div>
        </template>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { auth } from '../auth'
import { listAiAnalyses, getAiAnalysis, feedbackAiAnalysis, triggerAiAnalysis } from '../api'

const STATUS_LABELS = {
  pending: '排队中', running: '分析中', done: '已完成',
  blocked: '已拦截', failed: '失败', skipped: '未启用跳过',
}
const STATUS_TAGS = { pending: 'info', running: 'primary', done: 'success', blocked: 'warning', failed: 'danger', skipped: 'info' }
const SEV_LABELS = { critical: '严重', high: '高', medium: '中', low: '低', info: '提示' }
const SEV_TAGS = { critical: 'danger', high: 'danger', medium: 'warning', low: 'success', info: 'info' }

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const rows = ref([])
const anomaliesMap = ref({})
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const filters = reactive({
  anomaly_id: route.query.anomaly_id ? Number(route.query.anomaly_id) : null,
  status: '',
  severity: '',
  q: '',
  order: 'time',
})

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const detailAnomaly = ref(null)
const feedbackNote = ref('')
const submitting = ref(false)
const canHandle = ref(false)

function anomalyOf(row) {
  return anomaliesMap.value[row.anomaly_id] || {}
}

function fmtTime(t) {
  if (!t) return '-'
  return String(t).replace('T', ' ').slice(0, 19)
}

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value, order: filters.order }
    if (filters.anomaly_id) params.anomaly_id = filters.anomaly_id
    if (filters.status) params.status = filters.status
    if (filters.severity) params.severity = filters.severity
    if (filters.q.trim()) params.q = filters.q.trim()
    const { data } = await listAiAnalyses(params)
    rows.value = data.items || []
    anomaliesMap.value = data.anomalies || {}
    total.value = data.total || 0
  } catch (e) {
    ElMessage.error('加载分析结果失败')
  } finally {
    loading.value = false
  }
}

function onFilterChange() {
  page.value = 1
  load()
}

function onSizeChange() {
  page.value = 1
  load()
}

function clearAnomalyFilter() {
  filters.anomaly_id = null
  router.replace({ query: {} })
  onFilterChange()
}

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  detailAnomaly.value = null
  feedbackNote.value = ''
  try {
    const { data } = await getAiAnalysis(row.id)
    detail.value = data
    detailAnomaly.value = data.anomaly || null
    canHandle.value = auth.has('ai:feedback')
  } catch (e) {
    ElMessage.error('加载详情失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function submitFeedback() {
  submitting.value = true
  try {
    await feedbackAiAnalysis(detail.value.id, { note: feedbackNote.value.trim() })
    ElMessage.success('处理记录已保存并写入审计日志')
    detailVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '提交失败')
  } finally {
    submitting.value = false
  }
}

async function reanalyze() {
  if (!detailAnomaly.value) return
  try {
    await triggerAiAnalysis(detailAnomaly.value.id)
    ElMessage.success('已重新派发分析任务')
    detailVisible.value = false
    onFilterChange()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '触发失败')
  }
}

// 双向关联：从分析结果定位原始告警
function gotoAnomaly(row) {
  router.push({ path: '/anomalies', query: { anomaly_id: row.anomaly_id } })
}

onMounted(() => {
  load()
})
</script>

<style scoped>
.ai-analyses {
  padding: 16px;
}
.row-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.row-gap {
  display: flex;
  gap: 10px;
  align-items: center;
}
.card-title {
  font-weight: 600;
}
.filters {
  margin-bottom: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.small {
  font-size: 12px;
}
.pager-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.blocked-alert {
  margin-bottom: 12px;
}
.detail-meta {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}
.anomaly-box {
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.box-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.anomaly-line {
  font-size: 13px;
}
.anomaly-sev {
  margin-left: 6px;
}
.section {
  margin-bottom: 14px;
}
.section-body {
  font-size: 13px;
  line-height: 1.7;
}
.pre-wrap {
  white-space: pre-wrap;
  word-break: break-word;
}
.plain-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.8;
}
.solution-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 8px;
}
.solution-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.solution-title {
  font-weight: 600;
  font-size: 13px;
}
.solution-tags {
  display: flex;
  gap: 6px;
}
.solution-detail {
  font-size: 13px;
  line-height: 1.7;
}
.raw-collapse {
  margin-bottom: 8px;
}
.raw-pre {
  margin: 0;
  max-height: 240px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.feedback-row {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
}
.feedback-input {
  width: 100%;
}
</style>
