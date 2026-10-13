<template>
  <!--
    顶部下拉导航（纯文字版）
    - 平时收起为顶栏中央文字胶囊（显示当前页面名），鼠标悬浮向下展开文字菜单，移出即收起
    - 当前页面在菜单中高亮；触屏（无 hover 能力）改为点击胶囊开合
  -->
  <div class="dock-zone" :class="{ open }" aria-label="主导航" @mouseenter="expand" @mouseleave="collapse">
    <button type="button" class="dock-trigger" :aria-expanded="open" aria-label="展开主导航" @click="toggle">
      <span class="tg-label">{{ activeName }}</span>
      <el-icon :size="12" class="tg-caret"><ArrowDown /></el-icon>
    </button>

    <div class="dock-pop">
      <transition name="drop">
        <nav v-show="open" class="dock-menu">
          <button
            v-for="item in items"
            :key="item.path"
            type="button"
            class="menu-item"
            :class="{ active: isActive(item) }"
            @click="go(item)"
          >
            {{ item.name }}
          </button>
        </nav>
      </transition>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const props = defineProps({
  items: { type: Array, default: () => [] }, // [{ name, path, icon }]
})

const route = useRoute()
const router = useRouter()

const isTouch = ref(false)
onMounted(() => {
  // 仅触屏（无 hover 能力）改为点击开合
  isTouch.value = window.matchMedia('(hover: none)').matches
})

// 展开/收起：桌面端悬浮展开、移出收起；触屏点击开合
const open = ref(false)
function expand() {
  if (!isTouch.value) open.value = true
}
function collapse() {
  open.value = false
}
function toggle() {
  open.value = !open.value
}

// 胶囊文字：当前页面名；不在菜单中的页面显示「导航」
const activeName = computed(() => props.items.find((m) => isActive(m))?.name || '导航')

function isActive(item) {
  return route.path === item.path || route.path.startsWith(item.path + '/')
}

function go(item) {
  if (route.path !== item.path) router.push(item.path)
  // 触屏点击跳转后收起菜单；桌面端保持展开（鼠标仍在区域内，避免闪烁）
  if (isTouch.value) collapse()
}

onBeforeUnmount(collapse)
</script>

<style scoped>
/* ===== 区域容器：固定顶栏中央；悬浮展开，菜单悬浮于内容之上 ===== */
.dock-zone {
  position: fixed;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
  /* 高于 el-loading-mask（~2001）：刷新遮罩不得盖住导航；低于弹窗 modal（2003+） */
  z-index: 2002;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: 8px;
}

/* 收起态胶囊：与顶栏同风格的毛玻璃文字胶囊 */
.dock-trigger {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 30px;
  padding: 0 13px;
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.6);
  backdrop-filter: blur(18px) saturate(160%);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
  color: var(--ink);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  outline: none;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
  transition: background-color var(--dur-fast) ease, box-shadow var(--dur-fast) ease;
}
.dock-trigger:hover {
  background: rgba(255, 255, 255, 0.82);
  box-shadow: 0 3px 14px rgba(0, 0, 0, 0.12);
}
html.dark .dock-trigger {
  background: rgba(44, 44, 46, 0.6);
  border-color: rgba(70, 70, 74, 0.9);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.3);
}
html.dark .dock-trigger:hover {
  background: rgba(58, 58, 60, 0.8);
  box-shadow: 0 3px 14px rgba(0, 0, 0, 0.4);
}
.tg-label {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tg-caret {
  color: var(--muted);
  transition: transform 0.18s var(--ease-out);
}
.dock-zone.open .tg-caret {
  transform: rotate(180deg);
}

/* 桥接层：填补胶囊与菜单间空隙，鼠标滑过不中断悬浮 */
.dock-pop {
  padding-top: 8px;
}

/* ===== 下拉菜单：毛玻璃圆角卡片 + 竖排文字项 ===== */
.dock-menu {
  min-width: 156px;
  padding: 6px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.75);
  box-shadow: 0 14px 38px rgba(0, 0, 0, 0.16);
  transform-origin: top center;
}
html.dark .dock-menu {
  background: rgba(28, 28, 30, 0.88);
  border-color: rgba(60, 60, 64, 0.9);
  box-shadow: 0 14px 38px rgba(0, 0, 0, 0.5);
}
.menu-item {
  display: block;
  width: 100%;
  padding: 7px 12px;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: var(--ink-2);
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
  outline: none;
  transition: background-color var(--dur-fast) ease, color var(--dur-fast) ease;
}
.menu-item:hover {
  background: rgba(0, 0, 0, 0.05);
  color: var(--ink);
}
.menu-item.active {
  color: var(--brand-2);
  font-weight: 600;
  background: rgba(10, 132, 255, 0.08);
}
.menu-item:active {
  filter: brightness(0.96);
}
html.dark .menu-item:hover {
  background: rgba(255, 255, 255, 0.08);
}
html.dark .menu-item.active {
  background: rgba(10, 132, 255, 0.16);
}

/* 下拉展开/收起过渡 */
.drop-enter-active,
.drop-leave-active {
  transition: opacity 0.16s var(--ease-out), transform 0.16s var(--ease-out);
}
.drop-enter-from,
.drop-leave-to {
  opacity: 0;
  transform: translateY(-8px) scale(0.98);
}

/* ===== 响应式：小屏收紧 ===== */
@media (max-width: 768px) {
  .dock-trigger {
    height: 28px;
    padding: 0 11px;
    font-size: 12px;
  }
  .tg-label {
    max-width: 96px;
  }
  .dock-menu {
    min-width: 132px;
  }
  .menu-item {
    padding: 7px 10px;
    font-size: 12.5px;
  }
}
</style>
