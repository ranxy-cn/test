<template>
  <div>
    <div class="page-head">
      <h2>菜单管理</h2>
      <el-button type="primary" @click="openCreate(null)">新增菜单</el-button>
    </div>
    <el-alert type="info" :closable="false" style="margin-bottom: 12px"
      title="菜单树即前端侧边栏来源：dir 为目录分组，menu 为页面（path 必填），button 为页面操作按钮（控制按钮显隐）。改动后用户重新登录生效。" />

    <el-table :data="tree" row-key="id" default-expand-all border v-loading="loading">
      <el-table-column prop="name" label="名称" min-width="160" />
      <el-table-column prop="code" label="编码" min-width="170" />
      <el-table-column prop="type" label="类型" width="80">
        <template #default="{ row }">
          <el-tag :type="typeTag(row.type)" size="small">{{ typeLabel(row.type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="path" label="路由" min-width="110" />
      <el-table-column prop="perm_code" label="权限点" min-width="150" />
      <el-table-column prop="sort_order" label="排序" width="70" />
      <el-table-column label="可见" width="70">
        <template #default="{ row }">
          <el-tag :type="row.visible ? 'success' : 'info'" size="small">{{ row.visible ? '显示' : '隐藏' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.status === 'enabled' ? 'success' : 'danger'" size="small">
            {{ row.status === 'enabled' ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="195" fixed="right" class-name="op-col">
        <template #default="{ row }">
          <el-button size="small" @click="openCreate(row)" v-if="row.type !== 'button'">加子项</el-button>
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="doDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlgVisible" :title="form.id ? '编辑菜单' : '新增菜单'" width="560px">
      <el-form label-width="90px">
        <el-form-item label="父节点">
          <el-tree-select v-model="form.parent_id" :data="parentOptions" check-strictly
            :render-after-expand="false" placeholder="不选则为顶级" clearable style="width: 100%" />
        </el-form-item>
        <el-form-item label="编码">
          <el-input v-model="form.code" :disabled="!!form.id" placeholder="如 menu:report / btn:report-export" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="form.type" :disabled="!!form.id">
            <el-radio value="dir">目录</el-radio>
            <el-radio value="menu">菜单</el-radio>
            <el-radio value="button">按钮</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.type === 'menu'" label="路由 path">
          <el-input v-model="form.path" placeholder="如 /report" />
        </el-form-item>
        <el-form-item label="权限点">
          <el-select v-model="form.perm_code" filterable allow-create clearable placeholder="选择或输入权限点编码"
            style="width: 100%">
            <el-option v-for="p in permOptions" :key="p.code" :value="p.code" :label="`${p.code}（${p.name}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="图标">
          <el-input v-model="form.icon" placeholder="Element Plus 图标名，如 Tickets / Bell / Setting" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" :max="999" />
        </el-form-item>
        <el-form-item label="显示">
          <el-switch v-model="form.visible" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.status" active-value="enabled" inactive-value="disabled"
            active-text="启用" inactive-text="停用" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlgVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchMenuTree, createMenu, patchMenu, deleteMenu, fetchPermissions } from '../api'

const loading = ref(false)
const saving = ref(false)
const tree = ref([])
const permOptions = ref([])
const dlgVisible = ref(false)

const form = reactive({
  id: null,
  parent_id: null,
  code: '',
  name: '',
  type: 'menu',
  path: '',
  perm_code: '',
  icon: '',
  sort_order: 0,
  visible: true,
  status: 'enabled',
  remark: '',
})

// 父节点候选：目录与菜单（按钮不能挂子节点）
const parentOptions = computed(() => {
  const out = []
  const walk = (items) =>
    (items || []).forEach((m) => {
      if (m.type !== 'button') out.push({ value: m.id, label: `${m.name}（${m.type}）` })
      walk(m.children)
    })
  walk(tree.value)
  return out
})

const typeLabel = (t) => ({ dir: '目录', menu: '菜单', button: '按钮' }[t] || t)
const typeTag = (t) => ({ dir: 'warning', menu: 'primary', button: 'success' }[t] || 'info')

async function load() {
  loading.value = true
  try {
    const [menuResp, permResp] = await Promise.all([fetchMenuTree(), fetchPermissions()])
    tree.value = menuResp.data.items
    permOptions.value = permResp.data.items
  } finally {
    loading.value = false
  }
}

function openCreate(parent) {
  Object.assign(form, {
    id: null,
    parent_id: parent ? parent.id : null,
    code: '',
    name: '',
    type: parent && parent.type === 'dir' ? 'menu' : 'menu',
    path: '',
    perm_code: '',
    icon: '',
    sort_order: 0,
    visible: true,
    status: 'enabled',
    remark: '',
  })
  dlgVisible.value = true
}

function openEdit(row) {
  Object.assign(form, {
    id: row.id,
    parent_id: row.parent_id,
    code: row.code,
    name: row.name,
    type: row.type,
    path: row.path || '',
    perm_code: row.perm_code || '',
    icon: row.icon || '',
    sort_order: row.sort_order,
    visible: row.visible,
    status: row.status,
    remark: row.remark || '',
  })
  dlgVisible.value = true
}

async function doSave() {
  if (!form.id && !form.code.trim()) {
    ElMessage.warning('请填写编码')
    return
  }
  if (!form.name.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  if (form.type === 'menu' && !form.path.trim()) {
    ElMessage.warning('菜单类型必须填写路由 path')
    return
  }
  saving.value = true
  try {
    if (form.id) {
      await patchMenu(form.id, {
        parent_id: form.parent_id,
        name: form.name,
        path: form.path || null,
        perm_code: form.perm_code || null,
        icon: form.icon,
        sort_order: form.sort_order,
        visible: form.visible,
        status: form.status,
        remark: form.remark,
      })
    } else {
      await createMenu({ ...form, path: form.path || null })
    }
    ElMessage.success('已保存')
    dlgVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function doDelete(row) {
  const ok = await ElMessageBox.confirm(
    `确认删除「${row.name}」？已授权该节点的角色会同步解除授权。`,
    '删除确认',
    { type: 'warning' },
  ).catch(() => false)
  if (!ok) return
  try {
    await deleteMenu(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除失败')
  }
}

onMounted(load)
</script>

<style scoped>
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
/* 操作列按钮单行排列，所有行左对齐 */
:deep(.op-col .cell) {
  white-space: nowrap;
  overflow: visible;
}
</style>
