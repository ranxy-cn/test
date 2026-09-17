<template>
  <el-card header="通知中心">
    <el-space style="margin-bottom: 12px">
      <el-switch v-model="unreadOnly" active-text="仅未读" @change="load" />
      <el-button @click="load">刷新</el-button>
    </el-space>
    <el-table :data="items">
      <el-table-column prop="created_at" label="时间" width="190" />
      <el-table-column prop="kind" label="类型" width="140" />
      <el-table-column prop="channel" label="通道" width="90" />
      <el-table-column prop="title" label="标题" width="140" />
      <el-table-column prop="body" label="内容" />
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button v-if="!row.read && row.channel === 'inbox'" size="small" @click="read(row)">已读</el-button>
          <span v-else>{{ row.read ? '已读' : '' }}</span>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { fetchNotifications, markNotificationRead } from '../api'

const items = ref([])
const unreadOnly = ref(false)

async function load() {
  items.value = (await fetchNotifications(unreadOnly.value ? { unread: true } : {})).data.items
}
async function read(row) {
  await markNotificationRead(row.id)
  await load()
}
onMounted(load)
</script>
