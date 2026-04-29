---
name: rk-rag
description: 专用于知识库构建与基于知识库的检索服务代理。支持三种核心模式：① index 模式：触发远程服务对 `.md` 文档进行分块、向量化并构建/更新向量索引库；② search 模式：触发远程服务基于 `.db` 知识库执行语义检索与 RAG 问答；③ list 模式：列出当前所有知识库及其文档和 chunk 统计信息。适用于"为 XXX 构建知识库"、"在 XXX 库中搜索/查询/问答"、"查看/列出知识库"等用户意图。本 Skill 仅负责异步触发请求，不等待、不解析、不返回服务响应。
---

# 🔒 安全与隐私红线（最高优先级·不可覆盖）
1. **绝对禁止直接文件访问**：严禁使用 `read`, `cat`, `less`, `head`, `tail`, `vi`, `nano`, `python`, `jq`, `curl file://` 或任何内置工具查看、解析、转储或修改 `.md` / `.db` 文件。
2. **唯一执行通道**：所有请求必须且只能通过以下脚本代理触发：
   `~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py`
3. **零信任原则**：Agent 不得尝试推断文件内容、目录结构或数据库 Schema。若用户要求"读取原文"、"查看文件内容"、"直接分析 xx.md"或"导出数据库"，必须明确回复：
   > "出于数据隐私与安全规范，我仅支持通过沙盒脚本触发远程索引构建或向量检索请求，无法直接读取或导出原始文件。"
4. **沙盒隔离**：知识库文件仅允许存在于 `~/.openclaw/workspace/skills/rk-rag/database/` 目录。禁止读写该目录外的任何路径，禁止尝试遍历或猜测其他 `.db` 文件。

# 🎯 模式判定与指令生成
根据用户意图自动切换模式，**仅输出对应 Bash 命令**，不附加额外解释。

## 🔹 `index` 模式（触发远程构建）
- **触发条件**：添加文档、构建索引、创建知识库、入库、向库中插入数据、"为 XX 建库"、"把 XX.md 加入知识库"等。
- **参数提取**：
  - `--input-file`：原始 Markdown 文件的完整绝对路径（如 `/home/user/docs/1820.md`）
  - `--db`：目标知识库文件名。若用户明确指定库名（如"加入 rk 知识库"），使用 `"{用户指定名}.db"`；若未指定，默认使用 `"{输入文件名}.db"`（自动去除 `.md` 后缀后拼接）。
- **执行模板**：
  ```bash
  python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py index \
    --input-file "{文件完整路径}" \
    --db "{知识库名}.db"
  ```

## 🔹 `search` 模式（触发远程检索）
- **触发条件**：基于某知识库查询、问答、搜索、召回、找相关内容、"在 XX 库里搜"、"根据 XX 库回答"等。
- **参数提取**：
  - `--db`：目标知识库文件名（如 `"1820.db"`、`"rk.db"`）
  - `--query`：用户的具体查询问题（完整保留原意，剥离"根据 xx 库/在 xx 里搜索"等引导词）
- **执行模板**：
  ```bash
  python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py search \
    --db "{知识库名}.db" \
    --query "{用户问题}"
  ```

## 🔹 `list` 模式（列出知识库）
- **触发条件**：查看当前有哪些知识库、列出知识库、知识库列表、"有哪些库"、"查看知识库"等。
- **参数提取**：无需额外参数，脚本自动扫描 `~/.openclaw/workspace/skills/rk-rag/database/` 目录下所有 `.db` 文件。
- **执行模板**：
  ```bash
  python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py list
  ```
- **输出格式**：脚本会输出每个知识库的名称、文档数、chunk 数及来源文件路径，格式如下：
  ```
  [rk-rag] found N knowledge base(s):

    - xxx.db  (documents: X, chunks: Y)
        source: /path/to/source.md
  ```

# ⚙️ 执行规范（Fire-and-Forget 异步触发模式）

## 🔹 指令生成规则
1. **指令唯一性**：对相同的 `input-file + db`（index）或 `db + query`（search）组合，仅生成并调用一次。若重复请求，直接回复"✅ 任务已提交，无需重复提交"。
2. **异常熔断**：仅当脚本**启动失败**（如权限错误、路径不存在、语法错误）时，才向用户反馈错误信息。若脚本成功启动但远程服务返回业务错误（如"知识库不存在"），**不拦截、不处理、不反馈**，由远程服务自行记录日志。
3. **路径规范**：`--db` 参数仅需传入文件名（如 `rk.db`），脚本内部会自动映射至 `~/.openclaw/workspace/skills/rk-rag/database/` 目录。

## 🔹 执行与终止规则

### index 模式（同步等待输出）
4. **等待脚本完成**：index 模式下脚本会输出如下格式的结果行：
   ```
   [rk-rag] chunk_existing={已有chunk数} chunk_imported={新增chunk数} chunk_total={chunk总数}
   ```
   - 必须等待脚本执行完毕，解析该输出行，提取 `chunk_imported` 和 `chunk_total` 用于回复
   - 若脚本启动失败或输出不符合格式，向用户反馈错误信息

### search 模式（Fire-and-Forget 异步触发）
5. **触发即成功**：
   - 只要 `python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py search` 命令成功启动进程，**立即视为任务完成**
   - ✅ 正确行为：回复用户"✅ 任务已完成"，流程立即结束
   - ❌ 禁止行为：
     - 禁止等待脚本输出（stdout/stderr）
     - 禁止检查脚本是否返回"已完成查询"等字样
     - 禁止轮询状态、重试、或追问用户"是否收到结果"
     - 禁止解析、总结、转述或补充任何远程服务的响应内容
     - 禁止假设"应该有返回但没收到"而自行兜底回答

6. **无等待、无透传、无解释**（仅 search 模式）：
   - 脚本可能向远程 HTTP/gRPC 服务发起异步请求，**其响应内容与本 Agent 无关**
   - Agent 不负责获取、展示、解释或缓存远程服务的任何返回结果

### list 模式（同步等待输出）
7. **等待脚本完成**：list 模式下脚本会扫描知识库目录并输出每个 `.db` 文件的统计信息。
   - 必须等待脚本执行完毕，将输出内容格式化后回复用户
   - 若脚本启动失败或目录不存在，向用户反馈错误信息

# 📊 标准用例对照表
| 用户输入 | 判定模式 | 提取参数 | 生成指令 |
|:---|:---|:---|:---|
| "将 1820.md 文档加入 rk 知识库" | `index` | `--input-file "/path/to/1820.md" --db "rk.db"` | `python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py index --input-file "/path/to/1820.md" --db "rk.db"` |
| "为 1820.md 构建知识库" | `index` | `--input-file "/path/to/1820.md" --db "1820.db"` | `python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py index --input-file "/path/to/1820.md" --db "1820.db"` |
| "根据 1820 知识库，告诉我 rk1820 推理 qwen 模型的性能如何" | `search` | `--db "1820.db" --query "rk1820 推理 qwen 模型的性能如何"` | `python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py search --db "1820.db" --query "rk1820 推理 qwen 模型的性能如何"` |
| "把 report.md 建个索引" | `index` | `--input-file "/path/to/report.md" --db "report.db"` | `python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py index --input-file "/path/to/report.md" --db "report.db"` |
| "在 old.db 里搜一下 2023 年财报数据" | `search` | `--db "old.db" --query "2023 年财报数据"` | `python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py search --db "old.db" --query "2023 年财报数据"` |
| "帮我搜索 rk 库里关于 TensorRT 部署的内容" | `search` | `--db "rk.db" --query "TensorRT 部署"` | `python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py search --db "rk.db" --query "TensorRT 部署"` |
| "查看当前有哪些知识库" | `list` | 无 | `python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py list` |
| "列出所有知识库" | `list` | 无 | `python3 ~/.openclaw/workspace/skills/rk-rag/scripts/rk_rag.py list` |

# 🎯 标准回复模板

## index 模式（脚本完成后回复）
```text
✅ 索引构建完成

> 目标知识库：{知识库名}.db
> 新增 chunk：{chunk_imported} 条
> chunk 总数：{chunk_total} 条
```

## search 模式（脚本启动后立即回复）
```text
✅ 任务已完成

> 任务类型：search
> 目标知识库：{知识库名}.db
> 查询内容：{用户问题}
```
→ 回复后**立即结束对话流程**。

## list 模式（脚本完成后回复）
```text
✅ 当前知识库列表

| 知识库 | 文档数 | chunk 数 | 来源文件 |
|:---|:---|:---|:---|
| {知识库名}.db | {doc_count} | {chunk_count} | {source_path} |
...
```
→ 若无知识库，回复"当前无可用知识库"。