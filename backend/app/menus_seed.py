"""菜单/按钮资源树种子数据。

迁移 0003 与 seed_menus() 共用本定义，保证两边一致。
树形约定：
- type=dir   目录（仅分组）
- type=menu  页面菜单（path=前端路由，perm_code=进入页面所需权限点）
- type=button 页面按钮（perm_code=按钮所需权限点，前端 v-perm 依据）
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Menu, RoleMenu

# (code, name, type, path, perm_code, icon, sort, [children])
MENU_TREE: list[tuple] = [
    ("menu:anomalies", "异常告警", "menu", "/anomalies", "anomalies:read", "warning", 5, []),
    ("menu:tickets", "任务单", "menu", "/tickets", "tickets:read", "tickets", 10, [
        ("btn:ticket-approve", "审批通过/驳回", "button", "", "tickets:operate", "", 10, []),
        ("btn:ticket-retry", "重试执行", "button", "", "tickets:operate", "", 20, []),
        ("btn:ticket-webhook", "模拟告警接入", "button", "", "tickets:operate", "", 30, []),
    ]),
    ("menu:employee", "数字员工", "menu", "/employee", "catalog:read", "user", 20, []),
    ("menu:assets", "资产台账", "menu", "/assets", "assets:read", "grid", 30, []),
    ("menu:backups", "备份管理", "menu", "/backups", "backups:read", "box", 40, [
        ("btn:backup-run", "立即备份", "button", "", "backups:operate", "", 10, []),
        ("btn:backup-verify", "恢复演练", "button", "", "backups:operate", "", 20, []),
    ]),
    ("menu:notifications", "通知中心", "menu", "/notifications", "notifications:read", "bell", 50, [
        ("btn:notification-read", "标记已读", "button", "", "notifications:read", "", 10, []),
    ]),
    ("menu:report", "运维日报", "menu", "/report", "reports:read", "document", 60, []),
    ("menu:status", "集成状态", "menu", "/status", "status:read", "connection", 70, [
        ("btn:stress-start", "启动压测", "button", "", "tools:operate", "", 10, []),
        ("btn:stress-stop", "停止压测", "button", "", "tools:operate", "", 20, []),
    ]),
    ("menu:system", "系统管理", "dir", "", "", "setting", 90, [
        ("menu:users", "用户管理", "menu", "/users", "users:manage", "user-filled", 10, []),
        ("menu:roles", "角色权限", "menu", "/roles", "roles:manage", "key", 20, []),
        ("menu:menus", "菜单管理", "menu", "/menus", "menus:manage", "menu", 30, []),
    ]),
]


def flatten(tree: list[tuple], parent: Menu | None = None) -> list[tuple[str, Menu]]:
    """把 MENU_TREE 展开为 [(code, Menu)]，返回已有同名节点不重建。"""
    out: list[tuple[str, Menu]] = []
    for code, name, mtype, path, perm, icon, sort, children in tree:
        menu = Menu(
            parent_id=parent.id if parent else None,
            code=code,
            name=name,
            type=mtype,
            path=path or None,
            perm_code=perm or None,
            icon=icon,
            sort_order=sort,
            visible=True,
            status="enabled",
        )
        out.append((code, menu))
        out.extend(flatten(children, menu))
    return out


def seed_menus(db: Session) -> dict[str, Menu]:
    """幂等创建菜单树，返回 code→Menu 映射（不删除库中已有节点）。

    逐级插入：父节点先 flush 拿到 id，子节点才能正确挂 parent_id。
    对历史数据中 parent_id 为空的非顶级节点做自愈修正。
    """
    existing = {m.code: m for m in db.scalars(select(Menu)).all()}

    def _insert(nodes: list[tuple], parent: Menu | None) -> None:
        for code, name, mtype, path, perm, icon, sort, children in nodes:
            menu = existing.get(code)
            if menu is None:
                menu = Menu(
                    parent_id=parent.id if parent else None,
                    code=code,
                    name=name,
                    type=mtype,
                    path=path or None,
                    perm_code=perm or None,
                    icon=icon,
                    sort_order=sort,
                    visible=True,
                    status="enabled",
                )
                db.add(menu)
                db.flush()
                existing[code] = menu
            elif menu.parent_id is None and parent is not None:
                # 自愈：历史数据父节点为空的非顶级节点
                menu.parent_id = parent.id
            _insert(children, menu)

    _insert(MENU_TREE, None)
    db.flush()
    return existing


def grant_tree_to_role(db: Session, role_id: int, menus: dict[str, Menu], codes: list[str]) -> None:
    """给角色授予一组菜单节点（精确集合，不做子孙展开；前端勾选时已级联），覆盖式替换该角色现有授权。"""
    wanted: set[int] = set()
    for code in codes:
        menu = menus.get(code)
        if menu is not None:
            wanted.add(menu.id)
    for row in list(db.scalars(select(RoleMenu).where(RoleMenu.role_id == role_id)).all()):
        db.delete(row)
    db.flush()
    for mid in wanted:
        db.add(RoleMenu(role_id=role_id, menu_id=mid))


def _subtree_codes(root_code: str) -> set[str]:
    """收集某节点及其全部子孙的 code（用于整棵子树过滤）。"""
    out: set[str] = set()

    def walk(nodes: list[tuple], active: bool) -> None:
        for code, *_rest, children in nodes:
            hit = active or code == root_code
            if hit:
                out.add(code)
            walk(children, hit)

    walk(MENU_TREE, False)
    return out


SYSTEM_MENU_CODES = _subtree_codes("menu:system")


def role_default_menu_codes(role_code: str) -> list[str]:
    """角色初始菜单分配：admin 全部 / operator 业务菜单+按钮 / viewer 仅业务菜单（无操作按钮）。

    operator/viewer 均不含「系统管理」子树（用户/角色/菜单管理仅 admin 可见）。
    """
    if role_code == "admin":
        return [code for code, *_ in flatten(MENU_TREE)]
    if role_code == "operator":
        return [code for code, *_ in flatten(MENU_TREE) if code not in SYSTEM_MENU_CODES]
    # viewer：业务菜单，不含 button 节点
    return [
        code
        for code, menu in flatten(MENU_TREE)
        if menu.type in ("dir", "menu") and code not in SYSTEM_MENU_CODES
    ]
