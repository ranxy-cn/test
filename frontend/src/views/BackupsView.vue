<template>
  <div>
    <el-alert :title="data?.note" type="warning" :closable="false" style="margin-bottom: 14px" />
    <el-table :data="data?.items || []">
      <el-table-column prop="id" label="任务" width="180" />
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="asset_id" label="资产" width="170" />
      <el-table-column prop="backup_status" label="备份" width="100" />
      <el-table-column prop="restore_status" label="恢复验证" width="110" />
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="doRun(row.id)">触发备份</el-button>
          <el-button size="small" @click="doVerify(row.id)">标记恢复验证</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-card header="最近运行" style="margin-top: 16px">
      <el-table :data="runs">
        <el-table-column prop="id" label="#" width="70" />
        <el-table-column prop="job_id" label="任务" />
        <el-table-column prop="status" label="状态" width="140" />
        <el-table-column label="备份成功" width="100">
          <template #default="{ row }">{{ row.backup_ok === true ? '是' : row.backup_ok === false ? '否' : '—' }}</template>
        </el-table-column>
        <el-table-column label="恢复验证" width="100">
          <template #default="{ row }">{{ row.restore_verified === true ? '是' : '否' }}</template>
        </el-table-column>
        <el-table-column prop="note" label="说明" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchBackupRuns, fetchBackups, runBackup, verifyRestore } from '../api'

const data = ref(null)
const runs = ref([])

async function load() {
  data.value = (await fetchBackups()).data
  runs.value = (await fetchBackupRuns()).data.items
}
async function doRun(id) {
  await runBackup(id)
  ElMessage.success('已触发 mock 备份（备份成功，恢复验证仍为未执行）')
  await load()
}
async function doVerify(id) {
  await verifyRestore(id)
  ElMessage.success('已单独记录恢复验证')
  await load()
}
onMounted(load)
</script>
