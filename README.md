# 校招助手

+ 混合检索+Cross-Encoder 重排 + DeepSeek 生成；Streamlit 演示）。

## 环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY
```

## 快速开始

1. 放入样例或自建数据到 `data/campus_kb/`，或配置 `config/crawl.yaml` 后执行爬虫。
2. 入库与建索引：

```bash
python scripts/ingest_folder.py --dir data/campus_kb
python scripts/build_index.py
```

3. 启动界面：

```bash
streamlit run streamlit_app.py
```

## 配置

- `config/settings.yaml`：分块、检索、阈值、模型名；`embedding_backend` 为 `openai` 时使用 API 嵌入（需 `OPENAI_API_KEY`）；`vector_store` 为 `sqlite_numpy` 时向量存 SQLite；为 `chroma` 时用 Chroma 持久化目录。
- `config/crawl.yaml`：爬虫 URL 列表与限速。
- `.env`：`DEEPSEEK_API_KEY`；若使用在线嵌入则再加 `OPENAI_API_KEY`。

切换 `local` / `openai` 嵌入后，向量维度会变，请删除旧 Chroma 数据后重新执行 `python scripts/build_index.py`。

## 目录说明

- `campus_rag/`：核心库（数据库、分块、检索、Agent、爬虫）。
- `scripts/`：入库、建索引、爬虫入口。
- `data/`：默认 SQLite、Chroma、原始知识文件。
