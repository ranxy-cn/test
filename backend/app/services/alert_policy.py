"""告警策略：模板级 CPU/内存/负载触发器的阈值与采集窗口。

修改的是 Zabbix 监控模板（Template OS Linux by Zabbix agent 及其 link 的子模块）
上的配置：阈值 = 模板用户宏，窗口 = 触发器表达式里的 min(Nm) 参数。
母机与所有通过自动注册接入的子机共享这套模板，因此策略一次修改、全网统一生效。
"""

from __future__ import annotations

import re
from typing import Any

# 平台母机部署/自动注册统一挂的 Linux 监控模板（壳模板，触发器分布在 link 的子模块上）
SHIM_TEMPLATE = "Template OS Linux by Zabbix agent"

# 三个核心告警的定位信息：按宏名找阈值，按 item key + min 函数找窗口
SPECS: list[dict[str, str]] = [
    {
        "key": "cpu",
        "label": "CPU",
        "macro": "{$CPU.UTIL.CRIT}",
        "item_key": "system.cpu.util",
        "threshold": "cpu_threshold",
        "window": "cpu_window_minutes",
    },
    {
        "key": "mem",
        "label": "内存",
        "macro": "{$MEMORY.UTIL.MAX}",
        "item_key": "vm.memory.utilization",
        "threshold": "mem_threshold",
        "window": "mem_window_minutes",
    },
    {
        "key": "load",
        "label": "负载",
        "macro": "{$LOAD_AVG_PER_CPU.MAX.WARN}",
        "item_key": "system.cpu.load[all,avg1]",
        "threshold": "load_threshold",
        "window": "load_window_minutes",
    },
]

DEFAULT_POLICY: dict[str, float] = {
    "cpu_threshold": 90,
    "cpu_window_minutes": 5,
    "mem_threshold": 90,
    "mem_window_minutes": 5,
    "load_threshold": 1.5,
    "load_window_minutes": 5,
}


def normalize_policy(data: dict | None) -> dict:
    """校验并规范化策略字段；缺失字段补默认值，非法值抛 ValueError。"""
    policy = dict(DEFAULT_POLICY)
    for key, value in (data or {}).items():
        if key in policy:
            policy[key] = value
    for name in ("cpu_threshold", "mem_threshold", "cpu_window_minutes", "mem_window_minutes", "load_window_minutes"):
        try:
            policy[name] = int(policy[name])
        except (TypeError, ValueError):
            raise ValueError(f"告警策略字段 {name} 必须是整数") from None
    try:
        policy["load_threshold"] = round(float(policy["load_threshold"]), 2)
    except (TypeError, ValueError):
        raise ValueError("告警策略字段 load_threshold 必须是数字") from None
    for name in ("cpu_threshold", "mem_threshold"):
        if not 1 <= policy[name] <= 99:
            raise ValueError(f"{name} 取值范围 1~99")
    if not 0.1 <= policy["load_threshold"] <= 100:
        raise ValueError("load_threshold 取值范围 0.1~100")
    for name in ("cpu_window_minutes", "mem_window_minutes", "load_window_minutes"):
        if not 1 <= policy[name] <= 120:
            raise ValueError(f"{name} 取值范围 1~120 分钟")
    return policy


def _template_ids(zc) -> list[str]:
    """壳模板 + 它 link 的全部子模块模板 id。"""
    rows = zc._rpc("template.get", {"output": ["templateid"], "filter": {"host": SHIM_TEMPLATE}})
    if not rows:
        raise RuntimeError(f"未找到监控模板 {SHIM_TEMPLATE}")
    shim_id = str(rows[0]["templateid"])
    row = zc._rpc(
        "template.get", {"templateids": [shim_id], "output": ["templateid"], "selectParentTemplates": ["templateid"]}
    )[0]
    return [shim_id] + [str(t["templateid"]) for t in row.get("parentTemplates", [])]


def _template_hosts(zc, ids: list[str]) -> dict[str, str]:
    """模板 id -> 技术名（5.0 template.get 主键是 templateid，没有 hostid 字段）。"""
    rows = zc._rpc("template.get", {"templateids": ids, "output": ["templateid", "host"]})
    return {str(r["templateid"]): str(r["host"]) for r in rows}


def _trigger_functions(zc, ids: list[str]) -> tuple[list[dict], dict[str, str]]:
    """子模块模板各自的触发器（tag _t_host = 物理所在模板技术名）与 itemid->key 映射。

    5.0 的 trigger.get 不返回所属模板字段，且模板 link 是实体克隆：壳模板上的触发器
    是继承副本，description/expression 等字段被继承锁定（改了会报 Cannot update ...），
    只能改子模块上的原件，Zabbix 自动传播回克隆。因此跳过 ids[0]（壳模板），
    只遍历子模块，并用该模板自己的技术名还原展开表达式。
    """
    host_by_id = _template_hosts(zc, ids)
    triggers: list[dict] = []
    for tid in ids[1:]:
        rows = zc._rpc(
            "trigger.get",
            {"templateids": [tid], "output": ["triggerid", "expression"], "selectFunctions": "extend"},
        )
        for t in rows if isinstance(rows, list) else []:
            t["_t_host"] = host_by_id.get(str(tid), SHIM_TEMPLATE)
            triggers.append(t)
    items = zc._rpc("item.get", {"hostids": ids, "output": ["itemid", "key_"]})
    key_by_id = {str(i["itemid"]): str(i["key_"]) for i in items}
    return triggers, key_by_id


def read_applied_policy(zc) -> dict:
    """读模板上当前生效的阈值（宏值）与窗口（min 函数参数）。客户端须已登录。"""
    ids = _template_ids(zc)
    macros: dict[str, str] = {}
    rows = zc._rpc("usermacro.get", {"hostids": ids, "output": ["macro", "value"]})
    for r in rows:
        macros.setdefault(str(r["macro"]), str(r["value"]))
    triggers, key_by_id = _trigger_functions(zc, ids)
    applied: dict[str, Any] = {}
    for spec in SPECS:
        applied[spec["threshold"]] = _to_num(macros.get(spec["macro"]))
        window_param = ""
        for t in triggers:
            for f in t.get("functions", []):
                if (
                    key_by_id.get(str(f.get("itemid")), "") == spec["item_key"]
                    and f.get("function") == "min"
                    and f.get("parameter")
                ):
                    window_param = str(f["parameter"])
        applied[spec["window"]] = _parse_window_minutes(window_param)
    return applied


def apply_policy(zc, policy: dict) -> dict:
    """把策略应用到模板：改模板宏（阈值）+ 重写触发器窗口表达式。

    需要只读客户端？不——必须传入 readonly=False 的客户端（允许 usermacro/trigger 写方法）。
    返回 {"template_ids": [...], "changes": ["CPU 阈值 -> 90", ...]}。
    """
    ids = _template_ids(zc)
    triggers, key_by_id = _trigger_functions(zc, ids)
    changes: list[str] = []

    # 1) 阈值：模板宏
    for spec in SPECS:
        want = str(policy[spec["threshold"]])
        rows = zc._rpc(
            "usermacro.get", {"hostids": ids, "output": ["hostmacroid", "macro", "value"], "filter": {"macro": spec["macro"]}}
        )
        if rows:
            row = rows[0]
            if str(row["value"]) != want:
                zc._rpc("usermacro.update", {"hostmacroid": row["hostmacroid"], "value": want})
                changes.append(f"{spec['label']}阈值 -> {want}")
        else:
            zc._rpc(
                "usermacro.create", {"hostid": ids[0], "macro": spec["macro"], "value": want}
            )
            changes.append(f"{spec['label']}阈值宏创建 = {want}")

    # 2) 窗口：模板触发器表达式里的 min(Nm) 参数
    for t in triggers:
        spec, min_func = _locate_spec(t, key_by_id)
        if spec is None or min_func is None:
            continue
        want_param = f"{int(policy[spec['window']])}m"
        if str(min_func["parameter"]) == want_param:
            continue
        t_host = t.get("_t_host") or SHIM_TEMPLATE
        new_expr = _rebuild_expression(t_host, t, key_by_id, spec, want_param)
        full = zc._rpc("trigger.get", {"triggerids": [t["triggerid"]], "output": "extend"})[0]
        body = {
            "triggerid": t["triggerid"],
            # 模板触发器 update 必须带上完整元数据，只传 triggerid+expression 会被拒
            **{k: full[k] for k in ("description", "priority", "comments", "url", "status", "type", "manual_close") if k in full},
            "expression": new_expr,
        }
        zc._rpc("trigger.update", body)
        changes.append(f"{spec['label']}窗口 -> {want_param}")
    return {"template_ids": ids, "changes": changes}


def _locate_spec(trigger: dict, key_by_id: dict[str, str]) -> tuple[dict | None, dict | None]:
    """定位该触发器属于哪个告警 spec：存在 min 函数且 item key 与 spec 匹配。"""
    for f in trigger.get("functions", []):
        key = key_by_id.get(str(f.get("itemid")), "")
        if f.get("function") != "min" or not f.get("parameter"):
            continue
        for spec in SPECS:
            if key == spec["item_key"]:
                return spec, f
    return None, None


def _rebuild_expression(t_host: str, trigger: dict, key_by_id: dict[str, str], spec: dict, want_param: str) -> str:
    """压缩表达式 {N} 还原为 {模板host:key.func(param)}；仅目标 item 的 min 参数替换为新窗口。"""
    fmap: dict[str, str] = {}
    for f in trigger["functions"]:
        key = key_by_id.get(str(f.get("itemid")), "")
        func = str(f.get("function") or "last")
        param = str(f.get("parameter") or "0")
        if func == "min" and key == spec["item_key"]:
            param = want_param
        fmap[str(f["functionid"])] = "{%s:%s.%s(%s)}" % (t_host, key, func, param)
    return re.sub(r"\{(\d+)\}", lambda m: fmap.get(m.group(1), m.group(0)), str(trigger.get("expression") or ""))


def _parse_window_minutes(param: str) -> int | None:
    m = re.fullmatch(r"(\d+)([smhd])", (param or "").strip())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    return {"s": max(1, value // 60), "m": value, "h": value * 60, "d": value * 1440}.get(unit)


def _to_num(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
