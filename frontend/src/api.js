import axios from 'axios'

// 登录态 401 时由 auth.js 注入的回调触发跳转，避免循环依赖
let onUnauthorized = () => {}
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn
}

export const http = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

// 请求拦截：自动附带 Bearer token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('devops_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截：401 → 清理登录态并跳转登录页
http.interceptors.response.use(
  (resp) => resp,
  (error) => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('devops_token')
      localStorage.removeItem('devops_user')
      onUnauthorized()
    }
    return Promise.reject(error)
  },
)

// ===== 认证 =====
export const login = (username, password) => http.post('/auth/login', { username, password })
export const logout = () => http.post('/auth/logout')
export const fetchMe = () => http.get('/auth/me')
export const changePassword = (oldPassword, newPassword) =>
  http.post('/auth/change-password', { old_password: oldPassword, new_password: newPassword })

// ===== 用户管理（admin） =====
export const fetchUsers = () => http.get('/admin/users')
export const fetchRoles = () => http.get('/admin/roles')
export const createUser = (payload) => http.post('/admin/users', payload)
export const patchUser = (id, payload) => http.patch(`/admin/users/${id}`, payload)
export const unlockUser = (id) => http.post(`/admin/users/${id}/unlock`)

// ===== 菜单与角色权限管理 =====
export const fetchMenuTree = () => http.get('/admin/menus')
export const createMenu = (payload) => http.post('/admin/menus', payload)
export const patchMenu = (id, payload) => http.patch(`/admin/menus/${id}`, payload)
export const deleteMenu = (id) => http.delete(`/admin/menus/${id}`)
export const fetchPermissions = () => http.get('/admin/permissions')
export const createRole = (payload) => http.post('/admin/roles', payload)
export const createPermission = (payload) => http.post('/admin/permissions', payload)
export const assignRoleMenus = (roleId, menuCodes) =>
  http.put(`/admin/roles/${roleId}/menus`, { menu_codes: menuCodes })
export const assignRolePermissions = (roleId, permissionCodes) =>
  http.put(`/admin/roles/${roleId}/permissions`, { permission_codes: permissionCodes })

// ===== 业务 =====
export const fetchTickets = (status) => http.get('/tickets', { params: status ? { status } : {} })
export const fetchTicket = (id) => http.get(`/tickets/${id}`)
export const fetchDict = () => http.get('/dict')
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
export const fetchStressStatus = () => http.get('/tools/cpu-stress')
export const startCpuStress = (durationSeconds) => http.post('/tools/cpu-stress', { duration_seconds: durationSeconds })
export const stopCpuStress = () => http.post('/tools/cpu-stress/stop')
