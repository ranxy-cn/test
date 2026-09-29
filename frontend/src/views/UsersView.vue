<template>
  <el-card shadow="never">
    <div class="toolbar">
      <h2 style="margin: 0">用户与权限管理</h2>
      <el-button type="primary" @click="openCreate">新建用户</el-button>
    </div>

    <el-table :data="users" v-loading="loading" size="default">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="username" label="用户名" width="160" />
      <el-table-column prop="display_name" label="显示名" width="160" />
      <el-table-column prop="email" label="邮箱" min-width="180" />
      <el-table-column label="角色" width="200">
        <template #default="{ row }">
          <el-tag v-for="r in row.roles" :key="r" size="small" style="margin-right: 4px">{{ roleLabel(r) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">
            {{ row.is_active ? '启用' : '停用' }}
          </el-tag>
          <el-tooltip v-if="locked(row)" content="账号已锁定" placement="top">
            <el-tag type="warning" size="small" style="margin-left: 4px">锁定</el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="最近登录" width="170">
        <template #default="{ row }">{{ fmtTime(row.last_login_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" @click="openResetPwd(row)">重置密码</el-button>
          <el-button v-if="locked(row)" size="small" type="warning" @click="doUnlock(row)">解锁</el-button>
          <el-button
            v-if="row.username !== auth.user?.username"
            size="small"
            :type="row.is_active ? 'danger' : 'success'"
            @click="toggleActive(row)"
          >
            {{ row.is_active ? '停用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-divider />
    <h3>角色权限矩阵</h3>
    <el-table :data="roles" size="small">
      <el-table-column prop="code" label="角色" width="120" />
      <el-table-column prop="name" label="名称" width="140" />
      <el-table-column prop="description" label="说明" min-width="220" />
      <el-table-column label="权限点" min-width="360">
        <template #default="{ row }">
          <el-tag v-for="p in row.permissions" :key="p" size="small" type="info" style="margin: 2px">{{ permName(p) }}</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建/编辑用户 -->
    <el-dialog v-model="dialogVisible" :title="editing ? '编辑用户' : '新建用户'" width="480px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="用户名">
          <el-input v-model="form.username" :disabled="editing" placeholder="3-32 位字母/数字/._-" />
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="form.display_name" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="form.email" />
        </el-form-item>
        <el-form-item v-if="!editing" label="初始密码">
          <el-input v-model="form.password" type="password" show-password placeholder="8-64 位，含大小写与数字" />
        </el-form-item>
        <el-form-item label="角色">
          <el-checkbox-group v-model="form.role_codes">
            <el-checkbox v-for="r in roles" :key="r.code" :value="r.code">{{ r.name }}（{{ r.code }}）</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-model="resetVisible" title="重置密码" width="440px">
      <el-input v-model="resetPassword" type="password" show-password placeholder="8-64 位，含大小写与数字" />
      <template #footer>
        <el-button @click="resetVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doResetPwd">确认重置</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchUsers, fetchRoles, fetchPermissions, createUser, patchUser, unlockUser } from '../api'
import { auth } from '../auth'
import { fmtTime } from '../time'

const users = ref([])
const roles = ref([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const resetVisible = ref(false)
const resetPassword = ref('')
const resetTarget = ref(null)
const editing = ref(null)
const form = reactive({ username: '', display_name: '', email: '', password: '', role_codes: [] })

const roleLabels = { admin: '管理员', operator: '操作员', viewer: '只读' }
const roleLabel = (c) => roleLabels[c] || c

// 权限点 code → 中文名（/admin/permissions）
const permMap = ref({})
const permName = (code) => permMap.value[code] || code

const locked = (row) => row.locked_until && new Date(row.locked_until).getTime() > Date.now()

async function load() {
  loading.value = true
  try {
    const [u, r, p] = await Promise.all([fetchUsers(), fetchRoles(), fetchPermissions()])
    users.value = u.data.items
    roles.value = r.data.items
    permMap.value = Object.fromEntries(p.data.items.map((x) => [x.code, x.name]))
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '加载失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, { username: '', display_name: '', email: '', password: '', role_codes: ['viewer'] })
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = row
  Object.assign(form, {
    username: row.username,
    display_name: row.display_name,
    email: row.email || '',
    password: '',
    role_codes: [...row.roles],
  })
  dialogVisible.value = true
}

async function save() {
  saving.value = true
  try {
    if (editing.value) {
      await patchUser(editing.value.id, {
        display_name: form.display_name,
        email: form.email || null,
        role_codes: form.role_codes,
      })
      ElMessage.success('已保存')
    } else {
      await createUser({
        username: form.username,
        password: form.password,
        display_name: form.display_name,
        email: form.email || null,
        role_codes: form.role_codes,
      })
      ElMessage.success('用户已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

function openResetPwd(row) {
  resetTarget.value = row
  resetPassword.value = ''
  resetVisible.value = true
}

async function doResetPwd() {
  if (!resetPassword.value) {
    ElMessage.warning('请输入新密码')
    return
  }
  saving.value = true
  try {
    await patchUser(resetTarget.value.id, { password: resetPassword.value })
    ElMessage.success('密码已重置')
    resetVisible.value = false
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重置失败')
  } finally {
    saving.value = false
  }
}

async function doUnlock(row) {
  try {
    await unlockUser(row.id)
    ElMessage.success('已解锁')
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '解锁失败')
  }
}

async function toggleActive(row) {
  const next = !row.is_active
  try {
    await ElMessageBox.confirm(
      `确认${next ? '启用' : '停用'}用户「${row.username}」？${next ? '' : '停用后其登录令牌立即失效'}`,
      '确认操作',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await patchUser(row.id, { is_active: next })
    ElMessage.success(next ? '已启用' : '已停用')
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}
</style>
