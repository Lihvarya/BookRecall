# BookRecall

BookRecall 是一个面向长篇阅读回忆场景的本地 Agent MVP。它的目标不是泛泛回答“这本书讲了什么”，而是尽可能快地帮用户找回已经读过、但一时想不起来的关键信息，比如人物首次登场、道具出现轨迹、某个观点前后变化，以及特定情节的上下文证据。

当前版本优先交付一个不依赖额外 Python 三方库、可直接运行的工程底座：

- 本地章节解析
- Parent / Child 分层分块
- SQLite 结构化实体索引
- 实体别名解析
- 基于阅读进度的防剧透检索
- 命令行问答入口
- 多本书管理命令
- 结构化 JSON 记忆卡片输出
- 可选云端大模型总结接口

## 为什么这样落地

你提供的目标架构是：

- 本地层：实体索引 + 分层检索 + 元数据过滤
- 云端层：大模型做复杂推理和总结
- Agent 层：根据问题类型决定调用哪类检索

仓库当前是空目录，而本机也还没有安装 `langgraph`、`llama-index`、`faiss`、`sentence-transformers`、`streamlit`。因此我先做了一个“纯标准库即可运行”的 MVP，把最重要的产品逻辑和工程边界先搭起来，同时在代码里预留了后续升级点。

## 当前项目结构

```text
src/bookrecall/
  agent.py          # 问题分类 + 防剧透回答流程
  chunking.py       # Parent / Child 分层切块
  cloud.py          # 可选 OpenAI 兼容接口
  config.py         # 分块和检索配置
  entity_index.py   # 实体词表加载与出现记录构建
  parser.py         # 章节解析
  retrieval.py      # 轻量本地检索
  storage.py        # SQLite 存储
  cli.py            # 命令行入口
  web.py            # 零依赖本地 Web 服务和浏览器界面
examples/
  sample_book.txt
  sample_entities.txt
tests/
  test_agent.py
  test_web.py
```

## 已实现能力

### 1. 三层索引的 MVP 映射

虽然当前还没接 FAISS，但逻辑结构已经对应上了：

1. Level 0：`child_chunks`
   用较小文本片段做精细匹配。
2. Level 1：`parent_chunks`
   保留章节上下文，防止只取到零散句子。
3. Level 2：`entities` + `entity_mentions`
   解决“第一次出现在哪”“出现轨迹如何”这种纯向量检索不擅长的问题。

### 2. 防剧透阅读进度

用户可以通过 `set-progress` 记录已读章节，BookRecall 在检索和回答时只使用该范围内的证据。如果实体首次出现章节在已读范围之后，系统会直接返回“当前范围内尚未出现”，不会暴露具体章节。

### 3. 问题类型分流

当前实现了一个轻量版 Agent 路由：

- `第一次 / 首次 / 最早` -> 实体首次出现定位
- `后来 / 还有出现 / 轨迹` -> 实体出现轨迹追踪
- `怎么 / 如何 / 为什么` -> 因果类语义检索
- `变化 / 对比 / 前后` -> 对比类语义检索
- 其他 -> 通用语义回忆

### 4. 这次新增的增强

1. 多本书管理
   可以直接列出已经建过索引的书，而不是只靠记住 `book_id`。
2. 实体别名
   词表支持“标准名 + 别名”，比如“黑衣人|黑袍人,黑衣客”，提问时会自动解析回标准实体。
3. 结构化记忆卡片
   `ask` 命令除了文本输出，还支持 JSON 输出，便于后续接 Web 界面、聊天前端或 API。
4. 章节摘要缓存
   建索引时会为每章生成一个轻量摘要，方便后续做更强的章节对比和 UI 展示。

## 快速开始

### 1. 建立索引

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
  --chapter 3
```

### 3. 提问

```bash
python bookrecall.py ask \
  --book-id sample \
  --question "【星辰之匙】第一次出现在哪一章？"
```

输出 JSON 版记忆卡片：

```bash
python bookrecall.py ask \
  --book-id sample \
  --format json \
  --question "黑袍人第一次出现在哪一章？"
```

或临时指定阅读进度：

```bash
python bookrecall.py ask \
  --book-id sample \
  --progress 2 \
  --question "黑衣人后来还有出现过吗？"
```

### 4. 查看书单与实体索引

```bash
python bookrecall.py list-books
python bookrecall.py list-entities --book-id sample
```

### 5. 启动本地 Web 界面

```bash
python bookrecall.py serve --host 127.0.0.1 --port 8000
```

启动后在浏览器打开：

```text
http://127.0.0.1:8000
```

当前 Web 界面支持：

- 选择已建索引的书籍
- 查看实体索引和别名
- 保存用户阅读进度
- 直接提问并看到结构化记忆卡片
- 点击推荐追问继续追溯情节

## 实体词表建议

当前版本为了保证“首次出现”这类问题的稳定性，建议优先提供实体词表文件。支持两种格式：

1. 仅标准名：每行一个实体
2. 标准名 + 别名：`标准名|别名1,别名2`

例如：

```text
星辰之匙|钥匙,星匙
黑衣人|黑袍人,黑衣客
自由意志
灰塔
```

如果不提供，系统会自动从 `【】`、`《》`、`「」` 中提取一批候选实体，但准确率不如手工词表高。

## 可选云端推理

如果环境变量里已经有 API Key，`ask` 命令会在语义类问题上优先调用 OpenAI 兼容接口进行总结，否则退回本地规则总结。

支持的环境变量：

- `BOOKRECALL_API_KEY` 或 `OPENAI_API_KEY`
- `BOOKRECALL_API_ENDPOINT`（可选）
- `BOOKRECALL_MODEL`（可选，默认 `gpt-4o-mini`）

## 下一步升级路线

这个 MVP 已经能承载你的产品方向，接下来推荐这样演进：

1. 检索层升级
   把 `retrieval.py` 从轻量词法检索升级为 `bge-small-zh-v1.5 + FAISS`，保留当前 SQLite 元数据过滤。
2. Agent 层升级
   把 `agent.py` 改造成真正的 LangGraph 状态图，引入多步规划、错误恢复和工具调用轨迹。
3. 交互层升级
   新增 Streamlit 界面，支持多本书、进度管理、记忆卡片展示和用户笔记。
4. 索引层升级
   增加实体别名、人物关系图、章节摘要缓存、自动主题线索抽取。

## 测试

```bash
python -m unittest discover -s tests -v
```

当前测试覆盖了两层：

- `test_agent.py`：实体首次出现、防剧透、别名解析、JSON 卡片输出
- `test_web.py`：书单接口、实体接口、进度接口、问答接口、首页渲染

## 说明

这是一个工程化 MVP，不是假装“已经把最终形态全做完”。它现在已经能跑通你的核心价值链路：

- 从原始文本建索引
- 记录阅读进度
- 回答首次出现 / 轨迹 / 语义回忆
- 识别实体别名
- 产出结构化记忆卡片
- 管理多本书的本地索引
- 给出可追溯证据

后续你可以直接在这个基础上继续替换检索器、接入云端模型和补 UI。
