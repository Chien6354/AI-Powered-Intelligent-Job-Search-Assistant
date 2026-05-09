from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import chromadb
import numpy as np
from chromadb.config import Settings

from campus_rag.config_loader import (
    embedding_backend,
    openai_embed_config,
    settings,
)
from campus_rag.model_paths import resolve_sentence_transformers_local_path
from campus_rag.paths import ROOT

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

# 不在模块顶层 import torch/sentence_transformers：embedding_backend=openai 时建索引不需要它们，
# 可避免 Windows 上与其它库组合时的异常退出且无 traceback。
_embed_model: Any = None
_openai_client: Any = None
# 同一进程内必须复用同一个 PersistentClient；多次 new 并打开同一 chroma 目录在 Windows 上易导致锁/闪退。
_persistent_chroma: Any = None


def chroma_client():
    global _persistent_chroma
    if _persistent_chroma is None:
        cfg = settings()
        p = Path(cfg["chroma_path"])
        if not p.is_absolute():
            p = ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        _persistent_chroma = chromadb.PersistentClient(
            path=str(p), settings=Settings(anonymized_telemetry=False)
        )
    return _persistent_chroma


def reset_chroma_singleton() -> None:
    """测试或更换 chroma_path 时可调用；一般不必用。"""
    global _persistent_chroma
    _persistent_chroma = None


COLLECTION = "campus_chunks"


def vector_store_mode() -> str:
    return str(settings().get("vector_store") or "chroma").strip().lower()


def _l2_normalize_rows(vectors: list[list[float]]) -> list[list[float]]:
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    out = (arr / norms).astype(np.float32)
    return out.tolist()


def get_embed_model() -> Any:
    if embedding_backend() == "openai":
        raise RuntimeError("当前 embedding_backend=openai，不应加载本地 SentenceTransformer")
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer

        cfg = settings()
        model_ref = resolve_sentence_transformers_local_path(str(cfg["embedding_model"]))
        _embed_model = SentenceTransformer(model_ref, trust_remote_code=True)
    return _embed_model


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        ec = openai_embed_config()
        if not ec["api_key"]:
            raise RuntimeError(
                "embedding_backend=openai 时请在 .env 中配置 OPENAI_API_KEY（勿泄露给他人）"
            )
        kwargs: dict[str, Any] = {"api_key": ec["api_key"]}
        if ec["base_url"]:
            kwargs["base_url"] = ec["base_url"]
        _openai_client = OpenAI(**kwargs)
    return _openai_client


def _encode_openai(texts: list[str]) -> list[list[float]]:
    from openai import AuthenticationError

    cfg = settings()
    model = str(cfg.get("openai_embedding_model") or "text-embedding-3-small")
    batch_size = int(cfg.get("openai_embedding_batch_size") or 64)
    client = _get_openai_client()
    all_vecs: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            print(f"    [openai] embeddings.create batch chars={sum(len(t) for t in batch)} …", flush=True)
            resp = client.embeddings.create(model=model, input=batch)
            print("    [openai] batch ok", flush=True)
        except AuthenticationError as e:
            raise RuntimeError(
                "OpenAI 嵌入鉴权失败 (401)。请核对：\n"
                "1) `.env` 里 `OPENAI_API_KEY` 是否为 **OpenAI 官方**（platform.openai.com）有效密钥；\n"
                "2) 若密钥来自 **中转/其他厂商**，必须在 `.env` 设置 `OPENAI_BASE_URL` 为该厂商的 OpenAI 兼容地址，否则会误打到官方接口；\n"
                "3) Key 一行内不要多余空格、引号或换行。\n"
                "原始错误：" + str(e)
            ) from e
        ordered = sorted(resp.data, key=lambda d: d.index)
        all_vecs.extend([list(d.embedding) for d in ordered])
    return _l2_normalize_rows(all_vecs)


def encode_queries(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if embedding_backend() == "openai":
        return _encode_openai(texts)
    m = get_embed_model()
    emb = m.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return emb.tolist()


def encode_passages(texts: list[str]) -> list[list[float]]:
    return encode_queries(texts)


def get_collection():
    client = chroma_client()
    return client.get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def reset_collection() -> None:
    client = chroma_client()
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    client.get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def meta_for_chroma(
    company: str | None,
    season: str | None,
    source_url: str,
    job_title: str | None = None,
    doc_type: str | None = None,
) -> dict[str, Any]:
    return {
        "company": company or "",
        "season": season or "",
        "source_url": source_url or "",
        "job_title": job_title or "",
        "doc_type": doc_type or "",
    }


def upsert_chunks(
    chunk_ids: list[str],
    texts: list[str],
    metadatas: list[dict[str, Any]],
) -> None:
    def _p(msg: str) -> None:
        print(msg, flush=True)

    _p("    [upsert] OpenAI/local encode …")
    embeddings = encode_passages(texts)
    dim = len(embeddings[0]) if embeddings else 0
    _p(f"    [upsert] embeddings ok, count={len(embeddings)}, dim={dim}")

    if vector_store_mode() == "sqlite_numpy":
        from campus_rag.db import connect, init_schema, replace_chunk_embeddings_batch

        _p("    [upsert] sqlite_numpy (BLOB) …")
        conn = connect()
        init_schema(conn)
        batch_rows: list[tuple[str, int, bytes]] = []
        for i, cid in enumerate(chunk_ids):
            arr = np.asarray([float(x) for x in embeddings[i]], dtype=np.float32)
            batch_rows.append((cid, dim, arr.tobytes()))
        replace_chunk_embeddings_batch(conn, batch_rows)
        _p("    [upsert] sqlite_numpy done")
        return

    _p("    [upsert] get_collection …")
    col = get_collection()
    _p("    [upsert] chroma upsert …")
    rows = [
        (
            chunk_ids[i],
            [float(x) for x in embeddings[i]],
            texts[i],
            metadatas[i],
        )
        for i in range(len(chunk_ids))
    ]
    for idx, (cid, emb, doc, meta) in enumerate(rows, start=1):
        _p(f"    [upsert] chroma row {idx}/{len(rows)} id={cid[:8]}…")
        col.upsert(
            ids=[cid],
            embeddings=[emb],
            documents=[doc],
            metadatas=[meta],
        )
    _p("    [upsert] chroma done")


def query_vectors(
    query: str,
    top_k: int,
    where: dict[str, Any] | None,
) -> dict[str, Any]:
    col = get_collection()
    qe = encode_queries([query])[0]
    kwargs: dict[str, Any] = {
        "query_embeddings": [qe],
        "n_results": top_k,
        "include": ["documents", "metadatas", "distances"],
    }
    if where is not None:
        kwargs["where"] = where
    return col.query(**kwargs)
