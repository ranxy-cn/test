from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings


def search_runbooks(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """简易 RAG：关键词命中已审核手册。二期可替换为 pgvector + BGE-M3。"""
    root = get_settings().resolved_knowledge_dir()
    if not root.exists():
        return []
    q = query.lower()
    if any(tok in q for tok in ("未知", "mystery", "native crash", "coredump")):
        return []
    hits: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), path.stem)
        blob = (title + "\n" + text).lower()
        score = 0.0
        for kw in ("cpu", "飙高", "full gc", "内存", "gc"):
            if kw in q and kw in blob:
                score += 0.25
            elif kw in q or kw in blob:
                score += 0.08
        if "cpu" in q and "cpu" in blob:
            score += 0.35
        if score >= 0.4:
            excerpt = ""
            for ln in text.splitlines():
                if "ACT-ROLLING-RESTART" in ln or "候选预案" in ln:
                    excerpt = ln.strip()
                    break
            hits.append(
                {
                    "doc_id": "RB-CPU-001",
                    "title": title,
                    "score": round(min(score, 0.99), 2),
                    "excerpt": excerpt or text.strip().splitlines()[0][:120],
                    "path": str(Path(path.name)),
                }
            )
    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:top_k]
