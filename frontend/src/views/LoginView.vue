<template>
  <div class="login-page">
    <el-card class="login-card">
      <div class="login-brand">
        <h1>DevOpsAgent</h1>
        <p>智能巡检 · 工作台登录</p>
      </div>
      <el-form :model="form" @submit.prevent="submit">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" autocomplete="username" />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            show-password
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" style="width: 100%" :loading="loading" @click="submit">登录</el-button>
      </el-form>
      <p class="login-hint">演示默认账号见 .env.example 的 ADMIN_USERNAME / ADMIN_PASSWORD。不要把生产密码提交进仓库。</p>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login, setToken } from '../api'

const router = useRouter()
const route = useRoute()
const loading = ref(false)
const form = reactive({ username: 'admin', password: 'admin' })

async function submit() {
  loading.value = true
  try {
    const { data } = await login(form.username, form.password)
    setToken(data.access_token)
    ElMessage.success(`欢迎 ${data.username}`)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/tickets'
    router.replace(redirect || '/tickets')
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>
