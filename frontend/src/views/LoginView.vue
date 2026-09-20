<template>
  <div class="login-page">
    <el-card class="login-card" shadow="always">
      <div class="login-brand">
        <h1>DevOpsAgent</h1>
        <p>智能运维数字员工 · 工作台登录</p>
      </div>
      <el-form :model="form" @keyup.enter="doLogin">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" size="large" autofocus>
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" placeholder="密码" size="large" show-password>
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" @click="doLogin">
          登 录
        </el-button>
      </el-form>
      <div class="login-tip">连续失败 5 次将锁定账号 15 分钟</div>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { auth } from '../auth'

const router = useRouter()
const route = useRoute()
const form = reactive({ username: '', password: '' })
const loading = ref(false)

async function doLogin() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const user = await auth.login(form.username, form.password)
    ElMessage.success(`欢迎，${user.display_name || user.username}`)
    router.push(route.query.redirect || '/tickets')
  } catch (e) {
    const detail = e.response?.data?.detail
    ElMessage.error(typeof detail === 'string' ? detail : '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #10182b 0%, #1d2b4f 55%, #16324f 100%);
}
.login-card {
  width: 380px;
  border-radius: 12px;
}
.login-brand {
  text-align: center;
  margin-bottom: 18px;
}
.login-brand h1 {
  margin: 0;
  font-size: 26px;
  letter-spacing: 1px;
}
.login-brand p {
  margin: 6px 0 0;
  color: #8a94a6;
  font-size: 13px;
}
.login-tip {
  margin-top: 14px;
  text-align: center;
  color: #98a2b3;
  font-size: 12px;
}
</style>
