<template>
  <!--
    智能对话右侧悬浮条（全局任意页面可见，受 knowledge:chat 权限控制）
    - 贴右边缘竖排胶囊，可上下拖动（位置本地记忆），点击弹出对话抽屉
    - 抽屉无遮罩（modal=false）：打开时页面、顶栏、Dock 仍可交互/可见
  -->
  <template v-if="allowed">
    <button
      v-show="!drawerVisible"
      ref="floatRef"
      type="button"
      class="chat-float"
      :class="{ dragging }"
      :style="{ top: `${floatY}px` }"
      :aria-label="'智能对话'"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="onUp"
      @pointercancel="onUp"
      @click="onClick"
    >
      <el-icon :size="17"><ChatDotRound /></el-icon>
      <span class="chat-float-text">智能对话</span>
    </button>

    <el-drawer
      v-model="drawerVisible"
      direction="rtl"
      size="460px"
      :modal="false"
      :z-index="2010"
      :with-header="true"
      title="智能运维对话"
      class="chat-drawer"
    >
      <div class="chat-drawer-status"><span />{{ sending ? '正在分析' : 'AI 已就绪' }}</div>
      <ChatPanel />
    </el-drawer>
  </template>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ChatDotRound } from '@element-plus/icons-vue'
import { auth } from '../auth'
import ChatPanel from './ChatPanel.vue'

const allowed = ref(false)
const drawerVisible = ref(false)
const floatRef = ref(null)

// 悬浮条垂直位置：本地记忆，越界自愈
const POS_KEY = 'devops-chat-float-y'
const floatY = ref(Math.round(window.innerHeight * 0.4))
const clampY = (v) => Math.min(Math.max(v, 60), window.innerHeight - 90)

onMounted(() => {
  allowed.value = auth.has('knowledge:chat')
  const saved = Number(localStorage.getItem(POS_KEY))
  if (saved) floatY.value = clampY(saved)
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  stopWatch()
})
function onResize() {
  floatY.value = clampY(floatY.value)
}

/* ===== 拖动（仅垂直）：位移超 5px 视为拖动，抑制随后的 click ===== */
const dragging = ref(false)
let startY = 0
let startTop = 0
let moved = false

function onDown(e) {
  if (e.button !== undefined && e.button !== 0) return
  dragging.value = true
  moved = false
  startY = e.clientY
  startTop = floatY.value
  floatRef.value?.setPointerCapture?.(e.pointerId)
}
function onMove(e) {
  if (!dragging.value) return
  const dy = e.clientY - startY
  if (Math.abs(dy) > 5) moved = true
  floatY.value = clampY(startTop + dy)
}
function onUp(e) {
  if (!dragging.value) return
  dragging.value = false
  floatRef.value?.releasePointerCapture?.(e.pointerId)
  if (moved) localStorage.setItem(POS_KEY, String(Math.round(floatY.value)))
}
function onClick() {
  if (moved) {
    moved = false
    return
  }
  drawerVisible.value = true
}
</script>

<style scoped>
.chat-float {
  position: fixed;
  right: 0;
  z-index: 2003; /* 高于页面内容与 AssetMonitor 悬浮窗(90)，低于全屏弹窗遮罩 */
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  width: 34px;
  padding: 12px 0 14px;
  border: none;
  border-radius: 12px 0 0 12px;
  background: linear-gradient(180deg, var(--violet), var(--brand-2));
  color: #fff;
  cursor: grab;
  box-shadow: -3px 4px 14px rgba(94, 92, 230, 0.35);
  user-select: none;
  touch-action: none;
  transition: box-shadow 0.2s ease, filter 0.2s ease;
}
.chat-float:hover {
  filter: brightness(1.08);
  box-shadow: -4px 6px 18px rgba(94, 92, 230, 0.45);
}
.chat-float.dragging {
  cursor: grabbing;
  filter: brightness(0.95);
}
.chat-float-text {
  writing-mode: vertical-lr;
  letter-spacing: 3px;
  font-size: 12px;
  font-weight: 600;
}
.chat-drawer-status {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: -6px 0 10px;
  color: var(--ok);
  font-size: 12px;
}
.chat-drawer-status span {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ok-vivid);
  box-shadow: 0 0 0 3px rgba(52, 199, 89, 0.14);
}
.chat-drawer :deep(.el-drawer__body) {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 12px 14px 14px;
  background: var(--el-bg-color);
}
.chat-drawer :deep(.el-drawer__body > .chat-panel) {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-lg, 12px);
  overflow: hidden;
  background: var(--surface);
}
</style>
