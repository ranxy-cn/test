from __future__ import annotations

import json
import io
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import KnowledgeDocument


_SENSITIVE_PATTERNS = (
    (re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"), r"\1: [REDACTED]"),
    (re.compile(r"-----BEGIN [^-]+ PRIVATE KEY-----[\s\S]+?-----END [^-]+ PRIVATE KEY-----"), "[REDACTED PRIVATE KEY]"),
    (re.compile(r"(?i)\bsk-[a-z0-9_-]{12,}\b"), "[REDACTED API KEY]"),
)


def redact_sensitive(text: str) -> str:
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _terms(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())


def _snippet(content: str, query: str, length: int = 260) -> str:
    clean = re.sub(r"\s+", " ", content).strip()
    if not query:
        return clean[:length]
    positions = [clean.lower().find(term.lower()) for term in _terms(query) if term]
    positions = [position for position in positions if position >= 0]
    start = max(0, min(positions) - 70) if positions else 0
    return clean[start : start + length]


def _score(content: str, query: str) -> int:
    haystack = content.lower()
    return sum(haystack.count(term) for term in _terms(query))


def extract_text(filename: str, payload: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if extension == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                if archive.getinfo("word/document.xml").file_size > 8 * 1024 * 1024:
                    return ""
                xml = archive.read("word/document.xml")
            root = ElementTree.fromstring(xml)
            paragraphs = []
            for paragraph in root.iter():
                if paragraph.tag.rsplit("}", 1)[-1] == "p":
                    text = "".join(node.text or "" for node in paragraph.iter() if node.tag.rsplit("}", 1)[-1] == "t")
                    if text.strip():
                        paragraphs.append(text.strip())
            return "\n".join(paragraphs)
        except Exception:
            return ""
    if extension == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(payload))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return ""
    for encoding in ("utf-8", "gb18030", "utf-16"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="ignore")


def _normalize_base_url(url: str) -> str:
    """规范化网关地址：剥掉误填的 /chat/completions 后缀（调用处会自行拼接完整端点）。"""
    return re.sub(r"/chat/completions/?$", "", url.rstrip("/"))


def _chat_config(db: Session | None = None) -> tuple[str, str, str, int]:
    """智能对话/知识分析的模型配置：全局 AI 服务配置（系统管理→AI 服务配置，AppSetting 加密存储）。

    传 db 时优先读取平台全局配置；未配置时 get_ai_config 内部回退 settings 的
    chat/openai 默认值。返回 (api_key, base_url, model, timeout_seconds)。
    """
    fallback = get_settings()
    cfg: dict[str, Any] = {
        "api_key": fallback.chat_api_key or fallback.openai_api_key or "",
        "base_url": _normalize_base_url(fallback.chat_base_url or fallback.openai_base_url or ""),
        "model": (fallback.chat_model if fallback.chat_api_key else fallback.openai_model) or "gpt-4o-mini",
        "timeout_seconds": fallback.chat_timeout_seconds,
    }
    if db is not None:
        try:
            from app.services.ai_analyzer import get_ai_config

            cfg = get_ai_config(db)
        except Exception:
            pass
    return (
        cfg.get("api_key") or "",
        _normalize_base_url(cfg.get("base_url") or ""),
        cfg.get("model") or "gpt-4o-mini",
        int(cfg.get("timeout_seconds") or 60),
    )


def chat_model_name(db: Session | None = None) -> str:
    """当前对话实际使用的模型名（全局 AI 服务配置优先，供前端展示/审计）。"""
    return _chat_config(db)[2]


def chat_completion(messages: list[dict[str, str]], max_tokens: int = 1200, db: Session | None = None) -> str:
    api_key, base_url, model, timeout = _chat_config(db)
    if not api_key:
        raise RuntimeError("未配置 AI API Key，请在「系统管理 → AI 服务配置」中填写并启用")
    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2},
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"].get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("AI 网关未返回有效回答")
    return content


def analyze_document(title: str, content: str) -> dict[str, Any]:
    prompt = (
        "请分析下面的企业运维文档，输出严格 JSON，不要 Markdown。字段：summary（不超过120字）、"
        "tags（3-8个中文标签数组）、rules（适用的运维规则数组）。只根据文档内容，不要编造。\n"
        f"文档标题：{title}\n文档内容：\n{redact_sensitive(content[:18000])}"
    )
    try:
        raw = chat_completion(
            [
                {"role": "system", "content": "你是企业运维知识库整理助手。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
            db=db,
        )
        raw = raw.strip().removeprefix("```").removeprefix("json").removesuffix("```").strip()
        data = json.loads(raw)
        return {
            "summary": str(data.get("summary") or content[:180].replace("\n", " "))[:1000],
            "tags": [str(tag) for tag in data.get("tags", [])][:8],
            "rules": [str(rule) for rule in data.get("rules", [])][:20],
            "ai_analyzed": True,
        }
    except Exception:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        tags = []
        for keyword in ("CPU", "内存", "磁盘", "数据库", "网络", "备份", "告警", "发布"):
            if keyword.lower() in content.lower():
                tags.append(keyword)
        return {
            "summary": " ".join(lines[:3])[:1000] or "已上传运维文档，等待补充分析。",
            "tags": tags[:8],
            "rules": [],
            "ai_analyzed": False,
        }


def search_knowledge(db: Session, query: str, limit: int = 6) -> list[dict[str, Any]]:
    query = (query or "").strip()
    rows: list[dict[str, Any]] = []
    for document in db.scalars(
        select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == get_settings().tenant_id)
        .order_by(KnowledgeDocument.updated_at.desc())
    ).all():
        score = _score(f"{document.title} {document.summary} {document.content}", query) if query else 1
        if score or not query:
            rows.append(
                {
                    "id": document.id,
                    "title": document.title or document.filename,
                    "source": "uploaded",
                    "summary": document.summary,
                    "tags": document.tags or [],
                    "score": score,
                    "snippet": _snippet(document.content, query),
                }
            )
    knowledge_dir = get_settings().resolved_knowledge_dir()
    if knowledge_dir.exists():
        for path in sorted(knowledge_dir.glob("*")):
            if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".yaml", ".yml", ".json", ".log"}:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            score = _score(f"{path.name} {content}", query) if query else 1
            if score or not query:
                rows.append(
                    {
                        "id": f"file:{path.name}",
                        "title": path.stem,
                        "source": "built-in",
                        "summary": _snippet(content, "", 180),
                        "tags": [],
                        "score": score,
                        "snippet": _snippet(content, query),
                    }
                )
    return sorted(rows, key=lambda item: item["score"], reverse=True)[:limit]


def new_document_id() -> str:
    return f"kb-{uuid.uuid4().hex}"
