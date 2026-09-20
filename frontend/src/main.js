import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import './styles.css'

const app = createApp(App)
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

// v-perm 按钮级权限指令：无对应权限点时隐藏元素
// 用法：<el-button v-perm="'tickets:operate'">审批</el-button>
app.directive('perm', {
  mounted(el, binding) {
    const perm = binding.value
    if (!perm) return
    let allowed = false
    try {
      const user = JSON.parse(localStorage.getItem('devops_user') || 'null')
      allowed = !!user && Array.isArray(user.permissions) && user.permissions.includes(perm)
    } catch {
      allowed = false
    }
    if (!allowed) el.style.display = 'none'
  },
})

app.use(router)
app.use(ElementPlus, { locale: zhCn })
// 等首次导航（含登录守卫重定向）完成后再挂载，避免未登录时首帧闪现工作台布局
router.isReady().then(() => app.mount('#app'))
