import { createRouter, createWebHistory } from 'vue-router'
import TicketsView from './views/TicketsView.vue'
import TicketDetailView from './views/TicketDetailView.vue'
import EmployeeView from './views/EmployeeView.vue'
import ReportView from './views/ReportView.vue'
import AssetsView from './views/AssetsView.vue'
import StatusView from './views/StatusView.vue'
import NotificationsView from './views/NotificationsView.vue'
import BackupsView from './views/BackupsView.vue'
import UsersView from './views/UsersView.vue'
import LoginView from './views/LoginView.vue'
import { auth } from './auth'
import { setUnauthorizedHandler } from './api'
import { ElMessage } from 'element-plus'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginView, meta: { public: true } },
    { path: '/', redirect: '/tickets' },
    { path: '/tickets', component: TicketsView },
    { path: '/tickets/:id', component: TicketDetailView, props: true },
    { path: '/employee', component: EmployeeView },
    { path: '/report', component: ReportView },
    { path: '/assets', component: AssetsView },
    { path: '/status', component: StatusView },
    { path: '/notifications', component: NotificationsView },
    { path: '/backups', component: BackupsView },
    { path: '/users', component: UsersView, meta: { requiresAdmin: true } },
  ],
})

// API 层收到 401 时统一踢回登录页
setUnauthorizedHandler(() => {
  if (router.currentRoute.value.path !== '/login') {
    ElMessage.warning('登录已过期，请重新登录')
    router.push('/login')
  }
})

router.beforeEach(async (to) => {
  if (to.meta.public) {
    if (auth.isLoggedIn()) return { path: '/tickets' }
    return true
  }
  if (!auth.isLoggedIn()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  // 刷新页面后内存态丢失：用 /auth/me 恢复用户信息（顺带校验 token 有效性）
  if (!auth.user) {
    const user = await auth.ensureLoaded()
    if (!user) return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAdmin && !auth.isAdmin()) {
    ElMessage.error('需要管理员权限')
    return { path: '/tickets' }
  }
  return true
})

export default router
