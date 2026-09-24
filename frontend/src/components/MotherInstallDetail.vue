<template>
  <el-dialog v-model="visible" title="母机安装详情" width="760" top="4vh" @opened="load">
    <div v-loading="loading">
      <el-alert
        v-if="detail && detail.deploy.status !== 'success'"
        type="warning"
        :closable="false"
        style="margin-bottom: 12px"
      >
        该母机当前状态为「{{ statusLabel }}」，以下信息可能不完整。
      </el-alert>

      <template v-if="detail">
        <!-- 基本信息 -->
        <div class="sec-title">基本信息</div>
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="主机名">{{ detail.hostname || '—' }}</el-descriptions-item>
          <el-descriptions-item label="服务器 IP">{{ detail.ip || '—' }}</el-descriptions-item>
          <el-descriptions-item label="环境">{{ ENV_LABELS[detail.env] || detail.env || '—' }}</el-descriptions-item>
          <el-descriptions-item label="部署状态">
            <el-tag size="small" :type="statusTagType">{{ statusLabel }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="Zabbix 版本">{{ detail.deploy.version || '—' }}</el-descriptions-item>
          <el-descriptions-item label="负责人">{{ detail.owner || '—' }}</el-descriptions-item>
          <el-descriptions-item label="开始时间">{{ fmtTime(detail.deploy.started_at) }}</el-descriptions-item>
          <el-descriptions-item label="完成时间">{{ fmtTime(detail.deploy.finished_at) }}</el-descriptions-item>
          <el-descriptions-item label="连通状态">
            <el-tag size="small" :type="detail.reachable ? 'success' : 'danger'" effect="plain">
              {{ detail.reachable ? '在线' : '离线' }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 安装位置 -->
        <div class="sec-title">安装位置（目标机上）</div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="安装方式" :span="2">{{ detail.install.method }}</el-descriptions-item>
          <el-descriptions-item label="安装目录">
            <span class="mono">{{ detail.install.dir }}</span>
            <CopyBtn :text="detail.install.dir" />
          </el-descriptions-item>
          <el-descriptions-item label="数据目录">
            <span class="mono">{{ detail.install.data_dir }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="编排文件">
            <span class="mono">{{ detail.install.compose_file }}</span>
            <CopyBtn :text="detail.install.compose_file" />
          </el-descriptions-item>
          <el-descriptions-item label="环境变量文件">
            <span class="mono">{{ detail.install.env_file }}</span>
            <CopyBtn :text="detail.install.env_file" />
          </el-descriptions-item>
        </el-descriptions>

        <!-- SSH 接入 -->
        <div class="sec-title">SSH 接入（部署时使用）</div>
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="地址">
            <span class="mono">{{ detail.ssh.ip || '—' }}</span>
            <CopyBtn :text="detail.ssh.ip" />
          </el-descriptions-item>
          <el-descriptions-item label="端口">{{ detail.ssh.port || 22 }}</el-descriptions-item>
          <el-descriptions-item label="用户名">{{ detail.ssh.username || '—' }}</el-descriptions-item>
        </el-descriptions>

        <!-- Zabbix 访问信息 -->
        <div class="sec-title">Zabbix 控制台 / API</div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="控制台地址" :span="2">
            <a :href="detail.zabbix.web_url" target="_blank" rel="noopener" class="mono link">{{ detail.zabbix.web_url }}</a>
            <CopyBtn :text="detail.zabbix.web_url" />
          </el-descriptions-item>
          <el-descriptions-item label="API 地址" :span="2">
            <span class="mono">{{ detail.zabbix.api_url }}</span>
            <CopyBtn :text="detail.zabbix.api_url" />
          </el-descriptions-item>
          <el-descriptions-item label="管理员账号">
            <span class="mono">{{ detail.zabbix.user || '—' }}</span>
            <CopyBtn :text="detail.zabbix.user" />
          </el-descriptions-item>
          <el-descriptions-item label="管理员密码">
            <el-input
              :model-value="detail.zabbix.password"
              readonly
              show-password
              size="small"
              style="width: 180px"
              class="mono"
            />
            <CopyBtn :text="detail.zabbix.password" />
          </el-descriptions-item>
        </el-descriptions>

        <!-- 端口 -->
        <div class="sec-title">端口占用</div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="Web 控制台端口">{{ detail.ports.web }}（浏览器访问）</el-descriptions-item>
          <el-descriptions-item label="Agent 上报端口">{{ detail.ports.trapper }}（子机 Trapper）</el-descriptions-item>
        </el-descriptions>

        <!-- 数据库 -->
        <div class="sec-title">数据库</div>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="部署模式">
            {{ detail.db.mode === 'bundled' ? '独立 MySQL 容器（bundled）' : '复用已有 MySQL（external）' }}
          </el-descriptions-item>
          <el-descriptions-item label="地址">
            <span class="mono">{{ detail.db.host || '—' }}:{{ detail.db.port }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="库名">
            <span class="mono">{{ detail.db.database }}</span>
            <CopyBtn :text="detail.db.database" />
          </el-descriptions-item>
          <el-descriptions-item label="用户名">
            <span class="mono">{{ detail.db.user || '—' }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <!-- 容器清单 -->
        <div class="sec-title">容器清单（docker ps 可见）</div>
        <el-table :data="containerRows" border size="small">
          <el-table-column prop="name" label="容器名" width="180" />
          <el-table-column prop="desc" label="说明" />
        </el-table>

        <!-- 部署日志 -->
        <el-collapse v-if="detail.logs?.length" style="margin-top: 12px">
          <el-collapse-item :title="`部署日志（${detail.logs.length} 行）`">
            <pre class="deploy-logs">{{ detail.logs.join('\n') }}</pre>
          </el-collapse-item>
        </el-collapse>
      </template>
    </div>
    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, h, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchMotherDeployDetail } from '../api'

const props = defineProps({
  modelValue: Boolean,
  motherId: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const loading = ref(false)
const detail = ref(null)

const ENV_LABELS = { prod: '生产', staging: '预发', test: '测试' }

const STATUS_LABELS = { success: '安装成功', running: '安装中', failed: '安装失败', uninstalling: '卸载中', '': '未安装' }
const statusLabel = computed(() => STATUS_LABELS[detail.value?.deploy?.status] || '未安装')
const statusTagType = computed(
  () => ({ success: 'success', running: 'warning', uninstalling: 'warning', failed: 'danger' }[detail.value?.deploy?.status] || 'info'),
)

const CONTAINER_DESCS = {
  mysql: 'MySQL 5.7 数据库（存储监控数据）',
  'zabbix-server': 'Zabbix Server（接收子机 Agent 上报，Trapper 端口）',
  'zabbix-web': 'Zabbix Web 控制台（Nginx + PHP）',
  'zabbix-agent': 'Zabbix Agent（监控母机自身）',
}
const containerRows = computed(() =>
  (detail.value?.install?.containers || []).map((name) => ({ name, desc: CONTAINER_DESCS[name] || '' })),
)

watch(
  () => props.modelValue,
  (v) => {
    if (v) {
      detail.value = null
    }
  },
)

async function load() {
  if (!props.motherId) return
  loading.value = true
  try {
    const { data } = await fetchMotherDeployDetail(props.motherId)
    detail.value = data
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '获取安装详情失败')
    visible.value = false
  } finally {
    loading.value = false
  }
}

function fmtTime(v) {
  if (!v) return '—'
  const d = new Date(v)
  return isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false })
}

async function copyText(text) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动复制')
  }
}

// 小复制按钮（内联组件）
const CopyBtn = {
  props: { text: { type: String, default: '' } },
  emits: [],
  setup(props) {
    return () =>
      props.text
        ? h(
            'button',
            {
              class: 'copy-btn',
              title: '复制',
              onClick: (e) => {
                e.preventDefault()
                copyText(props.text)
              },
            },
            '复制',
          )
        : null
  },
}
</script>

<style scoped>
.sec-title {
  font-weight: 600;
  font-size: 13px;
  color: #303133;
  margin: 14px 0 8px;
}
.sec-title:first-of-type {
  margin-top: 4px;
}
.mono {
  font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
  font-size: 12px;
  word-break: break-all;
}
.link {
  color: #409eff;
  text-decoration: none;
  margin-right: 6px;
}
.copy-btn {
  border: none;
  background: none;
  color: #409eff;
  font-size: 12px;
  cursor: pointer;
  margin-left: 6px;
  padding: 0;
}
.copy-btn:hover {
  text-decoration: underline;
}
.deploy-logs {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
</style>
