from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from typing import Literal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AnomalyEvent, Asset, KnowledgeDocument, Ticket
from app.routers.deps import CurrentUser, require_perm
from app.services.knowledge import analyze_document, chat_completion, chat_model_name, extract_text, new_document_id, redact_sensitive, search_knowledge

router = APIRouter()


def _document_out(document: KnowledgeDocument) -> dict[str, Any]:
    return {
        "id": document.id,
        "filename": document.filename,
        "title": document.title or document.filename,
        "mime_type": document.mime_type,
        "size_bytes": document.size_bytes,
        "summary": document.summary,
        "tags": document.tags or [],
        "status": document.status,
        "chunk_count": document.chunk_count,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


@router.get("/api/v1/knowledge/documents", dependencies=[Depends(require_perm("knowledge:read"))])
def list_documents(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == get_settings().tenant_id)
        .order_by(KnowledgeDocument.updated_at.desc())
    ).all()
    return {"items": [_document_out(row) for row in rows], "total": len(rows)}


@router.post("/api/v1/knowledge/documents/upload", dependencies=[Depends(require_perm("knowledge:write"))])
def upload_document(file: UploadFile, db: Session = Depends(get_db)):
    settings = get_settings()
    payload = file.file.read(settings.knowledge_max_upload_bytes + 1)
    if len(payload) > settings.knowledge_max_upload_bytes:
        raise HTTPException(413, "文档大小超过系统限制")
    filename = (file.filename or "未命名文档").replace("/", "_").replace("\\", "_")[:256]
    if Path(filename).suffix.lower() not in {".pdf", ".docx", ".md", ".txt", ".yaml", ".yml", ".json", ".log"}:
        raise HTTPException(422, "不支持的文档格式")
    content = extract_text(filename, payload).strip()
    if not content:
        raise HTTPException(422, "暂不支持读取该文件内容，请上传 PDF、DOCX、Markdown 或文本文件")
    content = content[:300000]
    analysis = analyze_document(filename, content)
    document = KnowledgeDocument(
        id=new_document_id(),
        tenant_id=settings.tenant_id,
        filename=filename,
        title=filename.rsplit(".", 1)[0],
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=len(payload),
        content=content,
        summary=analysis["summary"],
        tags=analysis["tags"],
        status="analyzed" if analysis["ai_analyzed"] else "indexed",
        chunk_count=max(1, (len(content) + 799) // 800),
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return {"document": _document_out(document), "analysis": analysis}


@router.delete("/api/v1/knowledge/documents/{document_id}", dependencies=[Depends(require_perm("knowledge:write"))])
def delete_document(document_id: str, db: Session = Depends(get_db)):
    document = db.get(KnowledgeDocument, document_id)
    if document is None or document.tenant_id != get_settings().tenant_id:
        raise HTTPException(404, "知识文档不存在")
    db.delete(document)
    db.commit()
    return {"ok": True}


@router.get("/api/v1/knowledge/search", dependencies=[Depends(require_perm("knowledge:read"))])
def search_documents(q: str = "", limit: int = 6, db: Session = Depends(get_db)):
    return {"items": search_knowledge(db, q, max(1, min(limit, 20)))}


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


def _runtime_context(db: Session) -> dict[str, Any]:
    assets = db.scalars(select(Asset).order_by(Asset.hostname)).all()
    anomalies = db.scalars(
        select(AnomalyEvent).where(AnomalyEvent.status == "abnormal").order_by(AnomalyEvent.last_seen_at.desc()).limit(8)
    ).all()
    open_tickets = db.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.status.not_in(["recovered", "skipped"]))
    ) or 0
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assets": {
            "total": len(assets),
            "reachable": sum(1 for asset in assets if asset.reachable),
            "db_ok": sum(1 for asset in assets if asset.db_ok),
            "unreachable_hosts": [asset.hostname for asset in assets if not asset.reachable][:20],
        },
        "open_ticket_count": open_tickets,
        "active_anomalies": [
            {"host": row.hostname or row.host, "trigger": row.trigger_name, "severity": row.severity, "message": row.message}
            for row in anomalies
        ],
    }


@router.post("/api/v1/knowledge/chat", dependencies=[Depends(require_perm("knowledge:chat"))])
def chat(body: ChatIn, db: Session = Depends(get_db), current: CurrentUser = Depends(require_perm("knowledge:chat"))):
    hits = search_knowledge(db, body.message, 6)
    context = _runtime_context(db)
    knowledge_text = "\n\n".join(
        f"【{item['title']}】\n{redact_sensitive(item['snippet'])}" for item in hits
    ) or "当前知识库没有命中内容。"
    system = (
        "你是企业内部智能运维助手。回答必须优先遵循知识库中的公司规则，再结合实时运维状态。"
        "如果信息不足，要明确说需要进一步采集；禁止编造服务器指标、禁止输出密码/token/私钥。"
        "对于重启、删除、切换、变更等动作，只能给出建议，不能声称已经执行。"
        f"\n实时状态：{context}\n知识库检索结果：\n{knowledge_text}"
    )
    messages = [{"role": "system", "content": system}]
    messages.extend({"role": item.role, "content": redact_sensitive(item.content)} for item in body.history[-12:])
    messages.append({"role": "user", "content": redact_sensitive(body.message)})
    try:
        answer = chat_completion(messages, db=db)
        model = chat_model_name(db)
        source = "ai"
    except Exception:
        answer = (
            "当前未成功连接 AI 网关，我先返回可确认的状态摘要："
            f"资产 {context['assets']['total']} 台，可达 {context['assets']['reachable']} 台，"
            f"活动异常 {len(context['active_anomalies'])} 条，未关闭任务单 {context['open_ticket_count']} 条。"
            "请检查 AI 网关配置和连接状态。"
        )
        model = "fallback"
        source = "fallback"
    return {"answer": answer, "model": model, "source": source, "knowledge": hits, "runtime": context, "user": current.username}
