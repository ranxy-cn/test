<template>
  <!--
    智能对话右侧悬浮条（全局任意页面可见，受 knowledge:chat 权限控制）
    - 贴右边缘竖排胶囊，可上下拖动（位置本地记忆），点击展开对话侧栏
    - 对话侧栏为布局内停靠列（content-row 的 flex 子项）：展开时挤压主内容区，
      类似 split view，而非悬浮遮盖；关闭后宽度归零、内容区恢复满宽
  -->
  <template v-if="allowed">
    <button
      v-show="!panelVisible"
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

    <aside class="chat-side" :class="{ open: panelVisible }" aria-label="智能运维对话">
      <section class="chat-side-panel" role="dialog" aria-label="智能运维对话">
        <header class="chat-side-head">
          <div class="chat-side-title">
            <el-icon :size="16"><ChatDotRound /></el-icon>
            <b>智能运维对话</b>
            <span class="chat-ready"><i />AI 已就绪</span>
          </div>
          <button class="chat-side-close" type="button" aria-label="关闭对话" @click="panelVisible = false">
            <el-icon :size="15"><Close /></el-icon>
          </button>
        </header>
        <div class="chat-side-body">
          <ChatPanel />
        </div>
      </section>
    </aside>
  </template>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ChatDotRound, Close } from '@element-plus/icons-vue'
import { auth } from '../auth'
import ChatPanel from './ChatPanel.vue'

const allowed = ref(false)
const panelVisible = ref(false)
const floatRef = ref(null)

// 展开挤压内容区后，广播 window resize 让 ECharts 等按容器宽度自适应
watch(panelVisible, () => {
  window.dispatchEvent(new Event('resize'))
  setTimeout(() => window.dispatchEvent(new Event('resize')), 300)
})

// 悬浮条垂直位置：本地记忆，越界自愈
const POS_KEY = 'devops-chat-float-y'
const floatY = ref(Math.round(window.innerHeight * 0.4))
const clampY = (v) => Math.min(Math.max(v, 60), window.innerHeight - 90)

function onKeydown(e) {
  if (e.key === 'Escape' && panelVisible.value) panelVisible.value = false
}

onMounted(() => {
  allowed.value = auth.has('knowledge:chat')
  const saved = Number(localStorage.getItem(POS_KEY))
  if (saved) floatY.value = clampY(saved)
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onKeydown)
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
  panelVisible.value = true
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

/* ===== 停靠侧栏：透明占位列，宽度 0 → 434px 动画展开，挤压主内容区 ===== */
.chat-side {
  flex: none;
  width: 0;
  transition: width 0.26s var(--ease-out, ease);
}
.chat-side.open {
  width: min(434px, 92vw);
}

/* 面板 fixed 定位：相对视口全高停靠（顶栏下方 → 视口底），页面滚动时纹丝不动；
   展开/收起用 translateX 从右缘滑入滑出，与占位列宽度动画同步 */
.chat-side-panel {
  position: fixed;
  top: 54px; /* 让出固定顶栏 */
  right: 0;
  bottom: 0;
  z-index: 90; /* 低于顶栏(95)：不遮挡顶部信息栏；Dock(2002) 悬浮其上 */
  margin: 0;
  display: flex;
  flex-direction: column;
  width: min(420px, 88vw);
  border: 1px solid var(--line);
  border-right: none;
  border-radius: 16px 0 0 0;
  background: var(--el-bg-color);
  box-shadow: -12px 10px 34px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  transform: translateX(105%);
  visibility: hidden;
  transition: transform 0.26s var(--ease-out, ease), visibility 0s linear 0.26s;
}
.chat-side.open .chat-side-panel {
  transform: translateX(0);
  visibility: visible;
  transition: transform 0.26s var(--ease-out, ease);
}
html.dark .chat-side-panel {
  box-shadow: -12px 10px 34px rgba(0, 0, 0, 0.45);
}

.chat-side-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 14px;
  border-bottom: 1px solid var(--line);
  background: linear-gradient(135deg, rgba(94, 92, 230, 0.08), rgba(10, 132, 255, 0.06));
}
.chat-side-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--ink);
  font-size: 14px;
}
.chat-side-title .el-icon { color: var(--violet); }
.chat-ready {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
  padding: 2px 8px;
  border-radius: var(--r-pill, 999px);
  background: rgba(52, 199, 89, 0.12);
  color: var(--ok);
  font-size: 11.5px;
  font-weight: 500;
  white-space: nowrap;
}
.chat-ready i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok-vivid);
  box-shadow: 0 0 0 3px rgba(52, 199, 89, 0.14);
}
.chat-side-close {
  flex: none;
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.chat-side-close:hover {
  background: rgba(0, 0, 0, 0.06);
  color: var(--ink);
}
html.dark .chat-side-close:hover { background: rgba(255, 255, 255, 0.1); }

.chat-side-body {
  flex: 1;
  min-height: 0;
  padding: 12px 14px 14px;
  display: flex;
}
.chat-side-body > :deep(.chat-panel) {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-lg, 12px);
  overflow: hidden;
  background: var(--surface);
}

@media (max-width: 768px) {
  .chat-side.open { width: min(434px, 96vw); }
  .chat-side-panel {
    width: min(420px, 92vw);
    top: 50px;
    border-radius: 14px 0 0 0;
  }
}
</style>
