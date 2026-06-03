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
    except Exception:
        pass  # fall through to local on any remote failure
    return [_pack(_local_embed(t)) for t in texts]
