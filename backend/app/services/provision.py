"""SSH 远程执行辅助：供 agent 部署器（agent_deployer）等模块复用。

只保留连接/执行/提权探测等通用能力；纳管业务逻辑在 agent_deployer 中实现。
"""

from __future__ import annotations

import time
from typing import Any


class ProvisionError(RuntimeError):
    pass


def asset_id_for_ip(ip: str, port: int = 22) -> str:
    """节点资产 ID 含 SSH 端口：同 IP 不同端口是不同资产（如 ranxx.cn:22 与 ranxx.cn:1001）。"""
    return "node-" + ip.strip().replace(".", "-").replace(":", "-") + f"-{int(port)}"


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
