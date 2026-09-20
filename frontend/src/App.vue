<template>
  <el-container class="layout">
    <el-aside width="232px" class="aside">
      <div class="brand">
        <h1>DevOpsAgent</h1>
        <p>智能运维数字员工 · 工作台</p>
      </div>
      <el-menu :router="true" :default-active="$route.path" background-color="transparent">
        <el-menu-item index="/tickets">任务单</el-menu-item>
        <el-menu-item index="/employee">数字员工</el-menu-item>
        <el-menu-item index="/assets">资产台账</el-menu-item>
        <el-menu-item index="/backups">备份</el-menu-item>
        <el-menu-item index="/notifications">通知</el-menu-item>
        <el-menu-item index="/report">日报</el-menu-item>
        <el-menu-item index="/status">集成状态</el-menu-item>
        <el-menu-item v-if="auth.isAdmin()" index="/users">用户管理</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header" height="60px">
        <div class="slogan">LLM 只做 <b>分析与建议</b> · 策略引擎做 <b>决定</b> · 执行器做 <b>动作</b> · 证据链做 <b>证明</b></div>
        <div class="user-box">
          <el-tag type="success" effect="plain">DE-OPS-001 在岗</el-tag>
          <el-dropdown v-if="auth.user" @command="onCommand">
            <span class="user-chip">
              <el-icon><UserFilled /></el-icon>
              {{ auth.user.display_name || auth.user.username }}
              <el-tag size="small" type="info" style="margin-left: 4px">{{ roleLabel }}</el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="password">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>

    <el-dialog v-model="pwdVisible" title="修改密码" width="440px">
      <el-form label-width="90px">
        <el-form-item label="原密码">
          <el-input v-model="pwd.old" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwd.new1" type="password" show-password placeholder="8-64 位，含大小写字母与数字" />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="pwd.new2" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="doChangePwd">确认修改</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { auth } from './auth'
import { changePassword } from './api'

const router = useRouter()
const roleLabels = { admin: '管理员', operator: '操作员', viewer: '只读' }
const roleLabel = computed(() => {
  const codes = auth.user?.roles || []
  return roleLabels[codes[0]] || codes[0] || ''
})

const pwdVisible = ref(false)
const pwdSaving = ref(false)
const pwd = reactive({ old: '', new1: '', new2: '' })

function onCommand(cmd) {
  if (cmd === 'logout') {
    doLogout()
  } else if (cmd === 'password') {
    Object.assign(pwd, { old: '', new1: '', new2: '' })
    pwdVisible.value = true
  }
}

async function doLogout() {
  await auth.logout()
  router.push('/login')
}

async function doChangePwd() {
  if (!pwd.old || !pwd.new1) {
    ElMessage.warning('请填写完整')
    return
  }
  if (pwd.new1 !== pwd.new2) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  pwdSaving.value = true
  try {
    await changePassword(pwd.old, pwd.new1)
    ElMessage.success('密码已修改，请重新登录')
    pwdVisible.value = false
    auth.clear()
    router.push('/login')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '修改失败')
  } finally {
    pwdSaving.value = false
  }
}
</script>

<style scoped>
.user-box {
  display: flex;
  align-items: center;
  gap: 14px;
}
.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  color: #e5eaf3;
  font-size: 14px;
  outline: none;
}
</style>
