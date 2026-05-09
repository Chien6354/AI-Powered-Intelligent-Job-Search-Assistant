from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any

import numpy as np

from campus_rag.config_loader import settings
from campus_rag.model_paths import resolve_sentence_transformers_local_path
from campus_rag.db import connect, get_chunk_rows, init_schema

_reranker: Any = None


def get_reranker() -> Any:
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        cfg = settings()
        path = resolve_sentence_transformers_local_path(str(cfg["reranker_model"]))
        _reranker = CrossEncoder(path, trust_remote_code=True)
    return _reranker


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    source_url: str
    company: str
    season: str
    job_title: str
    vector_distance: float
    vector_similarity: float
    rerank_score: float | None = None


def _build_where(
    company: str | None,
    season: str | None,
    filter_doc_types: list[str] | None = None,
    *,
    company_like: str | None = None,
) -> dict[str, Any] | None:
    """Chroma 仅支持精确 metadata；company_like 在 query 后于 Python 侧过滤。"""
    parts: list[dict[str, Any]] = []
    if company and not company_like:
        parts.append({"company": {"$eq": company}})
    if season:
        parts.append({"season": {"$eq": season}})
    if filter_doc_types:
        parts.append({"doc_type": {"$in": filter_doc_types}})
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return {"$and": parts}


def _fetch_sqlite_relaxed(
    conn: sqlite3.Connection,
    fc: str | None,
    fcl: str | None,
    fs: str | None,
    filter_doc_types: list[str] | None,
) -> tuple[list[Any], list[str]]:
    from campus_rag.db import fetch_chunks_with_embeddings

    db_rows = fetch_chunks_with_embeddings(
        conn, fc, fs, filter_doc_types, filter_company_like=fcl
    )
    relaxation: list[str] = []
    had_company = bool(fc or fcl)
    if not db_rows and had_company:
        db_rows = fetch_chunks_with_embeddings(
            conn, None, fs, filter_doc_types, filter_company_like=None
        )
        if db_rows:
            relaxation.append("dropped_company_filter")
    if not db_rows:
        db_rows = fetch_chunks_with_embeddings(
            conn, None, None, filter_doc_types, filter_company_like=None
        )
        if db_rows:
            relaxation.append("full_corpus" if not relaxation else "full_corpus_after_empty")
    return db_rows, relaxation


def _vector_store_mode() -> str:
    return str(settings().get("vector_store") or "chroma").strip().lower()


def _post_rerank_min_similarity(cfg: dict) -> float:
    v = cfg.get("min_similarity_after_rerank")
    return float(v) if v is not None else float(cfg["min_similarity"])


def _min_rerank_score_threshold(cfg: dict) -> float | None:
    """重排分门禁；yaml 中 min_rerank_score: null 表示关闭。"""
    if "min_rerank_score" not in cfg:
        return 0.28
    v = cfg.get("min_rerank_score")
    if v is None:
        return None
    return float(v)


def _chroma_raw_to_candidates(
    raw: dict[str, Any],
) -> list[RetrievedChunk]:
    ids = (raw.get("ids") or [[]])[0]
    docs = (raw.get("documents") or [[]])[0]
    dists = (raw.get("distances") or [[]])[0]
    metas = (raw.get("metadatas") or [[]])[0]
    out: list[RetrievedChunk] = []
    for cid, doc, dist, meta in zip(ids, docs, dists, metas):
        sim = 1.0 - float(dist)
        out.append(
            RetrievedChunk(
                chunk_id=cid,
                text=doc or "",
                source_url=(meta or {}).get("source_url") or "",
                company=(meta or {}).get("company") or "",
                season=(meta or {}).get("season") or "",
                job_title=(meta or {}).get("job_title") or "",
                vector_distance=float(dist),
                vector_similarity=sim,
            )
        )
    return out


def _retrieve_sqlite_numpy(
    query: str,
    *,
    filter_company: str | None,
    filter_company_like: str | None = None,
    filter_season: str | None,
    filter_doc_types: list[str] | None = None,
    apply_min_similarity: bool = True,
) -> tuple[list[RetrievedChunk], dict[str, Any]]:
    from campus_rag.embed_store import encode_queries

    cfg = settings()
    top_k = int(cfg["retrieve_top_k"])
    rerank_top_n = int(cfg["rerank_top_n"])
    final_n = int(cfg["final_context_chunks"])
    min_sim = float(cfg["min_similarity"])
    post_min_sim = _post_rerank_min_similarity(cfg)
    min_rerank = _min_rerank_score_threshold(cfg)

    conn = connect()
    init_schema(conn)
    fc, fs = filter_company, filter_season
    fcl = filter_company_like.strip() if filter_company_like and filter_company_like.strip() else None
    db_rows, relaxation = _fetch_sqlite_relaxed(conn, fc, fcl, fs, filter_doc_types)

    qv = encode_queries([query])[0]
    q = np.asarray(qv, dtype=np.float32)

    if db_rows:
        stored_dim = int(db_rows[0]["dim"])
        if int(q.shape[0]) != stored_dim:
            raise RuntimeError(
                f"查询向量维度为 {int(q.shape[0])}，库中向量为 {stored_dim}，与建索引时使用的嵌入模型不一致。"
                f"（例如 OpenAI 多为 1536，bge-m3 多为 1024。）请在项目根目录执行: python scripts/build_index.py"
            )

    candidates: list[RetrievedChunk] = []
    for row in db_rows:
        raw = row["vec"]
        blob = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
        v = np.frombuffer(blob, dtype=np.float32, count=int(row["dim"]))
        sim = float(np.dot(q, v))
        dist = 1.0 - sim
        candidates.append(
            RetrievedChunk(
                chunk_id=row["chunk_id"],
                text=row["text"] or "",
                source_url=row["source_url"] or "",
                company=row["company"] or "",
                season=row["season"] or "",
                job_title=row["job_title"] or "",
                vector_distance=dist,
                vector_similarity=sim,
            )
        )

    candidates.sort(key=lambda x: -x.vector_similarity)
    candidates = candidates[:top_k]

    debug: dict[str, Any] = {
        "vector_store": "sqlite_numpy",
        "filter_company": filter_company,
        "filter_company_like": fcl,
        "filter_season": filter_season,
        "filter_doc_types": filter_doc_types,
        "apply_min_similarity": apply_min_similarity,
        "filters_attempted": {"company": fc, "company_like": fcl, "season": fs},
        "metadata_relaxation": relaxation,
        "retrieve_query": query[:500],
        "candidate_count": len(candidates),
        "top1_vector_similarity": candidates[0].vector_similarity if candidates else None,
    }

    if not candidates:
        debug["abstain_reason"] = "no_hits"
        return [], debug

    if apply_min_similarity and candidates[0].vector_similarity < min_sim:
        debug["abstain_reason"] = "below_similarity_threshold"
        return [], debug

    rerank = get_reranker()
    pairs = [(query, c.text) for c in candidates[:rerank_top_n]]
    scores = rerank.predict(pairs, show_progress_bar=False)
    for c, s in zip(candidates[:rerank_top_n], scores):
        c.rerank_score = float(s)
    head = candidates[:rerank_top_n]
    head.sort(key=lambda x: x.rerank_score or 0.0, reverse=True)
    debug["post_rerank_min_similarity"] = post_min_sim
    debug["post_rerank_head_count"] = len(head)
    if apply_min_similarity:
        filtered = [c for c in head if c.vector_similarity >= post_min_sim]
    else:
        filtered = list(head)
    debug["post_rerank_after_sim_filter"] = len(filtered)
    final = filtered[:final_n]
    if not final and head:
        debug["abstain_reason"] = "post_rerank_similarity_filter"
        return [], debug
    if min_rerank is not None and final:
        top_rs = final[0].rerank_score
        if top_rs is None or float(top_rs) < min_rerank:
            debug["abstain_reason"] = "below_rerank_threshold"
            debug["top_rerank_score"] = top_rs
            debug["min_rerank_score_applied"] = min_rerank
            return [], debug
    debug["reranked"] = [
        {
            "chunk_id": c.chunk_id,
            "rerank_score": c.rerank_score,
            "vector_similarity": round(c.vector_similarity, 4),
        }
        for c in final
    ]
    return final, debug


def retrieve_and_rerank(
    query: str,
    *,
    filter_company: str | None = None,
    filter_company_like: str | None = None,
    filter_season: str | None = None,
    filter_doc_types: list[str] | None = None,
    apply_min_similarity: bool = True,
) -> tuple[list[RetrievedChunk], dict[str, Any]]:
    if _vector_store_mode() == "sqlite_numpy":
        return _retrieve_sqlite_numpy(
            query,
            filter_company=filter_company,
            filter_company_like=filter_company_like,
            filter_season=filter_season,
            filter_doc_types=filter_doc_types,
            apply_min_similarity=apply_min_similarity,
        )

    from campus_rag.embed_store import query_vectors

    cfg = settings()
    top_k = int(cfg["retrieve_top_k"])
    rerank_top_n = int(cfg["rerank_top_n"])
    final_n = int(cfg["final_context_chunks"])
    min_sim = float(cfg["min_similarity"])
    post_min_sim = _post_rerank_min_similarity(cfg)
    min_rerank = _min_rerank_score_threshold(cfg)

    fc, fs = filter_company, filter_season
    fcl = filter_company_like.strip() if filter_company_like and filter_company_like.strip() else None
    fdt = filter_doc_types
    relaxation: list[str] = []
    over_n = min(max(top_k * 15, top_k), 400) if fcl else top_k

    where = _build_where(fc, fs, fdt, company_like=fcl)
    raw = query_vectors(query, top_k=over_n, where=where)
    candidates = _chroma_raw_to_candidates(raw)
    if fcl:
        candidates = [c for c in candidates if fcl in (c.company or "")]
        candidates = candidates[:top_k]

    had_company = bool(fc or fcl)
    if not candidates and had_company:
        where = _build_where(None, fs, fdt, company_like=None)
        raw = query_vectors(query, top_k=top_k, where=where)
        candidates = _chroma_raw_to_candidates(raw)
        if candidates:
            relaxation.append("dropped_company_filter")

    if not candidates:
        where = _build_where(None, None, fdt, company_like=None)
        raw = query_vectors(query, top_k=top_k, where=where)
        candidates = _chroma_raw_to_candidates(raw)
        if candidates:
            relaxation.append(
                "full_corpus" if not relaxation else "full_corpus_after_empty"
            )

    debug: dict[str, Any] = {
        "vector_store": "chroma",
        "where": where,
        "filter_company_like": fcl,
        "filter_doc_types": fdt,
        "apply_min_similarity": apply_min_similarity,
        "filters_attempted": {"company": fc, "company_like": fcl, "season": fs},
        "metadata_relaxation": relaxation,
        "retrieve_query": query[:500],
        "candidate_count": len(candidates),
        "top1_vector_similarity": candidates[0].vector_similarity if candidates else None,
    }

    if not candidates:
        debug["abstain_reason"] = "no_hits"
        return [], debug

    if apply_min_similarity and candidates[0].vector_similarity < min_sim:
        debug["abstain_reason"] = "below_similarity_threshold"
        return [], debug

    rerank = get_reranker()
    pairs = [(query, c.text) for c in candidates[:rerank_top_n]]
    scores = rerank.predict(pairs, show_progress_bar=False)
    for c, s in zip(candidates[:rerank_top_n], scores):
        c.rerank_score = float(s)
    head = candidates[:rerank_top_n]
    head.sort(key=lambda x: x.rerank_score or 0.0, reverse=True)
    debug["post_rerank_min_similarity"] = post_min_sim
    debug["post_rerank_head_count"] = len(head)
    if apply_min_similarity:
        filtered = [c for c in head if c.vector_similarity >= post_min_sim]
    else:
        filtered = list(head)
    debug["post_rerank_after_sim_filter"] = len(filtered)
    final = filtered[:final_n]
    if not final and head:
        debug["abstain_reason"] = "post_rerank_similarity_filter"
        return [], debug
    if min_rerank is not None and final:
        top_rs = final[0].rerank_score
        if top_rs is None or float(top_rs) < min_rerank:
            debug["abstain_reason"] = "below_rerank_threshold"
            debug["top_rerank_score"] = top_rs
            debug["min_rerank_score_applied"] = min_rerank
            return [], debug
    debug["reranked"] = [
        {
            "chunk_id": c.chunk_id,
            "rerank_score": c.rerank_score,
            "vector_similarity": round(c.vector_similarity, 4),
        }
        for c in final
    ]
    return final, debug


def hydrate_chunks(chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    conn = connect()
    init_schema(conn)
    return get_chunk_rows(conn, chunk_ids)
