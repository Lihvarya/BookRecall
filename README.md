# BookRecall

BookRecall 是一个面向长篇阅读场景的本地阅读记忆 Agent。它的目标不是“替你读书”，而是在你读完很久以后，帮你快速找回人物、道具、事件、主题和原文证据。

它特别适合这类问题：

- “某个人物第一次出现在哪一章？”
- “这个道具后来还有出现过吗？”
- “第 50 章那个黑衣人后来是谁？”
- “成为尊者条件是什么？”
- “这本书里关于自由意志的观点前后有什么变化？”

BookRecall 和普通 RAG 的区别在于：它不是只靠向量检索，而是把章节解析、结构化索引、向量召回、重排模型、阅读进度保护和 Agent 工具规划组合在一起，尽量做到“定位准确、有原文证据、不过度剧透”。

![1783338657318](image/README/1783338657318.png)

## 当前状态

BookRecall 目前是一个可运行的本地 Agent MVP，已经具备：

- 本地 SQLite 书库和索引。
- 中文长篇 TXT 章节解析，支持“卷 / 节 / 章”结构。
- Parent / Child 分层切块。
- 实体、关系、主题、事件、章节摘要等结构化索引。
- 阅读进度保护，工具调用和最终证据都会被限制在已读范围内。
- CLI 命令行工具。
- Vue 3 + Vite + TypeScript Web 控制台。
- TXT 文件导入，不在页面预览全文，避免大文件卡住前端。
- Two-Phase Indexing：导入阶段优先构建基础索引和向量索引，复杂结构化理解按需调用本地 LLM。
- Qwen3 Embedding + Qwen3 Reranker 本地召回链路。
- 本地 Qwen / LM Studio / OpenAI-compatible endpoint 接入入口。
- 多轮对话、会话历史、历史轮次编辑、工具调用轨迹展示。

更细的工程状态请看 [AGENT_STATUS.md](AGENT_STATUS.md)。

## 推荐架构

当前推荐链路是：

```text
User Query
   |
   v
BookRecall Agent
   |
   |- Query Understanding / Planner
   |  `- 本地 Qwen3.5-4B 或 OpenAI-compatible API
   |
   |- Tools
   |  |- lookup_first_appearance
   |  |- lookup_timeline
   |  |- lookup_relations
   |  |- search_theme
   |  |- search_events
   |  |- search_evidence
   |  |- search_exact_text
   |  |- lookup_entity_aliases
   |  |- get_chapter_summary
   |  `- list_entities
   |
   |- Retrieval
   |  |- LocalRetriever：倒排检索，稳定兜底
   |  |- EmbeddingRetriever：Qwen3-Embedding-0.6B 粗召回
   |  `- CrossEncoderReranker：Qwen3-Reranker-0.6B 精排
   |
   `- MemoryCard
      |- 回答
      |- 章节定位
      |- 原文证据
      |- 工具 trace
      `- 防剧透状态
```

本地数据层：

```text
D:\BookRecall
   |- .bookrecall/
   |  |- bookrecall.db        SQLite 数据库
   |  `- vectors/             FAISS / numpy 向量索引
   |
   |- .cache/
   |  |- huggingface/         Hugging Face / sentence-transformers 缓存
   |  `- torch/               torch 缓存
   |
   |- models/
   |  |- Qwen3-Embedding-0.6B
   |  |- Qwen3-Reranker-0.6B
   |  `- llm/
   |
   |- frontend/               Vue 前端源码
   |- src/bookrecall/         Python 后端和 Agent
   |- tests/                  单元测试
   |- start_bookrecall.ps1    Windows 一键启动脚本
   `- bookrecall.py           CLI 入口
```

`models/`、`.cache/`、`.bookrecall/` 都不会进入 Git。

## 模型选择

当前默认推荐：

| 用途                        | 推荐模型                         | 说明                                                           |
| --------------------------- | -------------------------------- | -------------------------------------------------------------- |
| Embedding 粗召回            | `Qwen/Qwen3-Embedding-0.6B`    | 替代旧的`BAAI/bge-small-zh-v1.5`，语义召回更强               |
| Reranker 精排               | `Qwen/Qwen3-Reranker-0.6B`     | 对候选证据重新排序，提高命中准确率                             |
| 本地 Agent / 按需结构化理解 | 本地 Qwen3.5-4B 或 Qwen3-4B GGUF | 可通过 LM Studio / llama.cpp / OpenAI-compatible endpoint 接入 |

注意：

- 当前 embedding / reranker 代码使用 `sentence-transformers`，需要 Hugging Face 原版模型目录，不是 GGUF 单文件。
- GGUF 更适合本地对话 LLM，不适合直接填到 embedding / reranker 字段。
- 旧 BGE 向量索引不会自动变成 Qwen 索引。切换 Qwen embedding 后，需要重建向量索引。

## 环境要求

最低要求：

- Python `>=3.11`
- Windows / macOS / Linux 均可，当前项目主要在 Windows 路径下验证
- 基础 CLI 和基础 Web 可只使用 Python 标准库

推荐本地模型配置：

- NVIDIA GPU：RTX 3060 Laptop 6GB 或更高
- CPU：Intel 12700H 级别或更高
- 内存：16GB 起步，32GB 更舒服
- 磁盘：建议预留 10GB 以上

前端开发需要：

- Node.js
- npm

## 安装

### 1. 创建虚拟环境

PowerShell：

```powershell
cd D:\BookRecall
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
```

### 2. 安装 Python 依赖

基础安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

推荐安装本地召回能力：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[embedding,faiss,graph]"
```

如果要在进程内直接加载 GGUF 本地 LLM，可额外安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[local-llm]"
```

说明：

- `embedding` 包含 `numpy` 和 `sentence-transformers`。
- `faiss` 包含 `faiss-cpu`，用于更快的向量索引。
- `graph` 包含 `langgraph`，用于可选图策略。
- 云端 OpenAI-compatible API 调用使用 Python 标准库，不需要额外 cloud 依赖。

### 3. 安装前端依赖

如果你要修改或重新构建 Web 前端：

```powershell
cd D:\BookRecall\frontend
npm install
npm run build
```

仓库里 Python Web 服务会优先读取 `frontend/dist`。如果没有构建产物，会回退到后端内置的 legacy 静态页面。

## 下载本地模型

推荐把模型下载到 `D:\BookRecall\models`，不要放到 C 盘。

PowerShell：

```powershell
cd D:\BookRecall

$env:HF_HOME="D:\BookRecall\.cache\huggingface"
$env:SENTENCE_TRANSFORMERS_HOME="D:\BookRecall\.cache\huggingface\sentence-transformers"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING="1"
$env:HF_HUB_DISABLE_XET="1"
```

下载 Embedding：

```powershell
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen3-Embedding-0.6B', local_dir=r'D:\BookRecall\models\Qwen3-Embedding-0.6B', max_workers=1)"
```

下载 Reranker：

```powershell
.\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen3-Reranker-0.6B', local_dir=r'D:\BookRecall\models\Qwen3-Reranker-0.6B', max_workers=1)"
```

如果网络中断，重新执行同一个命令即可断点续传。完成后目录应类似：

```text
D:\BookRecall\models\Qwen3-Embedding-0.6B
   |- config.json
   |- model.safetensors
   |- modules.json
   |- tokenizer.json
   `- ...

D:\BookRecall\models\Qwen3-Reranker-0.6B
   |- config.json
   |- model.safetensors
   |- modules.json
   |- tokenizer.json
   `- ...
```

BookRecall 会自动把默认模型名：

```text
Qwen/Qwen3-Embedding-0.6B
Qwen/Qwen3-Reranker-0.6B
```

优先映射到：

```text
D:\BookRecall\models\Qwen3-Embedding-0.6B
D:\BookRecall\models\Qwen3-Reranker-0.6B
```

也可以在 Web 设置里直接填写本地模型路径。

## 启动 Web

推荐使用一键脚本：

```powershell
cd D:\BookRecall
.\start_bookrecall.ps1
```

启动后访问：

```text
http://127.0.0.1:8000
```

正常日志会显示：

```text
[BookRecall] Project root: D:\BookRecall
[BookRecall] Python: D:\BookRecall\.venv\Scripts\python.exe
[BookRecall] Local models: D:\BookRecall\models
[BookRecall] Model cache: D:\BookRecall\.cache\huggingface\sentence-transformers
[BookRecall] Using existing Vue frontend build.
[BookRecall] Starting BookRecall Web. Press Ctrl+C to stop.
```

也可以直接启动：

```powershell
.\.venv\Scripts\python.exe bookrecall.py serve --host 127.0.0.1 --port 8000
```

## Web 使用流程

### 1. 导入书籍

进入“导入与重建”页：

- 选择本地 TXT 文件。
- 填写 `book_id` 和书名。
- 如需重新导入同一本书，勾选覆盖。
- 默认可开启“自动构建向量索引”。

导入时页面不会预览全文，只保留文件内容用于发送到本地服务，避免大 TXT 卡住页面。

### 2. 构建向量索引

如果导入时没有自动构建，可以在“书库 / 模型与召回”中手动构建。

默认模型：

```text
Qwen/Qwen3-Embedding-0.6B
```

如果你本地已经下载到 `D:\BookRecall\models\Qwen3-Embedding-0.6B`，保持默认模型名即可，后端会优先使用本地目录。

构建时页面会显示真实 batch 进度：

```text
正在编码 embedding chunk：1024 / 2347
```

### 3. 设置重排

默认启用：

```text
Qwen/Qwen3-Reranker-0.6B
```

默认重排候选数是 `20`。如果机器较慢，可以调成 `10` 或临时关闭 Reranker。如果追求更高精度，可以调到 `50`，但响应会明显变慢。

### 4. 提问

进入“对话”页：

- 选择书籍。
- 设置阅读进度。
- 输入问题。
- 同一会话会连续追问，只有点击“新会话”才会开启新会话。
- 用户问题会立即显示，Agent 回复前会显示思考和工具调用状态。
- 回答会包含定位、证据、工具轨迹和防剧透信息。

## CLI 使用

查看帮助：

```powershell
.\.venv\Scripts\python.exe bookrecall.py --help
```

构建基础索引：

```powershell
.\.venv\Scripts\python.exe bookrecall.py build `
  --book-id gu `
  --title 蛊真人 `
  --input D:\Books\gu.txt
```

查看书库：

```powershell
.\.venv\Scripts\python.exe bookrecall.py list-books
```

设置阅读进度：

```powershell
.\.venv\Scripts\python.exe bookrecall.py set-progress `
  --book-id gu `
  --user default `
  --chapter 100
```

提问：

```powershell
.\.venv\Scripts\python.exe bookrecall.py ask `
  --book-id gu `
  --question "成为尊者条件是什么" `
  --progress 2200 `
  --retriever auto
```

构建向量索引：

```powershell
.\.venv\Scripts\python.exe bookrecall.py embed-build `
  --book-id gu `
  --model Qwen/Qwen3-Embedding-0.6B
```

测试向量召回：

```powershell
.\.venv\Scripts\python.exe bookrecall.py embed-search `
  --book-id gu `
  --query "成为尊者条件是什么"
```

查看本地模型和索引状态：

```powershell
.\.venv\Scripts\python.exe bookrecall.py models
```

## CLI 命令列表

当前主要命令：

| 命令              | 用途                          |
| ----------------- | ----------------------------- |
| `build`         | 为 TXT 书籍建立本地结构化索引 |
| `ask`           | 针对书籍提问                  |
| `set-progress`  | 保存阅读进度                  |
| `show-progress` | 查看阅读进度                  |
| `list-books`    | 列出书库                      |
| `list-entities` | 列出实体索引                  |
| `list-themes`   | 列出主题索引                  |
| `chapters`      | 查看章节解析结果              |
| `stats`         | 查看索引规模                  |
| `clear`         | 删除某本书的索引数据          |
| `serve`         | 启动 Web                      |
| `models`        | 探测依赖、模型和向量索引状态  |
| `embed-build`   | 构建本地 embedding 向量索引   |
| `embed-search`  | 直接测试向量召回              |

## 本地 Qwen / LM Studio 接入

Web 设置页支持配置本地 Qwen：

- Endpoint：推荐填 LM Studio 或 llama.cpp server 的 OpenAI-compatible 地址。
- Model：例如 `qwen3.5-4b`。
- GGUF 路径：只有在使用进程内 `llama-cpp-python` 加载时需要。

如果填写了 endpoint，BookRecall 会优先调用 endpoint，不会再尝试加载 GGUF 路径。

常见 endpoint 示例：

```text
http://127.0.0.1:1234/v1
http://127.0.0.1:8080/v1
```

本地 LLM 当前主要用于：

- 问题理解。
- Agent Planner。
- 按需动态结构化索引。
- 对候选证据做更高层次总结。

## Two-Phase Indexing

BookRecall 当前采用 Two-Phase Indexing：

### Phase 1：导入时快速预索引

导入 TXT 后，系统会：

- 解析章节。
- 切分 parent / child chunk。
- 建立 SQLite 基础索引。
- 可选构建 embedding 向量索引。

这一步尽量不让本地 LLM 全书逐章分析，因为那会非常慢。

### Phase 2：问答时按需理解

用户提问后，系统会：

- 用倒排检索和 embedding 找到候选片段。
- 用 reranker 对候选证据精排。
- 只把少量相关片段交给本地 Qwen 或云端 LLM。
- 将按需分析出的结构化结果写回动态索引。

这样比“导入时让 Qwen 全书扫一遍”快得多，也更适合 3060 级别本地硬件。

## 常见问题

### 为什么明明下载了模型，网页还在下载？

通常是 Web 进程还没重启，或模型没有放在默认目录。

推荐目录：

```text
D:\BookRecall\models\Qwen3-Embedding-0.6B
D:\BookRecall\models\Qwen3-Reranker-0.6B
```

重启：

```powershell
cd D:\BookRecall
.\start_bookrecall.ps1
```

日志应显示：

```text
Local models: D:\BookRecall\models
Model cache: D:\BookRecall\.cache\huggingface\sentence-transformers
```

### 为什么向量索引很慢？

首次加载 Qwen3-Embedding-0.6B 需要加载约 1.15GB 权重。之后还要编码全部 child chunk。几千个 chunk 花几分钟是正常的。

如果想快速试跑，可以设置 `limit_chunks`，或 CLI 使用：

```powershell
.\.venv\Scripts\python.exe bookrecall.py embed-build `
  --book-id gu `
  --limit-chunks 500
```

### 为什么 Reranker 让问答变慢？

Reranker 是 cross-encoder，会对“问题 + 候选片段”逐对打分。它比 embedding 召回更准，但也更慢。

建议：

- 3060 笔记本默认候选数使用 `10-20`。
- 需要更快时关闭 Reranker。
- 需要更准时再调到 `50`。

### 旧 BGE 索引还能用吗？

能用，但不是当前推荐链路。切到 Qwen3-Embedding 后，旧索引需要删除并重建。

Web 中可以删除当前书向量索引，再重新构建。CLI 也可以重新运行 `embed-build`。

### FAISS 缺失怎么办？

安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[faiss]"
```

如果没有 FAISS，系统会回退到 numpy 后端，但大书检索会慢一些。

### LM Studio 返回空 JSON 或 Thinking 内容怎么办？

部分 Qwen thinking 模型会把内容放到 `reasoning_content`，导致 JSON 解析失败。

解决办法：

- 在 LM Studio 关闭 Thinking。
- 确认服务支持 `enable_thinking=false`。
- 提高最大输出 token。
- 优先使用非 thinking 的 instruct 模式或 endpoint。

## 开发与测试

运行后端测试：

```powershell
cd D:\BookRecall
.\.venv\Scripts\python.exe -m unittest discover tests
```

当前验证状态：

```text
129 tests OK
```

构建前端：

```powershell
cd D:\BookRecall\frontend
npm run build
```

当前验证状态：

```text
vue-tsc --noEmit && vite build 通过
```

## 项目边界

BookRecall 目前仍是 MVP，不是完整商业产品。当前重点是：

- 长篇文本的本地索引和召回质量。
- 阅读进度保护。
- Agent 工具规划和证据链回答。
- 本地模型可控接入。

尚未完全完成：

- 完整 LangGraph checkpoint / interrupt / human-in-the-loop。
- 多用户权限系统。
- 多格式电子书解析，例如 EPUB / PDF / DOCX。
- 大规模多书知识库的统一跨书检索。
- 自动化模型安装器。
- 完整桌面应用打包。

## License

本项目使用 [LICENSE](LICENSE) 中声明的许可证。
