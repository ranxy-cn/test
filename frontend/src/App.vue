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
        <el-menu-item index="/report">日报</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header" height="60px">
        <div class="slogan">LLM 只做 <b>分析与建议</b> · 策略引擎做 <b>决定</b> · 执行器做 <b>动作</b> · 证据链做 <b>证明</b></div>
        <el-tag type="success" effect="plain">DE-OPS-001 在岗</el-tag>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
</script>
