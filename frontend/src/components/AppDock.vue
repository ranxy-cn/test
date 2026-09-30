<template>
  <!--
    Mac 风格底部 Dock 栏
    - 图标悬停时按鼠标距离连续放大上浮（经典 Dock 鱼眼效果），放大可超出 Dock 上沿
    - 放大的图标按比例加宽自身占位：推开相邻图标、拉长 Dock 栏，互不重叠
    - tooltip 胶囊跟随显示功能名称，激活项底部带指示点
    - 小屏（触屏）禁用鱼眼，图标缩小并允许横向滚动
  -->
  <nav ref="dockRef" class="dock" :class="{ touch: isTouch }" aria-label="主导航" @mousemove="onMove" @mouseleave="resetScales">
    <div class="dock-tip" :style="tipStyle" :class="{ show: tip.show }">{{ tip.text }}</div>
    <button
      v-for="(item, i) in items"
      :key="item.path"
      :ref="(el) => (iconEls[i] = el)"
      type="button"
      class="dock-item"
      :class="{ active: isActive(item) }"
      :style="slotStyle(i)"
      :aria-label="item.name"
      @mouseenter="onEnter(i)"
      @click="go(item)"
    >
      <span class="dock-icon" :style="iconStyle(i)"><el-icon :size="iconSize(i)"><component :is="item.icon || 'Menu'" /></el-icon></span>
      <i v-if="isActive(item)" class="dock-dot" />
    </button>
  </nav>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const props = defineProps({
  items: { type: Array, default: () => [] }, // [{ name, path, icon }]
})

const route = useRoute()
const router = useRouter()

const dockRef = ref(null)
const iconEls = ref([])
const isTouch = ref(false)
onMounted(() => {
  // 仅触屏（无 hover 能力）禁用鱼眼；窄窗口保留交互，只靠 CSS 缩小布局
  isTouch.value = window.matchMedia('(hover: none)').matches
})

// 鱼眼缩放状态：scale 放大倍率、lift 上浮像素
const scales = ref(props.items.map(() => ({ scale: 1, lift: 0 })))

// tooltip：跟随当前悬停项显示名称
const tip = reactive({ show: false, text: '', x: 0 })
const tipStyle = computed(() => ({ left: `${tip.x}px` }))

function isActive(item) {
  return route.path === item.path || route.path.startsWith(item.path + '/')
}

// Dock 渐变色板：按路由分配稳定的高饱和 Mac 风渐变
const GRADIENTS = [
  'linear-gradient(145deg, #0a84ff, #0055d4)',
  'linear-gradient(145deg, #ff453a, #c92a20)',
  'linear-gradient(145deg, #ff9f0a, #e07800)',
  'linear-gradient(145deg, #5e5ce6, #3d3bb5)',
  'linear-gradient(145deg, #30d158, #1f9d4d)',
  'linear-gradient(145deg, #64d2ff, #0071e3)',
  'linear-gradient(145deg, #ac8e68, #7d5a3c)',
  'linear-gradient(145deg, #ff375f, #c1104a)',
  'linear-gradient(145deg, #6ac4dc, #2b8ca3)',
  'linear-gradient(145deg, #ffd60a, #d9a400)',
  'linear-gradient(145deg, #bf5af2, #8944ab)',
]

// 占位宽度随放大倍率增加：推开相邻图标并拉长 Dock，避免视觉重叠；同时绑定图标渐变色
function slotStyle(i) {
  const s = scales.value[i] || { scale: 1 }
  const grow = Math.max(0, (Math.max(1, s.scale) - 1) * 46)
  return { '--grad': GRADIENTS[i % GRADIENTS.length], '--grow': `${grow.toFixed(1)}px` }
}
function iconStyle(i) {
  const s = scales.value[i] || { scale: 1, lift: 0 }
  return { transform: `translateY(${s.lift}px) scale(${s.scale})` }
}
function iconSize(i) {
  const s = scales.value[i] || { scale: 1 }
  return Math.round(21 * Math.max(1, s.scale))
}

// 鼠标移动时按与各图标中心的距离做连续放大（越近越大）
function onMove(e) {
  if (isTouch.value) return
  let bestI = -1
  let bestK = 0
  const next = props.items.map((_, i) => {
    const el = iconEls.value[i]
    if (!el) return { scale: 1, lift: 0 }
    const r = el.getBoundingClientRect()
    const d = Math.abs(e.clientX - (r.left + r.width / 2))
    const k = Math.max(0, 1 - d / 120) // 影响半径 120px
    if (k > bestK) {
      bestK = k
      bestI = i
    }
    const kk = k * k // 平方衰减，中心更突出
    return { scale: 1 + 0.45 * kk, lift: -12 * kk }
  })
  scales.value = next
  // tooltip 跟随放大最明显的图标
  if (bestI >= 0) {
    const el = iconEls.value[bestI]
    const dockRect = dockRef.value.getBoundingClientRect()
    const r = el.getBoundingClientRect()
    tip.x = r.left + r.width / 2 - dockRect.left
    tip.text = props.items[bestI].name
    tip.show = true
  } else {
    tip.show = false
  }
}

function onEnter(i) {
  if (isTouch.value) return
  const el = iconEls.value[i]
  if (!el) return
  const dockRect = dockRef.value.getBoundingClientRect()
  const r = el.getBoundingClientRect()
  tip.x = r.left + r.width / 2 - dockRect.left
  tip.text = props.items[i].name
  tip.show = true
}

function resetScales() {
  scales.value = props.items.map(() => ({ scale: 1, lift: 0 }))
  tip.show = false
}

function go(item) {
  if (route.path !== item.path) router.push(item.path)
}

onBeforeUnmount(resetScales)
</script>

<style scoped>
/* ===== Dock 容器：底部居中悬浮浅色毛玻璃条 ===== */
.dock {
  position: fixed;
  left: 50%;
  bottom: 10px;
  transform: translateX(-50%);
  z-index: 90;
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 9px 14px 8px;
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.55);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.7);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.1), 0 2px 8px rgba(0, 0, 0, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.7);
  /* 不设 overflow：放大图标可超出上沿、tooltip 正常显示 */
}
.dock-item {
  position: relative;
  flex: none;
  /* 占位宽度 = 基础尺寸 + 鱼眼加宽，撑开相邻图标并拉长 Dock */
  width: calc(var(--dock-size, 46px) + var(--grow, 0px));
  height: 46px;
  padding: 0;
  border: none;
  border-radius: 13px;
  background: transparent;
  cursor: pointer;
  display: grid;
  place-items: end center;
  transition: width 0.18s var(--ease-out);
}

/* 图标本体：渐变底 + 白色图标，从底部锚点生长，可超出 Dock 上沿 */
.dock-icon {
  width: var(--dock-size, 46px);
  height: var(--dock-size, 46px);
  border-radius: 13px;
  display: grid;
  place-items: center;
  background: var(--grad);
  color: #fff;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.35);
  transform-origin: 50% 100%;
  transition: transform 0.14s var(--ease-out), box-shadow 0.14s var(--ease-out);
  will-change: transform;
}
.dock:not(.touch) .dock-item:hover .dock-icon {
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.35);
}
.dock-item:active .dock-icon {
  filter: brightness(0.88);
}

/* 激活指示点 */
.dock-dot {
  position: absolute;
  left: 50%;
  bottom: -7px;
  transform: translateX(-50%);
  width: 4.5px;
  height: 4.5px;
  border-radius: 50%;
  background: var(--ink);
  opacity: 0.75;
  transition: opacity var(--dur-fast) ease;
}

/* tooltip 胶囊 */
.dock-tip {
  position: absolute;
  top: -44px;
  transform: translateX(-50%) translateY(4px);
  padding: 5px 11px;
  border-radius: var(--r-pill);
  background: rgba(29, 29, 31, 0.82);
  color: #fff;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease, transform 0.15s var(--ease-out);
  z-index: 5;
}
.dock-tip.show { opacity: 1; transform: translateX(-50%) translateY(0); }

/* ===== 响应式：小屏图标缩小（tooltip 隐藏）；仅触屏允许横向滚动 ===== */
@media (max-width: 768px) {
  .dock {
    gap: 6px;
    padding: 7px 10px 7px;
    bottom: 8px;
    border-radius: 18px;
    --dock-size: 38px;
  }
  .dock-item { height: 38px; border-radius: 11px; }
  .dock-icon { border-radius: 11px; }
  .dock-tip { display: none; }
}
/* 触屏（无 hover）：无鱼眼放大，可安全横向滚动 */
@media (hover: none) {
  .dock {
    max-width: calc(100vw - 24px);
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: none;
  }
  .dock::-webkit-scrollbar { display: none; }
  .dock-item { transition: none; }
}
</style>
