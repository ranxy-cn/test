"""资产页一键纳管：SSH(密码) 直连新机 → 装 Zabbix Agent → 自动注册 → 回写 CMDB。

与 scripts/zabbix/add-node.sh（本地控制机执行）等价，但由后端 API 容器执行：
前端「新增节点」提交 IP + SSH 密码即可，新机上无需任何人工操作。
SSH 密码仅在安装期间使用，不落库、不写日志。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.models import Asset, utcnow
from app.services.audit import add_audit
from sqlalchemy.exc import IntegrityError

# 与 frontend/public/install-zabbix-agent.sh 保持一致（tests/test_provision.py 会校验一致）
INSTALLER_PATH = Path(__file__).resolve().parents[1] / "integrations" / "zabbix" / "install-zabbix-agent.sh"
AUTOREG_META = "devops-auto"

# 巡检公钥与私钥同目录（容器内挂载 ./keys:/app/keys:ro）
INSPECT_PUBKEY_PATH = Path("/app/keys/inspect_key.pub")

INSTALL_TIMEOUT_SECONDS = 420
REGISTER_WAIT_SECONDS = 190  # agent 最长 2 分钟主动连一次 server，留 3 分钟余量


class ProvisionError(RuntimeError):
    pass


def asset_id_for_ip(ip: str, port: int = 22) -> str:
    """节点资产 ID 含 SSH 端口：同 IP 不同端口是不同资产（如 ranxx.cn:22 与 ranxx.cn:1001）。"""
    return "node-" + ip.strip().replace(".", "-").replace(":", "-") + f"-{int(port)}"


def resolve_zabbix_server(explicit: str = "") -> str:
    """表单未填时依次取：PROVISION_ZABBIX_SERVER → ZABBIX_URL 的 host。"""
    if explicit.strip():
        return explicit.strip()
    s = get_settings()
    if s.provision_zabbix_server:
        return s.provision_zabbix_server
    from urllib.parse import urlparse

    return (urlparse(s.zabbix_url).hostname or "") if s.zabbix_url else ""


# ---------- 远程执行辅助 ----------


def _connect_ssh(ip: str, port: int, username: str, password: str):
    import paramiko

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            ip,
            port=port,
            username=username,
            password=password,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
    except paramiko.AuthenticationException as exc:
        raise ProvisionError(
            f"SSH 登录被拒绝（{username}@{ip}:{port}）：用户名或密码错误，或目标机禁用了密码登录，请核对后重试"
        ) from exc
    except (paramiko.SSHException, OSError) as exc:
        raise ProvisionError(f"SSH 连接失败：无法连到 {ip}:{port}（{exc}）。请检查 IP/端口是否正确、网络是否可达") from exc
    # 长命令（安装/卸载）期间发送 keepalive，防止 NAT/frp 等转发链路因空闲判定死亡而断连
    ssh.get_transport().set_keepalive(30)
    return ssh


def _run(ssh: Any, cmd: str, timeout: float, logs: list[str], *, secret_hint: str = "") -> tuple[int, str]:
    """执行远程命令（PTY 合并 stderr），流式写日志，返回 (退出码, 全部输出)。

    secret_hint 用于超时报错脱敏：报错文案不携带命令原文。
    """
    chan = ssh.get_transport().open_session()
    chan.get_pty()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    chunks: list[str] = []
    deadline = time.monotonic() + timeout
    while True:
        while chan.recv_ready():
            data = chan.recv(4096).decode("utf-8", errors="replace")
            chunks.append(data)
            logs.extend(ln.strip()[:500] for ln in data.splitlines() if ln.strip())
        if chan.exit_status_ready() and not chan.recv_ready():
            break
        if time.monotonic() > deadline:
            chan.close()
            if secret_hint:
                raise ProvisionError(f"命令超时（>{int(timeout)}s）：{secret_hint}")
            raise ProvisionError(f"命令超时（>{int(timeout)}s）：{cmd[:120]}")
        time.sleep(0.2)
    return chan.recv_exit_status(), "".join(chunks)


def _detect_sudo(ssh: Any, logs: list[str]) -> str:
    """返回 ''（root 直跑）或 'sudo'（需用 sudo -S 注入密码）。"""
    _, out = _run(
        ssh,
        'if [ "$(id -u)" = "0" ]; then echo ROOT; elif command -v sudo >/dev/null 2>&1; then echo SUDO; '
        "else echo NOROOT; fi",
        15,
        logs,
    )
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    mode = lines[-1] if lines else ""
    if mode == "ROOT":
        return ""
    if mode == "SUDO":
        return "sudo"
    raise ProvisionError("SSH 账号既非 root 也无 sudo，请改用 root 账号")


def install_agent_via_ssh(
    ssh: Any, *, password: str, zabbix_server: str, refresh_seconds: int = 120, logs: list[str]
) -> str:
    """上传安装脚本并执行，返回新机主机名。

    refresh_seconds：写入 agent 的 RefreshActiveChecks（主动检查上报间隔，秒）。
    """
    remote = "/tmp/install-zabbix-agent-devops.sh"
    sftp = ssh.open_sftp()
    try:
        sftp.put(str(INSTALLER_PATH), remote)
        sftp.chmod(remote, 0o755)
    finally:
        sftp.close()
    logs.append(f"安装脚本已上传 → {remote}")

    sudo = _detect_sudo(ssh, logs)
    if sudo == "sudo":
        # sudo -S 从 stdin 读密码；herestring 不会回显到日志
        cmd = (
            f"sudo -S -p '' bash {remote} {zabbix_server} {AUTOREG_META} {refresh_seconds} <<'PW'\n{password}\nPW"
        )
        code, _ = _run(ssh, cmd, INSTALL_TIMEOUT_SECONDS, logs, secret_hint="安装 zabbix-agent 超时")
    else:
        code, _ = _run(
            ssh, f"bash {remote} {zabbix_server} {AUTOREG_META} {refresh_seconds}", INSTALL_TIMEOUT_SECONDS, logs
        )
    if code != 0:
        raise ProvisionError(f"agent 安装失败（退出码 {code}），详见上方日志")

    code, out = _run(ssh, "hostname", 10, logs)
    if code != 0 or not out.strip():
        raise ProvisionError("安装完成但获取主机名失败")

    # 顺带下发平台巡检公钥，纳管后即可免密实时巡检
    if INSPECT_PUBKEY_PATH.is_file():
        pub = INSPECT_PUBKEY_PATH.read_text().strip()
        if pub:
            cmd = (
                f'mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && '
                f'grep -qxF "{pub}" ~/.ssh/authorized_keys || echo "{pub}" >> ~/.ssh/authorized_keys; '
                f'chmod 600 ~/.ssh/authorized_keys'
            )
            code, _ = _run(ssh, cmd, 15, logs)
            if code == 0:
                logs.append("巡检公钥已下发（后续可免密实时巡检）")
            else:
                logs.append("巡检公钥下发失败（不影响纳管）")

    return out.strip().splitlines()[-1].strip()


def collect_install_info(ssh: Any, *, password: str, logs: list[str]) -> dict:
    """安装完成后采集安装详情（版本/路径/运行方式等），供资产卡「安装详情」展示。

    单次 SSH 跑一段脚本，用标记切分输出；root 直跑，否则 sudo -S 注入密码。
    """
    script = r'''
echo "@@VER@@"
(rpm -q --qf '%{VERSION}-%{RELEASE}\n' zabbix-agent 2>/dev/null || dpkg-query -W -f='${Version}\n' zabbix-agent 2>/dev/null) | head -n1
echo "@@BIN@@"
command -v zabbix_agentd 2>/dev/null || true
echo "@@CONF@@"
grep -E '^(Server|ServerActive|Hostname|HostMetadata)=' /etc/zabbix/zabbix_agentd.conf 2>/dev/null || true
echo "@@LOG@@"
ls /var/log/zabbix/zabbix_agentd.log 2>/dev/null || true
echo "@@RUN@@"
if [ -d /run/systemd/system ] && grep -qa systemd /proc/1/comm 2>/dev/null; then
  echo "systemd:$(systemctl is-enabled zabbix-agent 2>/dev/null)/$(systemctl is-active zabbix-agent 2>/dev/null)"
else
  echo "process:$(pidof zabbix_agentd 2>/dev/null | head -c 60)"
fi
echo "@@PORT@@"
(ss -tln 2>/dev/null || netstat -tln 2>/dev/null) | grep -q ':10050 ' && echo yes || echo no
echo "@@DONE@@"
'''.strip()
    sudo = _detect_sudo(ssh, logs)
    if sudo:
        cmd = f"sudo -S -p '' bash -s <<'EOS'\n{password}\n{script}\nEOS"
    else:
        cmd = f"bash -s <<'EOS'\n{script}\nEOS"
    code, out = _run(ssh, cmd, 60, logs, secret_hint="采集安装详情超时")
    if code != 0:
        raise ProvisionError(f"采集安装详情失败（退出码 {code}）")

    def section(name: str) -> str:
        m = re.search(rf"@@{name}@@\n(.*?)(?=@@\w+@@|\Z)", out, re.S)
        return m.group(1).strip() if m else ""

    lines = [ln for ln in section("CONF").splitlines() if "=" in ln]
    conf = dict(ln.split("=", 1) for ln in lines)
    run = section("RUN")
    run_mode, run_state = (run.split(":", 1) if ":" in run else (run or "unknown", ""))
    info = {
        "version": section("VER") or "unknown",
        "binary": section("BIN") or "/usr/sbin/zabbix_agentd",
        "conf": "/etc/zabbix/zabbix_agentd.conf" if section("CONF") else "",
        "server": conf.get("Server", ""),
        "server_active": conf.get("ServerActive", ""),
        "hostname_in_conf": conf.get("Hostname", ""),
        "log_file": section("LOG") or "/var/log/zabbix/zabbix_agentd.log",
        "run_mode": run_mode,  # systemd / process
        "run_state": run_state,  # enabled/active 或 pid 列表
        "port_10050_listening": section("PORT") == "yes",
        "collected_at": utcnow().isoformat(),
    }
    logs.append(f"安装详情已采集：版本 {info['version']}，运行方式 {run_mode}，配置 {info['conf']}")
    return info


def speedup_host_items(client: Any, hostid: str, logs: list[str]) -> None:
    """注册成功后把 CPU/内存/磁盘/负载采集项的更新间隔调到 30s。

    模板默认 1m 才更新一次，卡片指标会滞后约 1 分钟；调到 30s 后卡片轮询
    （10s）能拿到明显更"实时"的数据。最佳努力：失败只记日志，不影响纳管。
    """
    changed = failed = 0
    for key in ("system.cpu.util", "vm.memory", "vfs.fs.size", "system.cpu.load"):
        try:
            items = (
                client._rpc(
                    "item.get",
                    {
                        "output": ["itemid", "key_"],
                        "hostids": [hostid],
                        "monitored": True,
                        "search": {"key_": key},
                        "limit": 50,
                    },
                )
                or []
            )
            for it in items:
                try:
                    client._rpc("item.update", {"itemid": it["itemid"], "update_interval": "30s"})
                    changed += 1
                except Exception:  # noqa: BLE001
                    failed += 1
        except Exception:  # noqa: BLE001
            failed += 1
    if changed:
        logs.append(f"已把 {changed} 个采集项的更新间隔调到 30s（卡片指标更实时）")
    if failed:
        logs.append(f"{failed} 个采集项间隔调整失败（按模板默认间隔采集，不影响使用）")


def uninstall_agent_via_ssh(ssh: Any, *, password: str, logs: list[str]) -> None:
    """从目标机卸载 zabbix-agent：停服务 → 卸载软件包 → 删配置/日志/安装脚本。

    与 install_agent_via_ssh 对称；执行后校验二进制与进程均已清除。
    yum/apt 阶段保持输出可见（维持链路流量）；经 frp/NAT 等转发的链路可能把
    连接中途掐断（paramiko 表现为退出码 -1），此时幂等重试一次。
    """
    script = (
        "if [ -d /run/systemd/system ] && grep -qa systemd /proc/1/comm 2>/dev/null; then "
        "systemctl stop zabbix-agent 2>/dev/null || true; "
        "systemctl disable zabbix-agent 2>/dev/null || true; "
        "else pkill -f 'zabbix[_-]agentd' 2>/dev/null || true; fi; "
        "if command -v yum >/dev/null 2>&1; then echo '==> yum remove zabbix-agent ...'; yum remove -y zabbix-agent || true; fi; "
        "if command -v apt-get >/dev/null 2>&1; then "
        "echo '==> apt-get purge zabbix-agent ...'; "
        "DEBIAN_FRONTEND=noninteractive apt-get purge -y zabbix-agent || true; fi; "
        "rm -rf /etc/zabbix /var/log/zabbix /tmp/install-zabbix-agent-devops.sh; "
        "if command -v zabbix_agentd >/dev/null 2>&1 || pidof zabbix_agentd >/dev/null 2>&1; then echo LEFT; "
        "else echo GONE; fi"
    )
    sudo = _detect_sudo(ssh, logs)
    out = ""
    for attempt in (1, 2):
        if sudo:
            cmd = f"sudo -S -p '' bash -s <<'EOS'\n{password}\n{script}\nEOS"
        else:
            cmd = f"bash -s <<'EOS'\n{script}\nEOS"
        code, out = _run(ssh, cmd, 300, logs, secret_hint="卸载 zabbix-agent 超时")
        if code == 0:
            break
        if code == -1 and attempt == 1:
            logs.append("SSH 链路在卸载过程中断开（退出码 -1），幂等重试一次 ...")
            continue
        if code == -1:
            raise ProvisionError(
                "卸载失败：SSH 链路在卸载过程中断开（退出码 -1，常见于 frp/NAT 转发链路不稳），请重试；"
                "或取消勾选「联动卸载」仅删除台账记录"
            )
        raise ProvisionError(f"卸载失败（退出码 {code}），详见上方日志")
    if "GONE" not in out:
        raise ProvisionError("卸载后仍检测到 zabbix-agent 残留，请登录目标机手工检查")
    logs.append("远端卸载完成：服务已停止，软件包/配置/日志目录已删除")


def wait_registered(zabbix_host: str, logs: list[str]) -> str:
    """轮询 Zabbix 等自动注册生效，返回 hostid；未配置 API 或超时返回空串（不算失败）。"""
    s = get_settings()
    has_api = bool(s.zabbix_token or (s.zabbix_url and s.zabbix_user and s.zabbix_password))
    if not has_api:
        logs.append("未配置 Zabbix API（ZABBIX_URL/USER/PASSWORD），跳过注册验证")
        return ""

    from app.integrations.zabbix.http import build_http_zabbix

    client = build_http_zabbix()
    logs.append(f"等待 Zabbix 自动注册（最长 {REGISTER_WAIT_SECONDS}s）...")
    deadline = time.monotonic() + REGISTER_WAIT_SECONDS
    last_err = ""
    while time.monotonic() < deadline:
        try:
            host = client.resolve_host("", {"zabbix_host": zabbix_host, "hostname": zabbix_host})
            if host.get("mapped"):
                return str(host["hostid"])
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        time.sleep(10)
    if last_err:
        logs.append(f"Zabbix API 查询异常：{last_err}")
    return ""


def provision_node(
    asset_id: str,
    *,
    display_name: str = "",
    ip: str,
    port: int,
    username: str,
    password: str,
    zabbix_server: str,
    requested_by: str = "",
    refresh_seconds: int | None = None,
) -> None:
    """后台任务：装 agent 并登记。全程把进度写入 assets.extra["provision"]。

    display_name：用户填的子机显示名（选填）。填写后资产列表用它展示，
    Zabbix 侧仍以真实主机名注册（agent 配置的 Hostname），两者通过 zabbix_host 字段关联。

    refresh_seconds：agent 上报间隔（RefreshActiveChecks，秒）；为空时用全局默认值。
    """
    from app.database import SessionLocal

    display_name = display_name.strip()
    refresh = refresh_seconds or get_settings().zabbix_agent_refresh_seconds

    logs: list[str] = []
    state: dict[str, Any] = {
        "status": "running",
        "ip": ip,
        "port": port,
        "username": username,
        "zabbix_server": zabbix_server,
        "requested_by": requested_by,
        "started_at": utcnow().isoformat(),
        "finished_at": "",
        "error": "",
        "hostname": "",
        "hostid": "",
    }

    def save(finished: bool = False) -> None:
        db = SessionLocal()
        try:
            a = db.get(Asset, asset_id)
            if a is None:
                # 直接调用（非 API 入口）也保证留痕，便于排查
                a = Asset(
                    id=asset_id,
                    hostname=ip,
                    app="",
                    role="app",
                    env="prod",
                    owner="",
                    tenant_id=get_settings().tenant_id,
                    reachable=False,
                )
            snap = dict(state)
            snap["logs"] = logs[-200:]
            if finished:
                snap["finished_at"] = utcnow().isoformat()
            a.extra = {**(a.extra or {}), "provision": snap}
            db.add(a)
            db.commit()
        finally:
            db.close()

    try:
        save()
        logs.append(f"[1/4] 连接 {username}@{ip}:{port} ...")
        ssh = _connect_ssh(ip, port, username, password)
        try:
            logs.append("[2/4] 安装 zabbix-agent（约 1 分钟）...")
            hostname = install_agent_via_ssh(
                ssh, password=password, zabbix_server=zabbix_server, refresh_seconds=refresh, logs=logs
            )
            # 采集安装详情（版本/安装位置/运行方式等），资产卡「安装详情」展示
            try:
                state["install_info"] = collect_install_info(ssh, password=password, logs=logs)
            except Exception as exc:  # noqa: BLE001
                logs.append(f"采集安装详情失败（不影响纳管）：{exc}")
        finally:
            ssh.close()

        state["hostname"] = hostname
        db = SessionLocal()
        try:
            disp = display_name or hostname
            a = db.get(Asset, asset_id)
            if a is not None:
                a.hostname = disp
                # Zabbix 侧始终用真实主机名（agent 的 Hostname 配置），展示名与监控名解耦
                a.zabbix_host = hostname
                a.reachable = True
                db.add(a)
                try:
                    db.commit()
                except IntegrityError:
                    # hostname 唯一约束冲突（重名/同一宿主机多个 SSH 端口指向同机）：
                    # 展示名加端口后缀保唯一；Zabbix 侧监控名不受影响
                    db.rollback()
                    a = db.get(Asset, asset_id)
                    a.hostname = f"{disp}-{port}"
                    a.zabbix_host = hostname
                    a.reachable = True
                    db.add(a)
                    db.commit()
        finally:
            db.close()
        logs.append(f"新机主机名：{hostname}" + (f"，显示名：{display_name}" if display_name else "") + "，已登记 CMDB")

        logs.append("[3/4] 等待 Zabbix 自动注册 ...")
        hostid = wait_registered(hostname, logs)
        state["hostid"] = hostid
        if hostid:
            # 卡片指标实时性：模板默认 1m 采集一次太慢，调到 30s（最佳努力）
            try:
                from app.integrations.zabbix.http import build_http_zabbix

                speedup_host_items(build_http_zabbix(), hostid, logs)
            except Exception as exc:  # noqa: BLE001
                logs.append(f"调整采集间隔失败（不影响纳管）：{exc}")
            db = SessionLocal()
            try:
                a = db.get(Asset, asset_id)
                if a is not None:
                    a.external_id = hostid
                    db.add(a)
                    db.commit()
            finally:
                db.close()
            state["status"] = "registered"
            logs.append(f"[4/4] 已注册进 Zabbix（hostid={hostid}），纳管完成")
        else:
            state["status"] = "installed"
            logs.append(
                "[4/4] agent 已安装；Zabbix 注册未确认（检查新机出站 10051 / ZABBIX_* 配置），安装日志见上"
            )
    except Exception as exc:  # noqa: BLE001
        state["status"] = "failed"
        state["error"] = str(exc)[:300]
        logs.append(f"失败：{state['error']}")
    finally:
        save(finished=True)

    db = SessionLocal()
    try:
        add_audit(
            db,
            ticket_id=None,
            event_type="asset_provision",
            actor=requested_by or None,
            result={"asset_id": asset_id, "ip": ip, "status": state["status"], "error": state["error"]},
            params_digest=f"ip={ip},port={port},user={username}",
        )
        db.commit()
    finally:
        db.close()
