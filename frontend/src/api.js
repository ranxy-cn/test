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
export const fetchAnomalies = (params) => http.get('/anomalies', { params: params || {} })
export const fetchAnomalyStats = () => http.get('/anomalies/stats')
export const fetchAnomalyDetail = (id) => http.get(`/anomalies/${id}`)
export const runAnomalySnapshot = (id) => http.post(`/anomalies/${id}/snapshot`)
export const fetchTickets = (params) => http.get('/tickets', { params: params || {} })
export const fetchTicket = (id) => http.get(`/tickets/${id}`)
export const fetchDict = () => http.get('/dict')
export const approveTicket = (id, payload) => http.post(`/tickets/${id}/approve`, payload)
export const rejectTicket = (id, payload) => http.post(`/tickets/${id}/reject`, payload)
export const fetchEmployee = (id = 'DE-OPS-001') => http.get(`/employee/${id}`)
export const fetchReport = (date) => http.get('/reports/daily', { params: date ? { date } : {} })
export const fetchAssets = (params) => http.get('/assets', { params })

export const fetchMotherOverview = () => http.get('/assets/mother')
export const fetchMothers = () => http.get('/assets/mothers')
export const fetchMotherGroups = (id) => http.get(`/assets/mothers/${id}/groups`)
export const fetchMotherOverviewById = (id) => http.get(`/assets/mothers/${id}/overview`)
export const createMother = (payload) => http.post('/assets/mothers', payload)
export const deployMotherStack = (id, payload) => http.post(`/assets/mothers/${id}/deploy`, payload, { timeout: 30000 })
export const fetchMotherDeployStatus = (id) => http.get(`/assets/mothers/${id}/deploy`)
export const fetchMotherDeployDetail = (id) => http.get(`/assets/mothers/${id}/deploy-detail`)
export const uninstallMother = (id, payload) => http.post(`/assets/mothers/${id}/uninstall`, payload, { timeout: 30000 })
export const renameGroup = (motherId, name, newName) =>
  http.post(`/assets/mothers/${motherId}/groups/rename`, { name, new_name: newName })
export const deleteGroup = (motherId, name) => http.post(`/assets/mothers/${motherId}/groups/delete`, { name })
export const verifyZabbix = (payload) => http.post('/assets/verify-zabbix', payload, { timeout: 20000 })
export const fetchAlertPolicy = (id) => http.get(`/assets/mothers/${id}/alert-policy`, { timeout: 20000 })
export const updateAlertPolicy = (id, policy) =>
  http.put(`/assets/mothers/${id}/alert-policy`, policy, { timeout: 60000 })

export const updateAsset = (id, payload) => http.patch(`/assets/${id}`, payload)
export const fetchAsset = (id) => http.get(`/assets/${id}`)
export const deleteAsset = (id) => http.delete(`/assets/${id}`)
export const exportAssets = () => http.get('/assets/export', { responseType: 'blob', timeout: 120000 })
export const importAssets = (file) => {
  const form = new FormData()
  form.append('file', file)
  return http.post('/assets/import', form, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 120000 })
}
export const provisionAsset = (payload) => http.post('/assets/provision', payload)
export const fetchAssetProvision = (id) => http.get(`/assets/${id}/provision`)
// 删除子机：uninstall=true 时后端先 SSH 卸载服务器上的 zabbix-agent 再删台账
export const removeAsset = (id, payload = {}) => http.post(`/assets/${id}/remove`, payload, { timeout: 240000 })
export const probeAssets = () => http.post('/assets/probe', {}, { timeout: 60000 })
export const fetchAssetMetrics = (id, minutes = 60) => http.get(`/assets/${id}/metrics`, { params: { minutes } })
export const inspectAsset = (id, payload) => http.post(`/assets/${id}/inspect`, payload, { timeout: 20000 })
export const fetchAssetSysinfo = (id, payload = {}) => http.post(`/assets/${id}/sysinfo`, payload, { timeout: 25000 })
export const runAction = (payload) => http.post('/actions/run', payload)
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
export const startCpuStress = (durationSeconds, assetId = '') =>
  http.post('/tools/cpu-stress', { duration_seconds: durationSeconds, asset_id: assetId })
export const stopCpuStress = () => http.post('/tools/cpu-stress/stop')
export const fetchMemStressStatus = () => http.get('/tools/mem-stress')
export const startMemStress = (targetPercent, durationSeconds, assetId = '') =>
  http.post('/tools/mem-stress', { target_percent: targetPercent, duration_seconds: durationSeconds, asset_id: assetId })
export const stopMemStress = () => http.post('/tools/mem-stress/stop')
export const fetchStressTargets = () => http.get('/tools/stress-targets')
export const fetchRemoteStress = () => http.get('/tools/remote-stress')
export const stopRemoteStress = (assetId) => http.post('/tools/stress/stop', { asset_id: assetId })
