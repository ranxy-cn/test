import { createRouter, createWebHistory } from 'vue-router'
import TicketsView from './views/TicketsView.vue'
import TicketDetailView from './views/TicketDetailView.vue'
import EmployeeView from './views/EmployeeView.vue'
import ReportView from './views/ReportView.vue'
import AssetsView from './views/AssetsView.vue'
import StatusView from './views/StatusView.vue'
import NotificationsView from './views/NotificationsView.vue'
import BackupsView from './views/BackupsView.vue'
import LoginView from './views/LoginView.vue'
import { getToken } from './api'

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
  ],
})

router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!getToken()) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  return true
})

export default router
