import axios from 'axios'

const http = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

export const fetchTickets = (status) => http.get('/tickets', { params: status ? { status } : {} })
export const fetchTicket = (id) => http.get(`/tickets/${id}`)
export const approveTicket = (id, payload) => http.post(`/tickets/${id}/approve`, payload)
export const rejectTicket = (id, payload) => http.post(`/tickets/${id}/reject`, payload)
export const fetchEmployee = (id = 'DE-OPS-001') => http.get(`/employee/${id}`)
export const fetchReport = (date) => http.get('/reports/daily', { params: date ? { date } : {} })
export const fetchAssets = () => http.get('/assets')
export const fetchPlaybooks = () => http.get('/playbooks')
export const postWebhook = (payload, secret = 'dev-webhook-secret') =>
  http.post('/webhooks/zabbix', payload, { headers: { 'X-Webhook-Secret': secret } })
export const fetchStatus = () => http.get('/status')
export const fetchNotifications = (params) => http.get('/notifications', { params })
export const markNotificationRead = (id) => http.post(`/notifications/${id}/read`)
export const fetchBackups = () => http.get('/backups')
export const fetchBackupRuns = () => http.get('/backups/runs')
export const runBackup = (id) => http.post(`/backups/${id}/run`)
export const verifyRestore = (id) => http.post(`/backups/${id}/verify-restore`)
export const fetchLocks = () => http.get('/locks')
export const retryExecution = (id) => http.post(`/tickets/${id}/retry-execution`)
