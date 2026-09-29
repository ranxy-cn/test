// 登录态管理：token/user 持久化 + 登录/登出/拉取当前用户
import { http, login as apiLogin, logout as apiLogout, fetchMe } from './api'

const TOKEN_KEY = 'devops_token'
const USER_KEY = 'devops_user'

function readJSON(key) {
  try {
    return JSON.parse(localStorage.getItem(key))
  } catch {
    return null
  }
}

export const auth = {
  get token() {
    return localStorage.getItem(TOKEN_KEY) || ''
  },
  user: readJSON(USER_KEY),

  isLoggedIn() {
    return !!this.token
  },

  isAdmin() {
    return !!this.user && Array.isArray(this.user.roles) && this.user.roles.includes('admin')
  },

  has(perm) {
    return !!this.user && Array.isArray(this.user.permissions) && this.user.permissions.includes(perm)
  },

  async login(username, password) {
    const { data } = await apiLogin(username, password)
    localStorage.setItem(TOKEN_KEY, data.access_token)
    this.user = data.user
    localStorage.setItem(USER_KEY, JSON.stringify(data.user))
    return data.user
  },

  async logout() {
    try {
      await apiLogout()
    } catch {
      // token 失效时静默忽略
    }
    this.clear()
  },

  clear() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    this.user = null
  },

  // 路由守卫用：有 token 但内存态丢失时补拉一次
  async ensureLoaded() {
    if (!this.token) return null
    if (this.user) return this.user
    try {
      const { data } = await fetchMe()
      this.user = data
      localStorage.setItem(USER_KEY, JSON.stringify(data))
      return data
    } catch {
      this.clear()
      return null
    }
  },
}

export function authHeader() {
  return auth.token ? { Authorization: `Bearer ${auth.token}` } : {}
}

// 供其他模块（如文件下载）使用
export { http }
