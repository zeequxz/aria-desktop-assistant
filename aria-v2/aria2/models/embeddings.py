"""models/embeddings.py - Text embeddings with an offline fallback.

Memory and knowledge retrieval need vectors. We support Voyage (Anthropic's
recommended embeddings) and OpenAI, but default to a dependency-free local
hashing embedding so the app works offline with no key. The local embedding is
lower quality but deterministic and good enough for small personal corpora.

Vectors are packed to float32 BLOBs for storage (see core/schema.sql).
"""

from __future__ import annotations

import hashlib
import math
import re
import struct

from aria2.core import config

_LOCAL_DIM = 256


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


try:
    import numpy as _np
    _HAVE_NUMPY = True
except Exception:  # numpy optional — fall back to the pure-Python loop
    _HAVE_NUMPY = False


def score_batch(query, blobs: list) -> list[float]:
    """Cosine similarity of a query vector against many *packed* embeddings
    (the per-turn recall/search hot path). Vectorised with numpy when available,
    else the pure-Python loop. Same semantics as cosine(): a dimension mismatch
    (e.g. mixed embedding providers) scores 0 — so retrieval degrades, not breaks.

    `query` may be a packed BLOB or an already-unpacked vector."""
    qv = unpack(query) if isinstance(query, (bytes, bytearray)) else list(query or [])
    if not qv:
        return [0.0] * len(blobs)
    if _HAVE_NUMPY:
        return _score_batch_np(qv, blobs)
    return [cosine(qv, unpack(b)) for b in blobs]


def _score_batch_np(qv: list, blobs: list) -> list[float]:
    q = _np.asarray(qv, dtype=_np.float32)
    d = q.shape[0]
    qn = float(_np.linalg.norm(q)) or 1.0
    out = [0.0] * len(blobs)
    idxs, rows = [], []
    for i, b in enumerate(blobs):
        if not b:
            continue
        v = _np.frombuffer(b, dtype=_np.float32)
        if v.shape[0] != d:  # different embedding dimension → score 0
            continue
        idxs.append(i)
        rows.append(v)
    if not rows:
        return out
    m = _np.vstack(rows)               # (k, d)
    norms = _np.linalg.norm(m, axis=1)
    norms[norms == 0] = 1.0
    sims = (m @ q) / (norms * qn)      # (k,) — one matmul instead of k Python loops
    for j, i in enumerate(idxs):
        out[i] = float(sims[j])
    return out


# ── Local hashing embedding (offline fallback) ──────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _local_embed(text: str) -> list[float]:
    """Hashed bag-of-words projected into a fixed-dim unit vector."""
    vec = [0.0] * _LOCAL_DIM
    for tok in _TOKEN_RE.findall(text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        idx = h % _LOCAL_DIM
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


# ── Remote embeddings ───────────────────────────────────────────────────────

def _voyage_embed(texts: list[str], key: str) -> list[list[float]]:
    import requests

    r = requests.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"input": texts, "model": "voyage-3"},
        timeout=60,
    )
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]


def _openai_embed(texts: list[str], key: str) -> list[list[float]]:
    import openai

    client = openai.OpenAI(api_key=key)
    resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in resp.data]


def _ollama_embed(texts: list[str], base_url: str, model: str) -> list[list[float]]:
    """Real local semantic embeddings via Ollama's OpenAI-compatible endpoint
    (e.g. nomic-embed-text). Free, offline, and far better than the hashing
    fallback — but the model must be pulled (`ollama pull nomic-embed-text`)."""
    import requests

    base = (base_url or "http://localhost:11434").rstrip("/")
    r = requests.post(
        f"{base}/v1/embeddings",
        headers={"Authorization": "Bearer ollama"},
        json={"model": model, "input": texts},
        timeout=120,
    )
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]


def embed(text: str) -> bytes:
    """Embed one string and return a packed float32 BLOB for storage."""
    return embed_batch([text])[0]


def embed_batch(texts: list[str]) -> list[bytes]:
    s = config.load()
    provider = s.get("embedding_provider", "local")
    try:
        if provider == "voyage" and s.get("voyage_api_key"):
            vecs = _voyage_embed(texts, s["voyage_api_key"])
            return [_pack(v) for v in vecs]
        if provider == "openai" and s.get("openai_api_key"):
            vecs = _openai_embed(texts, s["openai_api_key"])
            return [_pack(v) for v in vecs]
        if provider == "ollama":
            vecs = _ollama_embed(texts, s.get("ollama_url", "http://localhost:11434"),
                                 s.get("ollama_embed_model", "nomic-embed-text"))
            return [_pack(v) for v in vecs]
    except Exception:
        pass  # fall through to local on any remote/connection failure
    return [_pack(_local_embed(t)) for t in texts]
