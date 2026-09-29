# 架构调整：移除 Zabbix · 自研 Agent 采集体系

> 分支：`feature/agent-architecture` · 日期：2026-09-28
> 目标：全面移除 Zabbix 组件，子机监控改为自研 Agent（Python/Go 双实现）常驻推送，母机确认机制纯业务化。

## 1. 现状与影响评估

### 1.1 生产 Zabbix 形态（124.221.251.186）
- Zabbix Server 5.0.47 以 **Docker 栈**运行（非 systemd/rpm），Web :8081、Trapper :10051 均为 docker-proxy 监听。
- 由平台「母机部署」流程经 `mother_deploy.py upload_stack` 上传 compose 并拉起；卸载走 `uninstall_stack`。
- 子机纳管（`provision.py install_agent_via_ssh`）安装的是 **Zabbix agent**（`install-zabbix-agent.sh`）。

### 1.2 代码影响面（改动策略）

| 模块 | 现状 | 处理 |
|---|---|---|
| `integrations/zabbix/*` | HttpZabbixClient/Mock/只读白名单/映射 | 移除目录；metrics 端点改读 agent 落库数据 |
| `api.py` verify-zabbix / 母机总览 host.get / 告警策略同步模板 / metrics 端点 | Zabbix API 调用 | 端点删除或改为读本地 agent 数据；告警策略纯平台存储 |
| `services/metrics_store.py` | `zabbix_client_for` + celery 采集循环 | 移除 zabbix 客户端依赖；采集源=agent 上报（system_metric_samples.asset_id） |
| `services/provision.py` | 子机纳管装 Zabbix agent | 改为部署自研 agent（py/go，用户可选） |
| `services/inspector.py` | sysinfo 探测 zabbix_agent | 移除该检测段 |
| `models.Asset.zabbix_host` | 全链引用（CRUD/CSV/搜索/详情） | **保留列**（向后兼容，不再写入新值），界面不再展示 |
| `schemas.ZabbixWebhookIn` / `POST /webhooks/zabbix` | 告警接收 | 保留告警接收能力，**中性化为通用 webhook**（兼容原字段） |
| 前端 AssetsView/AssetMonitor/MotherInstallDetail/StatusView/trigger-cn.js | Zabbix 步骤/徽标/文案 | 去 Zabbix 化；添加母机只留纯业务字段；新增「部署 Agent」+ 配置管理 |
| seed/dict_seed | zabbix 演示数据 | 移除 zabbix 专属种子；资产字典改为中性 |
| 测试（14 个文件引用 zabbix） | — | 删纯 Zabbix 用例；其余断言改造；新增 agent 体系用例 |

### 1.3 不变量（功能完整性保障）
- 异常告警 → 任务单管线不变（webhook 契约向后兼容）。
- 资产 CRUD / CSV / 分组 / 巡检（SSH 只读采集）不变。
- 母机实时监控与历史全揽（本机 /proc）不变，数据源不含 Zabbix。

## 2. 新架构：自研 Agent 推送

```
子机（Python agent.py 或 Go agent 二进制，常驻）
  ├─ 每 interval 秒读 /proc（CPU/内存/磁盘/负载/网络 差值算法）
  ├─ POST /api/v1/agent/report   （X-Agent-Token 认证，body 带 asset_id）
  ├─ GET  /api/v1/agent/config   （启动时 + 定期拉取，平台侧可改采集项/频率）
  └─ 断网本地缓存（环形，上限可配），恢复后按序补发
母机（DevOpsAgent 平台）
  ├─ 上报校验 → system_metric_samples（新增 asset_id 列）落库，保留 60 天
  ├─ 离线检测：asset 超过 offline_after 秒无上报 → 状态=offline
  ├─ 「部署 Agent」：复用纳管 SSH 通道，按用户选择下发 py / go 实现
  └─ 配置管理界面：采集间隔、上报间隔、采集项开关、离线阈值、缓存上限
```

### 2.1 双语言对比（部署界面内嵌说明）

| 维度 | Python（agent.py） | Go（agent 静态二进制） |
|---|---|---|
| 目标机要求 | 有 python3（≥3.6，部署时自动检测，缺失自动 yum/apt 安装） | 无任何运行时要求 |
| 部署物 | 单文件 ~15KB | 单文件 ~6MB（linux/amd64 静态编译） |
| 资源占用 | 常驻 ~15-25MB RSS | 常驻 ~8-12MB RSS |
| 启动速度 | ~100ms | <10ms |
| CPU（1s 采集） | ~0.3-0.8% | ~0.1-0.3% |
| 兼容性 | 依赖 glibc 环境的 python3；极老系统（py2）不可用 | 任意 linux/amd64，含最小化镜像/容器 |
| 可维护性 | 平台上可直接改脚本重下发 | 改动需重新编译发布 |

### 2.2 配置模型（平台侧存储，agent 定期同步）
- `report_interval`（默认 5s，1-3600）、`collect_items`（cpu/mem/disk/load/net 开关）、`offline_after`（默认 30s）、`buffer_max`（默认 600 条）。
- 存储：`Asset.extra["agent_config"]`；管理界面按资产配置，部署时随 agent 下发初始值。

## 3. 实施步骤（本分支提交序列）
1. `feat(agent)`: 后端上报/配置/状态 API + asset_id 迁移（0010）
2. `feat(agent)`: Python/Go 双实现 agent + 构建脚本
3. `feat(ui)`: 部署 Agent（语言选择+对比说明+参数配置）+ 监控按资产隔离
4. `refactor(zabbix)`: 平台代码去 Zabbix 化 + 测试改造
5. `ops`: 生产卸载 Zabbix docker 栈 + 残留验证

## 4. 回退方案

### 4.1 版本锚点
- 合入前主分支打 tag `pre-agent-architecture`（回退点）。
- 本分支每个提交序号对应上述实施步骤，可 `git revert` 单步回退。

### 4.2 回退路径（按层级）
1. **代码回退**：`git revert <merge-commit>` 或 `git reset --hard pre-agent-architecture`（仅主分支未共享时）→ 前后端重建部署。数据库迁移回滚：`alembic downgrade -1`（0010 仅加列，安全可逆）。
2. **数据回退**：`system_metric_samples.asset_id` 为可空新增列，回退后旧代码忽略该列，数据无需清理。
3. **生产回退**：Zabbix 卸载不可自动回滚（容器/数据卷已清理）。如需恢复 Zabbix 体系：回退代码后重新执行母机「部署监控栈」流程（镜像仍在远端仓库，数据卷备份 `zabbix_data` 已在卸载前导出至 `/root/zabbix-backup-<date>.tar.gz`）。
4. **子机回退**：agent 停止并删除（`/opt/devops-agent/`），子机回到仅 SSH 巡检模式，平台功能不受影响。

### 4.3 回退测试机制
- 迁移可逆性：CI 中执行 `alembic upgrade head && alembic downgrade base && upgrade head`。
- webhook 兼容性：保留原 Zabbix 5.0 宏字段的用例通过 = 告警链路可回退。
- 回退演练：合入前在分支上执行 `alembic downgrade -1` + 部署上一版本镜像，验证 /api/v1/health 与资产列表正常。
