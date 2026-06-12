"""services/memory_service.py - Retrieval memory with provenance & belief revision.

Two layers beyond ordinary RAG memory:

  Retrieval — facts are embedded and scored by
      0.65*cosine + 0.20*importance + 0.15*recency  (+ pin bonus, *confidence)
  so the right fact surfaces at the right time instead of dumping everything.

  Provenance — every memory records the run that produced it (source_run_id) and
  the parent memories it was derived from (derived_from). That makes beliefs
  *contestable*: `retract()` marks a fact false and flags everything derived from
  it for review; `supersede()` replaces a fact while preserving lineage; recall
  never returns retracted facts. This is the part vendors don't build, because
  storing inferred beliefs about a person is a liability in the cloud and an
  asset on the device.
"""

from __future__ import annotations

import json
import math

from aria2.core import db
from aria2.core.ids import new_id, now_ms
from aria2.models import embeddings

_RECENCY_HALFLIFE_MS = 30 * 24 * 3600 * 1000  # 30 days


# ── Writing ───────────────────────────────────────────────────────────────────

def remember(text: str, scope: str, scope_id: str = "", importance: float = 0.6,
             kind: str = "semantic", source_run_id: str | None = None,
             derived_from: list[str] | None = None, confidence: float = 0.7) -> dict:
    text = (text or "").strip()
    if not text:
        return {"error": "empty memory"}
    existing = db.all(
        "SELECT id, text FROM memories WHERE scope=? AND scope_id=? AND retracted=0",
        (scope, scope_id),
    )
    for row in existing:
        if row["text"].strip().lower() == text.lower():
            return {"id": row["id"], "deduped": True}

    mem_id = new_id("mem")
    db.insert("memories", {
        "id": mem_id, "scope": scope, "scope_id": scope_id, "kind": kind,
        "text": text, "embedding": embeddings.embed(text),
        "importance": max(0.0, min(1.0, importance)),
        "confidence": max(0.0, min(1.0, confidence)),
        "access_count": 0, "source_run_id": source_run_id,
        "derived_from": json.dumps(derived_from or []),
        "retracted": 0, "superseded_by": None, "needs_review": 0, "pinned": 0,
        "created_at": now_ms(), "last_accessed": now_ms(), "expires_at": None,
    })
    return {"id": mem_id, "stored": True}


# ── Retrieval ─────────────────────────────────────────────────────────────────

def recall(query: str, scope: str, scope_id: str = "", limit: int = 6) -> list[dict]:
    rows = db.all(
        "SELECT * FROM memories WHERE scope=? AND scope_id=? AND retracted=0 "
        "AND (expires_at IS NULL OR expires_at > ?)",
        (scope, scope_id, now_ms()),
    )
    if not rows:
        return []
    qvec = embeddings.embed(query)
    sims = embeddings.score_batch(qvec, [r["embedding"] for r in rows])
    now = now_ms()
    scored = []
    for r, sim in zip(rows, sims):
        age = now - (r["last_accessed"] or r["created_at"])
        recency = math.exp(-age / _RECENCY_HALFLIFE_MS)
        pin = 0.25 if r["pinned"] else 0.0
        base = 0.65 * sim + 0.20 * (r["importance"] or 0) + 0.15 * recency + pin
        score = base * (0.4 + 0.6 * (r["confidence"] if r["confidence"] is not None else 0.7))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]
    for _, r in top:
        db.update("memories", r["id"], {
            "access_count": (r["access_count"] or 0) + 1, "last_accessed": now,
        })
    return [{"id": r["id"], "text": r["text"], "score": s, "kind": r["kind"],
             "importance": r["importance"], "confidence": r["confidence"],
             "pinned": bool(r["pinned"]), "needs_review": bool(r["needs_review"])}
            for s, r in top]


# ── Provenance & belief revision ───────────────────────────────────────────────

def get(mem_id: str) -> dict | None:
    r = db.one("SELECT * FROM memories WHERE id = ?", (mem_id,))
    return dict(r) if r else None


def derivation(mem_id: str) -> dict:
    """Return the full provenance of a memory: the run that produced it and the
    recursive chain of parent memories it was derived from."""
    m = get(mem_id)
    if not m:
        return {"error": "not found"}

    def _chain(mid: str, seen: set[str]) -> list[dict]:
        if mid in seen:
            return []
        seen.add(mid)
        node = get(mid)
        if not node:
            return []
        parents = json.loads(node["derived_from"] or "[]")
        return [{
            "id": node["id"], "text": node["text"],
            "confidence": node["confidence"], "retracted": bool(node["retracted"]),
            "source_run_id": node["source_run_id"],
            "parents": [p for pid in parents for p in _chain(pid, seen)],
        }]

    return {
        "memory": {"id": m["id"], "text": m["text"], "confidence": m["confidence"]},
        "source_run_id": m["source_run_id"],
        "derived_from": _chain(mem_id, set())[0]["parents"] if True else [],
    }


def dependents(mem_id: str) -> list[dict]:
    """Memories that list `mem_id` among their derived_from."""
    rows = db.all("SELECT * FROM memories WHERE derived_from LIKE ?", (f"%{mem_id}%",))
    out = []
    for r in rows:
        if mem_id in json.loads(r["derived_from"] or "[]"):
            out.append({"id": r["id"], "text": r["text"], "retracted": bool(r["retracted"])})
    return out


def retract(mem_id: str, reason: str = "") -> dict:
    """Mark a belief false and flag everything derived from it for review.
    Belief revision propagates: dependents are not deleted but surfaced so the
    user/agent can re-evaluate conclusions that rested on the retracted fact."""
    m = get(mem_id)
    if not m:
        return {"error": "not found"}
    db.update("memories", mem_id, {"retracted": 1})
    affected = dependents(mem_id)
    for d in affected:
        db.update("memories", d["id"], {"needs_review": 1, "confidence": 0.3})
    db.insert("audit_log", {
        "id": new_id("aud"), "actor": "user", "action": "memory.retract",
        "target": mem_id, "detail_json": json.dumps({"reason": reason, "flagged": len(affected)}),
        "run_id": None, "created_at": now_ms(),
    })
    return {"retracted": True, "flagged_for_review": len(affected)}


def supersede(old_id: str, new_text: str, confidence: float = 0.8) -> dict:
    """Replace a fact with a corrected version, preserving lineage."""
    old = get(old_id)
    if not old:
        return {"error": "not found"}
    res = remember(new_text, scope=old["scope"], scope_id=old["scope_id"],
                   importance=old["importance"], kind=old["kind"],
                   derived_from=[old_id], confidence=confidence)
    db.update("memories", old_id, {"retracted": 1, "superseded_by": res.get("id")})
    return {"superseded": True, "new_id": res.get("id")}


def clear_review(mem_id: str) -> None:
    db.update("memories", mem_id, {"needs_review": 0})


def set_pinned(mem_id: str, pinned: bool) -> None:
    db.update("memories", mem_id, {"pinned": 1 if pinned else 0})


# ── Listing / housekeeping ─────────────────────────────────────────────────────

def list_memories(scope: str, scope_id: str = "", include_retracted: bool = False) -> list[dict]:
    sql = "SELECT * FROM memories WHERE scope=? AND scope_id=?"
    if not include_retracted:
        sql += " AND retracted=0"
    sql += " ORDER BY needs_review DESC, created_at DESC"
    rows = db.all(sql, (scope, scope_id))
    return [dict(r) | {"embedding": None} for r in rows]


def update_memory(mem_id: str, changes: dict) -> None:
    allowed = {k: v for k, v in changes.items()
               if k in {"text", "importance", "confidence", "pinned", "kind", "expires_at"}}
    if "text" in allowed:
        allowed["embedding"] = embeddings.embed(allowed["text"])
    db.update("memories", mem_id, allowed)


def delete_memory(mem_id: str) -> None:
    db.delete("memories", mem_id)


def decay() -> int:
    cutoff = now_ms() - 60 * 24 * 3600 * 1000
    stale = db.all(
        "SELECT id FROM memories WHERE pinned=0 AND access_count=0 "
        "AND importance < 0.4 AND created_at < ?", (cutoff,),
    )
    for r in stale:
        db.delete("memories", r["id"])
    return len(stale)
