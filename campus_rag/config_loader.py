import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from campus_rag.paths import ROOT


def _load_dotenv_files() -> None:
    """优先项目根目录 .env，再尝试当前工作目录（避免在别的 cwd 下启动 Streamlit 时读不到）。"""
    paths = [ROOT / ".env", Path.cwd() / ".env"]
    seen: set[Path] = set()
    for p in paths:
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        if rp in seen:
            continue
        seen.add(rp)
        if p.is_file():
            load_dotenv(p, override=False)


_load_dotenv_files()


def load_yaml(name: str) -> dict:
    path = ROOT / "config" / name
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def settings() -> dict:
    return load_yaml("settings.yaml")


def crawl_config() -> dict:
    return load_yaml("crawl.yaml")


def _clean_secret(value: str) -> str:
    s = (value or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1].strip()
    return s


def deepseek_config() -> dict:
    return {
        "api_key": _clean_secret(os.getenv("DEEPSEEK_API_KEY", "")),
        "base_url": _clean_secret(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")).rstrip("/"),
        "model": _clean_secret(os.getenv("DEEPSEEK_MODEL", "deepseek-chat")),
    }


def deepseek_key_configured() -> bool:
    return bool(deepseek_config()["api_key"])


def tavily_api_key() -> str:
    return _clean_secret(os.getenv("TAVILY_API_KEY", ""))


def openai_embed_config() -> dict:
    """用于 OpenAI 官方或兼容接口的文本嵌入（如 text-embedding-3-small）。"""
    base = _clean_secret(os.getenv("OPENAI_BASE_URL", "")).rstrip("/")
    return {
        "api_key": _clean_secret(os.getenv("OPENAI_API_KEY", "")),
        "base_url": base or None,
    }


def openai_embed_key_configured() -> bool:
    return bool(openai_embed_config()["api_key"])


def embedding_backend() -> str:
    cfg = settings()
    return str(cfg.get("embedding_backend") or "local").strip().lower()
