<template>
  <div>
    <div class="page-head">
      <h2>Agent 配置</h2>
      <el-button @click="load" :loading="loading">刷新</el-button>
    </div>
    <el-alert type="info" :closable="false" style="margin-bottom: 12px"
      title="子机 Agent 定期拉取配置（默认每 5 分钟），修改后无需重启：未单独覆盖的子机随下轮拉取自动生效；在子机上单独设置过的配置优先于全局默认。" />

    <el-row :gutter="12">
      <el-col :span="10">
        <el-card v-loading="loading">
          <template #header>
            <div class="card-head">
              <span>全局默认配置</span>
              <el-tag size="small" type="info">对未单独覆盖的子机生效</el-tag>
            </div>
          </template>
          <el-form label-width="150px" label-position="left">
            <el-form-item label="上报间隔（秒）">
              <el-input-number v-model="form.report_interval" :min="1" :max="86400" />
            </el-form-item>
            <el-form-item label="采集间隔（秒）">
              <el-input-number v-model="form.collect_interval" :min="1" :max="86400" />
            </el-form-item>
            <el-form-item label="采集内容">
              <el-checkbox-group v-model="form.collect_items">
                <el-checkbox v-for="it in COLLECT_ITEMS" :key="it.key" :value="it.key">{{ it.label }}</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
            <el-form-item label="断网缓存上限（条）">
              <el-input-number v-model="form.buffer_max" :min="1" :max="86400" />
              <div class="hint">断网期间本地最多缓存条数，恢复后自动补发</div>
            </el-form-item>
            <el-form-item label="配置拉取间隔（秒）">
              <el-input-number v-model="form.config_refresh" :min="1" :max="86400" />
              <div class="hint">子机 agent 每隔该秒数向平台拉取最新配置</div>
            </el-form-item>
            <el-form-item label="离线判定阈值（秒）">
              <el-input-number v-model="form.offline_after" :min="1" :max="86400" />
              <div class="hint">超过该秒数无上报即视为离线</div>
            </el-form-item>
            <el-form-item v-if="canWrite">
              <el-button type="primary" :loading="saving" @click="save">保存全局默认</el-button>
              <el-button @click="resetToBuiltIn">恢复内置默认</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <el-col :span="14">
        <el-card>
          <template #header>
            <div class="card-head">
              <span>单独覆盖默认配置的子机（{{ overrides.length }}）</span>
            </div>
          </template>
          <el-table :data="overrides" border size="small" empty-text="所有子机均使用全局默认配置">
            <el-table-column prop="hostname" label="主机名" min-width="140" />
            <el-table-column prop="asset_id" label="资产 ID" min-width="150" />
            <el-table-column label="覆盖内容" min-width="260">
              <template #default="{ row }">
                <code class="cfg-code">{{ fmtOverride(row.config) }}</code>
              </template>
            </el-table-column>
            <el-table-column v-if="canWrite" label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button size="small" type="warning" plain :loading="resetting === row.asset_id" @click="resetAsset(row)">
                  重置为默认
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card style="margin-top: 12px">
          <template #header>
            <div class="card-head">
              <span>Agent 双语言版本对比</span>
              <el-tag size="small">部署时可按子机环境选择 py / go</el-tag>
            </div>
          </template>
          <el-table :data="LANG_COMPARE" border size="small">
            <el-table-column prop="dim" label="对比维度" width="130" />
            <el-table-column prop="py" label="Python 版（agent.py）" />
            <el-table-column prop="go" label="Go 版（agent-linux-amd64）" />
          </el-table>
          <div class="hint" style="margin-top: 8px">
            选型建议：目标机已有 python3（绝大多数 Linux 自带）→ Python 版，便于随改随调；追求零依赖、低占用或固化环境 →
            Go 版单二进制，一次编译随处运行。两种版本上报协议、配置项完全一致，可随时切换。
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchAgentConfigDefaults, updateAgentConfigDefaults, resetAgentAssetConfig } from '../api'
import { auth } from '../auth'

const COLLECT_ITEMS = [
  { key: 'cpu', label: 'CPU 使用率' },
  { key: 'mem', label: '内存使用率' },
  { key: 'disk', label: '磁盘使用率' },
  { key: 'load', label: '系统负载' },
  { key: 'net', label: '网络吞吐' },
]

const LANG_COMPARE = [
  { dim: '部署产物', py: '单文件 agent.py（约数百行，可读可改）', go: '静态编译单二进制 agent（约 5MB）' },
  { dim: '目标机依赖', py: '需目标机预装 python3（3.8+，主流 Linux 自带）', go: '零依赖，任何 linux-amd64 可直接运行' },
  { dim: '资源占用', py: '解释执行，常驻内存约 15-25MB', go: '原生编译，常驻内存约 5-10MB' },
  { dim: '升级方式', py: '平台重新下发覆盖，改脚本即时生效', go: '平台重新下发二进制覆盖，需重新编译发布' },
  { dim: '适用场景', py: '常规业务机、需要现场快速调整逻辑', go: '固化/裁剪系统、安全基线要求零解释器' },
]

const loading = ref(false)
const saving = ref(false)
const resetting = ref('')
const overrides = ref([])
const builtIn = ref({})
const form = reactive({
  report_interval: 5,
  collect_interval: 5,
  collect_items: [],
  buffer_max: 600,
  config_refresh: 30,
  offline_after: 30,
})

const canWrite = computed(() => auth.has('assets:write'))

async function load() {
  loading.value = true
  try {
    const { data } = await fetchAgentConfigDefaults()
    Object.assign(form, data.config)
    builtIn.value = data.built_in || {}
    overrides.value = data.overrides || []
  } catch (e) {
    ElMessage.error('加载 Agent 配置失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.collect_items.length) {
    ElMessage.warning('至少选择一项采集内容')
    return
  }
  saving.value = true
  try {
    await updateAgentConfigDefaults({ ...form })
    ElMessage.success('全局默认配置已保存，未覆盖子机将在下轮拉取时生效')
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

function resetToBuiltIn() {
  Object.assign(form, builtIn.value)
  ElMessage.info('已回填内置默认值，点击「保存全局默认」生效')
}

async function resetAsset(row) {
  resetting.value = row.asset_id
  try {
    await resetAgentAssetConfig(row.asset_id)
    ElMessage.success(`已清除 ${row.hostname} 的覆盖配置`)
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '重置失败')
  } finally {
    resetting.value = ''
  }
}

function fmtOverride(cfg) {
  return Object.entries(cfg || {})
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join('|') : v}`)
    .join('  ')
}

onMounted(load)
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}
.cfg-code {
  font-size: 12px;
  word-break: break-all;
}
</style>
