#!/usr/bin/env bash
# Zabbix Agent 一键安装（新节点上执行，配合母机 124.221.251.186 的“自动注册”动作）
#
# 用法（在新节点上，root 执行）：
#   curl -fsSL http://124.221.251.186:8080/install-zabbix-agent.sh | bash
#   或下载后: bash install-zabbix-agent.sh [Zabbix服务器IP或IP:Trapper端口] [注册元数据]
#
# 行为：
#   1. 识别系统（RPM 系 el7/8/9，DEB 系 ubuntu/debian）
#   2. 配置 Zabbix 5.0 官方源（与母机 5.0.41 LTS 版本对齐；CentOS7 EOL 自动切 vault 源）
#   3. 安装 zabbix-agent，写配置（Server / ServerActive / Hostname / HostMetadata）
#   4. 启动并自启，放行本机防火墙 10050（被动检查用，可选）
#   5. agent 主动连母机 Trapper 端口（默认 10051，可用 IP:端口 指定）→ 自动注册进 Zabbix
#
# 参数（均可选）：
#   $1 = Zabbix Server（IP 或 IP:端口）
#   $2 = 自动注册元数据（HostMetadata）
#   $3 = agent 主动检查上报间隔（RefreshActiveChecks，秒，默认 120）
set -euo pipefail

ZBX_SERVER="${1:-124.221.251.186}"
ZBX_META="${2:-devops-auto}"
ZBX_REFRESH="${3:-120}"

# Server= 只接受 IP/主机名；ServerActive= 接受 IP:端口（缺省 10051）
case "${ZBX_SERVER}" in
  *:*) ZBX_HOST="${ZBX_SERVER%%:*}"; ZBX_TRAP="${ZBX_SERVER##*:}" ;;
  *)   ZBX_HOST="${ZBX_SERVER}";      ZBX_TRAP="10051" ;;
esac

if [ "$(id -u)" != "0" ]; then
  echo "!! 请用 root 执行：sudo bash $0"; exit 1
fi

log() { echo -e "\n==> $*"; }

# ---------- 1. 识别系统 ----------
. /etc/os-release
ID_LC=$(printf '%s' "${ID:-}" | tr 'A-Z' 'a-z')
VER="${VERSION_ID:-0}"
VER="${VER%%.*}"
case "${ID_LC}" in
  centos|rhel|rocky|almalinux|anolis|opencloudos|tencentos|almalinux) FAMILY=rpm ;;
  ubuntu|debian) FAMILY=deb ;;
  *) echo "!! 暂不支持的系统：${PRETTY_NAME:-unknown}（支持 CentOS/RHEL/Ubuntu/Debian 系）"; exit 1 ;;
esac
log "系统：${PRETTY_NAME:-unknown}（${FAMILY}，主版本 ${VER}）"

# ---------- 2. 软件源 ----------
# CentOS 7 已 EOL，官方镜像源下线；把基础源切到 vault.centos.org，否则装依赖会失败
fix_centos7_vault() {
  local f=/etc/yum.repos.d/CentOS-Base.repo
  [ -f "$f" ] || return 0
  log "检测到 CentOS 7（EOL），基础源切换到 vault.centos.org"
  [ -f "$f.bak" ] || cp "$f" "$f.bak"
  sed -i -e 's|^mirrorlist=|#mirrorlist=|g' \
         -e 's|^#\?baseurl=http://mirror.centos.org|baseurl=http://vault.centos.org|g' "$f"
  yum clean all >/dev/null 2>&1 || true
}

setup_repo() {
  if [ "$FAMILY" = rpm ]; then
    cat > /etc/yum.repos.d/zabbix.repo <<REPO
[zabbix]
name=Zabbix Official Repository - \$basearch
baseurl=https://repo.zabbix.com/zabbix/5.0/rhel/\$releasever/\$basearch/
enabled=1
gpgcheck=1
gpgkey=https://repo.zabbix.com/zabbix-official-repo.key
REPO
    # 部分环境 rpm --import 拉取 key 失败或 key bundle 与包签名不匹配，导致 GPG 校验失败；
    # 内网场景直接跳过 GPG 校验，保证安装成功
    rpm --import https://repo.zabbix.com/zabbix-official-repo.key 2>/dev/null || true
    yum makecache -y >/dev/null 2>&1 || yum makecache fast >/dev/null 2>&1 || true
  else
    curl -fsSL https://repo.zabbix.com/zabbix-official-repo.key -o /etc/apt/trusted.gpg.d/zabbix.asc
    echo "deb https://repo.zabbix.com/zabbix/5.0/${ID_LC} ${VERSION_CODENAME:-} main" \
      > /etc/apt/sources.list.d/zabbix.list
    apt-get update -y >/dev/null
  fi
}

install_pkg() {
  if [ "$FAMILY" = rpm ]; then
    [ "${ID_LC}${VER}" = "centos7" ] && fix_centos7_vault
    setup_repo
    yum install -y --nogpgcheck zabbix-agent
  else
    setup_repo
    DEBIAN_FRONTEND=noninteractive apt-get install -y zabbix-agent
  fi
}

log "安装 zabbix-agent（5.0，与母机版本对齐）"
if ! install_pkg; then
  if [ "${ID_LC}${VER}" = "centos7" ] && ! grep -q vault.centos.org /etc/yum.repos.d/CentOS-Base.repo 2>/dev/null; then
    fix_centos7_vault; install_pkg
  else
    echo "!! 安装失败，请检查软件源连通性（repo.zabbix.com）"; exit 1
  fi
fi

# ---------- 3. 写配置 ----------
CONF=/etc/zabbix/zabbix_agentd.conf
[ -f "$CONF" ] || { echo "!! 未找到 $CONF"; exit 1; }
cp "$CONF" "${CONF}.bak.$(date +%s)"
sed -i -e "s|^Server=.*|Server=${ZBX_HOST}|" \
       -e "s|^ServerActive=.*|ServerActive=${ZBX_HOST}:${ZBX_TRAP}|" \
       -e "s|^#\?Hostname=.*|Hostname=$(hostname)|" \
       -e "s|^#\?HostMetadata=.*|HostMetadata=${ZBX_META}|" \
       -e "s|^#\?RefreshActiveChecks=.*|RefreshActiveChecks=${ZBX_REFRESH}|" "$CONF"
grep -q '^HostMetadata=' "$CONF" || echo "HostMetadata=${ZBX_META}" >> "$CONF"
grep -q '^RefreshActiveChecks=' "$CONF" || echo "RefreshActiveChecks=${ZBX_REFRESH}" >> "$CONF"
log "配置完成：Server=${ZBX_HOST}，ServerActive=${ZBX_HOST}:${ZBX_TRAP}，Hostname=$(hostname)，HostMetadata=${ZBX_META}，RefreshActiveChecks=${ZBX_REFRESH}s"

# ---------- 4. 启动 ----------
# 兼容无 systemd 的环境（Docker 容器等）：/proc/1/comm 为 systemd 才走 systemctl，
# 否则 systemctl start 会报 "Assertion 'bus' failed ... Aborting"（退出码 134）
if [ -d /run/systemd/system ] && grep -qa systemd /proc/1/comm 2>/dev/null; then
  systemctl enable zabbix-agent >/dev/null 2>&1 || true
  systemctl restart zabbix-agent
  # 本机防火墙放行 10050（被动检查用；不放开只影响“可用性”图标，不影响采集）
  systemctl is-active firewalld >/dev/null 2>&1 && \
    { firewall-cmd --add-port=10050/tcp --permanent >/dev/null; firewall-cmd --reload >/dev/null; } || true
  command -v ufw >/dev/null 2>&1 && ufw allow 10050/tcp >/dev/null 2>&1 || true
  sleep 2
  if systemctl is-active --quiet zabbix-agent; then
    log "zabbix-agent 已运行（systemd 管理，端口 10050）：$(systemctl is-active zabbix-agent)"
  else
    echo "!! 服务未起来，查看日志：journalctl -u zabbix-agent -n 50"; exit 1
  fi
else
  log "未检测到 systemd（容器环境），直接拉起 zabbix_agentd"
  pkill -f 'zabbix[_-]agentd' 2>/dev/null || true
  /usr/sbin/zabbix_agentd -c "$CONF"
  sleep 2
  if pidof zabbix_agentd >/dev/null 2>&1; then
    log "zabbix_agentd 已运行（端口 10050；随容器生命周期运行，容器重启后需重新执行本脚本或自启入口）"
  else
    echo "!! zabbix_agentd 启动失败，查看日志：tail -50 /var/log/zabbix/zabbix_agentd.log"; exit 1
  fi
fi

cat <<TIP

============================================================
 安装完成 ✅
  本机主机名（Zabbix 显示名）：$(hostname)
  注册元数据：${ZBX_META} → 母机 ${ZBX_SERVER}
agent 每 ${ZBX_REFRESH} 秒主动连一次母机 ${ZBX_HOST}:${ZBX_TRAP}，首次连接即自动注册。
约 1~3 分钟后到 Zabbix 前端“数据采集→主机”查看新主机。
============================================================
TIP
