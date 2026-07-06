# BookRecall Agent 状态说明

更新时间：`2026-07-06`

本文档面向继续开发 BookRecall 的人，说明当前 Agent 已经实现了什么、离完整产品还有哪些差距、下一步应该优先推进什么。

## 一句话判断

BookRecall 现在已经不是纯脚本 demo，而是一个可运行、可测试、可扩展的本地阅读记忆 Agent MVP。

它当前更准确的定位是：

- 一个本地长文本索引引擎。
- 一个带工具调用和状态管理的阅读回忆 Agent。
- 一个支持本地 embedding、reranker、本地 LLM 和云端 API 的混合检索问答系统。
- 一个已经具备 Vue Web 控制台的个人阅读记忆工具。

它还不是完整产品，主要差距在模型链路稳定性、索引质量自动评估、复杂 Agent 工作流、跨书管理、多格式导入和长期记忆产品化。

## 当前推荐架构

```text
导入阶段
   TXT
   -> 章节解析
   -> Parent / Child 切块
   -> SQLite 结构化基础索引
   -> Qwen3-Embedding-0.6B 向量索引

问答阶段
   用户问题
   -> 本地 Qwen / 规则策略做意图判断
   -> 工具规划
   -> 倒排检索 + embedding 粗召回
   -> Qwen3-Reranker-0.6B 精排
   -> 可选本地 Qwen 按需理解候选片段
   -> MemoryCard 输出
```

这是 Two-Phase Indexing：

- Phase 1 在导入时做快而稳定的基础索引。
- Phase 2 在问答时只分析少量相关片段，避免导入阶段让本地 LLM 扫完整本书。

## 已实现能力

## 1. 数据与索引层

已完成：

- 中文 TXT 章节解析。
- 常见章节标题识别。
- “第一卷 / 第一节 / 第 N 章”等分卷、小节结构识别。
- 无章节标题时回退为整本单章。
- Parent chunk 和 child chunk 分层切块。
- SQLite 持久化。
- 结构化实体索引。
- 结构化关系索引第一版。
- 结构化主题索引第一版。
- 结构化事件链索引第一版。
- 章节摘要表。
- 阅读进度表。
- Agent 会话记忆表。
- 用户偏好表。

SQLite 已覆盖的核心表：

| 表 | 用途 |
| --- | --- |
| `books` | 书籍元信息 |
| `chapters` | 章节原文 |
| `parent_chunks` | 章节级上下文 chunk |
| `child_chunks` | 细粒度检索 chunk |
| `entities` | 实体 |
| `entity_aliases` | 实体别名 |
| `entity_mentions` | 实体出现记录 |
| `relations` | 实体关系 |
| `relation_mentions` | 关系证据 |
| `themes` | 主题线索 |
| `theme_aliases` | 主题别名 |
| `theme_mentions` | 主题证据 |
| `events` | 事件 |
| `event_entities` | 事件关联实体 |
| `chapter_summaries` | 章节摘要 |
| `reader_state` | 阅读进度 |
| `agent_memory` | 会话轮次记忆 |

现状判断：

- 基础索引已经足够支撑“第一次出现”“后来有没有出现”“某章线索后来如何发展”等核心问题。
- 关系、事件、主题的规则抽取仍偏粗糙，已经不适合作为最终高质量结构化知识图谱。
- 后续应更多依赖“embedding 召回 + reranker 精排 + 本地 LLM 按需结构化”的动态索引，而不是导入阶段一次性全书智能抽取。

## 2. 检索与召回层

已完成：

- `LocalRetriever` 倒排检索。
- 中文长问题倒排检索从强交集改为候选 OR + scorer，避免召回为空。
- `EmbeddingRetriever` 本地向量召回。
- Qwen3-Embedding-0.6B 默认模型。
- Qwen3-Reranker-0.6B 精排模型。
- `RerankingRetriever` 粗召回后精排。
- FAISS / numpy 双后端。
- `models / embed-build / embed-search` CLI。
- Web 端模型与召回配置。
- Web 端向量索引构建、删除和状态展示。
- 向量索引构建真实 batch 进度。
- 默认 rerank candidates 从 50 调整为 20，更适合 RTX 3060 Laptop。
- 默认 Qwen 模型名自动映射到 `D:\BookRecall\models` 本地目录。
- `.cache`、`.bookrecall`、`models` 均被 Git 忽略。

当前默认模型：

| 模块 | 默认值 |
| --- | --- |
| Embedding | `Qwen/Qwen3-Embedding-0.6B` |
| Reranker | `Qwen/Qwen3-Reranker-0.6B` |
| Rerank candidates | `20` |
| 向量目录 | `.bookrecall/vectors` |
| 模型目录 | `D:\BookRecall\models` |
| 缓存目录 | `D:\BookRecall\.cache\huggingface\sentence-transformers` |

现状判断：

- 召回质量已经从旧 BGE 路线升级到 Qwen3 Embedding + Reranker 路线。
- 首次构建 Qwen3 embedding 向量索引会明显比 BGE 慢，这是正常的。
- Reranker 对 50 个长片段精排会慢，默认 20 是当前较合理的平衡。
- 旧 BGE 向量索引仍可读取，但不再是推荐链路，需要用户重建。

## 3. Agent 层

已完成：

- 手写 ReAct 状态机。
- `AgentState`。
- `ToolRegistry`。
- 规则策略 `RuleBasedPolicy`。
- 云端策略 `LLMReActPolicy`。
- 本地 Qwen Planner 策略。
- 可选 `LangGraphPolicy`。
- 原生 tool calling 优先，文本协议回退。
- 会话级连续追问。
- 工具调用 trace。
- 工具耗时统计。
- 防剧透触发统计。
- 问答输出统一为 `MemoryCard`。

当前工具：

| 工具 | 用途 |
| --- | --- |
| `lookup_first_appearance` | 查实体首次出现 |
| `lookup_timeline` | 查实体出现轨迹 |
| `lookup_relations` | 查实体关系 |
| `search_theme` | 查主题线索 |
| `search_events` | 查事件链 |
| `search_evidence` | 检索证据片段 |
| `lookup_entity_aliases` | 查实体别名 |
| `get_chapter_summary` | 查章节摘要 |
| `list_entities` | 列出实体 |

近期关键修复：

- “成为尊者条件是什么”这类条件问题已经恢复精准定位。
- 条件/标准/要求类问题会优先从证据段中抽取“第一、第二、第三、第四”等枚举结构。
- 无实体条件类问题会绕开本地 LLM Planner 的误规划，优先走规则策略。
- `search_evidence` 会返回 `parent_text`，便于规则策略从更完整上下文抽取答案。

现状判断：

- Agent 已能执行多步工具调用，不只是单次 RAG。
- 对强结构问题，规则策略仍然很重要，不能完全交给小模型。
- 本地 Qwen Planner 可以提高灵活性，但需要策略约束，避免“聪明但跑偏”。

## 4. 防剧透机制

已完成三重保护：

- 用户阅读进度作为全局上限。
- 工具调用时对 `max_chapter` 二次钳制。
- 结果出栈前裁掉越界证据。

已覆盖：

- CLI `--progress`。
- Web 阅读进度。
- Agent 工具调用。
- 最终 evidence 输出。
- 对话历史中的 progress 记录。

现状判断：

- 防剧透是 BookRecall 当前最重要的差异化能力之一。
- 后续需要把防剧透策略做成更明确的 UI 提示，例如“已隐藏第 N 章之后的证据”。

## 5. Web 端

已完成：

- Vue 3 + Vite + TypeScript + Pinia 前端。
- Python 标准库 HTTP 服务。
- 优先读取 `frontend/dist`，缺失时回退 legacy 静态资源。
- AI 对话助手式布局。
- 左侧会话列表。
- 当前会话连续追问。
- 点击“新会话”才创建新会话。
- 用户输入后立即显示。
- Agent 回复前显示思考状态和工具轨迹。
- 本地 TXT 文件导入。
- 导入时不预览全文。
- 书库管理。
- 书籍分组和标签。
- 删除书籍。
- 删除向量索引。
- 重建结构化索引。
- 构建向量索引。
- 向量索引真实进度条。
- 模型与召回配置。
- 本地 Qwen endpoint / model / GGUF 路径配置。
- Cloud OpenAI-compatible 配置。
- 工具箱调试。
- 证据检索实验。
- 原文阅读器和证据高亮。
- 长会话历史。
- 历史轮次编辑、删除、重新提问。
- 系统诊断。
- 浏览器本地偏好持久化。

已经移除或弱化：

- 用户笔记系统。
- 三栏式拥挤布局。
- 分支对比与合并功能。

现状判断：

- Web 端已经从调试控制台升级为可用的产品原型。
- 页面仍需要继续做信息密度优化。
- 一些模型配置项和索引状态还需要更明确的“当前是否生效”提示。

## 6. 本地模型接入

已完成：

- Qwen3-Embedding-0.6B 本地目录加载。
- Qwen3-Reranker-0.6B 本地目录加载。
- 默认模型名自动映射本地目录。
- `HF_HOME`、`SENTENCE_TRANSFORMERS_HOME`、`TORCH_HOME`、`BOOKRECALL_MODEL_DIR` 项目内路径管理。
- `HF_HUB_DISABLE_XET=1`，减少 Windows 下载卡顿。
- `models/` 加入 `.gitignore`。
- LM Studio / OpenAI-compatible endpoint 输入入口。
- Endpoint 优先，填了 endpoint 就不加载 GGUF。

已验证：

- PyTorch 可识别 `NVIDIA GeForce RTX 3060 Laptop GPU`。
- Qwen3-Embedding-0.6B 可离线加载并输出 1024 维向量。
- Qwen3-Embedding-0.6B 与 Qwen3-Reranker-0.6B 本地目录结构完整。

现状判断：

- 模型文件管理已经基本可用。
- 还缺一个 Web 端“模型自检”按钮，用于直接验证 embedding、reranker、local LLM 是否可加载。
- 还缺更友好的模型下载向导。

## 7. CLI

可用命令：

| 命令 | 状态 |
| --- | --- |
| `build` | 可用 |
| `ask` | 可用 |
| `set-progress` | 可用 |
| `show-progress` | 可用 |
| `list-books` | 可用 |
| `list-entities` | 可用 |
| `list-themes` | 可用 |
| `chapters` | 可用 |
| `stats` | 可用 |
| `clear` | 可用 |
| `serve` | 可用 |
| `models` | 可用 |
| `embed-build` | 可用 |
| `embed-search` | 可用 |

现状判断：

- CLI 足够支撑开发和排障。
- CLI 还没有 reranker 参数和 local planner 参数，主要配置集中在 Web。
- 后续可以补 `ask --rerank`、`ask --policy local_planner` 等高级参数。

## 8. 测试

当前验证：

```text
python -m unittest discover tests
129 tests OK
```

前端验证：

```text
npm run build
vue-tsc --noEmit && vite build 通过
```

测试覆盖：

- 章节解析。
- 倒排检索。
- embedding 索引构建与检索。
- Agent 核心问答。
- Agent 工具层。
- 关系索引、存储、工具和问答链路。
- 主题索引、存储、工具和问答链路。
- 事件索引、存储、工具和问答链路。
- LLM ReAct 文本解析。
- 本地 LLM JSON 解析容错。
- Web API。
- “成为尊者条件是什么”这类结构化条件回答。

现状判断：

- 后端测试已经能防止核心回归。
- 前端缺少自动化 UI 测试。
- 本地模型加载和性能目前主要靠手动验证。

## 仍未完成的关键差距

## 1. 结构化索引质量仍不够好

问题：

- 规则实体抽取容易抽出泛词。
- 关系索引仍偏共现。
- 事件链仍偏关键词。
- 全书 LLM 智能索引太慢，不适合导入阶段默认启用。

当前方向：

- 不再追求导入时一次性全书智能结构化。
- 保留基础结构化索引作为低成本骨架。
- 用 embedding + reranker 在问答期找相关片段。
- 用本地 Qwen 对少量片段做按需结构化。
- 将高置信结果动态写回索引。

下一步：

- 给动态索引结果加入置信度和来源片段。
- 增加“本次回答学到了什么”的可视化。
- 对实体、事件、关系做去重和人工修正入口。

## 2. LangGraph 仍只是可选策略，不是完整工作流

已完成：

- `LangGraphPolicy` 可选接入。
- 未安装时保持零依赖。
- Web 可选择策略。

未完成：

- checkpoint 持久化。
- 中断恢复。
- human-in-the-loop。
- 多节点 planner / retriever / validator / writer 工作流。
- 图状态可视化。

下一步：

- 把当前 ReAct 状态机迁移成真正多节点图。
- 将 query understanding、tool planning、retrieval、rerank、answer validation 拆成独立节点。

## 3. 本地 LLM Planner 还需要更强约束

问题：

- 小模型有时会错误规划工具。
- 无实体问题容易召回偏题。
- JSON 输出仍可能不稳定。
- Thinking 模型可能把答案放在 `reasoning_content`，导致解析失败。

已做修复：

- 条件类问题优先走规则策略。
- JSON 解析加入容错。
- Web 提示关闭 Thinking。
- endpoint 优先，避免误加载 GGUF。

下一步：

- 建立工具选择评测集。
- 对 Planner 输出做 schema 级校验和自动修复。
- 对高风险规划做规则兜底。

## 4. 召回质量需要系统评测

问题：

- 当前主要靠人工问题验证。
- 缺少标准问答集。
- 缺少 MRR / Recall@K / rerank hit rate 指标。
- 不同模型、候选数、chunk 大小之间缺少对比。

下一步：

- 建立 `eval/` 目录。
- 收集 30-100 个真实问题。
- 记录正确章节、正确证据。
- 自动比较 lexical、embedding、embedding+rerank。
- 输出召回指标和慢查询报告。

## 5. 性能仍需优化

当前瓶颈：

- Qwen3-Embedding 首次加载慢。
- 向量索引构建需要编码全部 child chunk。
- Reranker 对长片段和较多候选打分慢。
- 本地 Qwen 生成结构化 JSON 慢。

已做优化：

- Two-Phase Indexing。
- 导入时不默认全书智能 LLM 索引。
- 向量构建真实进度。
- 默认 rerank candidates 降到 20。

下一步：

- 模型常驻进程或懒加载缓存。
- Reranker 批处理和文本截断策略调优。
- 增加“快速模式 / 精准模式”切换。
- 支持后台任务取消。
- 支持索引构建断点续建。

## 6. Web 产品化还不完整

问题：

- 一些面板信息仍偏工程化。
- 模型配置生效状态不够直观。
- 后台任务不能取消。
- 长任务失败后的恢复指引还不够清楚。
- 缺少模型自检页面。

下一步：

- 增加模型自检卡片。
- 增加索引任务取消按钮。
- 增加“当前回答使用了哪些模型”的透明提示。
- 对召回结果增加可展开的评分和重排说明。

## 7. 多格式导入尚未实现

当前只重点支持 TXT。

未完成：

- EPUB。
- PDF。
- DOCX。
- Markdown 目录结构。
- 多文件批量导入。

下一步：

- 优先支持 EPUB。
- 再支持 Markdown。
- PDF 需要单独做版面清洗，不建议优先。

## 8. 多书知识库还不完整

已完成：

- 多本书管理。
- 分组和标签。
- 当前书内问答。

未完成：

- 跨书检索。
- 系列作品统一索引。
- 同名实体跨书区分。
- 多书主题对比。

下一步：

- 先支持同一分组内跨书搜索。
- 再支持系列人物和地点归并。

## 近期建议路线图

## Phase 1：稳定本地 Qwen 召回链路

目标：

- Qwen3 Embedding / Reranker 在 Web 中默认可用。
- 旧 BGE 索引重建后不再影响召回。
- 用户能清楚知道当前使用的是哪个模型。

任务：

- 增加模型自检 API。
- Web 显示 embedding / reranker 是否本地路径命中。
- 增加旧索引提醒和一键重建。
- 对 rerank candidates 提供速度提示。

## Phase 2：建立召回评测集

目标：

- 不再靠感觉判断“变聪明/变笨”。
- 每次重构都能知道召回是否退化。

任务：

- 建立真实问题集。
- 标注正确章节和证据。
- 输出 Recall@K、MRR、Top1 命中率。
- 对比 lexical、embedding、rerank、hybrid。

## Phase 3：动态索引写回

目标：

- 本地 Qwen 不在导入时全书慢扫。
- 问答时只分析相关片段。
- 高价值结构化结果逐步沉淀。

任务：

- 设计 dynamic_entities / dynamic_events / dynamic_relations 表。
- 写回来源证据、置信度、生成模型和时间。
- Web 显示“本次问答新增索引”。
- 支持用户确认/删除动态索引。

## Phase 4：完整 Agent 图工作流

目标：

- 从策略包装升级为真正 LangGraph 工作流。

任务：

- query_understanding 节点。
- retrieval 节点。
- rerank 节点。
- tool execution 节点。
- answer validation 节点。
- memory writeback 节点。
- checkpoint 和恢复。

## 当前最重要的工程事实

- 当前测试数量：`129`。
- 后端测试：通过。
- 前端构建：通过。
- 默认 embedding：`Qwen/Qwen3-Embedding-0.6B`。
- 默认 reranker：`Qwen/Qwen3-Reranker-0.6B`。
- 默认 rerank candidates：`20`。
- 本地模型目录：`D:\BookRecall\models`。
- 模型缓存目录：`D:\BookRecall\.cache\huggingface\sentence-transformers`。
- 向量索引目录：`D:\BookRecall\.bookrecall\vectors`。
- 数据库路径：`D:\BookRecall\.bookrecall\bookrecall.db`。
- 旧 BGE 模型缓存已清理。
- 旧 BGE 向量索引可能仍存在，需按书重建。

## 当前工作区注意事项

- 工作区存在大量未提交改动。
- 不要随意回退用户已有改动。
- 不要提交 `models/`、`.cache/`、`.bookrecall/`。
- 不要把 API key、token、`.env`、证书加入仓库。
- 修改后建议运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests
```

以及：

```powershell
cd frontend
npm run build
```

最后建议运行 `/diff` 查看变更。
