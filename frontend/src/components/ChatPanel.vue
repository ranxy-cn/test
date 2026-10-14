<template>
  <!--
    智能对话面板（复用组件）
    - 智能对话页面与右侧悬浮抽屉共用：消息区 + 快捷提问 + 输入区
    - 高度由父容器决定（height:100%），页面/抽屉各自控制外框
  -->
  <div class="chat-panel">
    <div ref="messageBox" class="message-box">
      <div v-if="!messages.length" class="welcome">
        <div class="bot-mark"><el-icon><Monitor /></el-icon></div>
        <h3>你好，我是新知AI-智能巡检</h3>
        <p>{{ dataMode ? '我可以直接查询业务数据库（只读），回答例如：' : '我可以基于企业运维知识和实时状态，回答例如：' }}</p>
        <div class="suggestions">
          <button v-for="item in activeSuggestions" :key="item" @click="send(item)">
            {{ item }}<el-icon><ArrowRight /></el-icon>
          </button>
        </div>
      </div>
      <div v-for="item in messages" :key="item.id" class="message" :class="item.role">
        <div class="message-avatar">{{ item.role === 'assistant' ? 'AI' : '我' }}</div>
        <div class="message-content">
          <div class="message-meta">
            {{ item.role === 'assistant' ? '新知AI-智能巡检' : '我' }}
            <small v-if="item.source">· {{ item.source === 'ai' ? '企业模型' : '状态兜底' }}</small>
          </div>
          <div class="message-text">{{ item.content }}</div>
          <div v-if="item.data" class="data-block">
            <div class="data-meta">查询到 {{ item.data.row_count }} 行（最多展示 {{ item.data.rows.length }} 行）</div>
            <el-table :data="item.data.rows" size="small" max-height="240" class="data-table">
              <el-table-column
                v-for="(col, i) in item.data.columns"
                :key="col + i"
                :prop="String(i)"
                :label="col"
                min-width="110"
                show-overflow-tooltip
              />
            </el-table>
            <details class="sql-detail">
              <summary>执行的只读 SQL（安全审计通过）</summary>
              <code>{{ item.data.sql }}</code>
            </details>
          </div>
          <div v-if="item.knowledge?.length" class="citation">
            <el-icon><Collection /></el-icon>
            参考 {{ item.knowledge.length }} 条知识：{{ item.knowledge.map((row) => row.title).join('、') }}
          </div>
        </div>
      </div>
      <div v-if="sending && !receiving" class="message assistant">
        <div class="message-avatar">AI</div>
        <div class="message-content">
          <div class="message-meta">新知AI-智能巡检</div>
          <div class="typing"><i /><i /><i /></div>
        </div>
      </div>
    </div>
    <div class="quick-bar">
      <span>快捷提问</span>
      <button v-for="item in activeSuggestions.slice(0, 3)" :key="item" @click="send(item)">{{ item }}</button>
      <span class="mode-switch">
        <el-switch v-model="dataMode" size="small" />
        数据问答<small>直连业务库（只读）</small>
      </span>
    </div>
    <div class="composer">
      <el-input
        v-model="draft"
        type="textarea"
        :autosize="{ minRows: 2, maxRows: 5 }"
        maxlength="4000"
        show-word-limit
        :placeholder="dataMode ? '用自然语言查询业务数据，如：每个负责人名下有多少台子机？' : '描述你的运维问题，或询问当前服务器状态...'"
        @keydown.enter.exact.prevent="send()"
      />
      <el-button type="primary" :loading="sending" :disabled="!draft.trim()" @click="send()">
        <el-icon><Promotion /></el-icon>发送
      </el-button>
    </div>
    <div class="composer-tip">Enter 发送 · Shift + Enter 换行 · AI 仅做分析与建议，涉及变更仍需人工确认</div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowRight, Collection, Monitor, Promotion } from '@element-plus/icons-vue'
import { sendDataQuery, streamKnowledgeChat } from '../api'
import { createTyper } from '../composables/typewriter'

const draft = ref('')
const sending = ref(false)
const receiving = ref(false)
const messages = ref([])
const messageBox = ref(null)
const dataMode = ref(false)
const suggestions = [
  '当前服务器整体状态怎么样？',
  '请分析最近的活动告警并给出处理建议',
  '公司磁盘告警的标准处置规则是什么？',
  '哪些任务单还没有闭环？',
]
const dataSuggestions = [
  '现在有多少台资产？母机和子机各多少？',
  '当前各严重级别的异常告警有多少？',
  '每个负责人名下有多少台机器？',
  '最近的 AI 分析结论有哪些？',
]
const activeSuggestions = computed(() => (dataMode.value ? dataSuggestions : suggestions))

async function scrollBottom() {
  await nextTick()
  if (messageBox.value) messageBox.value.scrollTop = messageBox.value.scrollHeight
}

async function send(text = '') {
  const value = (text || draft.value).trim()
  if (!value || sending.value) return
  messages.value.push({ id: `${Date.now()}-q`, role: 'user', content: value })
  draft.value = ''
  sending.value = true
  receiving.value = false
  await scrollBottom()
  try {
    if (dataMode.value) {
      const result = (await sendDataQuery(value)).data
      messages.value.push({
        id: `${Date.now()}-a`,
        role: 'assistant',
        content: result.answer,
        source: result.model === 'llm' ? 'ai' : 'fallback',
        data: {
          sql: result.sql,
          columns: result.columns,
          rows: (result.rows || []).map((row) => Object.fromEntries(row.map((v, i) => [String(i), v]))),
          row_count: result.row_count,
        },
      })
    } else {
      // 流式输出：首个增量到达时才创建助手消息，打字点在收到内容后隐藏
      const history = messages.value.slice(-12).map((item) => ({ role: item.role, content: item.content }))
      let live = null
      const ensureLive = () => {
        if (!live) {
          messages.value.push({ id: `${Date.now()}-a`, role: 'assistant', content: '', source: 'ai', knowledge: [] })
          // 必须取回数组里的响应式代理：直接改 push 前的原始对象不会触发视图更新，
          // 流式增量会攒到最后一次性渲染（打字机效果失效）
          live = messages.value[messages.value.length - 1]
          receiving.value = true
        }
      }
      // rAF 逐帧匀速上屏：突发大块 chunk 也顺滑；用户上翻离开底部时暂停自动跟随
      const typer = createTyper((chunk) => {
        ensureLive()
        live.content += chunk
        const box = messageBox.value
        if (!box || box.scrollHeight - box.scrollTop - box.clientHeight < 90) scrollBottom()
      })
      const result = await streamKnowledgeChat(value, history.slice(0, -1), {
        onDelta: (t) => typer.push(t),
      })
      typer.flush()
      ensureLive()
      live.source = result.source
      if (result.knowledge?.length) live.knowledge = result.knowledge
      if (!live.content.trim()) live.content = result.error || '（AI 未返回内容，请稍后重试）'
    }
  } catch (error) {
    const detail = error.response?.data?.detail
    ElMessage.error(detail || error.message || '对话请求失败')
    messages.value.push({ id: `${Date.now()}-e`, role: 'assistant', content: detail || error.message || '对话服务暂时不可用，请稍后重试。' })
  } finally {
    sending.value = false
    receiving.value = false
    await scrollBottom()
  }
}
</script>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
.message-box {
  flex: 1;
  min-height: 0;
  padding: 20px 22px;
  overflow: auto;
  background: linear-gradient(180deg, var(--el-fill-color-lighter) 0, var(--el-bg-color) 100%);
}
.welcome {
  display: flex;
  align-items: center;
  flex-direction: column;
  justify-content: center;
  min-height: 300px;
  text-align: center;
  color: var(--muted);
}
.bot-mark {
  display: grid;
  place-items: center;
  width: 54px;
  height: 54px;
  border-radius: 17px;
  color: #fff;
  background: linear-gradient(135deg, var(--violet), var(--brand-2));
  box-shadow: 0 8px 24px rgba(94, 92, 230, 0.24);
  font-size: 26px;
}
.welcome h3 {
  margin: 14px 0 5px;
  color: var(--ink);
  font-size: 19px;
}
.welcome p {
  margin: 0 0 14px;
  font-size: 13px;
}
.suggestions {
  display: grid;
  grid-template-columns: 1fr;
  gap: 9px;
  width: min(460px, 100%);
}
.suggestions button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 9px;
  color: var(--ink-2);
  background: var(--surface);
  cursor: pointer;
  text-align: left;
  transition: all 0.2s;
}
.suggestions button:hover {
  border-color: var(--brand);
  color: var(--brand);
  transform: translateY(-1px);
}
.message {
  display: flex;
  gap: 11px;
  margin-bottom: 22px;
}
.message.user {
  flex-direction: row-reverse;
}
.message-avatar {
  display: grid;
  place-items: center;
  flex: none;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  color: #fff;
  background: var(--violet);
  font-size: 11px;
  font-weight: 700;
}
.message.user .message-avatar {
  background: var(--brand);
}
.message-content {
  max-width: 82%;
}
.message.user .message-content {
  text-align: right;
}
.message-meta {
  margin-bottom: 6px;
  color: var(--muted);
  font-size: 12px;
}
.message-meta small {
  margin-left: 6px;
  color: var(--faint);
}
.message-text {
  display: inline-block;
  padding: 12px 14px;
  border-radius: 4px 14px 14px 14px;
  color: var(--ink-2);
  background: var(--el-fill-color);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  text-align: left;
}
.message.user .message-text {
  border-radius: 14px 4px 14px 14px;
  color: #fff;
  background: var(--brand);
}
.citation {
  margin-top: 8px;
  color: var(--muted);
  font-size: 11px;
  text-align: left;
}
.typing {
  display: flex;
  gap: 4px;
  padding: 14px;
  border-radius: 4px 14px 14px 14px;
  background: var(--el-fill-color);
}
.typing i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--faint);
  animation: blink 1s infinite;
}
.typing i:nth-child(2) { animation-delay: 0.15s; }
.typing i:nth-child(3) { animation-delay: 0.3s; }
@keyframes blink {
  0%, 80%, 100% { opacity: 0.3; }
  40% { opacity: 1; }
}
.quick-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 18px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 12px;
  overflow: auto;
}
.quick-bar button {
  flex: none;
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: 9px;
  color: var(--ink-2);
  background: var(--surface);
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s;
}
.quick-bar button:hover {
  border-color: var(--brand);
  color: var(--brand);
}
.mode-switch {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  color: var(--ink-2);
  font-size: 12px;
  white-space: nowrap;
}
.mode-switch small {
  color: var(--faint);
  font-size: 11px;
}
.data-block {
  margin-top: 8px;
  text-align: left;
}
.data-meta {
  margin-bottom: 6px;
  color: var(--muted);
  font-size: 11px;
}
.data-table {
  border-radius: 8px;
  width: 100%;
}
.sql-detail {
  margin-top: 6px;
  color: var(--muted);
  font-size: 11px;
}
.sql-detail code {
  display: block;
  margin-top: 4px;
  padding: 8px 10px;
  border-radius: 8px;
  color: var(--ink-2);
  background: var(--el-fill-color-light);
  font-size: 11px;
  word-break: break-all;
  white-space: pre-wrap;
}
.composer {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 12px 18px 6px;
  border-top: 1px solid var(--line);
}
.composer :deep(.el-textarea__inner) {
  box-shadow: none;
  border: 0;
  background: var(--el-fill-color-light);
}
.composer .el-button {
  height: 40px;
}
.composer-tip {
  padding: 0 18px 12px;
  color: var(--faint);
  font-size: 11px;
}
</style>
