# BookRecall

BookRecall 是一个面向长篇阅读场景的本地阅读记忆 Agent。

它的目标不是“替你读书”，而是帮你在读完很久之后，仍然能快速找回：

- 某个人物第一次出现在哪一章
- 某个道具后来还出现过没有
- 某条线索在你已读范围内是怎么发展的
- 某个主题在前后章节里发生了什么变化

和普通 RAG 不同，BookRecall 不是只靠向量检索。它把结构化实体索引、章节级上下文、细粒度证据片段和一个可控的 ReAct Agent 组合起来，优先解决“第一次出现”“有没有再出现”“不要剧透”这类阅读回忆问题。
![Uploading PixPin_2026-07-04_14-30-28.png…]()

## 项目特点

- 本地三层索引：章节解析 -> parent/child chunk -> 结构化实体索引。
- 强顺序问题可精确回答：例如“第一次出现在哪一章”。
- 三重防剧透：用户阅读进度会限制检索、工具调用和最终证据输出。
- 默认零运行时依赖：核心链路只用 Python 标准库。
- 可选本地 embedding：支持 `sentence-transformers` 本地语义检索。
- 可选外部大模型：支持 OpenAI-compatible API，例如 DeepSeek。
- 内置网页端：可以查看书库、设置阅读进度、切换检索器、配置外部 API、查看本地模型状态。

## 当前能力

截至当前代码状态，BookRecall 已经具备以下能力：

- CLI 可用：
  - `build`
  - `ask`
  - `set-progress`
  - `show-progress`
  - `list-books`
  - `list-entities`
  - `chapters`
  - `stats`
  - `clear`
  - `serve`
  - `models`
  - `embed-build`
  - `embed-search`
- Agent 可用：
  - 手写 ReAct 状态机
  - 规则策略 `RuleBasedPolicy`
  - 可选云端策略 `LLMReActPolicy`
  - LangGraph 预留接口
- Web 可用：
  - 书库总览
  - 实体索引浏览
  - 章节概览
  - 阅读进度管理
  - 问答卡片
  - 检索器切换
  - DeepSeek / OpenAI-compatible API 设置
  - 本地 embedding 与向量索引状态查看
- 本地 embedding 可用：
  - `sentence-transformers`
  - 推荐模型：`BAAI/bge-small-zh-v1.5`
  - 本地向量索引保存到 `.bookrecall/vectors/`

如果你想看“已经实现了什么、还差什么”，请看 [AGENT_STATUS.md](/D:/BookRecall/AGENT_STATUS.md)。

## 技术架构

```text
User Question
   |
   v
BookRecall Agent
   |- Policy
   |  |- RuleBasedPolicy
   |  |- LLMReActPolicy (optional)
   |  `- LangGraphPolicy (placeholder)
   |
   |- Tools
   |  |- lookup_first_appearance
   |  |- lookup_timeline
   |  |- search_evidence
   |  |- lookup_entity_aliases
   |  |- get_chapter_summary
   |  `- list_entities
   |
   |- Retriever
   |  |- LocalRetriever
   |  `- EmbeddingRetriever (optional)
   |
   `- Render
      |- text
      `- json

Local Storage Layer
   |- SQLite
   |- chapters
   |- parent_chunks
   |- child_chunks
   |- entities / aliases / mentions
   `- reader_state

Optional Cloud Layer
   `- OpenAI-compatible Chat Completions
```

## 依赖说明

### 核心模式

核心模式默认没有第三方运行时依赖。

- Python `>=3.11`
- SQLite 使用 Python 标准库内置模块
- Web 使用 Python 标准库 `http.server`
- 云端 API 调用使用 Python 标准库 `urllib`

也就是说，最基础的 `build / ask / serve` 可以不安装任何额外包。

### 可选依赖

`pyproject.toml` 中目前定义了这些可选依赖组：

- `embedding`
  - `numpy>=1.26.0`
  - `sentence-transformers>=3.0.0`
- `full`
  - `numpy>=1.26.0`
  - `langgraph>=0.2.0`
  - `llama-index>=0.11.0`
  - `faiss-cpu>=1.8.0`
  - `sentence-transformers>=3.0.0`
  - `streamlit>=1.36.0`

注意：

- 当前代码已经实际使用的是 `embedding` 这一组。
- `full` 里的很多能力还没有全部在代码中接通，它更像未来路线预留。
- `cloud` 依赖组目前为空，因为云端 API 走的是标准库。

## 安装方式

### 方式一：直接运行仓库

```bash
git clone <your-repo-url>
cd BookRecall
python bookrecall.py --help
```

这是最简单的方式，不需要先安装成包。

### 方式二：开发模式安装

```bash
git clone <your-repo-url>
cd BookRecall
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

安装后可以直接使用：

```bash
bookrecall --help
```

### 安装本地 embedding 能力

```bash
pip install -e .[embedding]
```

如果你在 Windows + NVIDIA GPU 上使用，也可以手动先装好适配你 CUDA 版本的 `torch`，再安装：

```bash
pip install sentence-transformers
```

## 快速开始

### 1. 用示例书建索引

```bash
python bookrecall.py build \
  --book-id sample \
  --input examples/sample_book.txt \
  --entities examples/sample_entities.txt
```

### 2. 设置阅读进度

```bash
python bookrecall.py set-progress \
  --book-id sample \
  --user default \
  --chapter 3
```

### 3. 提问

```bash
python bookrecall.py ask \
  --book-id sample \
  --question "黑袍人第一次出现在哪一章？"
```

### 4. 输出 JSON 卡片

```bash
python bookrecall.py ask \
  --book-id sample \
  --format json \
  --question "黑袍人第一次出现在哪一章？"
```

## Web 界面

启动本地网页：

```bash
python bookrecall.py serve --host 127.0.0.1 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

网页端当前支持：

- 选择书籍
- 查看书库统计
- 查看实体索引
- 查看章节概览
- 设置用户阅读进度
- 提交问答
- 选择检索器：`lexical / embedding / auto`
- 查看本地模型依赖状态
- 查看每本书是否已有向量索引
- 直接配置外部 OpenAI-compatible API
- 快速套用 DeepSeek / OpenAI 预设

说明：

- API Key 不会保存在服务端文件中。
- 如果勾选“保存”，只会保存在当前浏览器的 `localStorage`。

## 本地 embedding 用法

### 查看模型状态

```bash
python bookrecall.py models
```

### 构建向量索引

```bash
python bookrecall.py embed-build \
  --book-id sample \
  --model BAAI/bge-small-zh-v1.5
```

可选参数：

- `--batch-size`
- `--vector-dir`
- `--limit-chunks`

### 直接做 embedding 检索

```bash
python bookrecall.py embed-search \
  --book-id sample \
  --query "黑袍人后来还出现过吗？" \
  --progress 3
```

### 在问答里启用 embedding 检索

```bash
python bookrecall.py ask \
  --book-id sample \
  --retriever embedding \
  --question "这本书前面关于自由意志的观点是什么？"
```

也可以用自动模式：

```bash
python bookrecall.py ask \
  --book-id sample \
  --retriever auto \
  --question "这本书前面关于自由意志的观点是什么？"
```

自动模式的行为是：

- 如果该书已有向量索引且本地依赖可用，则使用 embedding 检索。
- 否则自动回退到倒排检索。

## 外部 API 用法

BookRecall 支持 OpenAI-compatible Chat Completions 接口。

默认读取这些环境变量：

```bash
BOOKRECALL_API_KEY
BOOKRECALL_API_ENDPOINT
BOOKRECALL_MODEL
```

例如：

```bash
export BOOKRECALL_API_KEY="sk-xxx"
export BOOKRECALL_API_ENDPOINT="https://api.deepseek.com/v1/chat/completions"
export BOOKRECALL_MODEL="deepseek-chat"
```

或者在网页端直接填写：

- Endpoint
- Model
- API Key
- 启用外部大模型 ReAct 规划

当前云端模型主要用于：

- 复杂问题的多步规划
- 对多个证据片段做综合
- 给出更自然的最终总结

它不会替代本地索引层，也不会绕过防剧透限制。

## CLI 命令总览

### 索引与书库

- `build`
  - 为一本书建立 SQLite 索引
- `list-books`
  - 查看当前书库
- `stats`
  - 查看索引规模
- `chapters`
  - 查看章节标题
- `clear`
  - 删除某本书的索引，需要 `--yes`

### 阅读状态

- `set-progress`
  - 保存阅读进度
- `show-progress`
  - 查看阅读进度

### 问答与检索

- `ask`
  - 提问并输出记忆卡片
- `list-entities`
  - 查看实体索引
- `models`
  - 查看本地模型状态
- `embed-build`
  - 构建向量索引
- `embed-search`
  - 直接做向量检索

### Web

- `serve`
  - 启动本地 Web 控制台

## 实体词表格式

支持手工实体词表，每行一个实体。

格式：

```text
标准名|别名1,别名2
```

例如：

```text
星辰之匙|钥匙,星匙
黑衣人|黑袍人,黑衣客
自由意志
```

如果不传 `--entities`，系统会尝试自动发现实体。

## 输出结构

BookRecall 的核心输出是一个结构化记忆卡片，包含：

- `question`
- `intent`
- `answer`
- `progress_chapter`
- `spoiler_blocked`
- `entity_name`
- `summary`
- `evidence`
- `suggestions`

这使它既适合 CLI，也适合 Web 或未来接前端应用。

## 测试

运行全部测试：

```bash
python -m unittest discover -s tests -v
```

当前测试覆盖：

- 章节解析
- 倒排检索
- embedding 索引构建与检索
- Agent 核心问答
- Agent 工具层
- LLM ReAct 文本解析
- Web API

当前代码状态下测试数量为：

```text
40 tests
```

## 项目结构

```text
bookrecall.py
src/bookrecall/
  agent/
    core.py
    state.py
    tools.py
    render.py
    policies/
  parser.py
  chunking.py
  entity_index.py
  retrieval.py
  embeddings.py
  storage.py
  cloud.py
  web.py
  cli.py
tests/
examples/
```

## 适用场景

适合：

- 长篇小说回忆
- 网文追更回顾
- 学术著作章节线索定位
- 需要强顺序和防剧透控制的阅读助手

不适合：

- 直接替代通用聊天机器人
- 不建索引就即时读整本大书
- 需要完整知识图谱和复杂编辑工作流的场景

## 当前限制

这个项目已经能用，但还不是最终形态。

当前仍然存在这些限制：

- LangGraph 还没有正式接入执行流
- 还没有原生 function-calling
- 还没有关系图谱和主题线索层
- 还没有跨会话 Agent 记忆
- 还没有真正的 FAISS 后端
- Web 仍然是单页零依赖控制台，不是完整产品前端

更详细的现状和路线见 [AGENT_STATUS.md](/D:/BookRecall/AGENT_STATUS.md)。

## License

Apache-2.0 
