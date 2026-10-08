<template>
  <div>
    <!-- 资产台账联动：母机 → 分组 按钮切换筛选 -->
    <div class="filter-block">
      <span class="filter-label">母机</span>
      <el-radio-group v-model="motherId" size="default" @change="onMotherChange">
        <el-radio-button value="">全部母机</el-radio-button>
        <el-radio-button v-for="m in mothers" :key="m.id" :value="m.id">{{ m.hostname }}</el-radio-button>
      </el-radio-group>
    </div>
    <div class="filter-block">
      <span class="filter-label">分组</span>
      <el-radio-group v-model="group" size="default" @change="search">
        <el-radio-button value="">全部分组</el-radio-button>
        <el-radio-button v-for="g in groups" :key="g.name" :value="g.name">
          {{ g.name || '未分组' }}<span class="opt-count">({{ g.total }})</span>
        </el-radio-button>
      </el-radio-group>
    </div>

    <!-- 筛选框 + 操作区 -->
    <div class="filter-block row">
      <el-select v-model="status" placeholder="全部状态" clearable style="width: 140px" @change="search">
        <el-option v-for="(label, val) in STATUS_OPTIONS" :key="val" :label="label" :value="val" />
      </el-select>
      <el-input
        v-model="keyword"
        placeholder="搜索编号 / 标题 / 资产"
        clearable
        style="width: 240px"
        @keyup.enter="search"
        @clear="search"
      />
      <el-button type="primary" plain @click="search">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
      <span class="spacer"></span>
      <el-button @click="load">刷新</el-button>
    </div>

    <el-table :data="items" stripe style="width: 100%" empty-text="暂无任务单" v-loading="loading">
      <el-table-column prop="number" label="编号" width="170" />
      <el-table-column label="标题" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">
          <span :title="row.title">{{ triggerLabel(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="母机" width="130" show-overflow-tooltip>
        <template #default="{ row }">{{ row.asset_info?.mother_hostname || '-' }}</template>
      </el-table-column>
      <el-table-column label="分组" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.asset_info?.group" size="small" effect="plain">{{ row.asset_info.group }}</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="子机" width="160" show-overflow-tooltip>
        <template #default="{ row }">
          <template v-if="row.asset_info?.hostname">{{ row.asset_info.hostname }}</template>
          <span v-else :title="row.asset_id">{{ row.asset_id }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="owner" label="负责人" width="90" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="策略" width="90">
        <template #default="{ row }">
          <span v-if="row.policy_light"><i class="light-dot" :class="'light-' + row.policy_light"></i>{{ lightLabel(row.policy_light) }}</span>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="170" :formatter="fmtTimeCol('created_at')" />
      <el-table-column label="操作" width="100" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="go(row)">查看详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
        @current-change="load"
        @size-change="search"
      />
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchTickets,
  fetchDict,
  fetchMothers,
  fetchMotherGroups,
} from '../api'
import { fmtTimeCol } from '../time'

const STATUS_OPTIONS = {
  pending_analysis: '待分析',
  pending_approval: '待审批',
  pending_execution: '待执行',
  executing: '执行中',
  verifying: '验证中',
  recovered: '已恢复',
  escalated: '已升级',
  skipped: '已跳过',
}

const router = useRouter()
const items = ref([])
const loading = ref(false)
// 资产台账联动筛选
const mothers = ref([])
const groups = ref([])
const motherId = ref('')
const group = ref('')
// 筛选与分页
const status = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
let timer

// ===== 业务字典（code → 中文名映射）=====
const dictMaps = ref({ trigger: {}, asset: {}, action: {} })

function loadDict() {
  fetchDict()
    .then(({ data }) => {
      const maps = { trigger: {}, asset: {}, action: {} }
      for (const it of data) {
        if (maps[it.dict_type]) maps[it.dict_type][it.code] = it.label
      }
      dictMaps.value = maps
    })
    .catch(() => {})
}

function triggerLabel(row) {
  return (dictMaps.value.trigger || {})[row.title || row.trigger_name] || row.title || row.trigger_name
}

// ===== 资产台账联动 =====
async function loadMothers() {
  try {
    const { data } = await fetchMothers()
    mothers.value = data.items || []
  } catch {
    mothers.value = []
  }
}

async function loadGroups() {
  if (!motherId.value) {
    groups.value = []
    return
  }
  try {
    const { data } = await fetchMotherGroups(motherId.value)
    groups.value = data.items || []
  } catch {
    groups.value = []
  }
}

function onMotherChange() {
  group.value = ''
  loadGroups()
  search()
}

function search() {
  page.value = 1
  load()
}

function resetFilters() {
  motherId.value = ''
  group.value = ''
  status.value = ''
  keyword.value = ''
  groups.value = []
  search()
}

const statusLabel = (s) => STATUS_OPTIONS[s] || s

const statusType = (s) => ({
  recovered: 'success',
  escalated: 'danger',
  pending_approval: 'warning',
  executing: 'primary',
  verifying: 'primary',
}[s] || 'info')

const lightLabel = (l) => ({ green: '绿灯', yellow: '黄灯', red: '红灯' }[l] || l)

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (motherId.value) params.mother_id = motherId.value
    if (group.value) params.group = group.value
    if (status.value) params.status = status.value
    if (keyword.value && keyword.value.trim()) params.keyword = keyword.value.trim()
    const { data } = await fetchTickets(params)
    items.value = data.items
    total.value = data.total ?? data.items.length
  } finally {
    loading.value = false
  }
}

function go(row) {
  router.push(`/tickets/${row.id}`)
}

onMounted(() => {
  load()
  loadDict()
  loadMothers()
  timer = setInterval(load, 4000)
})
onUnmounted(() => {
  clearInterval(timer)
})
</script>

<style scoped>
.filter-block {
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.filter-block.row {
  gap: 10px;
}
.filter-label {
  font-size: 13px;
  color: var(--muted);
  width: 36px;
  text-align: right;
  flex-shrink: 0;
}
.opt-count {
  font-size: 12px;
  opacity: 0.65;
  margin-left: 2px;
}
.spacer {
  flex: 1;
}
.pager {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
}
</style>
