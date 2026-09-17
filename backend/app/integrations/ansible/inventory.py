from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings


def _asset_field(asset: Any, *names: str, default: str = "") -> str:
    if asset is None:
        return default
    extra = {}
    if isinstance(asset, dict):
        extra = asset.get("extra") or {}
        for name in names:
            value = asset.get(name)
            if value:
                return str(value)
    else:
        extra = getattr(asset, "extra", None) or {}
        for name in names:
            value = getattr(asset, name, None)
            if value:
                return str(value)
    for name in names:
        value = extra.get(name)
        if value:
            return str(value)
    return default


def demo_inventory_example_path() -> Path:
    here = Path(__file__).resolve().parents[4] / "inventory" / "demo.ini.example"
    if here.exists():
        return here
    return Path(__file__).resolve().parents[2].parent.parent / "inventory" / "demo.ini.example"


def inventory_from_asset(asset: Any) -> tuple[str, str]:
    """返回 (ini_text, source)。source: asset | file | demo-local。"""
    settings = get_settings()
    configured = (settings.ansible_inventory or "").strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise FileNotFoundError(f"ANSIBLE_INVENTORY 不存在: {configured}")
        return path.read_text(encoding="utf-8"), "file"

    hostname = _asset_field(asset, "hostname") or "devops-node"
    host = _asset_field(asset, "ansible_host", "ip", "ssh_host") or hostname
    user = _asset_field(asset, "ansible_user", "ssh_user") or (settings.ansible_ssh_user or "devops")
    port = _asset_field(asset, "ansible_port") or str(settings.ansible_ssh_port or 22)
    conn = _asset_field(asset, "ansible_connection")
    if settings.ansible_check_mode and not _asset_field(asset, "ansible_host", "ip", "ssh_host"):
        conn = conn or "local"
        host = "127.0.0.1"
    lines = [
        "# generated from CMDB asset; do not put secrets here",
        "[devops]",
        f"{hostname} ansible_host={host} ansible_user={user} ansible_port={port}",
    ]
    if conn:
        lines[-1] += f" ansible_connection={conn}"
    source = "asset-local" if conn == "local" else "asset"
    return "\n".join(lines) + "\n", source


def fallback_demo_inventory() -> tuple[str, str]:
    example = demo_inventory_example_path()
    if example.is_file():
        return example.read_text(encoding="utf-8"), "demo-example"
    text = (
        "[devops]\n"
        "order-app-01 ansible_host=127.0.0.1 ansible_connection=local ansible_user=devops\n"
    )
    return text, "demo-builtin"
