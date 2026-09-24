#!/usr/bin/env python3
"""在 Zabbix 5.0 上幂等配置「DevOps 异常推送」全链路：

1. 媒介类型（Webhook）：POST 到 DevOps /api/v1/webhooks/zabbix，携带 X-Webhook-Secret，
   PROBLEM 与 RESOLVED 两种通知共用同一入口（后端按 EVENT.VALUE 归一为 异常/恢复 两态）。
2. 触发器动作：PROBLEM 发送 + 恢复操作（RESOLVED）发送，条件为目标主机。
3. Admin 用户 Media：绑定该媒介类型（全级别、全天生效）。

凭据只读环境变量（不写入仓库）：
  ZABBIX_URL（默认线上 http://124.221.251.186:8081/api_jsonrpc.php）
  ZABBIX_USER（默认 Admin）
  ZABBIX_PASSWORD（必填）
可选：DEVOPS_WEBHOOK_URL / WEBHOOK_SECRET / ZABBIX_HOST_ID
用法：ZABBIX_PASSWORD=*** python3 scripts/configure_zabbix_webhook.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ZABBIX_URL = (os.environ.get("ZABBIX_URL") or "http://124.221.251.186:8081/api_jsonrpc.php").strip()
ZABBIX_USER = (os.environ.get("ZABBIX_USER") or "Admin").strip()
ZABBIX_PASSWORD = (os.environ.get("ZABBIX_PASSWORD") or "").strip()
DEVOPS_URL = (os.environ.get("DEVOPS_WEBHOOK_URL") or "http://124.221.251.186:8000/api/v1/webhooks/zabbix").strip()
WEBHOOK_SECRET = (os.environ.get("WEBHOOK_SECRET") or "dev-webhook-secret").strip()
HOST_ID = (os.environ.get("ZABBIX_HOST_ID") or "10084").strip()

MEDIA_NAME = "DevOps Anomaly Webhook"
ACTION_NAME = "DevOps anomaly notify"

MEDIA_SCRIPT = """
try {
    var params = JSON.parse(value),
        req = new CurlHttpRequest(),
        payload = {
            'EVENT.ID': params.EVENT_ID,
            'EVENT.NAME': params.EVENT_NAME,
            'EVENT.STATUS': params.EVENT_STATUS,
            'EVENT.VALUE': params.EVENT_VALUE,
            'EVENT.SEVERITY': params.EVENT_SEVERITY,
            'EVENT.NSEVERITY': params.EVENT_NSEVERITY,
            'HOST.ID': params.HOST_ID,
            'HOST.HOST': params.HOST_HOST,
            'HOST.NAME': params.HOST_NAME,
            'HOST.IP': params.HOST_IP,
            'TRIGGER.ID': params.TRIGGER_ID,
            'TRIGGER.NAME': params.TRIGGER_NAME,
            'EVENT.DATE': params.EVENT_DATE,
            'EVENT.TIME': params.EVENT_TIME
        },
        response;
    req.AddHeader('Content-Type: application/json');
    req.AddHeader('X-Webhook-Secret: ' + params.SECRET);
    response = req.Post(params.DEVOPS_URL, JSON.stringify(payload));
    Zabbix.Log(4, '[DevOps webhook] HTTP ' + req.Status() + ' ' + response);
    if (req.Status() !== 200) {
        throw 'DevOps webhook HTTP ' + req.Status() + ': ' + response;
    }
    return 'OK ' + response;
} catch (err) {
    Zabbix.Log(3, '[DevOps webhook] failed: ' + err);
    throw 'DevOps webhook failed: ' + err;
}
""".strip()

MEDIA_PARAMS = [
    {"name": "DEVOPS_URL", "value": DEVOPS_URL},
    {"name": "SECRET", "value": WEBHOOK_SECRET},
    {"name": "EVENT_ID", "value": "{EVENT.ID}"},
    {"name": "EVENT_NAME", "value": "{EVENT.NAME}"},
    {"name": "EVENT_STATUS", "value": "{EVENT.STATUS}"},
    {"name": "EVENT_VALUE", "value": "{EVENT.VALUE}"},
    {"name": "EVENT_SEVERITY", "value": "{EVENT.SEVERITY}"},
    {"name": "EVENT_NSEVERITY", "value": "{EVENT.NSEVERITY}"},
    {"name": "HOST_ID", "value": "{HOST.ID}"},
    {"name": "HOST_HOST", "value": "{HOST.HOST}"},
    {"name": "HOST_NAME", "value": "{HOST.NAME}"},
    {"name": "HOST_IP", "value": "{HOST.IP}"},
    {"name": "TRIGGER_ID", "value": "{TRIGGER.ID}"},
    {"name": "TRIGGER_NAME", "value": "{TRIGGER.NAME}"},
    {"name": "EVENT_DATE", "value": "{EVENT.DATE}"},
    {"name": "EVENT_TIME", "value": "{EVENT.TIME}"},
]

TOKEN: str | None = None
_req_id = 0


def rpc(method: str, params: dict | None = None, *, auth: bool = True):
    global _req_id
    _req_id += 1
    payload: dict = {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": _req_id}
    if auth and TOKEN:
        payload["auth"] = TOKEN
    req = urllib.request.Request(ZABBIX_URL, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method}: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    if "error" in data:
        raise RuntimeError(f"{method}: {data['error']}")
    return data.get("result")


def ensure_mediatype() -> str:
    rows = rpc("mediatype.get", {"filter": {"name": MEDIA_NAME}}) or []
    body = {
        "name": MEDIA_NAME,
        "type": 4,  # webhook
        "script": MEDIA_SCRIPT,
        "parameters": MEDIA_PARAMS,
        "timeout": "30s",
        "description": "异常/恢复两态推送 DevOps（PROBLEM 与 RESOLVED 共用入口）",
        # 5.0 webhook 媒介必须定义消息模板，否则发送时报 "No message defined for media type"
        "message_templates": [
            {
                "eventsource": 0,
                "recovery": 0,
                "subject": "DevOps 异常: {TRIGGER.NAME}",
                "message": "EVENT.ID={EVENT.ID} HOST.ID={HOST.ID} HOST={HOST.HOST} IP={HOST.IP}\n{EVENT.NAME}\nSEVERITY={EVENT.SEVERITY}({EVENT.NSEVERITY})",
            },
            {
                "eventsource": 0,
                "recovery": 1,
                "subject": "DevOps 恢复: {TRIGGER.NAME}",
                "message": "EVENT.ID={EVENT.ID} HOST.ID={HOST.ID} HOST={HOST.HOST} IP={HOST.IP}\n{EVENT.NAME}",
            },
        ],
    }
    if rows:
        mt_id = rows[0]["mediatypeid"]
        rpc("mediatype.update", {**body, "mediatypeid": mt_id})
        print(f"媒介类型已更新: {MEDIA_NAME} (id={mt_id})")
    else:
        mt_id = rpc("mediatype.create", body)["mediatypeids"][0]
        print(f"媒介类型已创建: {MEDIA_NAME} (id={mt_id})")
    return str(mt_id)


def ensure_action(mt_id: str) -> str:
    uid = _admin_id()
    opmessage = {"default_msg": 1, "mediatypeid": mt_id}
    operations = [{
        "operationtype": 0,  # 发送消息
        "esc_period": "0",
        "opmessage": opmessage,
        "opmessage_usr": [{"userid": uid}],
    }]
    recovery_operations = [{
        "operationtype": 0,  # 发送恢复消息
        "opmessage": opmessage,
        "opmessage_usr": [{"userid": uid}],
    }]
    flt = {"evaltype": 0, "conditions": [{"conditiontype": 1, "operator": 0, "value": HOST_ID}]}  # 主机=10084
    body = {
        "eventsource": 0,  # trigger 事件源（5.0 字段名无下划线）
        "esc_period": "1h",
        "filter": flt,
        "operations": operations,
        "recovery_operations": recovery_operations,
        "status": 0,
    }
    rows = rpc("action.get", {"filter": {"name": ACTION_NAME}, "eventsource": 0}) or []
    if rows:
        action_id = rows[0]["actionid"]
        upd = {k: v for k, v in body.items() if k != "eventsource"}  # eventsource 创建后不可改
        rpc("action.update", {**upd, "actionid": action_id})
        print(f"动作已更新: {ACTION_NAME} (id={action_id})")
    else:
        action_id = rpc("action.create", {**body, "name": ACTION_NAME})["actionids"][0]
        print(f"动作已创建: {ACTION_NAME} (id={action_id})")
    return str(action_id)


_ADMIN: str | None = None


def _admin_id() -> str:
    global _ADMIN
    if _ADMIN is None:
        rows = rpc("user.get", {"filter": {"alias": ZABBIX_USER}}) or []
        if not rows:
            raise RuntimeError(f"找不到用户 {ZABBIX_USER}")
        _ADMIN = rows[0]["userid"]
    return _ADMIN


def ensure_user_media(mt_id: str) -> None:
    uid = _admin_id()
    current = rpc("user.get", {"userids": uid, "selectMedia": "extend"}) or []
    media = [m for m in (current[0].get("media") or []) if m.get("mediatypeid") != mt_id]
    media.append({
        "mediatypeid": mt_id,
        "sendto": "devops-agent",
        "active": 0,  # 启用
        "severity": 63,  # 全部级别
        "period": "1-7,00:00-24:00",
    })
    # 5.0 用户媒介通过 user.update 的 user_medias 参数整体替换
    rpc("user.update", {"userid": uid, "user_medias": media})
    print(f"用户 Media 已绑定: {ZABBIX_USER} ← {MEDIA_NAME}")


def main() -> int:
    global TOKEN
    if not ZABBIX_PASSWORD:
        print("请设置 ZABBIX_PASSWORD 环境变量（凭据不写入仓库）")
        return 2
    TOKEN = rpc("user.login", {"user": ZABBIX_USER, "password": ZABBIX_PASSWORD}, auth=False)
    try:
        version = rpc("apiinfo.version", {}, auth=False)
        print(f"Zabbix API: {ZABBIX_URL} version={version}")
        mt_id = ensure_mediatype()
        action_id = ensure_action(mt_id)
        ensure_user_media(mt_id)
        print("全部就绪：异常(PROBLEM)→条目异常，恢复(RESOLVED)→条目恢复。")
        print(f"验证入口: {DEVOPS_URL}")
    finally:
        try:
            rpc("user.logout", {}, auth=True)
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"失败: {exc}")
        raise SystemExit(1) from exc
