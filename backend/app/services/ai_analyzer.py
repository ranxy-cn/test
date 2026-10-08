"""AI 日志分析服务。

告警产生 →（联动触发，见 workers/tasks.analyze_anomaly_logs）→ 本服务完成：
日志上下文预处理 → OpenAI 兼容接口调用 → 响应解析 → 内容安全过滤 → 落库与审计。

安全红线：
- 请求仅包含日志/指标数据与问题描述，不含任何凭据（原始响应落库前经 redact_sensitive 脱敏）；
- AI 仅做文字性分析，响应命中服务器操作指令黑名单一律屏蔽并标记 blocked；
- 全过程写 AiAuditLog 审计。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import timedelta

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import AiAnalysis, AiAuditLog, AnomalyEvent, AnomalyLog, AppSetting
from app.services import metrics_store
from app.services.knowledge import redact_sensitive

log = logging.getLogger("devops.ai_analyzer")

CONFIG_KEY = "ai_analyzer_config"
# 告警级别 → 分析优先级（数字越小越优先）：P0/P1 关键告警插队，P2/P3 排后
SEVERITY_PRIORITY = {"P0": 0, "P1": 1, "P2": 6, "P3": 9}
DEFAULT_PRIORITY = 5
# 送审日志上下文上限（字符）：控制 token 成本与延迟
_CONTEXT_LIMIT = 6000

SYSTEM_PROMPT = (
    "你是运维日志分析助手。你只能做日志分析与文字性建议，绝对不能执行任何操作，"
    "也不能建议任何具体的服务器命令或脚本。只输出一个 JSON 对象，字段："
    '{"summary": "一句话结论", "diagnosis": "问题诊断（引用日志证据）", '
    '"causes": ["可能原因1", ...], '
    '"solutions": [{"title": "建议标题", "detail": "处理思路（文字描述，禁止命令）", '
    '"tag": "分类标签如 内存/磁盘/CPU/网络/进程/配置", "severity": "critical|high|medium|low"}], '
    '"severity": "critical|high|medium|low|info", "confidence": 0.0到1.0}。'
    "全部字段用中文，不要输出 JSON 以外的任何文字。"
)

# 服务器操作指令黑名单：命中即屏蔽（AI 无执行权限，这里只过滤文字性输出）
_BLOCK_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(p, re.IGNORECASE), tag)
    for p, tag in [
        (r"\brm\s+(-[a-z]+\s+)*-?[rf]", "rm 删除命令"),
        (r"\b(shutdown|reboot|halt|poweroff|init\s+0|init\s+6)\b", "关机/重启命令"),
        (r"\b(mkfs|fdisk|parted)\b", "磁盘分区/格式化命令"),
        (r"\bdd\s+if=", "dd 写盘命令"),
        (r":\(\)\s*\{.*\};\s*:", "fork 炸弹"),
        (r"\b(chmod|chown)\s+(-R\s+)?777\b", "chmod 777"),
        (r"\b(iptables|ufw|firewall-cmd)\s+", "防火墙命令"),
        (r"\b(systemctl|service)\s+\w+\s+(restart|stop|start)\b", "服务管理命令"),
        (r"\b(kill|pkill|killall)\s+(-9\s+)?\d", "进程查杀命令"),
        (r"\b(ssh|scp|sftp|nc|netcat|telnet)\s+\S+@", "远程连接命令"),
        (r"\b(eval|exec)\s*\(", "eval/exec 调用"),
        (r"\bbase64\s+-d\b", "base64 解码执行"),
        (r"\b(curl|wget)\s+https?://", "下载命令"),
        (r"\b(DROP|DELETE|TRUNCATE)\s+(TABLE|DATABASE|FROM)\b", "数据库破坏语句"),
        (r"\b(apt|yum|dnf|pip3?)\s+(install|remove|purge)\b", "软件包变更命令"),
        (r"\bcat\s+/etc/(shadow|passwd)\b", "敏感文件读取"),
    ]
]


# ---------------------------------------------------------------- 配置存取

def _fernet() -> Fernet:
    """由 JWT_SECRET（或 AI_ENCRYPT_KEY）派生 Fernet 密钥，用于 api_key 落库加密。"""
    import base64

    s = get_settings()
    raw = (getattr(s, "ai_encrypt_key", "") or s.jwt_secret or "devops-agent-fallback-key").encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))


def get_ai_config(db: Session) -> dict:
    """读取 AI 分析配置（api_key 已解密）。未配置时回退 settings 里的 chat/openai 默认值。"""
    s = get_settings()
    cfg = {
        "enabled": False,
        "base_url": (s.chat_base_url or s.openai_base_url or "").rstrip("/"),
        "api_key": s.chat_api_key or s.openai_api_key or "",
        "model": s.chat_model or s.openai_model or "gpt-4o-mini",
        "timeout_seconds": 60,
        "max_retries": 3,
    }
    row = db.get(AppSetting, CONFIG_KEY)
    if row and isinstance(row.value, dict):
        saved = dict(row.value)
        if saved.get("api_key_enc"):
            try:
                saved["api_key"] = _fernet().decrypt(saved["api_key_enc"].encode()).decode()
            except InvalidToken:
                log.warning("AI 配置 api_key 解密失败，回退默认 key")
        saved.pop("api_key_enc", None)
        for k, v in saved.items():
            if v not in (None, ""):
                cfg[k] = v
    cfg["timeout_seconds"] = int(cfg.get("timeout_seconds") or 60)
    cfg["max_retries"] = int(cfg.get("max_retries") or 3)
    cfg["enabled"] = bool(cfg.get("enabled"))
    return cfg


def save_ai_config(db: Session, data: dict, operator: str = "system") -> dict:
    """保存配置：api_key 用 Fernet 加密落库；返回脱敏视图。"""
    current = get_ai_config(db)
    enc = None
    if data.get("api_key"):
        enc = _fernet().encrypt(str(data["api_key"]).encode()).decode()
    elif data.get("keep_api_key") and current.get("api_key"):
        enc = _fernet().encrypt(current["api_key"].encode()).decode()

    saved = {
        "enabled": bool(data.get("enabled")),
        "base_url": str(data.get("base_url") or current.get("base_url") or "").rstrip("/"),
        "api_key_enc": enc or "",
        "model": str(data.get("model") or current.get("model") or ""),
        "timeout_seconds": int(data.get("timeout_seconds") or 60),
        "max_retries": int(data.get("max_retries") or 3),
    }
    row = db.get(AppSetting, CONFIG_KEY)
    if row is None:
        row = AppSetting(key=CONFIG_KEY, value={})
        db.add(row)
    row.value = saved
    db.add(
        AiAuditLog(
            action="config_update",
            operator=operator,
            detail={"enabled": saved["enabled"], "base_url": saved["base_url"], "model": saved["model"],
                    "timeout_seconds": saved["timeout_seconds"], "api_key_changed": bool(enc)},
        )
    )
    db.commit()
    return masked_config(get_ai_config(db))


def masked_config(cfg: dict) -> dict:
    """api_key 脱敏：仅展示末 4 位。"""
    key = cfg.get("api_key") or ""
    masked = {"enabled": cfg.get("enabled", False), "base_url": cfg.get("base_url", ""),
              "model": cfg.get("model", ""), "timeout_seconds": cfg.get("timeout_seconds", 60),
              "max_retries": cfg.get("max_retries", 3), "has_api_key": bool(key),
              "api_key_tail": key[-4:] if len(key) > 8 else ("****" if key else "")}
    return masked


# ---------------------------------------------------------------- 上下文预处理

def gather_context(db: Session, anomaly: AnomalyEvent) -> str:
    """提取告警相关关键日志片段并格式化：告警信息 + payload 上下文 + 诊断快照 + 通知留痕 + 指标摘要。"""
    parts: list[str] = []
    parts.append(f"## 告警\n- 主机: {anomaly.hostname or anomaly.host} ({anomaly.ip})\n"
                 f"- 触发器: {anomaly.trigger_name}\n- 级别: {anomaly.severity}\n- 消息: {anomaly.message}")
    payload = anomaly.payload or {}
    if payload:
        brief = {k: payload.get(k) for k in
                 ("metric", "threshold", "op", "latest", "window_seconds", "samples", "oom_detail",
                  "policy_source", "rule_id", "level", "window") if payload.get(k) is not None}
        parts.append("## 告警上下文\n" + json.dumps(brief, ensure_ascii=False))
    if payload.get("window_series"):
        pts = payload["window_series"]
        head = pts[:20] if len(pts) <= 40 else pts[:10] + [["..."], ] + pts[-10:]
        parts.append("## 窗口样本序列 (时间, 值)\n" + json.dumps(head, ensure_ascii=False))
    if anomaly.diagnostics:
        snap = dict(anomaly.diagnostics)
        if snap.get("procs"):
            snap["procs"] = snap["procs"][:10]
        parts.append("## 异常时刻进程快照\n" + json.dumps(snap, ensure_ascii=False)[:1500])
    logs = db.scalars(
        select(AnomalyLog).where(AnomalyLog.anomaly_id == anomaly.id).order_by(AnomalyLog.received_at.desc()).limit(5)
    ).all()
    if logs:
        entries = [{"action": lg.action, "at": lg.received_at.isoformat(timespec="seconds"),
                    "payload": {k: v for k, v in (lg.payload or {}).items()
                                if k in ("trigger_name", "severity", "message", "oom_detail", "threshold", "latest")}}
                   for lg in logs]
        parts.append("## 告警通知留痕\n" + json.dumps(entries, ensure_ascii=False)[:1500])
    if anomaly.asset_id:
        try:
            data = metrics_store.query_series(db, anomaly.asset_id, hours=0.5)
            series = {k: v[-15:] for k, v in (data.get("series") or {}).items() if v}
            if series:
                parts.append("## 最近 30 分钟关键指标\n" + json.dumps(series, ensure_ascii=False)[:1500])
        except Exception:
            log.exception("AI 分析取指标序列失败 anomaly_id=%s", anomaly.id)
    text = "\n\n".join(p for p in parts if p)
    if len(text) > _CONTEXT_LIMIT:
        text = text[:_CONTEXT_LIMIT] + "\n…(超长截断)"
    return text


def _context_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- 内容安全过滤

def sanitize_ai_text(text: str) -> tuple[str, list[str]]:
    """屏蔽响应中的服务器操作指令。返回 (清洗后文本, 命中标签列表)。"""
    hits: list[str] = []
    clean = text
    for pat, tag in _BLOCK_PATTERNS:
        if pat.search(clean):
            hits.append(tag)
            clean = pat.sub("【已屏蔽：疑似服务器操作指令】", clean)
    return clean, hits


def _sanitize_solution(sol: dict) -> tuple[dict, list[str]]:
    hits: list[str] = []
    out = dict(sol)
    for field in ("title", "detail"):
        cleaned, h = sanitize_ai_text(str(sol.get(field) or ""))
        out[field] = cleaned
        hits.extend(h)
    out["title"] = out["title"][:120]
    out["detail"] = out["detail"][:800]
    out["tag"] = str(sol.get("tag") or "通用")[:32]
    out["severity"] = str(sol.get("severity") or "medium")[:16]
    return out, hits


# ---------------------------------------------------------------- 响应解析

def parse_ai_response(text: str) -> dict:
    """解析 AI 响应为结构化结果：剥 markdown 代码块 → JSON → 字段规范化。容错抛 ValueError。"""
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start: end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("AI 响应不是 JSON 对象")

    sev = str(data.get("severity") or "medium").lower()
    if sev not in ("critical", "high", "medium", "low", "info"):
        sev = "medium"
    causes = [str(c)[:300] for c in (data.get("causes") or []) if str(c).strip()][:8]
    solutions = []
    for sol in (data.get("solutions") or [])[:8]:
        if isinstance(sol, dict) and str(sol.get("title") or "").strip():
            solutions.append(sol)
    try:
        confidence = min(1.0, max(0.0, float(data.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "summary": str(data.get("summary") or "")[:500],
        "diagnosis": str(data.get("diagnosis") or "")[:3000],
        "causes": causes,
        "solutions": solutions,
        "severity": sev,
        "confidence": round(confidence, 2),
    }


# ---------------------------------------------------------------- LLM 调用

def _chat(cfg: dict, context: str) -> tuple[str, str, int]:
    """调用 OpenAI 兼容接口。返回 (响应文本, 模型, 耗时ms)。请求体只含日志上下文与系统约束。"""
    base = (cfg.get("base_url") or "").rstrip("/")
    if base.endswith("/chat/completions"):
        url = base  # 兼容直接填完整端点的写法
    elif base.endswith("/v1") or "/v1/" in base:
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"
    body = {
        "model": cfg.get("model") or "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "以下是告警相关日志与监控数据，请按要求输出 JSON 分析结果：\n\n" + context},
        ],
        "temperature": 0,
        # 不带 response_format：部分网关（如 glm 中转）对该对象参数会挂起不响应，
        # JSON 输出由 SYSTEM_PROMPT 强约束 + parse_ai_response 兜底解析
    }
    headers = {"Authorization": f"Bearer {cfg.get('api_key')}"}
    t0 = time.monotonic()
    timeout = httpx.Timeout(float(cfg.get("timeout_seconds") or 60))
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"AI 响应非 JSON：{resp.text[:200]}") from exc
        # 兼容部分中转网关把标准响应包在 upstream 字段里
        if isinstance(data.get("upstream"), dict):
            data = data["upstream"]
    latency = int((time.monotonic() - t0) * 1000)
    text = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
    if not text:
        raise RuntimeError(f"AI 响应缺少内容：{str(data)[:200]}")
    model = data.get("model") or cfg.get("model") or ""
    return text, model, latency


def _mock_response(anomaly: AnomalyEvent, context: str) -> tuple[str, str, int]:
    """无 api_key 时的确定性演示输出（也用于无外部依赖的负载测试）。"""
    payload = anomaly.payload or {}
    threshold = payload.get("threshold")
    latest = payload.get("latest")
    oom = payload.get("oom_detail") or []
    cause = "内存耗尽触发内核 OOM killer 回收进程" if oom else f"指标越限（当前 {latest}，阈值 {threshold}）"
    text = json.dumps({
        "summary": f"{anomaly.hostname or anomaly.host} {anomaly.trigger_name}：AI（演示模式）判定 {cause}",
        "diagnosis": f"根据告警上下文与窗口样本，{anomaly.trigger_name} 于窗口期内越限。"
                     + (f"OOM 明细 {len(oom)} 条，最近一条：{oom[-1]}" if oom else "样本序列呈持续上升趋势。"),
        "causes": [cause, "业务负载突增或存在异常进程占用资源", "告警阈值配置可能偏低"],
        "solutions": [
            {"title": "确认资源占用进程", "detail": "登录主机查看资源占用最高的进程，确认是否为预期业务负载。", "tag": "进程", "severity": "high"},
            {"title": "评估扩容或限流", "detail": "若负载持续高于容量规划，建议扩容或对业务限流。", "tag": "容量", "severity": "medium"},
            {"title": "复核告警阈值", "detail": "结合历史基线复核告警阈值与窗口设置是否合理。", "tag": "配置", "severity": "low"},
        ],
        "severity": "high" if anomaly.severity in ("high", "P0", "P1") else "medium",
        "confidence": 0.55,
    }, ensure_ascii=False)
    return text, f"mock-{get_settings().chat_model or 'gpt-4o-mini'}", 15


# ---------------------------------------------------------------- 主流程

def priority_for(anomaly: AnomalyEvent) -> int:
    """告警级别 → AI 分析优先级映射。"""
    sev = (anomaly.severity or "").upper()
    if sev in SEVERITY_PRIORITY:
        return SEVERITY_PRIORITY[sev]
    return SEVERITY_PRIORITY.get({"critical": "P0", "high": "P1", "warning": "P2"}.get(sev.lower(), ""), DEFAULT_PRIORITY)


def run_analysis(anomaly_id: int, operator: str = "system") -> AiAnalysis | None:
    """执行一次 AI 日志分析（同步函数；由 celery 任务异步调用，也可手动触发重试）。"""
    db = SessionLocal()
    analysis: AiAnalysis | None = None
    try:
        anomaly = db.get(AnomalyEvent, anomaly_id)
        if anomaly is None:
            log.warning("AI 分析目标告警不存在 anomaly_id=%s", anomaly_id)
            return None
        # 幂等：已有进行中/完成的分析则跳过重复触发
        exists = db.scalar(select(AiAnalysis).where(AiAnalysis.anomaly_id == anomaly_id).order_by(AiAnalysis.id.desc()).limit(1))
        if exists and exists.status in ("pending", "running"):
            return exists

        cfg = get_ai_config(db)
        analysis = AiAnalysis(
            anomaly_id=anomaly_id, status="running",
            priority=exists.priority if exists else priority_for(anomaly),
        )
        db.add(analysis)
        anomaly.ai_status = "running"
        db.commit()

        if not cfg.get("enabled"):
            analysis.status = "skipped"
            anomaly.ai_status = "skipped"
            db.add(AiAuditLog(anomaly_id=anomaly_id, analysis_id=analysis.id, action="skipped",
                              operator=operator, ok=True, detail={"reason": "AI 分析未启用"}))
            db.commit()
            return analysis

        context = gather_context(db, anomaly)
        digest = _context_digest(context)
        try:
            if cfg.get("api_key"):
                raw_text, model, latency = _chat(cfg, context)
            else:
                raw_text, model, latency = _mock_response(anomaly, context)
            parsed = parse_ai_response(raw_text)
        except Exception as exc:  # 失败落库 + 审计，交由 celery 重试
            analysis.status = "failed"
            analysis.error = str(exc)[:1000]
            analysis.model = cfg.get("model") or ""
            anomaly.ai_status = "failed"
            db.add(AiAuditLog(anomaly_id=anomaly_id, analysis_id=analysis.id, action="failed",
                              operator=operator, ok=False, model=cfg.get("model") or "",
                              detail={"error": str(exc)[:500], "retries_left": cfg.get("max_retries")}))
            db.commit()
            log.exception("AI 日志分析失败 anomaly_id=%s", anomaly_id)
            raise

        # 内容安全过滤：任何字段命中操作指令 → 整体标记 blocked 并屏蔽命中片段
        all_hits: list[str] = []
        parsed["summary"], h = sanitize_ai_text(parsed["summary"])
        all_hits += h
        parsed["diagnosis"], h = sanitize_ai_text(parsed["diagnosis"])
        all_hits += h
        parsed["causes"] = []
        sols = []
        for sol in parsed["solutions"]:
            s, h = _sanitize_solution(sol)
            sols.append(s)
            all_hits += h
        parsed["solutions"] = sols
        blocked = bool(all_hits)

        analysis.status = "blocked" if blocked else "done"
        analysis.severity = parsed["severity"]
        analysis.summary = parsed["summary"]
        analysis.diagnosis = parsed["diagnosis"]
        analysis.causes = parsed["causes"]
        analysis.solutions = parsed["solutions"]
        analysis.confidence = parsed["confidence"]
        analysis.model = model
        analysis.latency_ms = latency
        analysis.blocked = blocked
        analysis.raw_response = redact_sensitive(raw_text)[:8000]
        analysis.context_digest = digest
        anomaly.ai_status = analysis.status
        db.add(AiAuditLog(anomaly_id=anomaly_id, analysis_id=analysis.id,
                          action="blocked" if blocked else "success",
                          operator=operator, ok=True, model=model, latency_ms=latency, blocked=blocked,
                          detail={"blocked_hits": all_hits, "severity": parsed["severity"],
                                  "solutions": len(sols), "context_digest": digest}))
        db.commit()
        log.info("AI 日志分析完成 anomaly_id=%s status=%s model=%s latency=%sms blocked=%s",
                 anomaly_id, analysis.status, model, latency, blocked)
        return analysis
    except Exception:
        db.rollback()
        log.exception("AI 分析流程异常 anomaly_id=%s", anomaly_id)
        try:
            if analysis is not None and analysis.id:
                db.rollback()
                fresh = db.get(AiAnalysis, analysis.id)
                if fresh is not None and fresh.status == "running":
                    fresh.status = "failed"
                    fresh.error = "分析流程内部错误"
                    db.add(fresh)
                anomaly2 = db.get(AnomalyEvent, anomaly_id)
                if anomaly2 is not None:
                    anomaly2.ai_status = "failed"
                db.add(AiAuditLog(anomaly_id=anomaly_id, action="failed", operator=operator, ok=False,
                                  detail={"error": "internal"}))
                db.commit()
        except Exception:
            db.rollback()
            log.exception("AI 分析失败状态落库失败 anomaly_id=%s", anomaly_id)
        raise
    finally:
        db.close()
