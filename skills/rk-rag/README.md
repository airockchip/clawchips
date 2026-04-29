# rk-rag

快速搜索索引构建脚本（单个 Markdown 文档读取 + 结构化切分 + embedding + SQLite 落库）。

## 功能
- 只读取单个 Markdown 文档（`.md` / `.markdown`）
- 按 Markdown 标题层级切分 chunk（结构优先）
- 表格与代码块保持完整，不会在中间切断
- `chunk-size` 作为软上限，仅在同一标题下合并块时使用
- 调用 OpenAI-compatible `POST /v1/embeddings` 接口提取向量
- embedding 请求固定逐条发送（batch-size=1）
- 存储 `chunk_text + embedding` 到 SQLite

## 脚本
- `scripts/rk_rag.py`

## 示例
```bash
python3 skills/rk-rag/scripts/rk_rag.py index \
  --input-file /path/to/doc.md \
  --db /path/to/quick_search.db
```

```bash
python3 skills/rk-rag/scripts/rk_rag.py search \
  --db /path/to/quick_search.db \
  --query "你的查询内容" \
  --topk 5
```

查询模式使用如下 dense 相似度计算：
`dense_scores = (embeddings_query_dense @ embeddings_doc_dense.T)`

## 表结构
- `documents(path, sha256, updated_at)`
- `chunks(document_id, chunk_index, chunk_text, start_char, end_char, embedding, embedding_dim, embedding_model, created_at)`

其中 `embedding` 以 `float32` little-endian `BLOB` 存储。
