<template>
  <el-dialog
    :model-value="modelValue"
    :title="`添加进程监听 · ${asset?.hostname || asset?.id || ''}`"
    width="540"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert type="info" :closable="false" class="pw-tip">
      保存后由该子机的自研 Agent 自动采集识别，约 10–20 秒内回填进程信息，无需登录服务器。
    </el-alert>
    <el-form label-width="90" style="margin-top: 12px">
      <el-form-item label="进程类型">
        <el-radio-group v-model="form.ptype" @change="onPtypeChange">
          <el-radio-button v-for="p in PTYPE_OPTIONS" :key="p.value" :value="p.value">{{ p.label }}</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="定位方式">
        <el-radio-group v-model="form.match" @change="onMatchChange">
          <template v-if="form.ptype === 'docker'">
            <el-radio-button value="container">按容器名/ID</el-radio-button>
            <el-radio-button value="pid">按主进程 PID</el-radio-button>
            <el-radio-button value="port">按端口号</el-radio-button>
          </template>
          <template v-else>
            <el-radio-button value="name">按程序名称</el-radio-button>
            <el-radio-button value="pid">按 PID</el-radio-button>
            <el-radio-button value="port">按端口号</el-radio-button>
          </template>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.match === 'pid'" label="进程 PID" required>
        <el-input
          v-model="form.pid"
          placeholder="如 1234（纯数字）"
          style="width: 260px"
          maxlength="16"
          @keyup.enter="submit"
        />
      </el-form-item>
      <el-form-item v-else-if="form.match === 'port'" label="监听端口" required>
        <el-input
          v-model="form.port"
          placeholder="进程监听的端口号，如 8080"
          style="width: 260px"
          maxlength="5"
          @keyup.enter="submit"
        />
      </el-form-item>
      <el-form-item v-else-if="form.ptype === 'docker'" label="容器" required>
        <el-input
          v-model="form.container"
          placeholder="容器名称或容器 ID（服务器上 docker ps 查看）"
          style="width: 320px"
          maxlength="128"
          @keyup.enter="submit"
        />
      </el-form-item>
      <el-form-item v-else label="程序名称" required>
        <el-input
          v-model="form.name"
          placeholder="命令行包含的关键字，如 nginx / my-app.jar"
          style="width: 320px"
          maxlength="128"
          @keyup.enter="submit"
        />
      </el-form-item>
      <div class="pw-hint">{{ currentTip }}</div>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">添加监听</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
// 添加进程监听弹窗：三类进程（外部程序 / Tomcat / Docker 容器）× 定位方式（名称/PID/端口/容器名）。
// 仅保存监听配置到资产，进程识别与信息采集由子机自研 Agent 完成后随帧回填（无 SSH）。
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { addProcessWatch } from '../api'

const props = defineProps({
  modelValue: Boolean,
  asset: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const PTYPE_OPTIONS = [
  { value: 'external', label: '外部程序', tip: '宿主机上的独立进程（nginx、redis 等）：按命令行关键字、PID 或监听端口定位，Agent 采集运行状态与监听端口' },
  { value: 'tomcat', label: 'Tomcat 服务', tip: 'Java/Tomcat 服务进程：定位方式与外部程序一致，额外识别 CATALINA_BASE 部署目录' },
  { value: 'docker', label: 'Docker 容器', tip: '容器内主进程：按容器名/ID 跟踪（推荐，容器重启不失效），或按主进程 PID / 宿主发布端口反查容器' },
]
const currentTip = computed(() => (PTYPE_OPTIONS.find((p) => p.value === form.ptype) || {}).tip || '')

const form = reactive({ ptype: 'external', match: 'name', pid: '', name: '', container: '', port: '' })
const submitting = ref(false)

watch(
  () => props.modelValue,
  (v) => {
    if (v) Object.assign(form, { ptype: 'external', match: 'name', pid: '', name: '', container: '', port: '' })
  },
)

function onPtypeChange() {
  form.match = form.ptype === 'docker' ? 'container' : 'name'
}

function onMatchChange() {
  // 切换定位方式时清空无关输入，避免残留值误提交
  Object.assign(form, { pid: '', name: '', container: '', port: '' })
}

async function submit() {
  const payload = { ptype: form.ptype, match: form.match }
  if (form.match === 'pid') {
    payload.pid = form.pid.trim()
    if (!/^\d{1,16}$/.test(payload.pid)) {
      ElMessage.warning('请填写正确的进程 PID（纯数字）')
      return
    }
  } else if (form.match === 'port') {
    payload.port = form.port.trim()
    const n = Number(payload.port)
    if (!/^\d{1,5}$/.test(payload.port) || n < 1 || n > 65535) {
      ElMessage.warning('请填写正确的端口号（1-65535）')
      return
    }
  } else if (form.ptype === 'docker') {
    payload.container = form.container.trim()
    if (!payload.container) {
      ElMessage.warning('请填写容器名称或容器 ID')
      return
    }
  } else {
    payload.name = form.name.trim()
    if (!payload.name) {
      ElMessage.warning('请填写程序名称')
      return
    }
  }
  submitting.value = true
  try {
    await addProcessWatch(props.asset?.id, payload)
    ElMessage.success('已添加监听，Agent 将自动采集并在 10–20 秒内回填进程信息')
    emit('update:modelValue', false)
    emit('saved')
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '添加失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.pw-tip {
  --el-alert-padding: 8px 12px;
  font-size: 12px;
}
.pw-hint {
  margin: 0 0 4px 90px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--muted, #909399);
}
</style>
