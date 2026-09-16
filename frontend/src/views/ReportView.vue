<template>
  <div v-if="report">
    <div class="card-grid">
      <el-card shadow="never"><b>日期</b><div>{{ report.date }}</div></el-card>
      <el-card shadow="never"><b>计划 / 完成</b><div>{{ report.planned }} / {{ report.completed }}</div></el-card>
      <el-card shadow="never"><b>负责系统</b><div>{{ (report.systems || []).join('、') }}</div></el-card>
      <el-card shadow="never"><b>备份</b><div>{{ report.backups.status }}</div></el-card>
    </div>
    <el-alert :title="report.note" type="warning" :closable="false" style="margin-bottom: 14px" />
    <el-card header="当日异常">
      <el-table :data="report.anomalies">
        <el-table-column prop="number" label="编号" width="170" />
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="status" label="状态" width="140" />
        <el-table-column prop="root_cause" label="根因" />
      </el-table>
    </el-card>
    <el-card header="未结事项（含升级交接）" style="margin-top: 16px">
      <el-table :data="report.open_items">
        <el-table-column prop="number" label="编号" width="170" />
        <el-table-column prop="owner" label="负责人" width="100" />
        <el-table-column prop="status" label="状态" width="140" />
        <el-table-column prop="escalate_reason" label="说明" />
      </el-table>
      <p style="color:#64748b;font-size:13px">{{ report.backups.note }}</p>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { fetchReport } from '../api'
const report = ref(null)
onMounted(async () => {
  report.value = (await fetchReport()).data
})
</script>
