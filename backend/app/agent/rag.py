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
        for kw in ("cpu", "飙高", "full gc", "内存", "gc", "磁盘", "日志", "探针", "probe", "tmp"):
            if kw in q and kw in blob:
                score += 0.25
        if "cpu" in q and "cpu" in blob:
            score += 0.35
        if any(k in q for k in ("磁盘", "disk", "探针", "probe")) and any(
            k in blob for k in ("磁盘", "探针", "act-clean", "act-restart-probe")
        ):
            score += 0.4
        if score >= 0.4:
            excerpt = next(
                (ln.strip() for ln in text.splitlines() if "ACT-" in ln or "候选预案" in ln),
                text.strip().splitlines()[0][:120],
            )
            doc_id = "RB-DISK-001" if "disk" in path.name or "磁盘" in title else "RB-CPU-001"
            hits.append(
                {
                    "doc_id": doc_id,
                    "title": title,
                    "score": round(min(score, 0.99), 2),
                    "excerpt": excerpt,
                    "path": str(Path(path.name)),
                }
            )
    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:top_k]
