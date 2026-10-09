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
// 母机名下全部子机实时指标批量端点（分组卡片 1 秒轮询；与监控详情实时面板同源）
export const fetchChildrenRealtime = (id) => http.get(`/assets/mothers/${id}/children/realtime`)
export const createMother = (payload) => http.post('/assets/mothers', payload)
export const renameGroup = (motherId, name, newName) =>
  http.post(`/assets/mothers/${motherId}/groups/rename`, { name, new_name: newName })
export const deleteGroup = (motherId, name) => http.post(`/assets/mothers/${motherId}/groups/delete`, { name })
// 告警策略统一端点：asset_id 可为母机或子机（子机自有策略 > 继承母机 > 平台默认）
export const fetchAlertPolicy = (id) => http.get(`/assets/${id}/alert-policy`, { timeout: 20000 })
export const updateAlertPolicy = (id, policy) =>
  http.put(`/assets/${id}/alert-policy`, policy, { timeout: 60000 })
export const resetAlertPolicy = (id) => http.delete(`/assets/${id}/alert-policy`, { timeout: 20000 })

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
// 删除子机：uninstall=true 时后端先 SSH 卸载服务器上的自研 agent 再删台账
export const removeAsset = (id, payload = {}) => http.post(`/assets/${id}/remove`, payload, { timeout: 240000 })
export const fetchAssetMetrics = (id, minutes = 60) => http.get(`/assets/${id}/metrics`, { params: { minutes } })
// 本机系统资源实时监控（类 macOS 活动监视器）：1 秒轮询实时值 / 后台采样落库历史
// assetId：子机=该子机 Agent 上报；母机=其本机子机曲线；留空=平台本机
export const fetchSystemRealtime = (assetId = '') =>
  http.get('/system/metrics/realtime', { params: assetId ? { asset_id: assetId } : {} })
export const fetchSystemHistory = (minutes = 60, assetId = '') =>
  http.get('/system/metrics/history', { params: assetId ? { minutes, asset_id: assetId } : { minutes } })
// SSH 连通性测试（新增母机/子机前的「测试连接」按钮）
export const testSsh = (payload) => http.post('/assets/ssh-test', payload, { timeout: 45000 })
// 子机 Agent：全局默认配置 + 资产级覆盖 + SSH 部署（py/go）
export const fetchAgentConfigDefaults = () => http.get('/agent-config/defaults')
export const updateAgentConfigDefaults = (patch) => http.put('/agent-config/defaults', patch)
export const updateAgentAssetConfig = (id, patch) => http.put(`/assets/${id}/agent/config`, patch)
export const resetAgentAssetConfig = (id) => http.delete(`/assets/${id}/agent/config`)
export const fetchAgentStatus = (id) => http.get(`/assets/${id}/agent/status`)
export const deployAgent = (id, payload) => http.post(`/assets/${id}/agent/deploy`, payload, { timeout: 30000 })
export const fetchAgentDeployStatus = (id) => http.get(`/assets/${id}/agent/deploy`)
export const inspectAsset = (id, payload) => http.post(`/assets/${id}/inspect`, payload, { timeout: 20000 })
export const fetchAssetSysinfo = (id, payload = {}) => http.post(`/assets/${id}/sysinfo`, payload, { timeout: 25000 })
// SSH 终端：在资产上实时执行一条命令并回显（免密巡检通道，命令/输出即内容）
export const execAssetCommand = (id, payload) => http.post(`/assets/${id}/exec`, payload, { timeout: 30000 })
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
export const fetchDashboardOverview = () => http.get('/dashboard/overview')
export const fetchDashboardNetdata = (params) => http.get('/dashboard/netdata', { params: params || { limit: 12, minutes: 5 } })
export const fetchKnowledgeDocuments = () => http.get('/knowledge/documents')
export const searchKnowledge = (q, limit = 6) => http.get('/knowledge/search', { params: { q, limit } })
export const uploadKnowledgeDocument = (file) => {
  const form = new FormData()
  form.append('file', file)
  return http.post('/knowledge/documents/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}
export const deleteKnowledgeDocument = (id) => http.delete(`/knowledge/documents/${id}`)
export const sendKnowledgeChat = (message, history = []) => http.post('/knowledge/chat', { message, history }, { timeout: 90000 })

// ===== AI 日志分析 =====
export const getAiSettings = () => http.get('/ai/settings')
export const saveAiSettings = (payload) => http.put('/ai/settings', payload)
export const testAiConnection = (payload = {}) => http.post('/ai/settings/test', payload, { timeout: 120000 })
export const listAiAnalyses = (params) => http.get('/ai/analyses', { params: params || {} })
export const getAiAnalysis = (id) => http.get(`/ai/analyses/${id}`)
export const feedbackAiAnalysis = (id, payload) => http.post(`/ai/analyses/${id}/feedback`, payload)
export const triggerAiAnalysis = (anomalyId) => http.post(`/ai/anomalies/${anomalyId}/analyze`)

// ===== 告警恢复任务 / 恢复脚本 =====
export const listRecoveryTasks = (params) => http.get('/recovery/tasks', { params: params || {} })
export const executeRecoveryTask = (id) => http.post(`/recovery/tasks/${id}/execute`, null, { timeout: 180000 })
export const cancelRecoveryTask = (id) => http.post(`/recovery/tasks/${id}/cancel`)
export const listRecoveryScripts = () => http.get('/recovery/scripts')
export const createRecoveryScript = (payload) => http.post('/recovery/scripts', payload)
export const updateRecoveryScript = (id, payload) => http.put(`/recovery/scripts/${id}`, payload)
export const deleteRecoveryScript = (id) => http.delete(`/recovery/scripts/${id}`)
