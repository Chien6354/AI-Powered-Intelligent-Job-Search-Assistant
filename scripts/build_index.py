"""从 SQLite chunks 重建 Chroma 向量索引。"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 减少「跑完却像没反应」：尽快看到输出（Windows 下 print 可能被缓冲）
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="从 SQLite chunks 写入向量索引")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--full",
        action="store_true",
        help="清空向量库后全量嵌入（不传参时默认）",
    )
    mode.add_argument(
        "--incremental",
        action="store_true",
        help="仅嵌入尚未写入 chunk_embeddings 的 chunk（不清空已有向量）",
    )
    args = parser.parse_args()
    incremental = bool(args.incremental)

    from campus_rag.db import (
        connect,
        db_path,
        delete_all_chunk_embeddings,
        init_schema,
        iter_chunks_missing_embeddings,
        iter_chunks_with_doc_type,
    )
    from campus_rag.config_loader import embedding_backend, settings
    from campus_rag.embed_store import (
        meta_for_chroma,
        reset_collection,
        upsert_chunks,
        vector_store_mode,
    )

    _log("build_index: start")
    _log(f"  sqlite: {db_path()}")
    _log(f"  embedding_backend: {embedding_backend()}")
    cfg = settings()
    if embedding_backend() == "openai":
        _log(f"  openai_embedding_model: {cfg.get('openai_embedding_model')}")
    _log(f"  vector_store: {vector_store_mode()}")
    _log(f"  mode: {'incremental' if incremental else 'full (default)'}")

    conn = connect()
    init_schema(conn)
    rows = (
        iter_chunks_missing_embeddings(conn)
        if incremental
        else iter_chunks_with_doc_type(conn)
    )
    _log(f"  chunks to index: {len(rows)}")

    if not rows:
        if incremental:
            _log("无待嵌入 chunk（或已全部建索引）。")
        else:
            _log("No chunks — run: python scripts/ingest_folder.py")
            _log("无 chunk，请先 ingest。")
        return

    if incremental:
        pass
    elif vector_store_mode() == "sqlite_numpy":
        _log("clear sqlite chunk_embeddings …")
        delete_all_chunk_embeddings(conn)
    else:
        _log("resetting Chroma collection …")
        reset_collection()
    batch = 32
    for i in range(0, len(rows), batch):
        part = rows[i : i + batch]
        ids = [r["chunk_id"] for r in part]
        texts = [r["text"] for r in part]
        metas = [
            meta_for_chroma(
                r["company"],
                r["season"],
                r["source_url"] or "",
                r["job_title"],
                r["doc_type"],
            )
            for r in part
        ]
        _log(f"embedding batch {i // batch + 1} … ({len(part)} chunks)")
        try:
            upsert_chunks(ids, texts, metas)
        except Exception:
            _log("ERROR in upsert_chunks (see traceback below)")
            traceback.print_exc()
            raise
        _log(f"  indexed {i + len(part)} / {len(rows)}")
    _log("完成。Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("build_index failed:", flush=True)
        traceback.print_exc()
        sys.exit(1)
