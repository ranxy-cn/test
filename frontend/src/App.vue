<template>
  <!-- 登录等公开页：全屏裸布局，不渲染侧边栏与顶栏 -->
  <router-view v-if="isPublicPage" />

  <el-container v-else class="layout">
    <!-- 侧边栏：桌面常驻，移动端抽屉 -->
    <el-aside width="244px" class="aside" :class="{ open: sidebarOpen }">
      <div class="brand">
        <div class="brand-logo" aria-hidden="true">D</div>
        <div class="brand-text">
          <h1>DevOpsAgent</h1>
          <p>智能运维数字员工</p>
        </div>
      </div>
      <!-- 动态菜单：由后端按角色授权返回（menus 表驱动） -->
      <el-menu :router="true" :default-active="$route.path" background-color="transparent" class="side-menu">
        <template v-for="item in menuItems" :key="item.code">
          <el-sub-menu v-if="item.children && item.children.length" :index="item.code">
            <template #title>
              <el-icon><component :is="item.icon || 'Folder'" /></el-icon>
              <span>{{ item.name }}</span>
            </template>
            <el-menu-item v-for="child in item.children" :key="child.code" :index="child.path">
              <el-icon><component :is="child.icon || 'Menu'" /></el-icon>
              <span>{{ child.name }}</span>
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else-if="item.path" :index="item.path">
            <el-icon><component :is="item.icon || 'Menu'" /></el-icon>
            <span>{{ item.name }}</span>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>
    <!-- 移动端抽屉遮罩 -->
    <transition name="fade">
      <div v-if="sidebarOpen" class="aside-mask" @click="sidebarOpen = false" />
    </transition>

    <el-container class="body-col">
      <el-header class="header" height="56px">
        <div class="header-left">
          <button class="menu-btn" type="button" aria-label="切换导航菜单" @click="sidebarOpen = !sidebarOpen">
            <el-icon :size="20"><Expand /></el-icon>
          </button>
          <div class="slogan">LLM 只做 <b>分析与建议</b> · 策略引擎做 <b>决定</b> · 执行器做 <b>动作</b> · 证据链做 <b>证明</b></div>
        </div>
        <div class="user-box">
          <span class="onboard-pill"><i class="light-dot light-green" />DE-OPS-001 在岗</span>
          <el-dropdown v-if="auth.user" @command="onCommand">
            <span class="user-chip">
              <span class="avatar" aria-hidden="true">{{ avatarInitial }}</span>
              <span class="user-name">{{ auth.user.display_name || auth.user.username }}</span>
              <span class="role-chip">{{ roleLabel }}</span>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="password">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main">
        <!-- 路由过渡：轻微上浮淡入，方向感与层次感 -->
        <router-view v-slot="{ Component }">
          <transition name="page" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>

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
  </el-container>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { auth } from './auth'
import { changePassword } from './api'

const route = useRoute()
const router = useRouter()
const isPublicPage = computed(() => Boolean(route.meta.public))

// 移动端抽屉式侧边栏：路由切换后自动收起
const sidebarOpen = ref(false)
watch(
  () => route.path,
  () => (sidebarOpen.value = false),
)

// 动态菜单树（登录接口返回，来自 menus 表按角色授权过滤）
const menuItems = computed(() => auth.user?.menus || [])

const roleLabels = { admin: '管理员', operator: '操作员', viewer: '只读' }
const roleLabel = computed(() => {
  const codes = auth.user?.roles || []
  return roleLabels[codes[0]] || codes[0] || ''
})
const avatarInitial = computed(() => {
  const name = auth.user?.display_name || auth.user?.username || 'U'
  return name.trim().charAt(0).toUpperCase()
})

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
.layout { min-height: 100%; }

/* ===== 侧边栏：浅色毛玻璃，内容优先 ===== */
.aside {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border-right: 1px solid var(--line);
  z-index: 30;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 22px 20px 18px;
}
.brand-logo {
  width: 36px;
  height: 36px;
  flex: none;
  border-radius: 10px;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, var(--brand-2), var(--violet));
  color: #fff;
  font-size: 17px;
  font-weight: 700;
  box-shadow: 0 4px 12px rgba(10, 132, 255, 0.32);
}
.brand-text h1 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--ink);
}
.brand-text p {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.4;
}

/* 菜单：圆角悬浮项，激活态清晰而不喧闹 */
.side-menu {
  border-right: none;
  background: transparent;
  padding: 0 12px 20px;
  overflow-y: auto;
}
.side-menu :deep(.el-menu-item),
.side-menu :deep(.el-sub-menu__title) {
  height: 42px;
  line-height: 42px;
  border-radius: 10px;
  margin: 2px 0;
  color: var(--ink-2);
  font-weight: 500;
  transition: background-color var(--dur-fast) ease, color var(--dur-fast) ease;
}
.side-menu :deep(.el-menu-item:hover),
.side-menu :deep(.el-sub-menu__title:hover) {
  background: rgba(0, 0, 0, 0.045);
}
.side-menu :deep(.el-sub-menu .el-menu) { background: transparent; }
.side-menu :deep(.el-sub-menu .el-menu .el-menu-item) { padding-left: 46px !important; }
.side-menu :deep(.el-menu-item.is-active) {
  background: rgba(0, 113, 227, 0.1);
  color: var(--brand);
  font-weight: 600;
}
.side-menu :deep(.el-menu-item.is-active .el-icon) { color: var(--brand); }

/* ===== 顶栏：毛玻璃悬浮，滚动时贴合 ===== */
.header {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 22px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border-bottom: 1px solid var(--line);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.slogan {
  font-size: 12.5px;
  color: var(--muted);
  letter-spacing: 0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.slogan b { color: var(--ink); font-weight: 600; }

.menu-btn {
  display: none;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--ink);
  cursor: pointer;
  transition: background-color var(--dur-fast) ease;
}
.menu-btn:hover { background: rgba(0, 0, 0, 0.05); }
.menu-btn:active { transform: scale(0.94); }

.user-box {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: none;
}
.onboard-pill {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 12px;
  font-weight: 500;
  color: var(--ok);
  background: var(--el-color-success-light-9);
  border-radius: var(--r-pill);
  padding: 5px 12px;
}
.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: var(--ink);
  font-size: 14px;
  font-weight: 500;
  outline: none;
  padding: 4px 6px;
  border-radius: var(--r-pill);
  transition: background-color var(--dur-fast) ease;
}
.user-chip:hover { background: rgba(0, 0, 0, 0.045); }
.avatar {
  width: 28px;
  height: 28px;
  flex: none;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, var(--brand-2), var(--violet));
  color: #fff;
  font-size: 12.5px;
  font-weight: 600;
}
.user-name { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.role-chip {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--muted);
  background: var(--el-fill-color-dark);
  border-radius: 6px;
  padding: 2px 7px;
}

/* 主内容区：充足留白 */
.main {
  padding: 22px 26px 36px;
  background: transparent;
}

/* 遮罩过渡 */
.fade-enter-active, .fade-leave-active { transition: opacity var(--dur-base) ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.aside-mask { display: none; }

/* ============================================================
   响应式：平板保持常驻侧栏；手机（≤768px）抽屉化
   ============================================================ */
@media (max-width: 1024px) {
  .slogan { display: none; }
  .main { padding: 18px 18px 28px; }
}

@media (max-width: 768px) {
  .aside {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    width: 268px !important;
    min-width: 268px;
    background: rgba(255, 255, 255, 0.92);
    transform: translateX(-102%);
    transition: transform 0.32s var(--ease-spring);
    box-shadow: none;
  }
  .aside.open {
    transform: translateX(0);
    box-shadow: var(--shadow-lg);
  }
  .aside-mask {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 25;
    background: rgba(0, 0, 0, 0.38);
  }
  .menu-btn { display: inline-flex; }
  .header { padding: 0 14px; }
  .main { padding: 14px 14px 24px; }
  .user-name { display: none; }
  .onboard-pill { display: none; }
}
</style>
