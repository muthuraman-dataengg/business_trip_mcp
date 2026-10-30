"""Policy document loader + retrieval index.

* Documents are Markdown files in ``data/policies`` (edit them, no code change).
* Each ``##`` section becomes one chunk carrying doc/section metadata.
* Semantic mode: OpenAI embeddings, cached on disk keyed by content hash, so
  the documents are embedded once — not on every server start.
* Fallback mode: BM25 lexical scoring when no embedding key is available,
  so the server still works offline / in CI.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import get_settings

log = logging.getLogger("copilot.policy")
TOKEN = re.compile(r"[a-z0-9]+")


@dataclass
class PolicyDoc:
    doc_id: str
    title: str
    meta: Dict[str, str]
    body: str


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    section: str
    text: str
    tokens: List[str] = field(default_factory=list)


def _parse_front_matter(raw: str) -> tuple:
    meta: Dict[str, str] = {}
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            for line in raw[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            raw = raw[end + 4 :]
    return meta, raw.strip()


def load_documents(folder: Optional[Path] = None) -> List[PolicyDoc]:
    folder = Path(folder or get_settings().policies_path)
    docs = []
    for path in sorted(folder.glob("*.md")):
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
        title = meta.get("title") or path.stem.replace("-", " ").title()
        docs.append(PolicyDoc(doc_id=path.stem, title=title, meta=meta, body=body))
    return docs


def _tokens(text: str) -> List[str]:
    return TOKEN.findall(text.lower())


def chunk_documents(docs: List[PolicyDoc]) -> List[Chunk]:
    chunks: List[Chunk] = []
    for doc in docs:
        parts = re.split(r"^##\s+", doc.body, flags=re.MULTILINE)
        for i, part in enumerate(parts):
            part = part.strip()
            if not part or part.startswith("# "):
                continue
            section, _, text = part.partition("\n")
            text = text.strip() or section
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}#{i}",
                    doc_id=doc.doc_id,
                    doc_title=doc.title,
                    section=section.strip(),
                    text=text,
                    tokens=_tokens(f"{section} {text}"),
                )
            )
    return chunks


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class PolicyIndex:
    def __init__(self, folder: Optional[Path] = None):
        self.settings = get_settings()
        self.docs = load_documents(folder)
        self.chunks = chunk_documents(self.docs)
        self._vectors: Optional[List[List[float]]] = None
        self._embedder = None
        self.mode = "semantic" if self._embeddings_available() else "lexical"
        # BM25 statistics
        self._df = Counter(t for c in self.chunks for t in set(c.tokens))
        self._avgdl = sum(len(c.tokens) for c in self.chunks) / max(1, len(self.chunks))

    # --------------------------------------------------------- embeddings
    def _embeddings_available(self) -> bool:
        import os

        return bool(self.settings.openai_api_key or os.environ.get("OPENAI_API_KEY"))

    def _get_embedder(self):  # noqa: ANN202
        if self._embedder is None:
            from langchain_openai import OpenAIEmbeddings

            self._embedder = OpenAIEmbeddings(model=self.settings.embedding_model)
        return self._embedder

    def _cache_file(self) -> Path:
        return self.settings.cache_dir / "policy_embeddings.json"

    def _load_vectors(self) -> List[List[float]]:
        if self._vectors is not None:
            return self._vectors
        model = self.settings.embedding_model
        cache: Dict[str, List[float]] = {}
        cache_file = self._cache_file()
        if cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text())
            except json.JSONDecodeError:
                cache = {}
        keys = [hashlib.sha256(f"{model}|{c.section}|{c.text}".encode()).hexdigest() for c in self.chunks]
        missing = [i for i, k in enumerate(keys) if k not in cache]
        if missing:
            log.info("Embedding %d new/changed policy chunks", len(missing))
            vectors = self._get_embedder().embed_documents(
                [f"{self.chunks[i].section}: {self.chunks[i].text}" for i in missing]
            )
            for i, vec in zip(missing, vectors):
                cache[keys[i]] = vec
            live = set(keys)
            cache = {k: v for k, v in cache.items() if k in live}
            cache_file.write_text(json.dumps(cache))
        self._vectors = [cache[k] for k in keys]
        return self._vectors

    # ------------------------------------------------------------- search
    def _bm25(self, query: str) -> List[float]:
        q = _tokens(query)
        n = len(self.chunks)
        k1, b = 1.5, 0.75
        scores = []
        for c in self.chunks:
            tf = Counter(c.tokens)
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(c.tokens) / self._avgdl))
            scores.append(s)
        return scores

    def search(self, query: str, top_k: int = 3) -> List[dict]:
        if not self.chunks:
            return []
        mode = self.mode
        if mode == "semantic":
            try:
                vectors = self._load_vectors()
                qv = self._get_embedder().embed_query(query)
                scores = [_cosine(qv, v) for v in vectors]
            except Exception as exc:  # noqa: BLE001 - degrade gracefully
                log.warning("Semantic search failed (%s); falling back to BM25", exc)
                scores, mode = self._bm25(query), "lexical"
        else:
            scores = self._bm25(query)
        ranked = sorted(zip(self.chunks, scores), key=lambda x: x[1], reverse=True)
        top_k = max(1, min(top_k, len(ranked)))
        return [
            {
                "doc_id": c.doc_id,
                "document": c.doc_title,
                "section": c.section,
                "text": c.text,
                "score": round(s, 4),
                "retrieval": mode,
            }
            for c, s in ranked[:top_k]
        ]


def load_rules() -> dict:
    path = get_settings().data_dir / "policy_rules.json"
    return json.loads(path.read_text(encoding="utf-8"))
