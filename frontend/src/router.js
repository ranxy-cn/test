import { createRouter, createWebHistory } from 'vue-router'
import TicketsView from './views/TicketsView.vue'
import TicketDetailView from './views/TicketDetailView.vue'
import EmployeeView from './views/EmployeeView.vue'
import ReportView from './views/ReportView.vue'
import AssetsView from './views/AssetsView.vue'
import StatusView from './views/StatusView.vue'
import NotificationsView from './views/NotificationsView.vue'
import BackupsView from './views/BackupsView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
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
