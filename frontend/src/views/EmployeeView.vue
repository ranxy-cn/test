<template>
  <el-card v-if="emp">
    <template #header>数字员工档案</template>
    <el-descriptions :column="2" border>
      <el-descriptions-item label="工号">{{ emp.id }}</el-descriptions-item>
      <el-descriptions-item label="名称">{{ emp.name }}</el-descriptions-item>
      <el-descriptions-item label="团队">{{ emp.team }}</el-descriptions-item>
      <el-descriptions-item label="状态">{{ emp.status === 'active' ? '在岗' : emp.status }}</el-descriptions-item>
      <el-descriptions-item label="负责系统">{{ (emp.systems || []).join('、') }}</el-descriptions-item>
      <el-descriptions-item label="真人主管">{{ emp.manager }}</el-descriptions-item>
      <el-descriptions-item label="值班联系人">{{ emp.oncall }}</el-descriptions-item>
      <el-descriptions-item label="技能版本">{{ emp.skill_version }}</el-descriptions-item>
      <el-descriptions-item label="授权期限">{{ fmtTime(emp.auth_expires_at) }}</el-descriptions-item>
      <el-descriptions-item label="累计任务">{{ emp.stats?.tickets }}</el-descriptions-item>
      <el-descriptions-item label="已恢复">{{ emp.stats?.recovered }}</el-descriptions-item>
    </el-descriptions>
    <h3 style="margin-top: 18px">职责</h3>
    <ul>
      <li v-for="d in emp.duties" :key="d">{{ d }}</li>
    </ul>
    <el-alert :title="emp.slogan" type="info" :closable="false" />
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { fetchEmployee } from '../api'
import { fmtTime } from '../time'
const emp = ref(null)
onMounted(async () => {
  emp.value = (await fetchEmployee()).data
})
</script>
