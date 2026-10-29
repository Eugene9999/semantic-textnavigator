"""Local E5 retrieval. No text is sent to a remote inference service."""
from __future__ import annotations

import os
import threading
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"


class Embedder:
    def __init__(self, cache: str | None = None):
        self.cache = cache or str(Path(__file__).resolve().parents[1] / ".model-cache")
        self.state = "idle"
        self.error = None
        self.model = None
        self.lock = threading.Lock()
        self.query_cache = {}

    def start(self):
        if self.state != "idle":
            return
        self.state = "loading"
        threading.Thread(target=self.load, daemon=True).start()

    def load(self):
        self.state = "loading"
        try:
            from sentence_transformers import SentenceTransformer
            import torch
            torch.set_num_threads(min(4, os.cpu_count() or 1))
            self.model = SentenceTransformer(
                MODEL_ID, revision=MODEL_REVISION, device="cpu", cache_folder=self.cache,
                local_files_only=os.environ.get("TEXTNAV_OFFLINE") == "1",
                trust_remote_code=False,
            )
            self.model.max_seq_length = 512
            self.state = "ready"
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            print(f"Model load failed: {self.error}", flush=True)
            self.state = "error"

    def encode(self, texts: list[str], query=False):
        if self.state != "ready":
            raise RuntimeError("Embedding-модель еще не готова.")
        prefix = "query: " if query else "passage: "
        with self.lock:
            return self.model.encode([prefix + s for s in texts],
                                     normalize_embeddings=True, batch_size=32,
                                     show_progress_bar=False, convert_to_numpy=True)

    def query(self, text: str):
        if text not in self.query_cache:
            if len(self.query_cache) > 256:
                self.query_cache.clear()
            self.query_cache[text] = self.encode([text], query=True)[0]
        return self.query_cache[text]


class SemanticIndex:
    def __init__(self, embedder: Embedder, docs: list[dict]):
        self.embedder = embedder
        self.rows = [{"document_id": d["id"], "contract_id": d["contract_id"],
                      "filename": d["filename"], "kind": d["kind"],
                      "paragraph_id": p["id"], "quote": p["text"], "line": p["line"]}
                     for d in docs for p in d["paragraphs"]]
        self.vectors = embedder.encode([r["quote"] for r in self.rows]) if self.rows else None

    def search(self, query: str, limit=5, document_id=None, contract_id=None):
        if not self.rows:
            return []
        scores = self.vectors @ self.embedder.query(query)
        indices = [i for i, row in enumerate(self.rows)
                   if (document_id is None or row["document_id"] == document_id)
                   and (contract_id is None or row["contract_id"] == contract_id)]
        indices.sort(key=lambda i: float(scores[i]), reverse=True)
        return [{**self.rows[i], "score": round(float(scores[i]), 4)} for i in indices[:limit]]
