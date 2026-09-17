<template>
  <div>
    <el-card header="集成模式与健康探测">
      <p v-if="status">全局 INTEGRATION_MODE：<b>{{ status.integrations?.integration_mode }}</b></p>
      <el-table v-if="status" :data="rows" style="margin-top: 12px">
        <el-table-column prop="name" label="组件" width="120" />
        <el-table-column prop="requested" label="请求" width="90" />
        <el-table-column prop="mode" label="生效" width="90" />
        <el-table-column prop="ok" label="探测" width="80" />
        <el-table-column prop="version" label="版本" width="90" />
        <el-table-column prop="latency" label="延迟" width="90" />
        <el-table-column prop="detail" label="说明 / 最近错误" />
      </el-table>
      <el-alert
        style="margin-top: 14px"
        title="无真实凭据或探测失败时保持 mock，demo 可跑。Zabbix 只读：ZABBIX_MODE=auto 时有 URL+Token 且 health 通过才用 real。"
        type="info"
        :closable="false"
      />
    </el-card>
    <el-card header="资源锁" style="margin-top: 16px">
      <el-table :data="locks">
        <el-table-column prop="asset_id" label="资产" />
        <el-table-column prop="ticket_id" label="任务" width="90" />
        <el-table-column prop="holder" label="持有者" width="140" />
        <el-table-column prop="expires_at" label="过期" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { fetchLocks, fetchStatus } from '../api'

const status = ref(null)
const locks = ref([])
const rows = computed(() => {
  const i = status.value?.integrations || {}
  const p = status.value?.probes || {}
  return ['zabbix', 'ansible', 'vault'].map((name) => ({
    name,
    requested: i[name]?.requested,
    mode: i[name]?.mode,
    ok: p[name]?.ok ? 'OK' : '异常/占位',
    version: i[name]?.version || p[name]?.version || '—',
    latency: (p[name]?.latency_ms ?? i[name]?.latency_ms) != null ? `${p[name]?.latency_ms ?? i[name]?.latency_ms} ms` : '—',
    detail: i[name]?.fallback_reason || p[name]?.last_error || p[name]?.detail || p[name]?.mode,
  }))
})

onMounted(async () => {
  status.value = (await fetchStatus()).data
  locks.value = (await fetchLocks()).data.items
})
</script>
