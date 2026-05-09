"""将 settings 中的本地模型路径解析为 SentenceTransformer / CrossEncoder 可用的目录。"""

from __future__ import annotations

from pathlib import Path

from campus_rag.paths import ROOT

_WEIGHT_SUFFIXES = frozenset({".bin", ".safetensors", ".pt", ".pth"})


def _has_transformers_weights(model_dir: Path) -> bool:
    if not model_dir.is_dir():
        return False
    for name in ("model.safetensors", "pytorch_model.bin"):
        if (model_dir / name).is_file():
            return True
    for idx in ("model.safetensors.index.json", "pytorch_model.bin.index.json"):
        if (model_dir / idx).is_file():
            return True
    return any(model_dir.glob("model-*.safetensors")) or any(
        model_dir.glob("pytorch_model-*.bin")
    )


def _hub_id_from_models_cache_dirname(folder_name: str) -> str | None:
    """models--BAAI--bge-reranker-v2-m3 -> BAAI/bge-reranker-v2-m3"""
    if not folder_name.startswith("models--"):
        return None
    rest = folder_name[len("models--") :]
    parts = rest.split("--", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0]}/{parts[1]}"


def _hub_fallback_for_incomplete_dir(
    resolved_dir: Path, *, cache_root: Path | None
) -> str | None:
    if cache_root is not None:
        hid = _hub_id_from_models_cache_dirname(cache_root.name)
        if hid:
            return hid
    if resolved_dir.parent.name == "snapshots":
        hid = _hub_id_from_models_cache_dirname(resolved_dir.parent.parent.name)
        if hid:
            return hid
    return None


def _hub_cache_snapshot_dir(cache_root: Path) -> Path | None:
    """HF 默认缓存目录形如 models--Org--Name/snapshots/<rev>/。"""
    snapshots = cache_root / "snapshots"
    if not snapshots.is_dir():
        return None
    ref_main = cache_root / "refs" / "main"
    if ref_main.is_file():
        rev = ref_main.read_text(encoding="utf-8").strip()
        if rev:
            cand = snapshots / rev
            if cand.is_dir():
                return cand
    subdirs = sorted(d for d in snapshots.iterdir() if d.is_dir())
    if len(subdirs) == 1:
        return subdirs[0]
    return subdirs[-1] if subdirs else None


def resolve_sentence_transformers_local_path(ref: str) -> str:
    """路径不存在时原样返回（作 HuggingFace Hub 模型 ID）；相对路径相对项目根。"""
    r = (ref or "").strip()
    if not r:
        return r
    p = Path(r)
    if not p.is_absolute():
        p = ROOT / p
    try:
        p = p.resolve()
    except OSError:
        return r
    if not p.exists():
        return r
    if p.is_file():
        if p.suffix.lower() in _WEIGHT_SUFFIXES:
            p = p.parent
        else:
            return str(p)
    if p.is_dir():
        cache_root: Path | None = None
        inner = _hub_cache_snapshot_dir(p)
        if inner is not None:
            cache_root = p
            p = inner
        if not _has_transformers_weights(p):
            hid = _hub_fallback_for_incomplete_dir(p, cache_root=cache_root)
            if hid is not None:
                return hid
        return str(p)
    return r
