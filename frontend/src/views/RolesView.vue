<template>
  <div>
    <div class="page-head">
      <h2>角色权限</h2>
      <div>
        <el-button @click="permDlg = true">新增权限点</el-button>
        <el-button type="primary" @click="roleDlg = true">新增角色</el-button>
      </div>
    </div>

    <el-row :gutter="16">
      <!-- 角色列表 -->
      <el-col :span="7">
        <el-card shadow="never">
          <template #header>角色</template>
          <div v-loading="loading">
            <div v-for="r in roles" :key="r.code" class="role-row" :class="{ active: current?.code === r.code }"
              @click="selectRole(r)">
              <div>
                <b>{{ r.name }}</b>
                <el-tag size="small" style="margin-left: 6px">{{ r.code }}</el-tag>
                <el-tag v-if="r.locked" size="small" type="warning" style="margin-left: 4px">保护</el-tag>
              </div>
              <div class="role-desc">{{ r.description }}</div>
            </div>
          </div>
        </el-card>
      </el-col>

      <!-- 授权配置 -->
      <el-col :span="17">
        <el-card shadow="never" v-if="current">
          <template #header>
            <span>「{{ current.name }}」的菜单/按钮授权</span>
          </template>
          <el-alert v-if="current.locked" type="warning" :closable="false" style="margin-bottom: 10px"
            title="admin 角色受保护：始终拥有全部菜单与权限，不可修改" />
          <el-tree ref="menuTreeRef" :data="menuTree" node-key="code" show-checkbox default-expand-all
            :props="{ label: 'name', children: 'children' }" :disabled="current.locked" style="margin-bottom: 14px" />
          <el-button type="primary" :disabled="current.locked" :loading="saving" @click="saveMenus">
            保存菜单授权
          </el-button>

          <el-divider />
          <div class="section-title">权限点（接口级 + 按钮级）</div>
          <el-checkbox-group v-model="permSelection" :disabled="current.locked" class="perm-grid">
            <el-checkbox v-for="p in perms" :key="p.code" :value="p.code">
              {{ p.code }}（{{ p.name }}）
            </el-checkbox>
          </el-checkbox-group>
          <div style="margin-top: 12px">
            <el-button type="primary" :disabled="current.locked" :loading="saving" @click="savePerms">
              保存权限点
            </el-button>
          </div>
        </el-card>
        <el-empty v-else description="选择左侧角色进行授权配置" />
      </el-col>
    </el-row>

    <!-- 新增角色 -->
    <el-dialog v-model="roleDlg" title="新增角色" width="480px">
      <el-form label-width="90px">
        <el-form-item label="编码">
          <el-input v-model="newRole.code" placeholder="如 ticket-handler（小写字母/数字/_/-）" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="newRole.name" placeholder="如 工单处理员工" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="newRole.description" />
        </el-form-item>
        <el-form-item label="初始权限">
          <el-select v-model="newRole.permission_codes" multiple filterable style="width: 100%">
            <el-option v-for="p in perms" :key="p.code" :value="p.code" :label="`${p.code}（${p.name}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始菜单">
          <el-tree ref="newRoleTreeRef" :data="menuTree" node-key="code" show-checkbox default-expand-all
            :props="{ label: 'name', children: 'children' }" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="roleDlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doCreateRole">创建</el-button>
      </template>
    </el-dialog>

    <!-- 新增权限点 -->
    <el-dialog v-model="permDlg" title="新增权限点" width="480px">
      <el-form label-width="90px">
        <el-form-item label="编码">
          <el-input v-model="newPerm.code" placeholder="格式 资源:动作，如 reports:export" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="newPerm.name" placeholder="如 日报导出" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="newPerm.description" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="permDlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doCreatePerm">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  fetchRoles,
  fetchMenuTree,
  fetchPermissions,
  createRole,
  createPermission,
  assignRoleMenus,
  assignRolePermissions,
} from '../api'

const loading = ref(false)
const saving = ref(false)
const roles = ref([])
const menuTree = ref([])
const perms = ref([])
const current = ref(null)
const permSelection = ref([])
const menuTreeRef = ref()
const newRoleTreeRef = ref()
const roleDlg = ref(false)
const permDlg = ref(false)
const newRole = ref({ code: '', name: '', description: '', permission_codes: [] })
const newPerm = ref({ code: '', name: '', description: '' })

async function load() {
  loading.value = true
  try {
    const [roleResp, menuResp, permResp] = await Promise.all([
      fetchRoles(),
      fetchMenuTree(),
      fetchPermissions(),
    ])
    roles.value = roleResp.data.items
    menuTree.value = menuResp.data.items
    perms.value = permResp.data.items
    if (!current.value && roles.value.length) selectRole(roles.value[0])
  } finally {
    loading.value = false
  }
}

function selectRole(role) {
  current.value = role
  permSelection.value = [...(role.permissions || [])]
  // 勾选已授权菜单（等 tree 渲染后）
  requestAnimationFrame(() => {
    menuTreeRef.value?.setCheckedKeys(role.menu_codes || [])
  })
}

async function saveMenus() {
  const codes = menuTreeRef.value.getCheckedKeys().concat(menuTreeRef.value.getHalfCheckedKeys())
  saving.value = true
  try {
    await assignRoleMenus(current.value.id, codes)
    ElMessage.success('菜单授权已保存，用户重新登录后生效')
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function savePerms() {
  saving.value = true
  try {
    await assignRolePermissions(current.value.id, permSelection.value)
    ElMessage.success('权限点已保存')
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function doCreateRole() {
  if (!newRole.value.code.trim() || !newRole.value.name.trim()) {
    ElMessage.warning('请填写编码与名称')
    return
  }
  saving.value = true
  try {
    const treeKeys = newRoleTreeRef.value
      ? newRoleTreeRef.value.getCheckedKeys().concat(newRoleTreeRef.value.getHalfCheckedKeys())
      : []
    await createRole({ ...newRole.value, menu_codes: treeKeys })
    ElMessage.success('角色已创建')
    roleDlg.value = false
    newRole.value = { code: '', name: '', description: '', permission_codes: [] }
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  } finally {
    saving.value = false
  }
}

async function doCreatePerm() {
  if (!newPerm.value.code.trim() || !newPerm.value.name.trim()) {
    ElMessage.warning('请填写编码与名称')
    return
  }
  saving.value = true
  try {
    await createPermission(newPerm.value)
    ElMessage.success('权限点已创建')
    permDlg.value = false
    newPerm.value = { code: '', name: '', description: '' }
    await load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  } finally {
    saving.value = false
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
.role-row {
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 6px;
}
.role-row.active {
  background: var(--el-color-primary-light-9);
  outline: 1px solid var(--el-color-primary-light-7);
}
.role-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}
.section-title {
  font-weight: 600;
  margin-bottom: 8px;
}
.perm-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
}
</style>
