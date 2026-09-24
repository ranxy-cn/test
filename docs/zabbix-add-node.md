# Zabbix 监控节点一键接入 / 摘除操作文档

> 适用环境：以母机 `124.221.251.186`（CentOS 7，Docker 化 Zabbix 5.0.41 LTS）为监控中心，批量接入其他 Linux 服务器。
> 目标：**加一台新服务器 = 一条命令**，装完自动出现在 Zabbix，无需在 Zabbix 前端手工建主机。

---

## 1. 原理（为什么能一条命令）

采用 **主动式 agent + 自动注册（Auto Registration）** 模式：

```
新服务器                          母机 124.221.251.186
┌────────────────┐   出站 10051   ┌──────────────────────┐
│ zabbix-agent   │ ──────────────▶│ zabbix-server (10051)│
│ HostMetadata=  │                │  └─ 自动注册动作:      │
│ devops-auto    │                │     检测到 metadata   │
└────────────────┘                │     → 加主机+入组+挂模板│
                                  └──────────────────────┘
```

- 新机器**不需要开放任何入站端口**（agent 主动连母机 10051，云服务器默认放行出站）。
- 安装脚本写入 `HostMetadata=devops-auto`，Zabbix 端自动注册动作按此元数据自动纳管：
  - 加入主机组 `Linux servers`
  - 链接模板 `Template OS Linux by Zabbix agent active`（主动式，CPU/内存/磁盘/网络全套）

---

## 2. 服务端一次性配置（✅ 已完成，无需重复操作）

已通过 API 在 Zabbix 上创建自动注册动作（**actionid=8**）：

| 项 | 值 |
|---|---|
| 名称 | `Auto registration: devops-auto` |
| 触发条件 | Host metadata 包含 `devops-auto` |
| 动作 | 添加主机 → 加入 `Linux servers` → 链接主动式 Linux 模板 |
| 状态 | **已启用** |

- 前端查看/停用：`http://124.221.251.186:8081` → **告警 → 动作 → 事件源选"自动注册"**
- 该配置由仓库脚本生成，**幂等可重放**（换 Zabbix 或误删后重跑即可）：

```bash
bash scripts/zabbix/setup-autoreg.sh        # 已存在则自动跳过
```

---

## 3. 前置准备（仅第一次，每台控制机做一次）

> 控制机 = 你执行加节点命令的机器（母机或本地 Mac 均可，仓库代码里都带脚本）。

1. **对目标新机做 SSH 免密**（装 agent 需要远程执行命令）：

   ```bash
   ssh-copy-id root@<新机IP>          # 输入一次新机密码，之后永久免密
   ```

   不想免密也行：本机装有 `sshpass` 时，加节点命令可带 `SSH_PASS='密码'` 自动完成免密（见 4.1）。

2. **（可选）启用"新机上 curl 一条命令"方式**：把安装脚本随 devops-ui 发布一次（仓库已含 `frontend/public/install-zabbix-agent.sh`）：

   ```bash
   cd /home/ranxiaoying/devops-agent && SKIP_PULL=1 bash scripts/deploy.sh
   ```

   之后新机器上可以直接 `curl ... | bash`（见 4.2）。不做这步不影响方式 A。

3. **（可选）接入后自动同步到 devopsAgent 资产页**：平台接口已上线（`POST /api/v1/assets/upsert`）。只需在控制机配置一次凭据：

   ```bash
   cp scripts/zabbix/.env.devops.example scripts/zabbix/.env.devops
   # 编辑 .env.devops，填入 DEVOPS_PASS（平台 admin 密码）
   ```

   `add-node.sh` 会自动读取；不配置也不影响接入，只是资产页看不到这台机器（Zabbix 里有）。

---

## 4. 加节点（四选一，推荐方式 A）

### 4.0 方式 A（最简单，推荐）：平台资产页直接新增

登录 devopsAgent 前端 `http://124.221.251.186:8080` → **资产台账（CMDB）** → 右上角 **新增节点**：

| 表单项 | 说明 |
|---|---|
| 服务器 IP / SSH 端口 / 用户 / 密码 | 必填 IP 和密码；账号要求 root 或有 sudo；密码仅本次安装使用，**不保存** |
| Zabbix Server | 留空即用系统配置（当前 `124.221.251.186`） |
| 角色 / 环境 / 负责人 | 资产台账展示字段，随意填 |

点「开始纳管」后平台自动完成：SSH 登录新机 → 安装 zabbix-agent → 等自动注册进 Zabbix → 回写台账。
列表每 5 秒自动刷新，「纳管状态」列显示 安装中 / 已纳管 / 已装待注册 / 失败（悬停看日志）。

等价接口（脚本调用）：

```bash
curl -X POST http://124.221.251.186:8000/api/v1/assets/provision \
  -H "Authorization: Bearer <平台token>" -H 'Content-Type: application/json' \
  -d '{"ip":"<新机IP>","username":"root","password":"<SSH密码>"}'
```

> 前提：新机能出站访问母机 10051 端口（云服务器默认放行出站）。

### 4.1 方式 B：控制机一条命令

```bash
bash scripts/zabbix/add-node.sh <新机IP>              # 默认 22 端口、root 账号
bash scripts/zabbix/add-node.sh <新机IP> 2222 admin   # 自定义端口/用户
SSH_PASS='密码' bash scripts/zabbix/add-node.sh <新机IP>   # 未免密时（需本机有 sshpass）
```

脚本自动完成：SSH 可达检查 → 上传并执行安装脚本 → 轮询 Zabbix API 等注册 → 打印结果。

### 4.2 方式 C：在新机器上直接一条命令（需做过第 3 步可选准备）

```bash
curl -fsSL http://124.221.251.186:8080/install-zabbix-agent.sh | bash
```

### 4.3 方式 D：手动兜底（两条命令）

```bash
scp frontend/public/install-zabbix-agent.sh root@<新机IP>:/tmp/
ssh root@<新机IP> bash /tmp/install-zabbix-agent.sh
```

> 安装脚本支持 CentOS/RHEL/Rocky/Alma 7~9 与 Ubuntu/Debian 系；
> CentOS 7（EOL）会自动把基础源切到 vault.centos.org 再装。
> 任何一台机器重复执行安装脚本都是安全的（幂等）。

---

## 5. 验证是否接入成功

- **前端**：`http://124.221.251.186:8081`（Admin 登录）→ **数据采集 → 主机**，新主机应已出现，约 2~5 分钟后"最新数据"有采集值。
- **命令行**（任意机器）：

  ```bash
  curl -s -X POST http://124.221.251.186:8081/api_jsonrpc.php \
    -H 'Content-Type: application/json-rpc' \
    -d '{"jsonrpc":"2.0","method":"user.login","params":{"user":"Admin","password":"p@ssW0rd"},"id":1}'
  # 用返回 token 查：
  curl -s -X POST http://124.221.251.186:8081/api_jsonrpc.php -H 'Content-Type: application/json-rpc' \
    -d '{"jsonrpc":"2.0","method":"host.get","params":{"countOutput":true,"filter":{"host":["<新机主机名>"]}},"auth":"<token>","id":2}'
  # result 非 0 即已注册
  ```

- **新机器本机**：`systemctl status zabbix-agent`、`tail -50 /var/log/zabbix/zabbix_agentd.log`。

---

## 6. 摘除 / 下线节点

```bash
# 新机器上停用并卸载：
ssh root@<节点IP> "systemctl disable --now zabbix-agent && yum remove -y zabbix-agent || apt-get remove -y zabbix-agent"
```

Zabbix 里删主机：前端 → 数据采集 → 主机 → 勾选 → 删除；
或 API：`host.delete`（传 hostids）。

---

## 7. 常见问题（FAQ）

| 现象 | 原因 / 处理 |
|---|---|
| 安装时报 yum 源 404 / 失败 | CentOS 7 EOL；脚本已自动切 vault 源。若是其他系统，检查 `https://repo.zabbix.com/zabbix/5.0/rhel/<版本>/` 是否存在（el9 请确认 5.0 有对应包，否则建议直接用 Zabbix 6.0 源并同步升级母机，或换装 6.0 agent——主动式向下兼容有限，优先同版本） |
| 3 分钟没自动注册 | ① 新机出站到 10051 不通：`nc -zv 124.221.251.186 10051`（云安全组出站一般全放行，若有限制需放行）；② `HostMetadata` 没写进去：`grep HostMetadata /etc/zabbix/zabbix_agentd.conf`；③ 改过配置没重启：`systemctl restart zabbix-agent` |
| 主机注册了但"可用性"是红色 | 可用性图标走被动检查（母机→节点 10050）。主动式采集不受影响；介意的话在节点安全组放行 10050 入站即可 |
| 同名主机重复/注册错乱 | 修改节点主机名后需同步改 agent 配置：`sed -i "s|^Hostname=.*|Hostname=$(hostname)|" /etc/zabbix/zabbix_agentd.conf && systemctl restart zabbix-agent`；Zabbix 中删除旧主机 |
| 想换注册元数据 | 安装脚本第二个参数：`bash install-zabbix-agent.sh 124.221.251.186 <新metadata>`；服务端动作条件需同步改（前端改动作过滤条件） |
| 安全加固（可选） | 生产建议给 agent 配 PSK 加密（agent 端 `TLSPSKIdentity/TLSPSKFile` + Zabbix 主机端加密设置），并把母机侧 10051 的访问限制在安全组白名单 IP 范围内 |

---

## 8. 与 DevOpsAgent 平台的关系

**接入的机器会出现在 devopsAgent 哪里：**

| 页面 | 能看到什么 | 条件 |
|---|---|---|
| Zabbix 前端（8081） | 所有接入机器 + 监控数据 | 自动，无需操作 |
| devopsAgent 资产页（CMDB） | 接入机器的台账（zabbix_host 已绑定） | 配了第 3 节第 3 步的 `DEVOPS_*` 环境变量，或走平台部署 |

两条互不冲突的路径：

1. **快捷路径**：`add-node.sh` 一条命令接入（Zabbix 自动纳管 + 可选自动登记 CMDB）；
2. **平台路径（留痕）**：在平台**资产页**录入新机器 → 点 **"部署 Zabbix Agent"** → 工单审批 → 白名单预案 `ACT-DEPLOY-ZABBIX-AGENT` 执行 → 自动回写 CMDB（`reachable=true`、绑定 `zabbix_host`）。

平台路径的前提：devops-api 以 `ANSIBLE_MODE=real` 运行，且容器内配置了对新机器的 SSH 私钥（`ANSIBLE_SSH_PRIVATE_KEY_FILE`）。

---

## 9. 本次涉及的文件

| 文件 | 作用 |
|---|---|
| `scripts/zabbix/setup-autoreg.sh` | 服务端一次性配置自动注册动作（幂等，已执行） |
| `scripts/zabbix/add-node.sh` | 控制机一条命令加节点（装 → 注册 → 验证） |
| `frontend/public/install-zabbix-agent.sh` | 新机上的安装脚本（curl 一键 / scp 兜底共用） |
