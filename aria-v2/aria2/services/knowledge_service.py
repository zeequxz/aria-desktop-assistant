"""services/knowledge_service.py - Document ingestion + RAG search.

Ingests text/code files into chunks with embeddings, then retrieves passages by
a hybrid of vector similarity and keyword overlap. Each hit carries its source
document so the agent can cite it. Re-ingesting an unchanged file is a no-op
(content_hash guard); a changed file bumps the document version.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from aria2.core import db
from aria2.core.ids import new_id, now_ms
from aria2.models import embeddings

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
_TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".html", ".css", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".sh", ".sql",
    ".toml", ".ini", ".cfg",
}


def _chunk(text: str) -> list[str]:
    chunks, i = [], 0
    while i < len(text):
        chunks.append(text[i : i + CHUNK_CHARS])
        i += CHUNK_CHARS - CHUNK_OVERLAP
    return chunks or [""]


def ingest_text(project_id: str, title: str, text: str, uri: str = "") -> dict:
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = db.one(
        "SELECT * FROM documents WHERE project_id=? AND uri=?", (project_id, uri or title)
    )
    if existing and existing["content_hash"] == content_hash:
        return {"document_id": existing["id"], "unchanged": True}

    doc_id = existing["id"] if existing else new_id("doc")
    version = (existing["version"] + 1) if existing else 1
    if existing:
        db.delete("documents", doc_id)  # cascade drops old chunks
    db.insert("documents", {
        "id": doc_id, "project_id": project_id, "uri": uri or title, "title": title,
        "content_hash": content_hash, "version": version, "ingested_at": now_ms(),
    })

    pieces = _chunk(text)
    vecs = embeddings.embed_batch(pieces)
    for ordinal, (piece, vec) in enumerate(zip(pieces, vecs)):
        db.insert("chunks", {
            "id": new_id("chk"), "document_id": doc_id, "ordinal": ordinal,
            "text": piece, "embedding": vec, "metadata_json": "{}",
        })
    return {"document_id": doc_id, "chunks": len(pieces), "version": version}


def ingest_file(project_id: str, path: str) -> dict:
    p = Path(path)
    if not p.exists() or p.suffix.lower() not in _TEXT_EXTS:
        return {"error": f"Unsupported or missing file: {path}"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"error": str(e)}
    return ingest_text(project_id, p.name, text, uri=str(p))


def ingest_folder(project_id: str, folder: str, max_files: int = 500) -> dict:
    root = Path(folder)
    if not root.exists():
        return {"error": f"Folder not found: {folder}"}
    count, chunks = 0, 0
    for f in root.rglob("*"):
        if count >= max_files:
            break
        if f.is_file() and f.suffix.lower() in _TEXT_EXTS and ".git" not in f.parts:
            res = ingest_file(project_id, str(f))
            if "chunks" in res:
                count += 1
                chunks += res["chunks"]
    return {"files": count, "chunks": chunks}


_WORD = re.compile(r"[a-z0-9]+")


def search(query: str, project_id: str, limit: int = 5) -> list[dict]:
    rows = db.all(
        "SELECT c.id, c.text, c.embedding, d.title, d.uri "
        "FROM chunks c JOIN documents d ON c.document_id = d.id "
        "WHERE d.project_id = ?", (project_id,),
    )
    if not rows:
        return []
    qvec = embeddings.embed(query)
    sims = embeddings.score_batch(qvec, [r["embedding"] for r in rows])
    qwords = set(_WORD.findall(query.lower()))
    scored = []
    for r, sim in zip(rows, sims):
        cwords = set(_WORD.findall(r["text"].lower()))
        overlap = len(qwords & cwords) / (len(qwords) or 1)
        score = 0.75 * sim + 0.25 * overlap
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"text": r["text"], "title": r["title"], "uri": r["uri"], "score": s}
            for s, r in scored[:limit] if s > 0.01]


def list_documents(project_id: str) -> list[dict]:
    rows = db.all(
        "SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) AS n_chunks "
        "FROM documents d WHERE project_id=? ORDER BY ingested_at DESC", (project_id,),
    )
    return [dict(r) for r in rows]


def delete_document(doc_id: str) -> None:
    db.delete("documents", doc_id)
