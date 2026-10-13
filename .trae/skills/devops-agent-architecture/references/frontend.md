# 前端（frontend/：Vue 3 + Element Plus + Vite）

## 核心模块（frontend/src/）

- **main.js**：createApp 入口；挂载前按 localStorage.theme/系统偏好设 dark class 防闪烁；全局注册 Element Plus 图标；注册 `v-perm` 指令（读 localStorage `devops_user` 的 permissions，无权限 display:none 隐藏按钮）；`router.isReady()` 后再 mount
- **App.vue**：工作台布局外壳。公开页（meta.public/standalone）裸渲染 router-view；否则渲染「毛玻璃顶栏 + 主内容 + 底部 AppDock」。顶栏：北京时间时钟、明暗主题切换、通知弹窗（60s 轮询未读数）、系统设置下拉（菜单树 dir 子项）、用户下拉
- **auth.js**：登录态单例。token 存 `devops_token`、user 存 `devops_user`（localStorage）。`isLoggedIn()`、`has(perm)`、`ensureLoaded()`（刷新后调 /auth/me 恢复）、`authHeader()` 与复用 `http` 实例
- **api.js**：axios 封装。baseURL `/api/v1`、timeout 30s；请求拦截器注入 Bearer；响应拦截器 401 → 清登录态 + 回调 onUnauthorized（router.js 注册，避免循环依赖）。按域分组具名 API 函数；长耗时接口单独放宽 timeout（最长 240s）；AI 组：getAiSettings/saveAiSettings/testAiConnection(120s)/listAiAnalyses/getAiAnalysis/feedbackAiAnalysis/triggerAiAnalysis
- **router.js**：createWebHistory，20 条路由静态 import。三类 meta：`public`（/login）、`standalone`（/screen 大屏无 Dock）、`perm`（页面权限点）。beforeEach：未登录跳 /login（带 redirect）；内存无 user 则 ensureLoaded；按 meta.perm + auth.has 校验，无权限跳菜单树第一个可访问路径

## views/ 20 个页面

| 文件 | 职责 |
|---|---|
| DashboardView | 运维指挥中心运营大屏（资产健康/告警态势/闭环效率，可全屏） |
| AnomaliesView | 异常事件列表：统计卡 + 母机/分组联动筛选，详情走 AnomalyDetailDrawer；支持 `?anomaly_id=` 自动打开详情（AI 分析页跳回） |
| AiAnalysesView | AI 日志分析结果：状态/严重度/关键词筛选 + time|severity 排序，详情弹窗（诊断/原因/方案卡片 tag+severity 标识/blocked 警示/原始响应折叠）+ 人工处理备注 + 重新分析；`?anomaly_id=` 联动告警（perm ai:view） |
| AiConfigView | AI 服务配置：enabled 开关/端点/密钥（留空沿用，显示尾 4 位）/模型可输入下拉/超时/重试次数 + 连接测试 + 安全说明（perm ai:config） |
| TicketsView | 自愈工单列表 |
| TicketDetailView | 工单详情：状态流转、红/黄/绿说明、审批/驳回/重试、证据链 |
| EmployeeView | 数字员工档案（静态档案页） |
| ReportView | 数字员工日报 |
| AssetsView | 资产台账：母机/子机/分组、纳管 provision、告警策略、监控入口；SSH 密码配置入口=添加母机/添加子机表单（加密保存）+ 编辑资产对话框（三态：留空不改/填写更新，`ssh_password_set` 驱动 placeholder）；删除子机卸载密码留空回退保存密码；**新增/编辑子机负责人必填**（el-select filterable allow-create 联想 `listOwnerContacts`，选已有负责人自动回填渠道配置 applyContactProfile）+ 推送方式 OwnerNotifyEditor（提交前 validateOwnerNotify：负责人必填→渠道至少一项→所选渠道地址必填；母机编辑不强制） |
| AgentConfigView | 子机 Agent 配置：全局默认 + 资产级覆盖（拉取式免重启） |
| StatusView | 集成模式与健康探测面板（requested/effective/ok/latency） |
| KnowledgeView | 知识库：文档上传、AI 分析标签、关键词检索 |
| ChatView | AI 运维对话（知识库 + 实时状态快照）；quick-bar「数据问答」开关（dataMode）：开启后走 `POST /knowledge/data-query` NL2SQL 只读查询，回复消息内渲染结果表格（rows 按列下标转对象，prop=String(i)）+ 可折叠执行 SQL，400 拦截时展示审计原因；dataSuggestions 为数据类快捷提问 |
| NotificationsView | 通知中心列表 |
| BackupsView | 备份任务管理 |
| UsersView | 用户管理（admin） |
| RolesView | 角色权限管理 |
| MenusView | 菜单树管理（dir/menu/button，侧边栏与按钮权限来源，改后重登生效） |
| LoginView | 登录页（public） |
| StandaloneScreenView | 独立综合态势大屏（standalone，可全屏轮播） |

## components/ 8 个组件

- **AnomalyDetailDrawer.vue**：64% 宽异常详情抽屉（trigger 名经 cnTrigger 汉化 + 异常时刻 SSH 进程快照）；含「AI 日志分析」块——ai_status 标签 + 手动（重）分析（ai:feedback）+ done/blocked 时「查看分析结果」跳 `/ai-analyses?anomaly_id=`（双向关联）
- **AppDock.vue**：Mac 风格底部 Dock（鱼眼放大、tooltip、激活指示点）
- **AssetMonitor.vue**：资产监控全屏 Dialog（实时监控 1s 轮询 / 进程巡检 / 服务器详情 / SSH 终端 / 异常 / 纳管记录）。SSH 终端 Tab：黑底模拟交互会话，命令走 `execAssetCommand` 逐条执行（凭据由后端解析：资产保存密码 > 免密私钥，前端不传密码）；↑/↓ 历史、Ctrl+L/Ctrl+C、`clear`/`exit` 内建、快捷命令 chips、cwd/HOME 由后端 marker 回传驱动提示符，关闭监控窗随 reset 清空
- **AssetTopology3D.vue**：大屏「资产关联」真 3D 拓扑（three.js ^0.186，参考 threejs-demo 主机排版，纯代码建模无 GLB）。props `mothers`（/dashboard/topology）+ `dark`（日夜主题）+ `bare`（大屏嵌入时隐藏内部图例/详情卡）+ `highlightKey`（外部联动高亮：面板 hover 资产传入节点 key `m-{id}/c-{id}`，focusHelper BoxHelper 描边，buildContent 重建后自动恢复）+ `scores`（健康分映射 `m-/c-` key→0-100，<70 判定 strain 亚健康态）。节点状态 4 态：ok 绿/strain 黄（健康分<70 且可达且 db_ok）/dbwarn 橙/down 红；STATE_COLORS+MAT 按态建材质，scores 变化经 lastScoresKey 去重后触发 buildContent 重算。机柜式隐喻：母机=半透明玻璃机柜（无子机退化为塔楼），分组=柜内分层堆叠（隔板+侧挂铭牌），子机=1U 服务器单元（前面板+状态指示灯，`unit.userData.topo` 挂节点数据）——「包含」关系不再用连线（流光连线已移除）；Raycaster 白名单拾取（须 `defineEmits(['select'])`，曾因漏声明报 ReferenceError）→ BoxHelper 悬浮描边 + 鼠标跟随 Popover，`emit('select')` 外抛；OrbitControls 阻尼（**无自动旋转**，仅用户交互变化）；性能策略：**按需渲染**（needsRender 标记 + controls.update() 返回值判定，静止不提交 GPU）+ 几何/材质全场景共享缓存（buildSharedAssets 按主题×状态建一份，disposeGroup 跳过共享资源）+ MeshPhong 替代 MeshStandard + 像素比封顶 1.5（高分屏免 MSAA）+ mothers 数据 JSON 未变跳过重建（防 30s 轮询churn）+ 挂载前 webgl2Available() 检测（three r186 仅 WebGL2，老浏览器空态降级）；ResizeObserver 自适应，`frameScene` 含高度取景，铭牌 CanvasTexture 渲染尺寸已缩半（scale 除数 92）；WebGL 不可用显示空态；onMounted 后才 initScene（提前初始化会因容器为 null 误报「不支持 WebGL」）；主题切换重建背景/雾/网格/铭牌
- **BigScreen.vue**：大屏本体（/dashboard 与 /screen 复用），「3D 主角 + 玻璃 HUD 配角」透明风布局——AssetTopology3D 以 `bare` 模式铺满整屏做背景层（接收 `scores` 按健康分着色 strain），中央 `.hud-center` 完全留白给 3D（pointer-events 穿透）；顶部 `kpi-dock` 居中超薄玻璃胶囊（值守中呼吸点 + MTTR/自动化闭环率/进行中工单/近7日告警 四枚大数字，带口径 tooltip）；左右栏各 318px×4 块 `glass-panel`（58% 透明 + blur20 saturate1.25 + 18px 圆角 + rise-in 级联入场），**面板内部去框化**（无内嵌卡片，开放行 + 发丝虚线分隔 + 等宽数字三件套）：左栏=恢复效能（MTTR hero+MTTA/P50/闭环数/节省工时）+自动化闭环（SVG 环形+全自动/审批后/升级+AI 建议采纳率/审批均等待/审批超时作废）+工单流转（7 行胶囊条漏斗+dwell-grid 四环节滞留小时 chips）+预案执行力（按预案成功率胶囊条+冷却命中+验证一次通过率）；右栏=告警热点资产帕累托（hover → `highlightKey` 3D 描边联动）+告警热力 7×24 矩阵（纯 CSS grid，北京时门）+资产健康与容量（worst5 健康分行 hover 3D 联动+磁盘写满预测 Top3+超 180 天未重启）+策略灯三段条+红灯归因+知识盲区（建议新增预案 Top3）；standalone 独立路由右上角保留日夜切换 ghost 按钮；isDark 由 MutationObserver 同步；数据 Promise.allSettled 并发拉 stats/topology（api.js `fetchDashboardStats`），30s 自动刷新，无 ECharts 依赖（全部 CSS/SVG 实现）
- **ChatPanel.vue**：智能对话复用组件（ChatView 页面 + 悬浮抽屉共用）；含与 ChatView 同款「数据问答」开关（dataMode → sendDataQuery NL2SQL）、数据结果表格与 SQL 折叠块
- **HelpLabel.vue**：label + 问号 tooltip 字段说明
- **OwnerNotifyEditor.vue**：负责人推送渠道编辑器（v-model 对象 {channels,phone,email,dingtalk_webhook,feishu_webhook,wecom_webhook}）；el-checkbox-group 多选 5 渠道（电话短信/邮箱/钉钉/飞书/企业微信，Element 2.9.1 checkbox 用 value prop）+ 选中渠道的条件输入行；tip「告警发生与 AI 日志分析完成时，向所选渠道推送（至少选一项）」；AssetsView 新增/编辑子机对话框使用

## 工具与样式

- **time.js**：统一北京时间格式化（`sv-SE` locale 固定 YYYY-MM-DD HH:mm:ss）+ 表格 formatter
- **trigger-cn.js**：Zabbix 触发器英文名→中文映射
- **styles.css**：Apple 极简设计系统（色彩令牌、圆角/阴影/动效变量、明暗双主题）

## 权限三层（改权限相关必读）

1. 页面级：router meta.perm + beforeEach 守卫
2. 按钮级：`v-perm` 自定义指令
3. 菜单可见性：登录接口返回 menus 树按角色过滤，驱动 AppDock 一级项与顶栏系统设置下拉

## 构建与部署

- **vite.config.js**：assetsDir `static`（避免与前端路由 /assets 冲突被 nginx 301）；dev 5173，`/api`、`/health` 代理到 `http://127.0.0.1:8000`
- **nginx.conf**：`resolver 127.0.0.11` 用 Docker DNS 动态解析 api 容器；`/api/`、`/health` 反代 `http://api:8000`（read_timeout 120s）；`absolute_redirect off`；index.html 禁缓存；`try_files ... /index.html` 支持 history 路由；client_max_body_size 21m
- **Dockerfile**：两阶段 node:22-alpine（npmmirror 源 install+build）→ nginx:1.27-alpine

## 约定

- 新页面：views/ 建组件 → router.js 加路由（带 meta.perm）→ 后端 menus_seed.py/菜单管理里登记菜单 → 权限点在 seed.py PERMISSIONS 目录
- API 调用一律走 api.js 具名函数，不在组件里裸写 axios
