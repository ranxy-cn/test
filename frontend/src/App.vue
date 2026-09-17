<template>
  <router-view v-if="isPublic" />
  <el-container v-else class="layout">
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
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header" height="60px">
        <div class="slogan">LLM 只做 <b>分析与建议</b> · 策略引擎做 <b>决定</b> · 执行器做 <b>动作</b> · 证据链做 <b>证明</b></div>
        <div class="header-actions">
          <el-tag type="success" effect="plain">DE-OPS-001 在岗</el-tag>
          <el-button text type="primary" @click="onLogout">退出</el-button>
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { logout } from './api'

const route = useRoute()
const router = useRouter()
const isPublic = computed(() => Boolean(route.meta.public))

function onLogout() {
  logout()
  router.replace('/login')
}
</script>
