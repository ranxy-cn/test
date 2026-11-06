# 2026-09-27 服务器部署记录

## 部署范围

- 服务器：124.221.251.186。
- 项目目录：`/home/ranxiaoying/devops-agent`。
- 发布编号：`20260927-ai-233520`。
- 仅更新本次功能涉及的文件，没有覆盖生产数据库连接、JWT、Zabbix、Ansible 等既有配置。
- 已重建本项目的 api / worker / beat / frontend；Redis 与其他应用、Zabbix 容器保持原实例。
- AI 网关使用 `CHAT_*` 环境变量，密钥只保存在服务器 `.env`，未写入代码或发布说明。

## 上线验收

- 19 项知识库、大屏和登录鉴权隔离测试通过。
- 数据库迁移至 `b7d2f9a4c1e8`，新增 `knowledge_documents` 表。
- 新增权限、菜单和三个前端页面均通过生产登录及浏览器验证。
- 通过 nginx 上传 1,368,127 字节中文文档，成功存储并完成真实 AI 分析。
- 知识检索和带文档引用、平台状态上下文的真实 AI 对话验证通过，模型为 `glm-4.7-channel-cg`。
- 验收文档已删除，不在业务知识库中保留测试规则。
- `/health` 正常，Zabbix 与 Ansible 继续保持 real 模式。

## 全量回归的已知问题

在断网、无生产凭据的完整项目挂载环境中，全量测试结果为 **211 通过、3 失败**。
失败项位于旧的 phase2 / pipeline / webhook 流程，表现为共享 SQLite 内存库的并发事务和对象状态异常。
为排除新增功能导致的问题，使用上线前代码和旧镜像在相同隔离环境中复测，结果为 **201 通过、4 失败**，同样出现旧流水线的 SQLite 并发异常。
这些失败需单独整理旧测试的后台线程与数据库隔离，本次不修改无关流程；不能将全量回归描述为全部通过。
本次新增功能和鉴权的 19 项定向测试及生产 MySQL、真实 AI 的线上验收已通过。

## 访问方式

- 大屏：`http://124.221.251.186:8080/dashboard`。
- 知识库：`http://124.221.251.186:8080/knowledge`。
- 对话：`http://124.221.251.186:8080/chat`。

使用原有账号。旧登录信息的菜单可能仍在浏览器缓存中，退出后重新登录即可刷新权限与菜单。
对话中的“当前状态”来自平台数据库快照；本版不在聊天时执行 SSH 命令或自动变更服务器，也不声称拥有未采集的 CPU、内存等指标。

## 备份与回退

备份目录：`/home/ranxiaoying/devops-backups/20260927-ai-233520`，含代码包、原 `.env`、一致性数据库快照。
该目录权限为 700，数据库及环境备份权限为 600；不要公开或提交到仓库。

发布日志、验收日志、隔离测试日志位于：
`/home/ranxiaoying/devops-releases/20260927-ai-233520`。

如需回退代码和镜像，执行：

```bash
bash /home/ranxiaoying/devops-releases/20260927-ai-233520/rollback.sh
```

该回退脚本保留新增知识表与上线后的生产数据，不自动恢复旧数据库快照。
如需恢复数据库，必须先确认影响及停写方案，不能直接覆盖正在使用的数据。

## 2026-09-29 Netdata 接入与重新部署

### 发布内容

- 发布提交：`9afab08`（`feat: integrate Netdata live monitoring`）。
- 新增后端 Netdata 只读适配层，调用 Agent 的 `/api/v1/info`、`/api/v1/charts` 和 `/api/v1/data` 接口。
- 新增聚合接口 `/api/v1/dashboard/netdata` 和单资产接口 `/api/v1/assets/{asset_id}/netdata`。
- 独立大屏 `/screen` 新增 Netdata 在线状态、CPU、内存、磁盘和网络实时态势面板。
- Netdata 连接失败时按资产单独降级，不影响 Zabbix、知识库、AI 对话和原有大屏。

### 部署过程

- 项目目录：`/home/ranxiaoying/devops-agent`。
- 服务器原 `.env`、知识库目录、密钥目录和 Docker 数据卷均未覆盖。
- 已重新构建并启动 `api`、`worker`、`beat`、`frontend`，Redis 保持原实例。
- `/health` 检查通过，前端静态资源已更新。
- 本次部署备份：`/home/ranxiaoying/devops-backups/netdata-deploy-20260929-101429/source-before.tgz`。

### 当前边界

服务器本机当前尚未安装 Netdata Agent，`127.0.0.1:19999` 暂不可达。因此本次已完成平台侧接入和部署，但大屏在安装 Agent 或登记其他服务器的 Netdata 地址前会显示未接入/离线状态。安装 Agent 属于生产主机变更，应在确认采集范围、端口策略和保留周期后单独执行。

## 2026-10-08 大屏视觉改版（/screen）

### 发布内容

- 发布编号：`20261008-screen-ui`。
- 重写 `frontend/src/views/StandaloneScreenView.vue`：深蓝科技底 + 机翼式发光标题栏 + 三栏布局；中央为 AI 数字员工核心与双层旋转轨道、左右可达率/闭环率双环；服务器实时监控支持 CPU/内存/磁盘切换；右栏为资产健康排行、告警等级分布、运维能力矩阵。
- 仅改前端，后端接口、数据库、`.env`、Redis 与 api/worker/beat 均未变更。

### 部署过程

- 服务器：124.221.251.186，项目目录 `/home/ranxiaoying/devops-agent`。
- 采用热更新：备份源文件 → 更新源码 → 将新构建产物灌入运行中的 `devops-agent_frontend_1` 容器，不重建镜像、不中断其他服务。
- 备份：`/home/ranxiaoying/devops-backups/screen-ui-20261008-175608`（源码），容器内存量旧资源备份于 `/usr/share/nginx/html.bak-1791453372654`。

### 验收

- 首页引用已切到新产物 `index-B0gew6FQ.js`，旧产物 `index-DnvFqPI7.js` 保留可回退。
- `/screen` 返回 200，外网 `http://124.221.251.186:8080/screen` 可访问。
- `/health` 正常，integration 仍为 real 模式；api / worker / beat / frontend / redis 容器均在线。

### 回退

如需回退前端：把 `screen-ui-20261008-175608` 中的 `StandaloneScreenView.vue.bak` 复制回 `frontend/src/views/StandaloneScreenView.vue`，并将容器内 `html.bak-1791453372654` 覆盖回 `/usr/share/nginx/html` 即可（无需重启数据库或后端）。

## 2026-10-09 首页大屏（/dashboard）同步改版

### 背景与改动

- 用户反馈：`/dashboard` 未变化（此前只改了 `/screen` 这一个独立大屏路由）。
- 将大屏视觉抽为共用组件 `frontend/src/components/BigScreen.vue`（`standalone` 属性区分全屏/嵌嵌两种模式）。
  - `/dashboard`（DashboardView.vue）：嵌在应用外壳内，负边距铺满可视区。
  - `/screen`（StandaloneScreenView.vue）：脱离外壳全屏展示。
- 两处共用同一套三栏布局（资产健康环形图 / 告警趋势 / 关键指标卡 ｜ 数字员工中央舞台 + 实时监控 ｜ 健康排行 / 等级分布 / 能力矩阵）。

### 部署方式变更（重要）

- 服务器 `/home/ranxiaoying/devops-agent`的前端源码比本地仓库新（含 `AiAnalysesView.vue`、`AiConfigView.vue` 等本地没有的页面）。
- 因此**不再**把本地 dist 直接灌入容器（会覆盖掉服务器自有页面），改为：同步源码 → 在服务器上 `docker-compose build frontend` + `up -d`（用服务器自身源码构建）。
- 同步工具：本机 PyPI 不通、无 sshpass/plink，SFTP 大文件写入不稳定；最终用 Node ssh2 的分块 stdin 写入 + md5 校验重试完成。

### 验收

- 服务器重建成功，容器内新产物 `index-4_7Zxg6i.js`。
- 新版标记（资产健康分布 / 工单闭环率 / 运维能力情况 / DIGITAL / CAPABILITIES）均存在。
- AI 页面未丢失（AiAnalyses / AiConfig 标记存在）。
- `/`、`/dashboard`、`/screen` 均 200；`/health` 正常；admin 可正常登录。
- 源码备份：`/home/ranxiaoying/devops-agent/tools/.release-staging/bak-*`。

### 注意事项

部署期间观察到服务器上有其他会话同时重建了容器（09:09 重建覆盖了原先同步的视图文件）。多人/多会话并行操作同一台机器时，建议部署前先确认源码状态，避免互相覆盖。

## 2026-10-09 首页大屏（/dashboard）同步改版

### 背景与改动

- 用户反馈：`/dashboard` 未变化（此前只改了 `/screen` 这一个独立大屏路由）。
- 将大屏视觉抽为共用组件 `frontend/src/components/BigScreen.vue`（`standalone` 属性区分全屏/嵌嵌两种模式）。
  - `/dashboard`（DashboardView.vue）：嵌在应用外壳内，负边距铺满可视区。
  - `/screen`（StandaloneScreenView.vue）：脱离外壳全屏展示。
- 两处共用同一套三栏布局（资产健康环形图 / 告警趋势 / 关键指标卡 ｜ 数字员工中央舞台 + 实时监控 ｜ 健康排行 / 等级分布 / 能力矩阵）。

### 部署方式变更（重要）

- 服务器 `/home/ranxiaoying/devops-agent`的前端源码比本地仓库新（含 `AiAnalysesView.vue`、`AiConfigView.vue` 等本地没有的页面）。
- 因此**不再**把本地 dist 直接灌入容器（会覆盖掉服务器自有页面），改为：同步源码 → 在服务器上 `docker-compose build frontend` + `up -d`（用服务器自身源码构建）。
- 同步工具：本机 PyPI 不通、无 sshpass/plink，SFTP 大文件写入不稳定；最终用 Node ssh2 的分块 stdin 写入 + md5 校验重试完成。

### 验收

- 服务器重建成功，容器内新产物 `index-4_7Zxg6i.js`。
- 新版标记（资产健康分布 / 工单闭环率 / 运维能力情况 / DIGITAL / CAPABILITIES）均存在。
- AI 页面未丢失（AiAnalyses / AiConfig 标记存在）。
- `/`、`/dashboard`、`/screen` 均 200；`/health` 正常；admin 可正常登录。
- 源码备份：`/home/ranxiaoying/devops-agent/tools/.release-staging/bak-*`。

### 注意事项

部署期间观察到服务器上有其他会话同时重建了容器（09:09 重建覆盖了原先同步的视图文件）。多人/多会话并行操作同一台机器时，建议部署前先确认源码状态，避免互相覆盖。

## 2026-10-09 大屏第二轮优化（信息层级 / 图表 / 告警机制 / 可读性）

### 需求要点（用户评审）

- 中心 AI 节点散乱、顶部 KPI 与主体脱节、底部图标无说明；
- 近 7 日趋势只有 2 天数据；环形图中心只显示总数导致"全崩了"观感；健康排行柱长雷同且标签截断；告警等级只有一根柱；实时监控空进度条；
- 字号偏小、中英混排拥挤、对比度不足；缺少全局告警态与最高级别红色警示。

### 改动

**前端 `frontend/src/components/BigScreen.vue`（整份重写）**

- 布局：删除环绕式散落节点；顶部新增全局告警滚动带 + 全宽 KPI 条带（40px 数字，带数字滚动动画）；中栏改为「AI 核心 + 双环形仪表 + 资产状态清单（可滚动列表）」；底栏增加状态图例。
- 图表：趋势按自然日补齐 7 天（前端兜底 + 后端补零）；环形图中心改为综合健康度百分比；健康排行改为可解释健康分（可达 40 + 数据库 20 + 告警影响 20 + 监控接入 20），悬浮显示扣分明细与完整主机名；告警等级改为「高/中/低 数值 + 占比 + 占比条」，高等级用红色并脉冲。
- 状态感知：告警/离线达阈值时整屏边缘红色呼吸 + 顶部滚动告警带；新增告警时告警带闪烁 8 秒。
- 可读性：面板标题中文 17px、英文副标题弱化；KPI 40px、指标卡 26px、能力卡 24px；配色重排（正常绿 / 监控蓝 / 异常琥珀 / 高级告警红）。
- 实时监控：未接入时显示"暂无实时监控数据"灰色断线态与具体原因，CPU/内存/磁盘显示 —；主机名 `IP:端口` 录入（如 124.221.251.186:22）展示时剥掉端口，悬浮显示全称。

**后端 `backend/app/routers/dashboard.py`**

- 趋势按自然日补齐 7 天（以数据库当前日期为准，与 `func.date()` 口径一致），无告警日期补 0。
- 新增 `severity` 字段：全量统计告警等级分布（此前用只取前 10 条的 anomalies 列表统计，导致高等级被截断为 10 而实际为 11）。

### 部署方式（重要变更）

- 部署期间服务器上**另一个会话持续还原源码并重建容器**（10:17 / 11:02 / 11:08 多次），写入项目目录的源码会被覆盖。
- 因此改为「不碰项目源码」的热更新：
  - 后端：把补丁后的 `dashboard.py` 直接写入运行中的 api 容器 `/app/app/routers/dashboard.py` 并 `docker restart`（容器内文件在 restart 后保留）。
  - 前端：把 `frontend/` 复制到 `/home/ranxiaoying/fe-stage`（排除 node_modules/dist），替换为新组件后 `docker build --no-cache -t devops-fe-new`，用临时容器导出 dist，再 tar 灌入运行中的 frontend 容器（原目录备份为 `html.bak-<时间戳>`）。
- 注意：该方式**不修改项目源码**，若他人再次 `docker-compose build/recreate` frontend，会回退到旧镜像。需要固化时应在无人部署时同步源码后重建。

### 验收

- 线上产物 `index-DNouGeJH.js`，新标记（综合健康度 5 / 资产状态清单 / alert-banner / sev-summary / 暂无实时监控数据）齐全，AI 页面保留。
- 后端 `/dashboard/overview` 返回 7 天趋势 `[0,0,0,0,0,13,3]` 与 `severity: {high:11,...}`。
- `/`、`/dashboard`、`/screen` 均 200，`/health` 正常。
