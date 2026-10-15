<template>
  <div class="recovery-scripts">
    <el-card shadow="never">
      <div class="head">
        <div>
          <h3 class="title">恢复脚本</h3>
          <p class="desc">告警规则命中后自动联动：低风险脚本告警触发即自动执行，高风险脚本生成恢复任务等人工确认执行（SSH 上机 root 运行）。</p>
        </div>
        <el-button type="primary" @click="openEdit()">新建脚本</el-button>
      </div>

      <el-table v-loading="loading" :data="scripts" border>
        <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
        <el-table-column prop="description" label="说明" min-width="180" show-overflow-tooltip />
        <el-table-column label="匹配规则" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.rule_key" size="small">{{ cnRule(row.rule_key) }}</el-tag>
            <el-tag v-else size="small" type="info">全部规则</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="风险等级" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.risk_level === 'high' ? 'danger' : 'success'">
              {{ row.risk_level === 'high' ? '高风险·人工' : '低风险·自动' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="command" label="命令" min-width="220" show-overflow-tooltip>
          <template #default="{ row }"><code class="cmd">{{ row.command }}</code></template>
        </el-table-column>
        <el-table-column label="超时" width="80">
          <template #default="{ row }">{{ row.timeout_seconds }}s</template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v) => toggleEnabled(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialog" :title="form.id ? '编辑脚本' : '新建脚本'" width="640" destroy-on-close>
      <el-form :model="form" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" maxlength="64" placeholder="如：CPU 过高自动降载" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" maxlength="256" placeholder="脚本用途与副作用说明" />
        </el-form-item>
        <el-form-item label="匹配规则">
          <el-select v-model="form.rule_key" clearable filterable allow-create placeholder="选择告警规则或输入自定义 key（留空匹配全部）" style="width: 100%">
            <el-option v-for="r in RULE_OPTIONS" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="风险等级">
          <el-radio-group v-model="form.risk_level">
            <el-radio value="low">低风险：告警触发后自动执行</el-radio>
            <el-radio value="high">高风险：生成任务等人工执行</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="命令" required>
          <el-input v-model="form.command" type="textarea" :rows="4" placeholder="#!/bin/sh 段落的 shell 命令，目标机 root 执行&#10;例如：systemctl restart order-app" />
        </el-form-item>
        <el-form-item label="超时（秒）">
          <el-input-number v-model="form.timeout_seconds" :min="5" :max="1800" :step="5" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listRecoveryScripts, createRecoveryScript, updateRecoveryScript, deleteRecoveryScript } from '../api'

// 告警规则 key 目录（与告警策略一致；应用层规则可自定义输入）
const RULE_OPTIONS = [
  { value: 'cpu', label: 'CPU 使用率' },
  { value: 'mem', label: '内存使用率' },
  { value: 'load1', label: '1分钟负载' },
  { value: 'swap', label: 'Swap 使用率' },
  { value: 'inode', label: 'Inode 使用率' },
  { value: 'await_ms', label: '磁盘 IO 延迟' },
  { value: 'loss_pct', label: '网络丢包率' },
  { value: 'latency_ms', label: '网络延迟' },
  { value: 'bw_rx_pct', label: '入口带宽使用率' },
  { value: 'bw_tx_pct', label: '出口带宽使用率' },
  { value: 'tcp_tw', label: 'TIME_WAIT 连接数' },
  { value: 'tcp_conn_pct', label: 'TCP 连接数/上限' },
  { value: 'oom', label: 'OOM kill 事件' },
  { value: 'process', label: '关键进程消失' },
  { value: 'port', label: '关键端口探活失败' },
]
function cnRule(key) {
  const hit = RULE_OPTIONS.find((r) => r.value === key)
  return hit ? hit.label : key
}

const scripts = ref([])
const loading = ref(false)
const dialog = ref(false)
const saving = ref(false)
const emptyForm = () => ({
  id: null, name: '', description: '', rule_key: '',
  risk_level: 'low', command: '', timeout_seconds: 60, enabled: true,
})
const form = ref(emptyForm())
let timer = null

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const { data } = await listRecoveryScripts()
    scripts.value = data.items || []
  } catch {
    /* 静默 */
  } finally {
    if (!silent) loading.value = false
  }
}

function openEdit(row) {
  form.value = row ? { ...row } : emptyForm()
  dialog.value = true
}

async function save() {
  if (!form.value.name || !form.value.command) {
    ElMessage.warning('名称与命令必填')
    return
  }
  saving.value = true
  try {
    const payload = { ...form.value }
    const id = payload.id
    delete payload.id
    if (id) await updateRecoveryScript(id, payload)
    else await createRecoveryScript(payload)
    ElMessage.success('已保存')
    dialog.value = false
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row, v) {
  try {
    await updateRecoveryScript(row.id, { ...row, enabled: v })
    row.enabled = v
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '更新失败')
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确认删除脚本「${row.name}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteRecoveryScript(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  }
}

onMounted(() => {
  load()
  timer = setInterval(() => load(true), 10000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.recovery-scripts {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 14px;
  gap: 16px;
}
.title {
  margin: 0 0 4px;
  font-size: 16px;
}
.desc {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.cmd {
  font-family: Menlo, Consolas, monospace;
  font-size: 12px;
}
</style>
