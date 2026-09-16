<template>
  <el-card header="订单系统 · CMDB 试点资产">
    <el-table :data="items">
      <el-table-column prop="id" label="asset_id" width="200" />
      <el-table-column prop="hostname" label="主机" />
      <el-table-column prop="role" label="角色" width="100" />
      <el-table-column prop="owner" label="负责人" width="100" />
      <el-table-column prop="env" label="环境" width="80" />
      <el-table-column label="可达" width="80">
        <template #default="{ row }">{{ row.reachable ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column label="DB" width="80">
        <template #default="{ row }">{{ row.db_ok ? '正常' : '异常' }}</template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { fetchAssets } from '../api'
const items = ref([])
onMounted(async () => {
  items.value = (await fetchAssets()).data.items
})
</script>
