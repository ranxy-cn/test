<template>
  <div class="ai-config">
    <el-card shadow="never">
      <template #header>
        <div class="row-between">
          <span class="card-title">AI 服务配置</span>
          <el-tag v-if="form.enabled" type="success" size="small">已启用</el-tag>
          <el-tag v-else type="info" size="small">未启用</el-tag>
        </div>
      </template>

      <el-form :model="form" label-width="120px" class="config-form" v-loading="loading">
        <el-form-item label="启用分析">
          <el-switch v-model="form.enabled" active-text="告警触发后自动调用 AI 分析日志" />
        </el-form-item>
        <el-form-item label="API 端点 URL">
          <el-input v-model="form.base_url" placeholder="如 https://api.openai.com/v1（填到 /v1 即可，填完整 /chat/completions 端点亦可）" clearable />
        </el-form-item>
        <el-form-item label="API 密钥">
          <el-input
            v-model="apiKeyInput"
            type="password"
            show-password
            :placeholder="keyPlaceholder"
            autocomplete="new-password"
          />
          <div class="hint" v-if="settings.has_api_key">当前密钥：已加密存储（尾号 ····{{ settings.api_key_tail }}），留空保存表示沿用。</div>
        </el-form-item>
        <el-form-item label="模型">
          <el-select v-model="form.model" filterable allow-create default-first-option placeholder="选择或输入模型名" class="model-select">
            <el-option v-for="m in MODEL_OPTIONS" :key="m" :label="m" :value="m" />
          </el-select>
        </el-form-item>
        <el-form-item label="请求超时(秒)">
          <el-input-number v-model="form.timeout_seconds" :min="5" :max="600" :step="5" />
        </el-form-item>
        <el-form-item label="失败重试次数">
          <el-input-number v-model="form.max_retries" :min="0" :max="10" />
          <span class="hint inline">分析任务失败后按指数退避重试（30s/60s/120s）</span>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="onSave">保存配置</el-button>
          <el-button :loading="testing" @click="onTest">测试连接</el-button>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="testResult"
        :title="testResult.title"
        :description="testResult.desc"
        :type="testResult.ok ? 'success' : 'error'"
        show-icon
        :closable="true"
        class="test-alert"
        @close="testResult = null"
      />

      <el-divider content-position="left">安全说明</el-divider>
      <ul class="security-notes">
        <li>API 密钥使用 Fernet 对称加密后落库，任何接口均不返回明文（仅展示末 4 位）。</li>
        <li>发送给 AI 服务的请求仅包含日志摘要与问题描述，不含凭据、密钥等敏感信息（自动脱敏）。</li>
        <li>AI 仅能进行日志分析与给出文字性建议，无法执行任何服务器操作；响应内容自动过滤服务器操作指令。</li>
        <li>配置修改、连接测试、每次分析请求与响应均记录完整审计日志。</li>
      </ul>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { getAiSettings, saveAiSettings, testAiConnection } from '../api'

const MODEL_OPTIONS = [
  'gpt-4o-mini', 'gpt-4o', 'deepseek-chat', 'deepseek-reasoner',
  'qwen-plus', 'qwen-max', 'glm-4-flash', 'moonshot-v1-8k',
]

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const settings = ref({})
const apiKeyInput = ref('')
const testResult = ref(null)

const form = reactive({
  enabled: false,
  base_url: '',
  model: '',
  timeout_seconds: 60,
  max_retries: 3,
})

const keyPlaceholder = computed(() =>
  settings.value.has_api_key
    ? '已配置（留空沿用现有密钥，输入新值则替换）'
    : 'sk-...（OpenAI 兼容 API 密钥）'
)

async function load() {
  loading.value = true
  try {
    const { data } = await getAiSettings()
    settings.value = data
    form.enabled = !!data.enabled
    form.base_url = data.base_url || ''
    form.model = data.model || ''
    form.timeout_seconds = data.timeout_seconds || 60
    form.max_retries = data.max_retries ?? 3
  } catch (e) {
    ElMessage.error('加载 AI 配置失败')
  } finally {
    loading.value = false
  }
}

async function onSave() {
  saving.value = true
  try {
    const payload = { ...form, keep_api_key: !apiKeyInput.value, api_key: apiKeyInput.value }
    const { data } = await saveAiSettings(payload)
    settings.value = data
    apiKeyInput.value = ''
    ElMessage.success('配置已保存（密钥加密落库，动作已审计）')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function onTest() {
  testing.value = true
  testResult.value = null
  try {
    // 未输入新密钥时不带 api_key 字段，后端用已存密钥测试
    const payload = { base_url: form.base_url, model: form.model }
    if (apiKeyInput.value) payload.api_key = apiKeyInput.value
    const { data } = await testAiConnection(payload)
    if (data.ok) {
      testResult.value = {
        ok: true,
        title: `连接成功${data.mock ? '（Mock 模式：未配置密钥，返回演示结果）' : ''}`,
        desc: `模型：${data.model || '-'}，耗时 ${data.latency_ms}ms`,
      }
    } else {
      testResult.value = { ok: false, title: '连接失败', desc: data.message }
    }
  } catch (e) {
    testResult.value = { ok: false, title: '连接失败', desc: e?.response?.data?.detail || String(e) }
  } finally {
    testing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.ai-config {
  padding: 16px;
}
.row-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  font-weight: 600;
}
.config-form {
  max-width: 680px;
}
.model-select {
  width: 100%;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
  width: 100%;
}
.hint.inline {
  width: auto;
  margin-left: 12px;
}
.test-alert {
  max-width: 680px;
  margin-bottom: 16px;
}
.security-notes {
  margin: 0;
  padding-left: 18px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 2;
}
</style>
