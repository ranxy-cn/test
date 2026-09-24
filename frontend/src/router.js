import { createRouter, createWebHistory } from 'vue-router'
import AnomaliesView from './views/AnomaliesView.vue'
import TicketsView from './views/TicketsView.vue'
import TicketDetailView from './views/TicketDetailView.vue'
import EmployeeView from './views/EmployeeView.vue'
import ReportView from './views/ReportView.vue'
import AssetsView from './views/AssetsView.vue'
import StatusView from './views/StatusView.vue'
import NotificationsView from './views/NotificationsView.vue'
import BackupsView from './views/BackupsView.vue'
import UsersView from './views/UsersView.vue'
import MenusView from './views/MenusView.vue'
import RolesView from './views/RolesView.vue'
import LoginView from './views/LoginView.vue'
import { auth } from './auth'
import { setUnauthorizedHandler } from './api'
import { ElMessage } from 'element-plus'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: LoginView, meta: { public: true } },
    { path: '/', redirect: '/anomalies' },
    { path: '/anomalies', component: AnomaliesView, meta: { perm: 'anomalies:read' } },
    { path: '/tickets', component: TicketsView, meta: { perm: 'tickets:read' } },
    { path: '/tickets/:id', component: TicketDetailView, props: true, meta: { perm: 'tickets:read' } },
    { path: '/employee', component: EmployeeView, meta: { perm: 'catalog:read' } },
    { path: '/report', component: ReportView, meta: { perm: 'reports:read' } },
    { path: '/assets', component: AssetsView, meta: { perm: 'assets:read' } },
    { path: '/status', component: StatusView, meta: { perm: 'status:read' } },
    { path: '/notifications', component: NotificationsView, meta: { perm: 'notifications:read' } },
    { path: '/backups', component: BackupsView, meta: { perm: 'backups:read' } },
    { path: '/users', component: UsersView, meta: { perm: 'users:manage' } },
    { path: '/roles', component: RolesView, meta: { perm: 'roles:manage' } },
    { path: '/menus', component: MenusView, meta: { perm: 'menus:manage' } },
    { path: '/:pathMatch(.*)*', redirect: '/anomalies' },
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
    if (auth.isLoggedIn()) return { path: '/anomalies' }
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
  // 页面级权限：按权限点校验（菜单展示与权限均来自角色授权）
  const perm = to.meta.perm
  if (perm && !auth.has(perm)) {
    ElMessage.error('没有访问该页面的权限')
    const fallback = firstAllowedPath()
    if (fallback && fallback !== to.path) return fallback
    return false
  }
  return true
})

// 从用户菜单树中取第一个可访问的页面路径（无任何权限时回登录页）
function firstAllowedPath() {
  const paths = []
  const walk = (items) =>
    (items || []).forEach((m) => {
      if (m.path) paths.push(m.path)
      walk(m.children)
    })
  walk(auth.user?.menus || [])
  return paths[0] || '/login'
}

export default router
