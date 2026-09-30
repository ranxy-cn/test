<template>
  <!-- 登录等公开页：全屏裸布局，不渲染 Dock 与顶栏 -->
  <router-view v-if="isPublicPage" />

  <div v-else class="workspace">
    <!-- 超薄顶栏：透明悬浮，仅保留品牌与右上角辅助区，内容区最大化 -->
    <header class="topbar">
      <div class="brand-mini" @click="router.push('/dashboard')">
        <span class="brand-logo" aria-hidden="true">D</span>
        <span class="brand-name">DevOpsAgent</span>
        <span class="clock mono" :title="'北京时间'">
          <el-icon :size="12"><Clock /></el-icon>
          北京时间 {{ clock }}
        </span>
      </div>

      <div class="assist">
        <!-- 夜间 / 白天模式切换 -->
        <button class="assist-btn" type="button" :aria-label="isDark ? '切换白天模式' : '切换夜间模式'" @click="toggleTheme">
          <el-icon :size="18">
            <Sunny v-if="isDark" />
            <Moon v-else />
          </el-icon>
        </button>

        <!-- 通知中心：铃铛 + 弹出面板（最近通知 + 查看全部） -->
        <el-popover placement="bottom-end" :width="340" trigger="click" popper-class="notif-popper" @show="loadNotifs">
          <template #reference>
            <button class="assist-btn" type="button" aria-label="通知中心">
              <el-badge :value="unreadCount" :hidden="!unreadCount" :max="99">
                <el-icon :size="18"><Bell /></el-icon>
              </el-badge>
            </button>
          </template>
          <div class="notif-head">
            <b>通知中心</b>
            <el-link type="primary" :underline="false" @click="router.push('/notifications')">查看全部</el-link>
          </div>
          <div v-if="notifs.length" class="notif-list">
            <div v-for="n in notifs" :key="n.id" class="notif-row" :class="{ unread: !n.read }">
              <div class="notif-title">{{ n.title || n.kind }}</div>
              <div class="notif-body">{{ n.body }}</div>
              <div class="notif-time">{{ fmtTime(n.created_at) }}</div>
            </div>
          </div>
          <el-empty v-else description="暂无通知" :image-size="56" />
        </el-popover>

        <!-- 系统设置：下拉（用户/角色/菜单管理，按授权过滤） -->
        <el-dropdown v-if="sysMenus.length" @command="(p) => router.push(p)">
          <button class="assist-btn" type="button" aria-label="系统设置">
            <el-icon :size="18"><Setting /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="m in sysMenus" :key="m.code" :command="m.path">
                <el-icon><component :is="m.icon || 'Menu'" /></el-icon>{{ m.name }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>

        <!-- 用户：下拉（修改密码 / 退出登录） -->
        <el-dropdown v-if="auth.user" @command="onCommand">
          <button class="assist-btn user-btn" type="button" aria-label="用户菜单">
            <span class="avatar" aria-hidden="true">{{ avatarInitial }}</span>
            <span class="user-name">{{ auth.user.display_name || auth.user.username }}</span>
            <span class="role-chip">{{ roleLabel }}</span>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="password">修改密码</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <!-- 主内容区：占满除顶栏与 Dock 外的全部空间 -->
    <main class="main">
      <router-view v-slot="{ Component }">
        <transition name="page" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>

    <!-- 底部 Dock：主要功能模块入口（通知中心与系统管理收进右上角） -->
    <AppDock :items="dockItems" />

    <el-dialog v-model="pwdVisible" title="修改密码" width="440px">
      <el-form label-width="90px">
        <el-form-item label="原密码">
          <el-input v-model="pwd.old" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwd.new1" type="password" show-password placeholder="8-64 位，含大小写字母与数字" />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="pwd.new2" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="doChangePwd">确认修改</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { auth } from './auth'
import { changePassword, fetchNotifications } from './api'
import AppDock from './components/AppDock.vue'
import { fmtTime } from './time'

const route = useRoute()
const router = useRouter()
const isPublicPage = computed(() => Boolean(route.meta.public || route.meta.standalone))

// 动态菜单树（登录接口返回，来自 menus 表按角色授权过滤）
const menuItems = computed(() => auth.user?.menus || [])

// Dock：一级页面菜单（通知中心收进右上角弹出面板，系统管理目录收进右上角下拉）
const dockItems = computed(() =>
  menuItems.value.filter((m) => m.type === 'menu' && m.path && m.path !== '/notifications'),
)

// 系统设置下拉：目录（无 path）下的子页面
const sysMenus = computed(() => menuItems.value.find((m) => m.type === 'dir' && m.children?.length)?.children || [])

const roleLabels = { admin: '管理员', operator: '操作员', viewer: '只读' }
const roleLabel = computed(() => {
  const codes = auth.user?.roles || []
  return roleLabels[codes[0]] || codes[0] || ''
})
const avatarInitial = computed(() => {
  const name = auth.user?.display_name || auth.user?.username || 'U'
  return name.trim().charAt(0).toUpperCase()
})

/* ===== 北京时间时钟：每秒刷新，显式锁定 Asia/Shanghai 时区 ===== */
const clock = ref('')
let clockTimer
function tickClock() {
  clock.value = new Date().toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}
onMounted(() => {
  tickClock()
  clockTimer = setInterval(tickClock, 1000)
})
onBeforeUnmount(() => clearInterval(clockTimer))

/* ===== 夜间 / 白天主题切换 ===== */
const isDark = ref(document.documentElement.classList.contains('dark'))
function toggleTheme() {
  // 切换瞬间启用全局颜色过渡，营造平滑的明暗渐变
  document.documentElement.classList.add('theme-anim')
  isDark.value = document.documentElement.classList.toggle('dark')
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
  setTimeout(() => document.documentElement.classList.remove('theme-anim'), 350)
}

/* ===== 通知中心：未读角标 + 最近通知面板 ===== */
const notifs = ref([])
const unreadCount = ref(0)
let notifTimer

async function refreshUnread() {
  try {
    const { data } = await fetchNotifications({ unread: true })
    unreadCount.value = data.items?.length || 0
  } catch { /* 静默：角标拉取失败不打扰用户 */ }
}

async function loadNotifs() {
  try {
    const { data } = await fetchNotifications({})
    notifs.value = (data.items || []).slice(0, 6)
  } catch { /* 面板数据失败时展示空态 */ }
  refreshUnread()
}

onMounted(() => {
  refreshUnread()
  notifTimer = setInterval(refreshUnread, 60000)
})
onBeforeUnmount(() => clearInterval(notifTimer))

/* ===== 修改密码 / 退出登录 ===== */
const pwdVisible = ref(false)
const pwdSaving = ref(false)
const pwd = reactive({ old: '', new1: '', new2: '' })

function onCommand(cmd) {
  if (cmd === 'logout') {
    doLogout()
  } else if (cmd === 'password') {
    Object.assign(pwd, { old: '', new1: '', new2: '' })
    pwdVisible.value = true
  }
}

async function doLogout() {
  await auth.logout()
  router.push('/login')
}

async function doChangePwd() {
  if (!pwd.old || !pwd.new1) {
    ElMessage.warning('请填写完整')
    return
  }
  if (pwd.new1 !== pwd.new2) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  pwdSaving.value = true
  try {
    await changePassword(pwd.old, pwd.new1)
    ElMessage.success('密码已修改，请重新登录')
    pwdVisible.value = false
    auth.clear()
    router.push('/login')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '修改失败')
  } finally {
    pwdSaving.value = false
  }
}
</script>

<style scoped>
.workspace {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

/* ===== 超薄顶栏：固定顶部 + 毛玻璃半透明（与 Dock 同风格），滚动时锁定不动 ===== */
.topbar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 95;
  height: 46px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 18px;
  background: rgba(255, 255, 255, 0.55);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border-bottom: 1px solid rgba(255, 255, 255, 0.7);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.05);
}
.brand-mini {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  cursor: pointer;
  user-select: none;
}
.brand-logo {
  width: 27px;
  height: 27px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, var(--brand-2), var(--violet));
  color: #fff;
  font-size: 13.5px;
  font-weight: 700;
  box-shadow: 0 3px 10px rgba(10, 132, 255, 0.32);
}
.brand-name {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--ink);
}

/* 夜间模式：顶栏毛玻璃转深色底 */
html.dark .topbar {
  background: rgba(28, 28, 30, 0.55);
  border-bottom-color: rgba(44, 44, 46, 0.8);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.35);
}

/* ===== 北京时间时钟 ===== */
.clock {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: 14px;
  padding: 4px 10px;
  border-radius: var(--r-pill);
  background: rgba(0, 0, 0, 0.045);
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.03em;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
html.dark .clock { background: rgba(255, 255, 255, 0.08); }

/* ===== 右上角辅助区：通知 / 系统设置 / 用户 ===== */
.assist {
  display: flex;
  align-items: center;
  gap: 6px;
}
.assist-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 9px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--ink-2);
  font-size: 13px;
  cursor: pointer;
  outline: none;
  transition: background-color var(--dur-fast) ease, transform var(--dur-fast) ease;
}
.assist-btn:hover { background: rgba(0, 0, 0, 0.05); }
.assist-btn:active { transform: scale(0.95); }
.avatar {
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, var(--brand-2), var(--violet));
  color: #fff;
  font-size: 11px;
  font-weight: 600;
}
.user-name { max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; color: var(--ink); }
.role-chip {
  font-size: 11px;
  font-weight: 500;
  color: var(--muted);
  background: var(--el-fill-color-dark);
  border-radius: 6px;
  padding: 2px 7px;
}

/* ===== 通知弹出面板 ===== */
.notif-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 6px;
}
.notif-list { max-height: 320px; overflow-y: auto; }
.notif-row {
  padding: 8px 6px;
  border-radius: 8px;
  transition: background-color var(--dur-fast) ease;
}
.notif-row:hover { background: var(--el-fill-color-light); }
.notif-row.unread .notif-title { color: var(--ink); font-weight: 600; }
.notif-title { font-size: 13px; font-weight: 500; color: var(--ink-2); }
.notif-body {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.notif-time { font-size: 11px; color: var(--faint); margin-top: 3px; }

/* ===== 主内容区：占满剩余空间，底部为 Dock 预留 ===== */
.main {
  flex: 1;
  min-width: 0;
  padding: 54px 22px 100px; /* 顶部补偿固定顶栏高度 */
}

/* 路由过渡：轻微上浮淡入 */
.page-enter-active, .page-leave-active { transition: opacity 0.22s var(--ease-out), transform 0.22s var(--ease-out); }
.page-enter-from { opacity: 0; transform: translateY(10px); }
.page-leave-to { opacity: 0; transform: translateY(-6px); }

/* ============================================================
   响应式
   ============================================================ */
@media (max-width: 1024px) {
  .main { padding: 54px 14px 96px; }
}
@media (max-width: 768px) {
  .topbar { padding: 0 10px; }
  .brand-name { display: none; }
  .clock { margin-left: 8px; padding: 4px 8px; font-size: 11px; }
  .clock .el-icon { display: none; }
  .user-name, .role-chip { display: none; }
  .main { padding: 52px 10px 92px; }
}
</style>
