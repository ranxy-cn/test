from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings


@dataclass
class Playbook:
    id: str
    name: str
    version: str
    description: str
    risk: str
    auto_allowed: bool
    requires_db_ok: bool
    requires_reachable: bool
    restart_cooldown: bool
    allowed_params: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    success_criteria: str = ""
    stop_conditions: str = ""
    file: str = ""


class CatalogError(Exception):
    pass


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def load_catalog(playbooks_dir: str | None = None) -> dict[str, Playbook]:
    settings = get_settings()
    root = Path(playbooks_dir) if playbooks_dir else settings.resolved_playbooks_dir()
    index_path = root / "catalog.yaml"
    if not index_path.exists():
        raise CatalogError(f"找不到预案目录: {index_path}")
    index = _load_yaml(index_path)
    catalog: dict[str, Playbook] = {}
    for item in index.get("playbooks", []):
        path = root / item["file"]
        data = _load_yaml(path)
        pb = Playbook(
            id=data["id"],
            name=data["name"],
            version=str(data["version"]),
            description=data.get("description", ""),
            risk=data.get("risk", "high"),
            auto_allowed=bool(data.get("auto_allowed", False)),
            requires_db_ok=bool(data.get("requires_db_ok", True)),
            requires_reachable=bool(data.get("requires_reachable", True)),
            restart_cooldown=bool(data.get("restart_cooldown", False)),
            allowed_params=data.get("allowed_params") or {},
            steps=data.get("steps") or [],
            success_criteria=data.get("success_criteria", ""),
            stop_conditions=data.get("stop_conditions", ""),
            file=item["file"],
        )
        catalog[pb.id] = pb
    return catalog


def get_playbook(action_id: str) -> Playbook | None:
    return load_catalog().get(action_id)


def catalog_ids() -> set[str]:
    return set(load_catalog().keys())


def dump_catalog() -> list[dict[str, Any]]:
    rows = []
    for pb in load_catalog().values():
        rows.append(
            {
                "id": pb.id,
                "name": pb.name,
                "version": pb.version,
                "risk": pb.risk,
                "auto_allowed": pb.auto_allowed,
                "description": pb.description,
                "steps": [s.get("id") for s in pb.steps],
            }
        )
    return rows
