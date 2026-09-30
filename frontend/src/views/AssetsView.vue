<template>
  <div>
    <!-- ===== 页面头：左侧标题（母机详情时带上下文），右侧=视图切换与操作 ===== -->
    <div class="page-header">
      <div class="page-title">
        <span class="mother-name">资产台账</span>
        <template v-if="detailView && mother">
          <el-tag size="small" :type="mother.reachable ? 'success' : 'danger'" effect="plain">
            {{ mother.hostname }}
          </el-tag>
        </template>
      </div>
      <!-- 空状态（尚无母机）时右上角操作按钮整体隐藏，仅空态引导内提供"添加母机"入口 -->
      <div v-if="!(!motherLoading && !mothers.length)" class="toolbar">
        <el-button v-if="!detailView" v-perm="'assets:write'" type="primary" @click="openAddMother">
          <el-icon style="margin-right: 4px"><Plus /></el-icon>
          添加母机
        </el-button>
      </div>
    </div>

    <!-- ===== 总览视图 ===== -->
    <!-- 空状态：尚未接入母机 -->
      <div v-if="!motherLoading && !mothers.length" class="empty-mother" v-loading="motherLoading">
        <el-icon class="empty-icon"><Cpu /></el-icon>
        <div class="empty-title">尚未接入母机</div>
        <div class="empty-desc">
          母机是子机的业务归属节点：子机上的自研 Agent 采集指标后按归属母机汇聚展示。<br />
          添加母机后，即可通过 SSH 向目标机一键下发自研 Agent（Go / Python 双语言可选）。
        </div>
        <el-button v-perm="'assets:write'" type="primary" @click="openAddMother">添加第一台母机</el-button>
      </div>

      <!-- 母机列表：一台一条记录 -->
      <template v-else-if="!detailView">
        <div class="overview-toolbar">
          <span class="section-title">母机（{{ mothers.length }}）</span>
          <span class="section-hint">点击母机进入，查看其接入的子机</span>
        </div>
        <el-card
          v-for="m in mothers"
          :key="m.id"
          shadow="hover"
          class="mother-row"
          v-loading="motherLoading"
          @click="openMotherDetail(m)"
        >
          <div class="row-main">
            <span class="dot" :class="m.provisioning ? 'pend' : m.reachable ? 'ok' : 'down'" />
            <span class="row-name">{{ m.hostname }}</span>
            <el-tag size="small" type="primary" effect="dark">母机</el-tag>
            <el-tag v-if="m.is_default" size="small" type="warning" effect="plain">默认</el-tag>
            <el-tag v-if="m.provisioning" size="small" type="warning">纳管中…</el-tag>
            <el-tag v-else size="small" :type="m.reachable ? 'success' : 'danger'" effect="plain">
              {{ m.reachable ? '在线' : '离线' }}
            </el-tag>
          </div>
          <div class="row-sub">
            <span>{{ m.ip || m.id }}</span>
            <el-divider direction="vertical" />
            <span>{{ ENV_LABELS[m.env] || m.env }}</span>
            <el-divider direction="vertical" />
            <span>{{ ROLE_LABELS[m.role] || m.role }}</span>
          </div>
          <div class="row-right">
            <span class="row-count">已接入子机 <b>{{ m.children_count ?? 0 }}</b> 台</span>
            <el-button v-perm="'assets:write'" size="small" plain @click.stop="openAlertPolicy(m)">告警策略</el-button>
            <el-icon><ArrowRight /></el-icon>
          </div>
        </el-card>
      </template>

      <!-- 母机详情：该母机接入的子机 -->
      <template v-else>
        <div class="detail-header">
          <el-button text size="small" @click="backToMothers">
            <el-icon><ArrowLeft /></el-icon>
            返回母机列表
          </el-button>
          <div class="detail-title">
            <span class="dot" :class="mother?.provisioning ? 'pend' : mother?.reachable ? 'ok' : 'down'" />
            <span class="mother-name">{{ mother?.hostname || '—' }}</span>
            <el-tag size="small" type="primary" effect="dark">母机</el-tag>
            <el-tag v-if="mother?.provisioning" size="small" type="warning">纳管中…</el-tag>
            <el-tag v-else size="small" :type="mother?.reachable ? 'success' : 'danger'" effect="plain">
              {{ mother?.reachable ? '在线' : '离线' }}
            </el-tag>
          </div>
          <div class="toolbar">
            <el-button type="primary" size="small" :disabled="!mother" @click="openMonitor(mother)">监控详情</el-button>
            <el-button
              v-perm="'assets:write'"
              size="small"
              plain
              @click="openAlertPolicy(mother || { id: selectedMotherId })"
            >
              告警策略
            </el-button>
            <el-button
              v-perm="'assets:write'"
              type="danger"
              plain
              size="small"
              @click="confirmDelete(mother || { id: selectedMotherId, kind: 'mother' })"
            >
              删除母机
            </el-button>
          </div>
        </div>
        <div class="detail-sub">
          <span>{{ mother?.ip || '—' }}</span>
          <el-divider direction="vertical" />
          <span>最后连通：{{ relTime(mother?.last_seen_at) }}</span>
          <el-divider direction="vertical" />
          <span>已接入子机 {{ children.length }} 台</span>
        </div>
      </template>

      <!-- 子机分组（母机详情内展示） -->
      <template v-if="detailView && mothers.length">
        <div class="overview-toolbar">
          <span class="section-title">已接入子机</span>
          <span class="sync-hint" title="卡片 CPU/内存/磁盘/负载/网络 每 1 秒拉取 Agent 最新帧，与监控详情实时面板同源同频">
            指标 1 秒/次实时同步<template v-if="lastSyncAt"> · 最后 {{ lastSyncAt }}</template>
          </span>
          <div class="children-toolbar">
            <el-button size="small" :loading="motherLoading" title="立即拉取最新指标" @click="loadOverview">
              <el-icon style="margin-right: 4px"><Refresh /></el-icon>
              刷新指标
            </el-button>
            <el-input
              v-model="query.keyword"
              placeholder="过滤 主机名/IP/应用/负责人"
              clearable
              style="width: 220px"
            />
            <el-button v-perm="'assets:write'" size="small" @click="addGroup">
              <el-icon style="margin-right: 4px"><FolderAdd /></el-icon>
              新增分组
            </el-button>
          </div>
        </div>

        <el-empty
          v-if="!filteredChildren.length && !groupedChildren.length"
          :description="children.length ? '没有匹配的子机' : '还没有子机接入'"
        />

        <div
          v-for="g in groupedChildren"
          :key="g.name"
          class="group-block"
          :class="{ 'drop-target': dragOverGroup === g.name }"
          @dragover.prevent="dragOverGroup = g.name"
          @dragleave="dragOverGroup === g.name && (dragOverGroup = '')"
          @drop.prevent="onDropToGroup(g.name)"
        >
          <div class="group-header">
            <el-icon class="group-icon"><Folder /></el-icon>
            <span class="group-name">{{ g.name || '未分组' }}</span>
            <el-tag size="small" type="info" effect="plain">{{ g.items.length }} 台</el-tag>
            <el-tag v-if="g.items.length" size="small" :type="g.online === g.items.length ? 'success' : 'warning'" effect="plain">
              在线 {{ g.online }}/{{ g.items.length }}
            </el-tag>
            <template v-if="g.name">
              <el-icon v-perm="'assets:write'" class="group-op" title="重命名分组" @click="doRenameGroup(g.name)"><EditPen /></el-icon>
              <el-icon v-perm="'assets:write'" class="group-op danger" title="删除分组" @click="doDeleteGroup(g.name)"><Delete /></el-icon>
            </template>
          </div>
          <el-row :gutter="12">
            <el-col v-for="c in g.items" :key="c.id" :xs="24" :sm="12" :md="8" :lg="6">
              <el-card
                shadow="hover"
                class="node-card"
                :class="{ down: !c.reachable, dragging: dragChild?.id === c.id }"
                draggable="true"
                @dragstart="onDragStart(c, $event)"
                @dragend="dragOverGroup = ''"
              >
                <!-- 未恢复告警徽标：点击直达监控详情（内含异常告警列表） -->
                <el-tooltip v-if="c.abnormal_count" placement="top" :content="`${c.abnormal_count} 条未恢复告警，点击查看`">
                  <div class="alarm-badge" @click.stop="openMonitor(c)">{{ c.abnormal_count }} 告警</div>
                </el-tooltip>
                <div class="node-top">
                  <!-- 心电图式在线指示：在线绿色流动脉冲，离线灰色静止 -->
                  <svg class="ecg" :class="c.reachable ? 'on' : 'off'" viewBox="0 0 72 20" aria-hidden="true">
                    <path
                      class="ecg-line"
                      d="M0 11 H12 L16 11 L18 7 L20 15 L22 11 H30 L33 11 L35 3 L38 18 L41 11 H48 L52 11 L54 8 L56 14 L58 11 H72"
                    />
                  </svg>
                  <span class="node-host" :title="c.id">{{ c.hostname || c.id }}</span>
                  <!-- 操作按钮固定在卡片右上角 -->
                  <div class="node-ops">
                    <el-tooltip content="监控详情" placement="top">
                      <el-icon class="node-op" @click="openMonitor(c)"><View /></el-icon>
                    </el-tooltip>
                    <el-tooltip content="告警策略" placement="top">
                      <el-icon v-perm="'assets:write'" class="node-op" @click="openAlertPolicy(c)"><Bell /></el-icon>
                    </el-tooltip>
                    <el-tooltip v-if="!c.is_mother" content="编辑 / 分组" placement="top">
                      <el-icon v-perm="'assets:write'" class="node-op" @click="openEdit(c)"><EditPen /></el-icon>
                    </el-tooltip>
                    <el-tooltip v-if="c.provision_status" content="安装详情（状态 / 语言 / pid / 安装日志）" placement="top">
                      <el-icon class="node-op" @click="openChildInstall(c)"><InfoFilled /></el-icon>
                    </el-tooltip>
                    <el-tooltip v-if="!c.is_mother" content="删除" placement="top">
                      <el-icon v-perm="'assets:write'" class="node-op danger" @click="confirmDelete(c)"><Delete /></el-icon>
                    </el-tooltip>
                  </div>
                </div>
                <div class="node-ip">{{ c.ip || '—' }}</div>
                <div class="node-tags">
                  <el-tooltip v-if="c.is_mother" content="与母机是同一台服务器（母机自身的系统指标，由平台侧采集）">
                    <el-tag size="small" type="primary" effect="dark">本机 · 母机</el-tag>
                  </el-tooltip>
                  <el-tag size="small" effect="plain">{{ ENV_LABELS[c.env] || c.env }}</el-tag>
                  <el-tag size="small" effect="plain" type="info">{{ ROLE_LABELS[c.role] || c.role }}</el-tag>
                  <el-tooltip
                    v-if="c.provision_status === 'failed'"
                    :content="c.provision_logs ? `安装日志（截尾）：${c.provision_logs.slice(-160)}` : '纳管失败'"
                  >
                    <el-tag size="small" type="danger">纳管失败</el-tag>
                  </el-tooltip>
                  <el-tag v-else-if="c.provision_status === 'registered'" size="small" type="success">
                    已纳管
                  </el-tag>
                  <el-tag v-else-if="c.provision_status === 'running'" size="small" type="warning">安装中…</el-tag>
                  <el-tag v-else-if="c.group" size="small" effect="plain" type="warning">{{ c.group }}</el-tag>
                </div>
                <!-- 子机实时指标（Agent 最新上报值） -->
                <div class="node-metrics">
                  <div v-for="k in nodeMetricCells(c)" :key="k.label" class="nm">
                    <div class="nm-v" :class="k.cls">
                      {{ k.value }}<span v-if="k.value !== '—' && k.unit" class="nm-unit">{{ k.unit }}</span>
                    </div>
                    <div class="nm-l">{{ k.label }}</div>
                  </div>
                </div>
                <!-- 网络实时速率：与资产监控页同口径（↓ 下载 / ↑ 上传，B/s→KB/s→MB/s） -->
                <div class="nm-net" v-html="netCell(c)"></div>
                <div class="node-foot">
                  <span>{{ c.owner || '未指定负责人' }}</span>
                  <span :class="{ stale: !c.reachable }">{{ relTime(c.last_seen_at) }}</span>
                </div>
                <div v-if="!c.reachable && c.unreachable_reason" class="node-err" :title="`${c.unreachable_reason}（检测于 ${fmtTime(c.last_check_at)}）`">
                  {{ c.unreachable_reason }}
                </div>
              </el-card>
            </el-col>
            <!-- 假卡片：点击新增子机，自动绑定当前分组 -->
            <el-col v-perm="'assets:write'" :xs="24" :sm="12" :md="8" :lg="6">
              <el-card shadow="never" class="node-card add-card" @click="openAddChild(g.name)">
                <div class="add-card-inner">
                  <el-icon :size="22"><Plus /></el-icon>
                  <span>添加子机</span>
                </div>
              </el-card>
            </el-col>
          </el-row>
        </div>
      </template>

    <!-- ===== 详情 / 监控大弹窗 ===== -->
    <AssetMonitor v-model="monitorVisible" :asset-id="monitorId" />

    <!-- 添加母机：SSH 验证 + 自动纳管本机子机 -->
    <el-dialog v-model="addMotherVisible" title="添加母机" width="560">
      <el-alert type="info" :closable="false" class="db-note">
        提交时将验证 SSH 连通性（用户名 / 密码，密码仅本次验证与部署使用，不落库），
        验证失败不会创建；成功后自动纳管一台「本机子机」并部署自研 Agent，
        母机的在线状态与监控数据均来自该子机的真实上报。
      </el-alert>
      <el-form label-width="110" style="margin-top: 12px">
        <el-form-item required label="主机名">
          <el-input v-model="motherForm.hostname" placeholder="母机显示名，如 ops-cc-02" style="width: 300px" />
        </el-form-item>
        <el-form-item required label="服务器 IP">
          <el-input v-model="motherForm.ip" placeholder="如 10.0.0.9" style="width: 300px" />
        </el-form-item>
        <el-form-item label="SSH 端口">
          <el-input-number v-model="motherForm.ssh_port" :min="1" :max="65535" />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="motherForm.username" style="width: 160px" />
        </el-form-item>
        <el-form-item label="SSH 密码" required>
          <el-input
            v-model="motherForm.password"
            type="password"
            show-password
            placeholder="仅本次验证与部署使用，不落库"
            style="width: 240px"
          />
        </el-form-item>
        <el-form-item label="环境">
          <el-select v-model="motherForm.env" style="width: 200px">
            <el-option v-for="e in ENV_OPTIONS" :key="e.value" :label="e.label" :value="e.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="负责人">
          <el-input v-model="motherForm.owner" style="width: 200px" />
        </el-form-item>

        <el-divider content-position="left">告警策略（默认值，可调整）</el-divider>
        <el-alert type="info" :closable="false" class="db-note">
          定义 CPU / 内存 / 负载的告警阈值与持续触发窗口（秒）：母机与所有接入子机按同一标准判定告警。
          OOM / 磁盘 / 网络 / 应用层 / 数据库层等更多规则，登记后可随时在母机列表或详情的「告警策略」抽屉中配置。
        </el-alert>
        <el-form-item v-for="f in POLICY_FIELDS" :key="f.key" :label="f.label">
          <el-input-number
            v-model="motherForm.alert_policy[f.key]"
            :min="f.min"
            :max="f.max"
            :step="f.step"
            :precision="f.precision"
          />
          <span v-if="f.unit" class="field-hint">{{ f.unit }}</span>
          <span class="field-hint">{{ f.hint }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :loading="sshTesting === 'mother'" @click="runSshTest('mother')">
          <el-icon style="margin-right: 4px"><Connection /></el-icon>
          测试连接
        </el-button>
        <el-button @click="addMotherVisible = false">取消</el-button>
        <el-button type="primary" :loading="addingMother" @click="submitMother">创建母机</el-button>
      </template>
    </el-dialog>

    <!-- 删除母机：名下有子机时禁止删除（必须先删净子机） -->
    <el-dialog v-model="motherDeleteVisible" title="删除母机" width="500">
      <el-alert type="warning" :closable="false" title="此操作不可恢复">
        <p style="margin: 0 0 4px">
          将删除母机台账记录 <b>{{ motherDeleteForm.id }}</b
          >（{{ motherDeleteForm.hostname }}）。
        </p>
        <p v-if="motherDeleteForm.children > 0" style="margin: 0; color: var(--danger-vivid)">
          该母机名下还有 <b>{{ motherDeleteForm.children }}</b> 台子机，禁止删除。请先在详情中逐台删除子机
          （删除时会卸载服务器上的 Agent），删净后才能删除母机。
        </p>
        <p v-else style="margin: 4px 0 0">
          删除仅清理平台台账记录，不影响服务器本身。
        </p>
      </el-alert>
      <template #footer>
        <el-button @click="motherDeleteVisible = false">取消</el-button>
        <el-button
          type="danger"
          :disabled="motherDeleteForm.children > 0"
          :loading="motherDeleting"
          @click="submitMotherDelete"
        >
          {{ motherDeleteForm.children > 0 ? '存在子机，禁止删除' : '确认删除' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 添加子机：SSH 自动部署自研 Agent -->
    <el-dialog v-model="addChildVisible" title="添加子机（自动部署自研 Agent）" width="560">
      <el-alert type="info" :closable="false" class="db-note">
        平台将 SSH 登录目标机下发自研 Agent：采集 CPU / 内存 / 磁盘 / 负载并主动上报（约 1 分钟），
        全程无需登录服务器操作。SSH 密码仅本次部署使用，不落库。
      </el-alert>
      <el-form label-width="130" style="margin-top: 12px">
        <el-form-item label="目标机 IP" required>
          <el-input v-model="addChildForm.ip" placeholder="如 10.0.0.15" style="width: 240px" />
        </el-form-item>
        <el-form-item label="SSH 端口">
          <el-input-number v-model="addChildForm.port" :min="1" :max="65535" />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="addChildForm.username" style="width: 160px" />
        </el-form-item>
        <el-form-item label="SSH 密码" required>
          <el-input
            v-model="addChildForm.password"
            type="password"
            show-password
            placeholder="仅本次安装使用，不落库"
            style="width: 240px"
          />
        </el-form-item>
        <el-form-item label="子机名称">
          <el-input
            v-model="addChildForm.display_name"
            placeholder="选填，如 订单-网关-01；不填则用安装后的主机名"
            style="width: 240px"
          />
        </el-form-item>
        <el-form-item label="Agent 语言">
          <el-radio-group v-model="addChildForm.lang">
            <el-radio v-for="l in LANG_OPTIONS" :key="l.value" :value="l.value">{{ l.label }}</el-radio>
          </el-radio-group>
          <span class="field-hint">{{ LANG_HINTS[addChildForm.lang] }}</span>
        </el-form-item>
        <el-form-item label="应用">
          <el-input v-model="addChildForm.app" placeholder="如 订单系统" style="width: 240px" />
        </el-form-item>
        <el-form-item label="业务分组">
          <el-input v-model="addChildForm.group" placeholder="可留空" style="width: 240px" />
        </el-form-item>
        <el-form-item label="负责人">
          <el-input v-model="addChildForm.owner" placeholder="可留空" style="width: 240px" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :loading="sshTesting === 'child'" @click="runSshTest('child')">
          <el-icon style="margin-right: 4px"><Connection /></el-icon>
          测试连接
        </el-button>
        <el-button @click="addChildVisible = false">取消</el-button>
        <el-button type="primary" :loading="childProvisioning" @click="submitAddChild">开始纳管</el-button>
      </template>
    </el-dialog>

    <!-- 编辑资产 / 分组 -->
    <el-dialog v-model="editVisible" title="编辑资产" width="460">
      <el-form label-width="100">
        <el-form-item label="主机名">
          <el-input v-model="editForm.hostname" />
        </el-form-item>
        <el-form-item label="业务分组">
          <template #label>
            <HelpLabel label="业务分组" tip="按业务系统给机器分组；可直接输入新组名，清空表示移出分组" />
          </template>
          <el-select v-model="editForm.group" filterable allow-create default-first-option clearable placeholder="选择或输入新分组" style="width: 220px">
            <el-option v-for="g in groupNames" :key="g" :label="g" :value="g" />
          </el-select>
        </el-form-item>
        <el-form-item label="应用">
          <el-input v-model="editForm.app" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="editForm.role" style="width: 200px">
            <el-option v-for="r in ROLE_OPTIONS" :key="r.value" :label="r.label" :value="r.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="环境">
          <el-select v-model="editForm.env" style="width: 200px">
            <el-option v-for="e in ENV_OPTIONS" :key="e.value" :label="e.label" :value="e.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="负责人">
          <el-input v-model="editForm.owner" style="width: 200px" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editing" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 子机安装详情：纳管状态 / Agent 语言 / pid / 完整安装日志 -->
    <el-dialog
      v-model="childInstallVisible"
      :title="`安装详情 · ${childInstallAsset?.hostname || childInstallAsset?.id || ''}`"
      width="600"
    >
      <div v-loading="childProvisionLoading" style="min-height: 120px">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="纳管状态">
            {{ PROVISION_LABELS[childProvision?.status ?? childInstallAsset?.provision_status ?? ''] || '未纳管' }}
          </el-descriptions-item>
          <el-descriptions-item label="纳管来源">
            {{ childProvision ? `${childProvision.ip}:${childProvision.port}` : childInstallAsset?.ip || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="Agent 语言">{{ LANG_LABELS[childProvision?.lang] || '—' }}</el-descriptions-item>
          <el-descriptions-item label="进程 pid">{{ childProvision?.pid || '—' }}</el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="childProvision?.error"
          type="error"
          :closable="false"
          style="margin-top: 10px"
          :title="`失败原因：${childProvision.error}`"
        />
        <el-divider content-position="left">安装日志</el-divider>
        <pre class="install-logs">{{ (childProvision?.logs || []).join('\n') || '暂无日志' }}</pre>
      </div>
    </el-dialog>

    <!-- 删除子机：默认联动卸载服务器上的自研 Agent（go/py）；服务器已失联可跳过 -->
    <el-dialog v-model="childDeleteVisible" title="删除子机" width="500">
      <el-alert type="warning" :closable="false" title="此操作不可恢复">
        将删除资产 {{ childDeleteForm.hostname }}（{{ childDeleteForm.id }}）的台账记录，并从监控中移除。
      </el-alert>
      <el-form label-width="100" style="margin-top: 12px">
        <el-form-item v-if="childDeleteForm.has_provision" label="联动卸载">
          <el-checkbox v-model="childDeleteForm.uninstall">
            登录 {{ childDeleteForm.ip }} 卸载自研 Agent（停服务、删除安装目录与日志，go/py 通用）
          </el-checkbox>
          <div v-if="!childDeleteForm.uninstall" class="uninstall-skip-note">
            跳过卸载将遗留服务器上的 Agent 进程与文件，仅建议在服务器已下线/重装时使用
          </div>
        </el-form-item>
        <el-form-item v-if="childDeleteForm.uninstall" label="SSH 密码" required>
          <el-input
            v-model="childDeleteForm.password"
            type="password"
            show-password
            :placeholder="`服务器 ${childDeleteForm.ip} 的 SSH 密码（仅本次卸载使用，不落库）`"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="childDeleteVisible = false">取消</el-button>
        <el-button type="danger" :loading="childDeleting" @click="submitChildDelete">确认删除</el-button>
      </template>
    </el-dialog>

    <!-- 告警策略：母机/子机统一抽屉（子机自有策略 > 继承母机 > 平台默认），分组配置 + P0-P3 级别 -->
    <el-drawer v-model="policyVisible" :title="`告警策略 · ${policyTarget?.hostname || ''}`" size="780px">
      <div v-loading="!policyInfo" class="policy-body">
        <el-alert type="info" :closable="false" class="db-note">
          {{ policyIsChild
            ? (policyInfo?.inherited === false
              ? '该子机使用自有策略，仅作用于本机；保存后 agent 自动拉取新配置即生效，删除自有策略可恢复继承母机。'
              : '当前继承所属母机的告警策略（无自有策略）；修改保存后将覆盖为子机自有策略，不再随母机变化。')
            : '阈值与触发窗口作用于该母机及其所有接入子机的越限判定，保存后 agent 自动拉取新策略，无需重启。' }}
        </el-alert>
        <div class="policy-meta">
          <el-tag size="small" :type="policyInfo?.inherited ? 'info' : 'success'" effect="plain">
            {{ policySourceLabel }}
          </el-tag>
          <span v-if="policyIsChild && policyInfo?.source === 'mother'" class="policy-meta-hint">跟随所属母机策略变化</span>
          <div class="policy-toolbar">
            <el-select v-model="templateId" placeholder="应用配置模板" size="small" style="width: 230px" @change="applyTemplate">
              <el-option v-for="t in policyInfo?.templates || []" :key="t.id" :label="t.label" :value="t.id" />
            </el-select>
            <el-button size="small" @click="resetToDefaults">恢复平台默认</el-button>
          </div>
        </div>

        <el-tabs v-model="policyTab">
          <!-- 基础指标（秒制窗口） -->
          <el-tab-pane label="基础指标" name="basic">
            <div v-for="s in sectionsOf('basic')" :key="s.key" class="policy-section">
              <div class="section-head">
                <span class="section-title">{{ s.title }}</span>
                <span class="section-desc">{{ s.desc }}</span>
              </div>
              <div class="section-body">
                <div v-for="f in s.fields" :key="f.key" class="field-row">
                  <span class="field-label">{{ f.label }}</span>
                  <el-input-number
                    v-model="policyForm[f.key]"
                    :min="f.min ?? 10"
                    :max="f.max ?? 86400"
                    :step="f.step ?? 10"
                    :precision="f.precision ?? 0"
                    size="small"
                    controls-position="right"
                  />
                  <span v-if="f.unit" class="field-unit">{{ f.unit }}</span>
                  <span v-if="f.hint" class="field-hint">{{ f.hint }}</span>
                </div>
                <div class="field-row">
                  <span class="field-label">告警级别</span>
                  <el-select v-model="policyForm[`${s.key}_level`]" size="small" style="width: 190px">
                    <el-option v-for="lv in levels" :key="lv.value" :value="lv.value" :label="`${lv.value} ${lv.label}`">
                      <span class="level-dot" :style="{ background: lv.color }"></span>{{ lv.value }} {{ lv.label }}
                    </el-option>
                  </el-select>
                  <span class="field-hint">
                    当前 <span class="level-dot" :style="{ background: levelColor(policyForm[`${s.key}_level`]) }"></span>
                    {{ policyForm[`${s.key}_level`] }}
                  </span>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <!-- 系统资源（开关 + 阈值 + 窗口 + 级别全可配） -->
          <el-tab-pane label="系统资源" name="sys">
            <div
              v-for="s in sectionsOf('sys')"
              :key="s.key"
              class="policy-section"
              :class="{ off: s.noSwitch !== true && !policyForm[`${s.key}_enabled`] }"
            >
              <div class="section-head">
                <el-switch v-if="s.noSwitch !== true" v-model="policyForm[`${s.key}_enabled`]" />
                <span class="section-title">{{ s.title }}</span>
                <span class="section-desc">{{ s.desc }}</span>
              </div>
              <div class="section-body">
                <div v-for="f in s.fields" :key="f.key" class="field-row">
                  <span class="field-label">{{ f.label }}</span>
                  <el-input-number
                    v-model="policyForm[f.key]"
                    :min="f.min ?? 10"
                    :max="f.max ?? 86400"
                    :step="f.step ?? 10"
                    :precision="f.precision ?? 0"
                    size="small"
                    controls-position="right"
                  />
                  <span v-if="f.unit" class="field-unit">{{ f.unit }}</span>
                  <span v-if="f.hint" class="field-hint">{{ f.hint }}</span>
                </div>
                <div v-if="s.text" class="field-row">
                  <span class="field-label">{{ s.text.label }}</span>
                  <el-input v-model="policyForm[s.text.key]" size="small" style="width: 200px" :placeholder="s.text.placeholder" />
                </div>
                <div class="field-row">
                  <span class="field-label">告警级别</span>
                  <el-select v-model="policyForm[`${s.key}_level`]" size="small" style="width: 190px">
                    <el-option v-for="lv in levels" :key="lv.value" :value="lv.value" :label="`${lv.value} ${lv.label}`">
                      <span class="level-dot" :style="{ background: lv.color }"></span>{{ lv.value }} {{ lv.label }}
                    </el-option>
                  </el-select>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <!-- 进程与端口（列表 + 窗口 + 级别可配） -->
          <el-tab-pane label="进程与端口" name="proc">
            <div
              v-for="s in sectionsOf('proc')"
              :key="s.key"
              class="policy-section"
              :class="{ off: !policyForm[`${s.key}_enabled`] }"
            >
              <div class="section-head">
                <el-switch v-model="policyForm[`${s.key}_enabled`]" />
                <span class="section-title">{{ s.title }}</span>
                <span class="section-desc">{{ s.desc }}</span>
              </div>
              <div class="section-body">
                <div v-if="s.items" class="field-row">
                  <span class="field-label">{{ s.items.label }}</span>
                  <el-input
                    :model-value="(policyForm[s.items.key] || []).join(', ')"
                    size="small"
                    style="width: 300px"
                    :placeholder="s.items.placeholder"
                    @update:model-value="(v) => setItems(s.items.key, v, s.items.numeric)"
                  />
                  <span class="field-hint">逗号分隔</span>
                </div>
                <div v-for="f in s.fields" :key="f.key" class="field-row">
                  <span class="field-label">{{ f.label }}</span>
                  <el-input-number
                    v-model="policyForm[f.key]"
                    :min="f.min ?? 10"
                    :max="f.max ?? 86400"
                    :step="f.step ?? 10"
                    :precision="f.precision ?? 0"
                    size="small"
                    controls-position="right"
                  />
                  <span v-if="f.unit" class="field-unit">{{ f.unit }}</span>
                  <span v-if="f.hint" class="field-hint">{{ f.hint }}</span>
                </div>
                <div class="field-row">
                  <span class="field-label">告警级别</span>
                  <el-select v-model="policyForm[`${s.key}_level`]" size="small" style="width: 190px">
                    <el-option v-for="lv in levels" :key="lv.value" :value="lv.value" :label="`${lv.value} ${lv.label}`">
                      <span class="level-dot" :style="{ background: lv.color }"></span>{{ lv.value }} {{ lv.label }}
                    </el-option>
                  </el-select>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <!-- 应用层规则（阈值/窗口/级别全可配，数据源为 agent 指标抓取） -->
          <el-tab-pane :label="`应用层（${catalogRows('app').length}）`" name="app">
            <div class="scrape-box">
              <el-switch v-model="policyForm.metrics_scrape_enabled" size="small" />
              <span class="section-title">指标抓取数据源</span>
              <el-input
                :model-value="(policyForm.metrics_urls || []).join('\n')"
                type="textarea"
                :rows="2"
                placeholder="Prometheus 文本指标 URL，每行一个（最多 16 个），如 http://127.0.0.1:9090/metrics"
                @update:model-value="setUrls"
              />
              <div class="field-hint">开启后 agent 定期抓取并上报指标；未接数据源的下方规则不会触发，但阈值/窗口/级别均可预先配置。</div>
            </div>
            <el-table :data="catalogRows('app')" size="small" class="rule-table">
              <el-table-column label="规则" min-width="210">
                <template #default="{ row }">
                  <div class="rule-name">{{ row.name }}</div>
                  <div class="rule-metric">{{ row.metric }}</div>
                </template>
              </el-table-column>
              <el-table-column label="启用" width="64">
                <template #default="{ row }">
                  <el-switch v-model="ruleOf(row.id).enabled" size="small" />
                </template>
              </el-table-column>
              <el-table-column label="阈值" width="128">
                <template #default="{ row }">
                  <el-input-number
                    v-model="ruleOf(row.id).threshold"
                    :min="0"
                    :step="row.threshold > 0 && row.threshold < 10 ? 0.5 : 10"
                    size="small"
                    controls-position="right"
                    style="width: 110px"
                  />
                  <span class="field-unit">{{ row.unit }}</span>
                </template>
              </el-table-column>
              <el-table-column label="窗口(秒)" width="122">
                <template #default="{ row }">
                  <el-input-number v-model="ruleOf(row.id).window_seconds" :min="10" :max="86400" :step="10" size="small" controls-position="right" style="width: 110px" />
                </template>
              </el-table-column>
              <el-table-column label="级别" width="130">
                <template #default="{ row }">
                  <el-select v-model="ruleOf(row.id).level" size="small" style="width: 120px">
                    <el-option v-for="lv in levels" :key="lv.value" :value="lv.value" :label="lv.value">
                      <span class="level-dot" :style="{ background: lv.color }"></span>{{ lv.value }} {{ lv.label }}
                    </el-option>
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="重复告警(分钟)" width="132">
                <template #default="{ row }">
                  <el-input-number v-model="ruleOf(row.id).notify_minutes" :min="0" :max="1440" :step="5" size="small" controls-position="right" style="width: 112px" />
                </template>
              </el-table-column>
            </el-table>
            <div class="field-hint" style="margin-top: 8px">重复告警：故障持续期间每隔该间隔重新提醒一次（如 30=每 30 分钟提醒）；0=不重复，仅触发/恢复时提醒一次（适合 OOM 等瞬时事件）。</div>
          </el-tab-pane>

          <!-- 数据库与中间件规则 -->
          <el-tab-pane :label="`数据库层（${catalogRows('db').length}）`" name="db">
            <el-table :data="catalogRows('db')" size="small" class="rule-table">
              <el-table-column label="规则" min-width="210">
                <template #default="{ row }">
                  <div class="rule-name">{{ row.name }}</div>
                  <div class="rule-metric">{{ row.metric }}</div>
                </template>
              </el-table-column>
              <el-table-column label="启用" width="64">
                <template #default="{ row }">
                  <el-switch v-model="ruleOf(row.id).enabled" size="small" />
                </template>
              </el-table-column>
              <el-table-column label="阈值" width="128">
                <template #default="{ row }">
                  <el-input-number
                    v-model="ruleOf(row.id).threshold"
                    :min="0"
                    :step="row.threshold > 0 && row.threshold < 10 ? 0.5 : 10"
                    size="small"
                    controls-position="right"
                    style="width: 110px"
                  />
                  <span class="field-unit">{{ row.unit }}</span>
                </template>
              </el-table-column>
              <el-table-column label="窗口(秒)" width="122">
                <template #default="{ row }">
                  <el-input-number v-model="ruleOf(row.id).window_seconds" :min="10" :max="86400" :step="10" size="small" controls-position="right" style="width: 110px" />
                </template>
              </el-table-column>
              <el-table-column label="级别" width="130">
                <template #default="{ row }">
                  <el-select v-model="ruleOf(row.id).level" size="small" style="width: 120px">
                    <el-option v-for="lv in levels" :key="lv.value" :value="lv.value" :label="lv.value">
                      <span class="level-dot" :style="{ background: lv.color }"></span>{{ lv.value }} {{ lv.label }}
                    </el-option>
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="重复告警(分钟)" width="132">
                <template #default="{ row }">
                  <el-input-number v-model="ruleOf(row.id).notify_minutes" :min="0" :max="1440" :step="5" size="small" controls-position="right" style="width: 112px" />
                </template>
              </el-table-column>
            </el-table>
            <div class="field-hint" style="margin-top: 8px">重复告警：故障持续期间每隔该间隔重新提醒一次；0=不重复，仅触发/恢复时提醒一次。</div>
          </el-tab-pane>
        </el-tabs>
      </div>
      <template #footer>
        <div class="drawer-footer">
          <el-button
            v-if="policyIsChild && policyInfo?.inherited === false"
            type="warning"
            plain
            :loading="policyResetting"
            @click="resetAlertPolicy"
          >
            恢复继承母机
          </el-button>
          <el-button @click="policyVisible = false">取消</el-button>
          <el-button type="primary" :loading="policySaving" :disabled="!policyInfo" @click="submitAlertPolicy">
            保存
          </el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Folder, FolderAdd, Cpu, ArrowRight, ArrowLeft, Plus, EditPen, Delete, View, InfoFilled, Refresh, Bell, Connection } from '@element-plus/icons-vue'
import HelpLabel from '../components/HelpLabel.vue'
import AssetMonitor from '../components/AssetMonitor.vue'
import {
  createMother,
  deleteAsset,
  deleteGroup as deleteGroupApi,
  fetchAlertPolicy,
  fetchMotherOverview,
  fetchMotherOverviewById,
  fetchChildrenRealtime,
  fetchMothers,
  provisionAsset,
  fetchAssetProvision,
  removeAsset,
  renameGroup as renameGroupApi,
  resetAlertPolicy as resetAlertPolicyApi,
  updateAlertPolicy,
  updateAsset,
} from '../api'
const query = reactive({ keyword: '' })

const detailView = ref(false) // 总览两层：false=母机记录列表，true=某台母机内部（子机视图）
const motherLoading = ref(false)
const lastSyncAt = ref('') // 最近一次指标同步时间（卡片指标 10s 轮询）
const mother = ref(null)
const children = ref([])
const mothers = ref([])
const selectedMotherId = ref('')

// ===== 枚举中文映射（存库存英文值，仅展示层翻译） =====
const ROLE_LABELS = { app: '应用', db: '数据库', gw: '网关', cache: '缓存', job: '任务机', other: '其他' }
const ENV_LABELS = { prod: '生产', staging: '预发', test: '测试' }
const ROLE_OPTIONS = Object.entries(ROLE_LABELS).map(([value, label]) => ({ value, label }))
const ENV_OPTIONS = Object.entries(ENV_LABELS).map(([value, label]) => ({ value, label }))

// ===== 详情 / 监控大弹窗 =====
const monitorVisible = ref(false)
const monitorId = ref('')

// ===== 编辑资产 / 分组 =====
const editVisible = ref(false)
const editing = ref(false)
const editForm = reactive({ id: '', hostname: '', group: '', app: '', role: 'app', env: 'prod', owner: '' })
const groupNames = computed(() => [...new Set(children.value.map((c) => c.group).filter(Boolean))])

// ===== 告警策略（v2：秒制窗口 + P0-P3 级别，默认值与后端 DEFAULT_POLICY 保持一致） =====
const ALERT_POLICY_DEFAULT = {
  cpu_threshold: 90,
  cpu_window_seconds: 300,
  cpu_level: 'P1',
  mem_threshold: 90,
  mem_window_seconds: 300,
  mem_level: 'P1',
  load_threshold: 1.5,
  load_window_seconds: 300,
  load_level: 'P1',
  oom_enabled: false,
  oom_window_seconds: 300,
  oom_level: 'P1',
  swap_enabled: false,
  swap_threshold: 80,
  swap_window_seconds: 300,
  swap_level: 'P2',
  inode_enabled: false,
  inode_threshold: 80,
  inode_window_seconds: 300,
  inode_level: 'P2',
  disk_io_enabled: false,
  disk_io_threshold: 100,
  disk_io_window_seconds: 300,
  disk_io_level: 'P2',
  net_perf_enabled: false,
  net_loss_threshold: 1.0,
  net_latency_threshold: 100,
  net_perf_window_seconds: 300,
  net_perf_level: 'P2',
  net_probe_target: '223.5.5.5:443',
  bandwidth_enabled: false,
  bandwidth_threshold: 80,
  bandwidth_window_seconds: 600,
  bandwidth_level: 'P2',
  tcp_conn_enabled: false,
  tcp_time_wait_threshold: 10000,
  tcp_conn_pct_threshold: 80,
  tcp_conn_window_seconds: 300,
  tcp_conn_level: 'P2',
  process_enabled: false,
  process_items: ['nginx', 'mysqld', 'java'],
  process_window_seconds: 60,
  process_level: 'P0',
  port_enabled: false,
  port_items: [22, 80, 443],
  port_window_seconds: 60,
  port_level: 'P0',
  metrics_scrape_enabled: false,
  metrics_urls: [],
  // 重复告警间隔（分钟）：事件持续期间每隔该间隔重新提醒；0=不重复仅状态变化提醒
  cpu_notify_minutes: 30,
  mem_notify_minutes: 30,
  load_notify_minutes: 30,
  swap_notify_minutes: 30,
  inode_notify_minutes: 30,
  disk_io_notify_minutes: 30,
  net_perf_notify_minutes: 30,
  bandwidth_notify_minutes: 30,
  tcp_conn_notify_minutes: 30,
  process_notify_minutes: 30,
  port_notify_minutes: 30,
  oom_notify_minutes: 0,
  rules: {},
}
// 添加母机表单里的基础三项（秒制）；完整策略登记后可在「告警策略」抽屉配置
const POLICY_FIELDS = [
  { key: 'cpu_threshold', label: 'CPU 阈值', unit: '%', min: 1, max: 99, step: 1, precision: 0, hint: 'CPU 使用率超过该值（%）即进入告警判定' },
  { key: 'cpu_window_seconds', label: 'CPU 触发窗口', unit: '秒', min: 10, max: 86400, step: 10, precision: 0, hint: '持续超过阈值该时长（秒）才触发告警' },
  { key: 'mem_threshold', label: '内存阈值', unit: '%', min: 1, max: 99, step: 1, precision: 0, hint: '内存使用率超过该值（%）即进入告警判定' },
  { key: 'mem_window_seconds', label: '内存触发窗口', unit: '秒', min: 10, max: 86400, step: 10, precision: 0, hint: '持续超过阈值该时长（秒）才触发告警' },
  { key: 'load_threshold', label: '负载阈值', unit: '', min: 0.1, max: 100, step: 0.1, precision: 1, hint: 'CPU load（1m 平均）超过该值进入告警判定' },
  { key: 'load_window_seconds', label: '负载触发窗口', unit: '秒', min: 10, max: 86400, step: 10, precision: 0, hint: '持续超过阈值该时长（秒）才触发告警' },
]
// 策略抽屉分组配置描述（数据驱动渲染；enabled 开关/阈值/窗口/级别全部可配）
const POLICY_SECTIONS = [
  { tab: 'basic', key: 'cpu', title: 'CPU 使用率', desc: 'CPU 使用率持续高位运行', noSwitch: true,
    fields: [
      { key: 'cpu_threshold', label: '使用率阈值', unit: '%', min: 1, max: 99, step: 1, precision: 0 },
      { key: 'cpu_window_seconds', label: '触发窗口', unit: '秒' },
      { key: 'cpu_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '持续期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ] },
  { tab: 'basic', key: 'mem', title: '内存使用率', desc: '内存使用率持续高位运行', noSwitch: true,
    fields: [
      { key: 'mem_threshold', label: '使用率阈值', unit: '%', min: 1, max: 99, step: 1, precision: 0 },
      { key: 'mem_window_seconds', label: '触发窗口', unit: '秒' },
      { key: 'mem_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '持续期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ] },
  { tab: 'basic', key: 'load', title: '系统负载（load1）', desc: '1 分钟平均负载持续越限', noSwitch: true,
    fields: [
      { key: 'load_threshold', label: '负载阈值', min: 0.1, max: 100, step: 0.1, precision: 1 },
      { key: 'load_window_seconds', label: '触发窗口', unit: '秒' },
      { key: 'load_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '持续期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ] },
  { tab: 'sys', key: 'oom', title: 'OOM Kill', desc: '发生 OOM kill 事件即告警；恢复窗口内无新事件自动恢复',
    fields: [
      { key: 'oom_window_seconds', label: '恢复窗口', unit: '秒' },
      { key: 'oom_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '瞬时事件默认 0：发生提醒一次，恢复后再次发生重新告警' },
    ] },
  { tab: 'sys', key: 'swap', title: 'Swap 使用率', desc: 'Swap 使用率持续越限',
    fields: [
      { key: 'swap_threshold', label: '使用率阈值', unit: '%', min: 1, max: 100, step: 1, precision: 0 },
      { key: 'swap_window_seconds', label: '触发窗口', unit: '秒' },
      { key: 'swap_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '持续期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ] },
  { tab: 'sys', key: 'inode', title: 'Inode 使用率', desc: '文件系统 inode 持续越限（耗尽前预警）',
    fields: [
      { key: 'inode_threshold', label: '使用率阈值', unit: '%', min: 1, max: 100, step: 1, precision: 0 },
      { key: 'inode_window_seconds', label: '触发窗口', unit: '秒' },
      { key: 'inode_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '持续期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ] },
  { tab: 'sys', key: 'disk_io', title: '磁盘 IO 延迟', desc: '磁盘 await（平均 IO 等待时间）持续过高',
    fields: [
      { key: 'disk_io_threshold', label: 'await 阈值', unit: 'ms', min: 1, max: 10000, step: 10, precision: 0 },
      { key: 'disk_io_window_seconds', label: '触发窗口', unit: '秒' },
      { key: 'disk_io_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '持续期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ] },
  { tab: 'sys', key: 'net_perf', title: '网络性能（丢包/延迟）', desc: 'agent 对探测目标 TCP 拨测，丢包率或延迟持续越限',
    fields: [
      { key: 'net_loss_threshold', label: '丢包率阈值', unit: '%', min: 0.1, max: 100, step: 0.1, precision: 1 },
      { key: 'net_latency_threshold', label: '延迟阈值', unit: 'ms', min: 1, max: 10000, step: 10, precision: 0 },
      { key: 'net_perf_window_seconds', label: '触发窗口', unit: '秒' },
      { key: 'net_perf_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '持续期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ],
    text: { key: 'net_probe_target', label: '探测目标', placeholder: '223.5.5.5:443' } },
  { tab: 'sys', key: 'bandwidth', title: '带宽使用率', desc: '出口/入口带宽使用率持续越限',
    fields: [
      { key: 'bandwidth_threshold', label: '带宽阈值', unit: '%', min: 1, max: 100, step: 1, precision: 0 },
      { key: 'bandwidth_window_seconds', label: '触发窗口', unit: '秒' },
      { key: 'bandwidth_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '持续期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ] },
  { tab: 'sys', key: 'tcp_conn', title: 'TCP 连接', desc: 'TIME_WAIT 过多或总连接数接近系统上限',
    fields: [
      { key: 'tcp_time_wait_threshold', label: 'TIME_WAIT 数', unit: '个', min: 1, max: 1000000, step: 100, precision: 0 },
      { key: 'tcp_conn_pct_threshold', label: '连接上限比', unit: '%', min: 1, max: 100, step: 1, precision: 0 },
      { key: 'tcp_conn_window_seconds', label: '触发窗口', unit: '秒' },
      { key: 'tcp_conn_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '持续期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ] },
  { tab: 'proc', key: 'process', title: '关键进程消失', desc: '核心进程异常退出即告警（按 /proc 进程名子串匹配）',
    items: { key: 'process_items', label: '监控进程', placeholder: 'nginx, mysqld, java' },
    fields: [
      { key: 'process_window_seconds', label: '恢复窗口', unit: '秒' },
      { key: 'process_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '进程持续消失期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ] },
  { tab: 'proc', key: 'port', title: '关键端口探活', desc: '本机端口 TCP 探活失败即告警',
    items: { key: 'port_items', label: '监控端口', placeholder: '22, 80, 443', numeric: true },
    fields: [
      { key: 'port_window_seconds', label: '恢复窗口', unit: '秒' },
      { key: 'port_notify_minutes', label: '重复告警', unit: '分钟', min: 0, max: 1440, step: 5, hint: '端口持续不可达期间每该间隔重新提醒；0=仅触发/恢复时提醒' },
    ] },
]
const policyVisible = ref(false)
const policySaving = ref(false)
const policyResetting = ref(false)
const policyTarget = ref(null) // 当前设置策略的资产（母机或子机）
const policyInfo = ref(null) // GET 返回（policy / inherited / source / defaults / levels / catalog / templates）
const policyDefaults = ref(null) // 平台默认策略（恢复默认用）
const policyTab = ref('basic')
const templateId = ref('')
const policyForm = reactive({ ...ALERT_POLICY_DEFAULT })
const policyIsChild = computed(() => !!policyTarget.value && !policyTarget.value.is_mother)
const policySourceLabel = computed(() => {
  const map = { self: '子机自有策略', mother: '继承母机策略', default: '平台默认策略' }
  return policyTarget.value?.is_mother ? '母机策略（子机未自定义时沿用）' : map[policyInfo.value?.source] || ''
})
// P0-P3 级别（后端 meta 下发含颜色；异常兜底与后端 LEVEL_COLORS 一致）
const FALLBACK_LEVELS = [
  { value: 'P0', label: '严重故障', color: '#ff3b30' },
  { value: 'P1', label: '重要告警', color: '#ff9500' },
  { value: 'P2', label: '一般告警', color: '#f7ba2a' },
  { value: 'P3', label: '提示信息', color: '#909399' },
]
const levels = computed(() => policyInfo.value?.levels || FALLBACK_LEVELS)
function levelColor(v) {
  return levels.value.find((l) => l.value === v)?.color || '#909399'
}
function sectionsOf(tab) {
  return POLICY_SECTIONS.filter((s) => s.tab === tab)
}
function catalogRows(group) {
  return (policyInfo.value?.catalog || []).filter((r) => r.group === group)
}
// 应用/数据库规则行：目录默认值合并已保存配置，保证新增规则有完整初始值
function buildPolicyForm(policy) {
  const base = { ...ALERT_POLICY_DEFAULT, ...(policy || {}) }
  const catalog = policyInfo.value?.catalog || []
  const rules = {}
  for (const r of catalog) {
    rules[r.id] = {
      enabled: false,
      threshold: r.threshold,
      window_seconds: r.window_seconds,
      level: r.level,
      notify_minutes: r.notify_minutes ?? 30,
      ...(base.rules?.[r.id] || {}),
    }
  }
  base.rules = rules
  return base
}
function ruleOf(id) {
  if (!policyForm.rules[id]) {
    policyForm.rules[id] = { enabled: false, threshold: 0, window_seconds: 60, level: 'P2', notify_minutes: 30 }
  }
  if (policyForm.rules[id].notify_minutes == null) policyForm.rules[id].notify_minutes = 30
  return policyForm.rules[id]
}
function setItems(key, value, numeric = false) {
  const parts = String(value).split(/[,，\s]+/).filter(Boolean)
  policyForm[key] = numeric
    ? parts.map(Number).filter((n) => Number.isInteger(n) && n > 0 && n < 65536)
    : parts.slice(0, 32)
}
function setUrls(value) {
  policyForm.metrics_urls = String(value).split(/\r?\n/).map((s) => s.trim()).filter(Boolean).slice(0, 16)
}
// 配置模板：standard/strict/relaxed 一键填充（保存后生效）
function applyTemplate(tid) {
  const tpl = (policyInfo.value?.templates || []).find((t) => t.id === tid)
  if (!tpl) return
  Object.assign(policyForm, buildPolicyForm(tpl.policy))
  ElMessage.success(`已应用模板「${tpl.label}」，保存后生效`)
}
function resetToDefaults() {
  Object.assign(policyForm, buildPolicyForm(policyDefaults.value || ALERT_POLICY_DEFAULT))
  templateId.value = ''
  ElMessage.success('已恢复平台默认值，保存后生效')
}

// ===== 添加母机（SSH 验证 + 自动纳管本机子机） =====
const addMotherVisible = ref(false)
const addingMother = ref(false)
const motherForm = reactive({
  hostname: '',
  ip: '',
  ssh_port: 22,
  username: 'root',
  password: '',
  env: 'prod',
  owner: '',
  alert_policy: { ...ALERT_POLICY_DEFAULT },
})

// ===== SSH 连通性测试（新增母机 / 子机表单共用） =====
const sshTesting = ref('') // '' | 'mother' | 'child'

// 错误信息提取：兼容 FastAPI 422（detail 为数组）、请求超时、网络异常等所有形态
function errMsg(err, fallback) {
  const d = err?.response?.data?.detail
  if (typeof d === 'string' && d) return d
  if (Array.isArray(d) && d.length) {
    const first = d[0]
    const field = (first.loc || []).slice(-1)[0] || ''
    return `参数校验失败${field ? `（${field}）` : ''}：${first.msg || '请检查表单填写'}`
  }
  if (err?.code === 'ECONNABORTED' || /timeout/i.test(err?.message || '')) {
    return '连接测试超时，请确认目标机 IP/端口正确且网络可达后重试'
  }
  if (!err?.response) {
    return '网络异常：无法连接到平台服务，请检查本机到平台的网络'
  }
  return fallback
}

async function runSshTest(target) {
  const form = target === 'mother' ? motherForm : addChildForm
  const port = target === 'mother' ? form.ssh_port : form.port
  if (!form.ip.trim()) {
    ElMessage.warning('请先填写服务器 IP')
    return
  }
  if (!form.password) {
    ElMessage.warning('请先填写 SSH 密码')
    return
  }
  sshTesting.value = target
  try {
    const { data } = await testSsh({ ip: form.ip.trim(), port, username: form.username, password: form.password })
    ElMessage.success(data.message || '连接成功')
  } catch (err) {
    ElMessage.error(errMsg(err, '连接失败：无法建立 SSH 会话'))
  } finally {
    sshTesting.value = ''
  }
}

let overviewTimer = null
let realtimeTimer = null
onMounted(() => {
  refresh()
  // 全量 overview 低频轮询：分组统计 / 告警徽标 / 归属关系（10s）
  overviewTimer = setInterval(() => {
    if (!motherLoading.value) loadOverview()
  }, 10000)
  // 分组卡片实时轮询：1 秒/次批量拉 Agent 最新帧（与监控详情实时面板同源同频）
  realtimeTimer = setInterval(loadChildrenRealtime, 1000)
})
onBeforeUnmount(() => {
  if (overviewTimer) clearInterval(overviewTimer)
  if (realtimeTimer) clearInterval(realtimeTimer)
})

// 分组卡片实时刷新：仅轻量指标，在线态翻转时触发一次全量 overview 同步徽标/分组统计
async function loadChildrenRealtime() {
  if (document.hidden || !detailView.value || !selectedMotherId.value) return
  try {
    const { data } = await fetchChildrenRealtime(selectedMotherId.value)
    const items = data.items || {}
    let flipped = false
    for (const c of children.value) {
      const m = items[c.id]
      if (!m) continue
      c.metrics = { ...m }
      if (c.reachable !== m.online) flipped = true
      c.reachable = m.online
    }
    lastSyncAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    if (flipped) loadOverview()
  } catch {
    /* 单次拉取失败静默，下一秒重试 */
  }
}

// 子机卡片指标单元格（Agent 最新上报；无数据显示 "—"）
function nodeMetricCells(c) {
  const m = c.metrics || {}
  const fmt = (v) => (v == null ? '—' : Number(v).toFixed(2))
  const cls = (v) => (v == null ? '' : v >= 90 ? 'danger' : v >= 75 ? 'warn' : 'ok')
  return [
    { label: 'CPU', value: fmt(m.cpu), unit: '%', cls: cls(m.cpu) },
    { label: '内存', value: fmt(m.mem), unit: '%', cls: cls(m.mem) },
    { label: '磁盘', value: fmt(m.disk), unit: '%', cls: cls(m.disk) },
    { label: '负载', value: fmt(m.load), unit: '', cls: '' },
  ]
}

// 网速格式化（与 AssetMonitor 实时面板一致）：B/s → KB/s → MB/s → GB/s
function speedText(bps) {
  if (bps == null) return '—'
  if (bps >= 1024 ** 3) return `${(bps / 1024 ** 3).toFixed(2)} GB/s`
  if (bps >= 1024 ** 2) return `${(bps / 1024 ** 2).toFixed(2)} MB/s`
  if (bps >= 1024) return `${(bps / 1024).toFixed(1)} KB/s`
  return `${Math.round(bps)} B/s`
}

// 卡片网络行：↓ 下载 / ↑ 上传（与资产监控页网络视图同口径）；无数据返回空（CSS 隐藏）
function netCell(c) {
  const m = c.metrics || {}
  if (m.net_rx_bps == null && m.net_tx_bps == null) return ''
  return (
    `<span style="color:#34c759">↓ ${speedText(m.net_rx_bps)}</span>` +
    '<span class="nm-net-dot">·</span>' +
    `<span style="color:#ff9500">↑ ${speedText(m.net_tx_bps)}</span>`
  )
}

function openMotherDetail(m) {
  selectedMotherId.value = m.id
  detailView.value = true
  loadOverview()
}

function backToMothers() {
  detailView.value = false
}

const filteredChildren = computed(() => {
  const kw = query.keyword.trim().toLowerCase()
  if (!kw) return children.value
  return children.value.filter((c) =>
    [c.id, c.hostname, c.ip, c.app, c.owner, c.group].some((v) => (v || '').toLowerCase().includes(kw)),
  )
})

const groupedChildren = computed(() => {
  const map = new Map()
  for (const c of filteredChildren.value) {
    const g = c.group || ''
    if (!map.has(g)) map.set(g, [])
    map.get(g).push(c)
  }
  // 无过滤关键词时，把母机上登记的自定义空分组也展示出来（作为拖拽目标）
  if (!query.keyword.trim()) {
    for (const gi of groupInfos.value) {
      if (gi.name && !map.has(gi.name)) map.set(gi.name, [])
    }
  }
  const names = [...map.keys()]
  names.sort((a, b) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b, 'zh-CN')))
  return names.map((name) => ({
    name,
    items: map.get(name),
    online: map.get(name).filter((c) => c.reachable).length,
  }))
})

// ===== 拖拽分组 =====
const dragChild = ref(null)
const dragOverGroup = ref('')
const groupInfos = ref([])

function onDragStart(c, ev) {
  dragChild.value = c
  if (ev?.dataTransfer) {
    ev.dataTransfer.effectAllowed = 'move'
    ev.dataTransfer.setData('text/plain', c.id)
  }
}

async function onDropToGroup(name) {
  const c = dragChild.value
  dragOverGroup.value = ''
  dragChild.value = null
  if (!c) return
  if ((c.group || '') === name) return // 已在该分组
  try {
    // 母机自身卡是合成 id（无库记录），group 存在母机资产上
    if (c.is_mother) await updateAsset(mother.value?.id || c.mother_id, { group: name || '' })
    else await updateAsset(c.id, { group: name || '' })
    ElMessage.success(name ? `已将「${c.hostname || c.id}」移入分组「${name}」` : `已将「${c.hostname || c.id}」移出分组`)
    refresh()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '分组调整失败')
  }
}

async function doRenameGroup(name) {
  try {
    const { value } = await ElMessageBox.prompt(`修改分组名称（组内子机将一并迁移）`, `重命名分组「${name}」`, {
      inputValue: name,
      inputPattern: /\S+/,
      inputErrorMessage: '分组名不能为空',
    })
    const nn = (value || '').trim()
    if (!nn || nn === name) return
    await renameGroupApi(mother.value?.id || selectedMotherId.value, name, nn)
    ElMessage.success(`分组已重命名为「${nn}」`)
    refresh()
  } catch (err) {
    if (err !== 'cancel' && err?.message !== 'cancel') {
      ElMessage.error(err.response?.data?.detail || '重命名失败')
    }
  }
}

async function doDeleteGroup(name) {
  try {
    await ElMessageBox.confirm(`确定删除分组「${name}」？（分组下有子机时无法删除）`, '删除分组', { type: 'warning' })
    await deleteGroupApi(mother.value?.id || selectedMotherId.value, name)
    ElMessage.success(`分组「${name}」已删除`)
    refresh()
  } catch (err) {
    if (err !== 'cancel' && err?.message !== 'cancel') {
      ElMessage.error(err.response?.data?.detail || '删除分组失败')
    }
  }
}

async function addGroup() {
  try {
    const { value } = await ElMessageBox.prompt('输入新分组名称（子机可通过拖拽归入）', '新增分组', {
      inputPattern: /\S+/,
      inputErrorMessage: '分组名不能为空',
      inputPlaceholder: '例如：数据库组 / 订单组',
    })
    const name = value.trim()
    if (!name) return
    const existing = groupInfos.value.filter((g) => g.name && g.total === 0).map((g) => g.name)
    if (existing.includes(name)) {
      ElMessage.info(`分组「${name}」已存在`)
      return
    }
    await updateAsset(mother.value?.id || selectedMotherId.value, { custom_groups: [...existing, name] })
    ElMessage.success(`分组「${name}」已创建`)
    refresh()
  } catch (err) {
    if (err !== 'cancel' && err?.message !== 'cancel') {
      ElMessage.error(err.response?.data?.detail || '创建分组失败')
    }
  }
}

async function refresh() {
  loadOverview()
}

async function loadOverview() {
  motherLoading.value = true
  try {
    // 母机列表（总览卡片 + 详情切换数据源）
    try {
      const { data: ml } = await fetchMothers()
      mothers.value = ml.items || []
      if (!selectedMotherId.value || !mothers.value.some((m) => m.id === selectedMotherId.value)) {
        selectedMotherId.value = selectedMotherId.value || ml.default_id || mothers.value[0]?.id || ''
      }
    } catch {
      mothers.value = []
    }
    const { data } = selectedMotherId.value
      ? await fetchMotherOverviewById(selectedMotherId.value)
      : await fetchMotherOverview()
    mother.value = data.mother
    children.value = data.children || []
    groupInfos.value = data.groups || []
    lastSyncAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } finally {
    motherLoading.value = false
  }
}

function openMonitor(row) {
  if (!row) return
  // 母机自身子机是合成 id（mother-xxx:self），数据库无此记录，监控详情须用母机真实 id
  monitorId.value = row.is_mother ? (mother.value?.id || row.mother_id) : row.id
  monitorVisible.value = true
}

function openEdit(row) {
  Object.assign(editForm, {
    id: row.id,
    hostname: row.hostname || '',
    group: row.group || '',
    app: row.app || '',
    role: row.role || 'app',
    env: row.env || 'prod',
    owner: row.owner || '',
  })
  editVisible.value = true
}

async function submitEdit() {
  editing.value = true
  try {
    await updateAsset(editForm.id, {
      hostname: editForm.hostname,
      group: editForm.group || '',
      app: editForm.app,
      role: editForm.role,
      env: editForm.env,
      owner: editForm.owner,
    })
    ElMessage.success('已保存')
    editVisible.value = false
    refresh()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '保存失败')
  } finally {
    editing.value = false
  }
}

function fmtTime(v) {
  if (!v) return '—'
  const d = new Date(v)
  return isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-CN', { hour12: false })
}

function relTime(v) {
  if (!v) return '—'
  const t = new Date(v).getTime()
  if (isNaN(t)) return '—'
  const diff = Date.now() - t
  if (diff < 60000) return '刚刚'
  const m = Math.floor(diff / 60000)
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

async function confirmDelete(row) {
  // 母机走删除弹窗（有子机禁止删除）；子机走删除弹窗（默认联动卸载 agent）
  if (row?.kind === 'mother' || mothers.value.some((m) => m.id === row?.id)) {
    openMotherDelete(row)
    return
  }
  openChildDelete(row)
}

// ===== 子机安装详情（纳管状态 / Agent 语言 / pid / 完整安装日志） =====
const PROVISION_LABELS = { running: '安装中', registered: '已纳管', installed: '已安装', failed: '纳管失败', uninstalling: '卸载中' }
const LANG_LABELS = { go: 'Go（静态二进制）', py: 'Python（单脚本）' }
const childInstallVisible = ref(false)
const childInstallAsset = ref(null)
const childProvision = ref(null)
const childProvisionLoading = ref(false)

async function openChildInstall(c) {
  childInstallAsset.value = c
  childProvision.value = null
  childInstallVisible.value = true
  childProvisionLoading.value = true
  try {
    const { data } = await fetchAssetProvision(c.id)
    childProvision.value = data
  } catch {
    // 拉不到详情就退回卡片自带信息展示
    childProvision.value = null
  } finally {
    childProvisionLoading.value = false
  }
}

// ===== 子机删除（可选联动卸载服务器上的自研 agent） =====
const childDeleteVisible = ref(false)
const childDeleting = ref(false)
const childDeleteForm = reactive({ id: '', hostname: '', ip: '', has_provision: false, uninstall: true, password: '' })

function openChildDelete(c) {
  Object.assign(childDeleteForm, {
    id: c.id,
    hostname: c.hostname || c.id,
    ip: c.ip || '',
    has_provision: !!c.ip && !!c.provision_status,
    uninstall: !!c.ip && !!c.provision_status,
    password: '',
  })
  childDeleteVisible.value = true
}

async function submitChildDelete() {
  if (childDeleteForm.uninstall && !childDeleteForm.password) {
    ElMessage.warning('请输入 SSH 密码，用于卸载服务器上的自研 agent')
    return
  }
  childDeleting.value = true
  try {
    const { data } = await removeAsset(childDeleteForm.id, {
      uninstall: childDeleteForm.uninstall,
      ssh_password: childDeleteForm.password,
    })
    const extra = data.uninstalled ? '，并已卸载服务器上的 agent' : ''
    ElMessage.success(`已删除 ${childDeleteForm.id}${extra}`)
    childDeleteVisible.value = false
    // 删除的是当前查看的母机时，退回母机列表（子机一般不触发，兜底）
    if (childDeleteForm.id === selectedMotherId.value) {
      selectedMotherId.value = ''
      detailView.value = false
    }
    refresh()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '删除失败')
  } finally {
    childDeleting.value = false
  }
}

// ===== 母机删除（纯台账，不触碰服务器；名下有子机时后端 400 拒绝） =====
const motherDeleteVisible = ref(false)
const motherDeleting = ref(false)
const motherDeleteForm = reactive({ id: '', hostname: '', children: 0 })

function openMotherDelete(row) {
  const m = mothers.value.find((x) => x.id === row.id) || row
  // 删除限制：名下有子机时禁止删除（前端禁用按钮 + 后端 400 兜底）
  // 统计真实子机：排除"本机·母机"合成行（is_mother），它不是数据库资产
  const cnt = m.children_count ?? (m.id === selectedMotherId.value ? children.value.filter((c) => !c.is_mother).length : 0)
  Object.assign(motherDeleteForm, { id: m.id, hostname: m.hostname || '', children: cnt ?? 0 })
  motherDeleteVisible.value = true
}

async function submitMotherDelete() {
  motherDeleting.value = true
  const wasSelected = motherDeleteForm.id === selectedMotherId.value
  try {
    const { data } = await deleteAsset(motherDeleteForm.id)
    motherDeleteVisible.value = false
    ElMessage.success(`已删除 ${motherDeleteForm.id}`)
    if (wasSelected) {
      selectedMotherId.value = ''
      detailView.value = false
    }
    refresh()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '删除失败')
  } finally {
    motherDeleting.value = false
  }
}

// ===== 添加子机（SSH 纳管：下发自研 Agent 并上报） =====
const addChildVisible = ref(false)
const childProvisioning = ref(false)
const LANG_OPTIONS = [
  { value: 'go', label: 'Go 版' },
  { value: 'py', label: 'Python 版' },
]
const LANG_HINTS = {
  go: 'Go：静态单文件、零依赖（推荐）',
  py: 'Python：单脚本，目标机需 Python3（缺失时自动尝试安装）',
}
const addChildForm = reactive({ display_name: '', ip: '', port: 22, username: 'root', password: '', lang: 'go', app: '', group: '', owner: '' })
let provisionTimer = null

function openAddChild(groupName = '') {
  Object.assign(addChildForm, { display_name: '', ip: '', port: 22, username: 'root', password: '', lang: 'go', app: '', group: groupName || '', owner: '' })
  addChildVisible.value = true
}

async function submitAddChild() {
  if (!addChildForm.ip.trim()) {
    ElMessage.warning('请填写目标机 IP')
    return
  }
  // 母机本机子机已在新增母机时自动纳管，同一 IP 重复添加没有意义
  if (mother.value && addChildForm.ip.trim() === (mother.value.ip || '').trim()) {
    ElMessage.warning('这是母机自身的 IP：新增母机时已自动纳管本机子机，无需重复添加')
    return
  }
  if (!addChildForm.password) {
    ElMessage.warning('请填写 SSH 密码（用于自动部署自研 Agent）')
    return
  }
  childProvisioning.value = true
  try {
    await provisionAsset({
      display_name: addChildForm.display_name.trim(),
      ip: addChildForm.ip.trim(),
      port: addChildForm.port,
      username: addChildForm.username,
      password: addChildForm.password,
      lang: addChildForm.lang,
      app: addChildForm.app,
      group: addChildForm.group,
      owner: addChildForm.owner,
      mother_id: selectedMotherId.value,
    })
    addChildVisible.value = false
    ElMessage.success('纳管任务已启动：正在部署自研 Agent，完成后子机将自动接入（约 1 分钟）')
    // 纳管是异步装机，轮询刷新总览让"安装中 → 已纳管"状态实时可见
    stopProvisionPolling()
    let ticks = 0
    provisionTimer = setInterval(async () => {
      ticks += 1
      await loadOverview()
      // 3 分钟后停止轮询（正常装机 1 分钟左右完成）
      if (ticks >= 18) stopProvisionPolling()
    }, 10000)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '纳管任务启动失败')
  } finally {
    childProvisioning.value = false
  }
}

function stopProvisionPolling() {
  if (provisionTimer) {
    clearInterval(provisionTimer)
    provisionTimer = null
  }
}

onBeforeUnmount(stopProvisionPolling)

function openAddMother() {
  Object.assign(motherForm, {
    hostname: '',
    ip: '',
    ssh_port: 22,
    env: 'prod',
    owner: '',
    alert_policy: { ...ALERT_POLICY_DEFAULT },
  })
  addMotherVisible.value = true
}

async function submitMother() {
  if (!motherForm.hostname.trim() || !motherForm.ip.trim()) {
    ElMessage.warning('请填写主机名和服务器 IP')
    return
  }
  if (!motherForm.password) {
    ElMessage.warning('请填写 SSH 密码（用于验证连通性并自动纳管本机子机）')
    return
  }
  addingMother.value = true
  try {
    const { data: row } = await createMother({
      hostname: motherForm.hostname.trim(),
      ip: motherForm.ip.trim(),
      ssh_port: motherForm.ssh_port,
      username: motherForm.username,
      password: motherForm.password,
      env: motherForm.env,
      owner: motherForm.owner,
      alert_policy: { ...motherForm.alert_policy },
    })
    ElMessage.success(
      row.provision_started
        ? `母机已创建，本机子机 ${row.self_child_id} 纳管与 Agent 部署已启动（约 1 分钟）`
        : '母机已登记',
    )
    addMotherVisible.value = false
    // 选中刚登记的母机并进入详情
    selectedMotherId.value = row.id
    await loadOverview()
  } catch (err) {
    ElMessage.error(errMsg(err, '创建失败'))
  } finally {
    addingMother.value = false
  }
}

// ===== 告警策略：查看 / 修改（母机与子机统一端点） =====
async function openAlertPolicy(m) {
  policyTarget.value = m
  policyInfo.value = null
  policyTab.value = 'basic'
  templateId.value = ''
  Object.assign(policyForm, { ...ALERT_POLICY_DEFAULT })
  policyVisible.value = true
  try {
    const { data } = await fetchAlertPolicy(m.id)
    policyInfo.value = data
    policyDefaults.value = data.defaults || null
    Object.assign(policyForm, buildPolicyForm(data.policy))
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '读取告警策略失败')
  }
}

async function submitAlertPolicy() {
  if (!policyTarget.value) return
  policySaving.value = true
  try {
    await updateAlertPolicy(policyTarget.value.id, { ...policyForm })
    ElMessage.success(
      policyIsChild.value ? '告警策略已保存（子机自有策略，立即生效）' : '告警策略已保存（母机与所有接入子机统一生效）',
    )
    policyVisible.value = false
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '保存失败')
  } finally {
    policySaving.value = false
  }
}

// 子机清除自有策略，恢复继承母机（无母机时回平台默认）
async function resetAlertPolicy() {
  if (!policyTarget.value) return
  policyResetting.value = true
  try {
    const { data } = await resetAlertPolicyApi(policyTarget.value.id)
    policyInfo.value = data
    policyDefaults.value = data.defaults || null
    Object.assign(policyForm, buildPolicyForm(data.policy))
    templateId.value = ''
    ElMessage.success('已恢复继承母机策略')
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '恢复继承失败')
  } finally {
    policyResetting.value = false
  }
}
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}
.page-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mother-name {
  font-size: 17px;
  font-weight: 600;
}
.card-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.toolbar .el-button {
  margin-left: 0;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

/* ===== 添加母机弹窗 ===== */
.db-note {
  margin: 0 0 12px 0;
}

/* ===== 空状态：未接入母机 ===== */
.empty-mother {
  margin-top: 48px;
  text-align: center;
  padding: 56px 24px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-xl);
  background: linear-gradient(180deg, var(--el-fill-color-lighter) 0%, var(--el-bg-color) 70%);
}
.empty-icon {
  font-size: 52px;
  color: var(--faint);
}
.empty-title {
  margin-top: 14px;
  font-size: 17px;
  font-weight: 600;
  color: var(--ink);
}
.empty-desc {
  margin: 10px auto 18px;
  max-width: 460px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

/* ===== 母机记录列表（一台一条记录，不展示 CPU 等指标） ===== */
.section-hint {
  color: var(--faint);
  font-size: 12px;
}
.mother-row {
  border-radius: 10px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: transform 0.2s var(--ease-out, ease), box-shadow 0.2s var(--ease-out, ease);
}
.mother-row:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.09);
}
.mother-row :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  padding: 14px 18px;
}
.row-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 260px;
  min-width: 240px;
}
.row-name {
  font-size: 15px;
  font-weight: 600;
}
.row-sub {
  flex: 1 1 240px;
  color: var(--muted);
  font-size: 13px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}
.row-right {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
}
.row-count b {
  color: var(--brand);
  font-size: 15px;
}

/* ===== 母机详情头部 ===== */
.detail-header {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 4px 0 10px;
  border-bottom: 1px solid var(--line);
}
.detail-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.detail-title .mother-name {
  font-size: 17px;
  font-weight: 600;
}
.detail-sub {
  display: flex;
  align-items: center;
  color: var(--muted);
  font-size: 13px;
  margin: 8px 0 2px;
}

/* ===== 子机实时指标 ===== */
.node-metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 4px;
  margin-top: 10px;
  padding: 8px 6px;
  background: var(--el-fill-color-light);
  border-radius: 10px;
}
.nm {
  text-align: center;
}
.nm-v {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.nm-v.warn {
  color: var(--warn);
}
.nm-v.danger {
  color: var(--danger);
}
.nm-v .nm-unit {
  font-size: 10px;
  color: var(--muted);
  margin-left: 1px;
  font-weight: 400;
}
.nm-l {
  margin-top: 1px;
  font-size: 11px;
  color: var(--muted);
}
/* 网络实时速率行（↓ 下载 / ↑ 上传）；无 Agent 数据时为空并隐藏 */
.nm-net {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--el-border-color-lighter);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.nm-net:empty {
  display: none;
}
.nm-net :deep(.nm-net-dot) {
  color: var(--muted);
}
.field-hint {
  margin-left: 10px;
  color: var(--faint);
  font-size: 12px;
}
.policy-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
.policy-meta-hint {
  color: var(--faint);
  font-size: 12px;
}

/* ===== 告警策略抽屉（分组配置） ===== */
.policy-body {
  min-height: 240px;
}
.policy-toolbar {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}
.policy-section {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.policy-section.off {
  opacity: 0.55;
}
.section-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-title {
  font-weight: 600;
  font-size: 13px;
}
.section-desc {
  color: var(--faint);
  font-size: 12px;
}
.section-body {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.field-label {
  width: 96px;
  flex: none;
  color: var(--muted, #6b7280);
  font-size: 12px;
  text-align: right;
}
.field-unit {
  color: var(--faint);
  font-size: 12px;
}
.level-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  display: inline-block;
  margin-right: 6px;
  vertical-align: middle;
}
.scrape-box {
  border: 1px dashed var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.rule-table {
  width: 100%;
}
.rule-name {
  font-size: 12px;
}
.rule-metric {
  color: var(--faint);
  font-size: 11px;
  font-family: ui-monospace, monospace;
}
.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
  flex: none;
}
.dot.ok {
  background: var(--ok-vivid);
  box-shadow: 0 0 0 3px rgba(52, 199, 89, 0.18);
}
.dot.down {
  background: var(--danger-vivid);
  box-shadow: 0 0 0 3px rgba(255, 59, 48, 0.18);
}
/* 纳管中：本机子机 Agent 部署未完成，状态未确立 */
.dot.pend {
  background: var(--warn-vivid, #ff9500);
  box-shadow: 0 0 0 3px rgba(255, 149, 0, 0.18);
}
/* 删除子机弹窗：跳过卸载的提示 */
.uninstall-skip-note {
  width: 100%;
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
}

/* ===== 子机分组 ===== */
.overview-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 16px 0 10px;
  flex-wrap: wrap;
  gap: 8px;
}
.children-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-title {
  font-size: 15px;
  font-weight: 600;
}
.group-block {
  margin-bottom: 18px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: 14px 16px 4px;
  transition: border-color var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out), background-color var(--dur-fast) var(--ease-out);
}
.group-block.drop-target {
  border-color: var(--brand);
  box-shadow: 0 0 0 2px rgba(0, 113, 227, 0.16);
}
.group-block.drop-target .group-name {
  color: var(--brand);
}
.group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.group-icon {
  color: var(--warn);
}
.group-name {
  font-size: 14px;
  font-weight: 600;
}
.group-op {
  margin-left: auto;
  color: var(--muted);
  cursor: pointer;
  transition: color var(--dur-fast) var(--ease-out);
}
.group-op + .group-op {
  margin-left: 0;
}
.group-op:hover {
  color: var(--brand);
}
.group-op.danger:hover {
  color: var(--danger);
}
/* ===== 子机卡片：心电图在线指示 ===== */
.ecg {
  width: 54px;
  height: 16px;
  flex: none;
}
.ecg .ecg-line {
  fill: none;
  stroke: var(--faint);
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.ecg.on .ecg-line {
  stroke: var(--ok-vivid);
  stroke-dasharray: 90 60;
  animation: ecg-flow 1.4s linear infinite;
  filter: drop-shadow(0 0 2px rgba(52, 199, 89, 0.55));
}
.ecg.off .ecg-line {
  opacity: 0.45;
}
@keyframes ecg-flow {
  from {
    stroke-dashoffset: 150;
  }
  to {
    stroke-dashoffset: 0;
  }
}
.node-card {
  border-radius: var(--r-lg);
  margin-bottom: 12px;
  position: relative;
  overflow: hidden;
}
.add-card {
  /* 与同行真卡同高：撑满 el-col（flex 拉伸）并保留与真卡一致的底部间距 */
  height: calc(100% - 12px);
  min-height: 202px;
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  border: 1.5px dashed var(--el-border-color-darker);
  background: var(--el-fill-color-lighter);
  cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease-out);
}
.add-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.add-card-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.add-card:hover {
  border-color: var(--el-color-primary);
}
.add-card:hover .add-card-inner {
  color: var(--el-color-primary);
}
.node-card[draggable='true'] {
  cursor: grab;
  user-select: none;
}
.node-card.dragging {
  opacity: 0.55;
}
.node-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--ok-vivid);
}
.node-card.down::before {
  background: var(--danger-vivid);
}
.node-card.down {
  background: var(--el-color-danger-light-9);
}
.node-top {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-right: 4px;
}
/* 未恢复告警徽标（红色，卡片右上角） */
.alarm-badge {
  position: absolute;
  top: 0;
  right: 0;
  z-index: 2;
  background: var(--danger-vivid);
  color: #fff;
  font-size: 11px;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 0 var(--r-sm) 0 var(--r-sm);
  cursor: pointer;
  font-weight: 600;
  transition: background var(--dur-fast) var(--ease-out);
}
.alarm-badge:hover {
  background: #ff453a;
}
/* 卡片右上角操作按钮（跟随 node-top 行对齐，主机名过长自动省略避让） */
.node-ops {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  flex-shrink: 0;
}
.node-op {
  cursor: pointer;
  color: var(--muted);
  font-size: 15px;
  transition: color var(--dur-fast) var(--ease-out);
}
.node-op:hover {
  color: var(--brand);
}
.node-op.danger:hover {
  color: var(--danger);
}
/* 指标自动同步提示 */
.sync-hint {
  font-size: 12px;
  color: var(--muted);
}
/* 安装详情弹窗里的完整安装日志 */
.install-logs {
  max-height: 260px;
  overflow: auto;
  background: #0d0d10;
  color: #d5dbe3;
  font-size: 12px;
  line-height: 1.6;
  padding: 12px 14px;
  border-radius: 10px;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
.node-host {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.node-ip {
  color: var(--muted);
  font-size: 13px;
  margin: 6px 0 8px;
  font-family: "SF Mono", ui-monospace, Menlo, monospace;
}
.node-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.node-foot {
  display: flex;
  justify-content: space-between;
  margin-top: 10px;
  color: var(--muted);
  font-size: 12px;
}
.node-foot .stale {
  color: var(--danger);
}
.node-err {
  margin-top: 6px;
  color: var(--danger);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.muted {
  color: var(--faint);
}
</style>
