# BookRecall Agent 状态说明

更新时间：`2026-07-15`

本文档是 BookRecall 的工程事实清单，面向继续开发、回归验证和版本规划。安装与日常使用请看 [README.md](README.md)，评测数据格式请看 [eval/README.md](eval/README.md)。

最近验证：`2026-07-15`，后端 `169` 项测试通过，前端 TypeScript 检查与 Vite 生产构建通过。

## 一句话判断

BookRecall 现在已经不是纯脚本 demo，而是一个可运行、可测试、可扩展的本地阅读记忆 Agent MVP。

它当前更准确的定位是：

- 一个本地长文本索引引擎。
- 一个带工具调用和状态管理的阅读回忆 Agent。
- 一个支持本地 embedding、reranker、本地 LLM 和云端 API 的混合检索问答系统。
- 一个已经具备 Vue Web 控制台的个人阅读记忆工具。

它还不是完整产品，主要差距在模型链路稳定性、索引质量自动评估、复杂 Agent 工作流、跨书管理、多格式导入和长期记忆产品化。

## 完成度总览

| 子系统 | 状态 | 当前结论 |
| --- | --- | --- |
| TXT 解析与分层切块 | 已完成 | 可用于长篇中文小说，支持卷 / 节 / 章 |
| SQLite 本地书库 | 已完成 | 书籍、章节、索引、进度、会话均已持久化 |
| 倒排 + 向量 + 重排 | 已完成 | 默认 Qwen3 Embedding + Reranker，FAISS 优先 |
| Agent 工具编排 | 已完成 MVP | 规则、本地 Qwen、云端与可选 LangGraph 策略并存 |
| 防剧透 | 已完成 MVP | 入口、工具参数和结果输出三层限制 |
| 动态结构化写回 | 部分完成 | `grounded_v2`、审计和逐条人工治理已完成，旧数据迁移未完成 |
| Vue Web | 已完成 MVP | 对话、书库、模型召回、工具调试、原文阅读可用 |
| 自动召回评测 | 部分完成 | CLI、指标和 3 条真实案例已完成，仍需扩充到 30-100 条 |
| 完整 LangGraph 工作流 | 未完成 | 当前仅为可选策略包装 |
| EPUB / PDF / 跨书检索 | 未完成 | 当前核心路径仍是单书 TXT |

“已完成 MVP”表示核心路径可运行且有测试，不表示已经具备商业产品级容错、权限、任务恢复和自动评测。

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
   -> 条件 / 高风险事实验证器
   -> 防剧透边界复核
   -> MemoryCard 输出
```

这是 Two-Phase Indexing：

- Phase 1 在导入时做快而稳定的基础索引。
- Phase 2 在问答时只分析少量相关片段，避免导入阶段让本地 LLM 扫完整本书。

## 已实现能力

### 1. 数据与索引层

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
- 问答期动态实体、关系、事件写回。
- 静态与动态结构化索引共享事实质量门。
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
| `dynamic_index_audit` | 动态记录的置信度、来源、质量门、证据与状态 |

现状判断：

- 基础索引已经足够支撑“第一次出现”“后来有没有出现”“某章线索后来如何发展”等核心问题。
- 关系、事件、主题的规则抽取仍偏粗糙，已经不适合作为最终高质量结构化知识图谱。
- 后续应更多依赖“embedding 召回 + reranker 精排 + 本地 LLM 按需结构化”的动态索引，而不是导入阶段一次性全书智能抽取。
- 动态索引已加入 `grounded_v2` 准入：问题实体锚定、别名必须出现于原文、低价值共现拒绝、高风险事实必须有直接证据。
- 同章、同事件类型的动态事件会按证据包含关系和中文二元组覆盖率去重；更完整的新证据可升级旧事件，而不会重复插入。
- 新动态实体、关系、事件会持久化置信度、来源问题、来源模型、质量门版本、证据和时间。
- 旧 `:dynamic:` 数据会在审计统计中归为 `legacy_untracked`，不会被升级逻辑静默删除。
- Web API 可按书读取审计记录与统计；索引页通过默认折叠面板展示，并可跳转原文章节。
- 审计记录支持逐条确认、证据/事件摘要修正和带原因拒绝，修正会同步实际索引。
- 拒绝在事务中更新引用计数并清理无证据父记录；静态事件受保护，不会被动态审核误删。
- `review_version` 防止并发覆盖，被拒绝的同一记录不会被后续模型写回静默复活。

### 2. 检索与召回层

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
- 默认 rerank candidates 调整为 6，batch size 调整为 1，更适合 RTX 3060 Laptop 6GB。
- Reranker 输入围绕命中的 Child 居中截取 384 字，模型 `max_length` 固定为 512 tokens。
- Web 服务进程内复用 Embedding 与 Reranker 实例，避免每轮问答重复加载权重。
- 默认 Qwen 模型名自动映射到 `D:\BookRecall\models` 本地目录。
- `.cache`、`.bookrecall`、`models` 均被 Git 忽略。
- 召回实验会显示加载态、空结果、错误、命中数、实际检索器和耗时。
- Runtime 会返回实际基础检索器、向量模型、FAISS/numpy 后端及 Reranker 生效状态。
- Library Lab 会标记旧 BGE 向量索引“需重建”，不会只显示笼统的“已构建”。
- 同一本书的倒排索引会在进程内缓存，避免每轮问答重复扫描全部 child chunk。

当前默认模型：

| 模块 | 默认值 |
| --- | --- |
| Embedding | `Qwen/Qwen3-Embedding-0.6B` |
| Reranker | `Qwen/Qwen3-Reranker-0.6B` |
| Rerank candidates | `6` |
| Rerank batch size | `1` |
| Rerank 输入上限 | `384` 字 / `512` tokens |
| 向量目录 | `.bookrecall/vectors` |
| 模型目录 | `D:\BookRecall\models` |
| 缓存目录 | `D:\BookRecall\.cache\huggingface\sentence-transformers` |

现状判断：

- 召回质量已经从旧 BGE 路线升级到 Qwen3 Embedding + Reranker 路线。
- 首次构建 Qwen3 embedding 向量索引会明显比 BGE 慢，这是正常的。
- 未限制时模型原始 `max_seq_length=40960` 会导致分钟级精排；当前 512 tokens 限制是关键性能修复。
- 三条真实问题测试中，优化后 Reranker P50 约 1.64 秒、P95 约 2.90 秒，不包含首次模型加载。
- 旧 BGE 向量索引仍可读取，但不再是推荐链路，需要用户重建。
- 大书首次倒排检索会建立缓存；真实数据测试中首次约 9 秒，后续约 0.14 秒。该数值仅用于说明缓存效果，不是固定性能承诺。

### 3. Agent 层

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
| `search_exact_text` | 全书原文精确词检索，兜底低频/未入索引实体 |
| `lookup_entity_aliases` | 查实体别名 |
| `get_chapter_summary` | 查章节摘要 |
| `list_entities` | 列出实体 |

近期关键修复：

- “成为尊者条件是什么”这类条件问题已经恢复精准定位。
- 条件/标准/要求类问题会优先从证据段中抽取“第一、第二、第三、第四”等枚举结构。
- 无实体条件类问题会绕开本地 LLM Planner 的误规划，优先走规则策略。
- `search_evidence` 会返回 `parent_text`，便于规则策略从更完整上下文抽取答案。
- 增加 `search_exact_text`，当结构化实体索引或语义召回漏掉只出现一次/少数几次的专名时，自动退回原文精确词检索。
- 死亡 / 死因 / 结局问题会绕过本地 4B Planner 的自由误规划，优先走确定性事实核验路由。
- 死亡事实检索会扩展“死了 / 尸躯 / 丧命”等直接词，必要时精确搜索“实体 + 尸躯”。
- 如果模型总结与明确死亡章节冲突，最终回答会拒绝错误总结并回到直接原文证据。
- 完整疑似专名会优先于已索引的短主题或实体，例如“自由残缺变”不会再被“自由”劫持。
- 自动完整词检索会排除“甲和乙”及普通“观点前后变化”表达，避免过度触发。

现状判断：

- Agent 已能执行多步工具调用，不只是单次 RAG。
- 对强结构问题，规则策略仍然很重要，不能完全交给小模型。
- 本地 Qwen Planner 可以提高灵活性，但需要策略约束，避免“聪明但跑偏”。
- Agent 的价值在于问题分流、工具组合、状态约束、证据验证和记忆写回；Embedding、Reranker 与 LLM 是被编排的能力组件，不等同于 Agent 本身。

### 4. 防剧透机制

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

### 5. Web 端

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
- Embedding、Reranker、本地 Qwen 分组件自检和结果卡片。
- 本地 Qwen endpoint / model / GGUF 路径配置。
- Cloud OpenAI-compatible 配置。
- 工具箱调试。
- 证据检索实验。
- 召回测试的加载、错误、空结果和耗时反馈。
- 原文阅读器和证据高亮。
- 长会话历史。
- 历史轮次编辑、删除、重新提问。
- 每轮回答直接展示实际 Agent 策略、基础召回、向量模型/后端、Reranker 和本地/云端模型链路。
- 回答 runtime 持久化到 `agent_memory.runtime_json`，刷新历史、复制会话或分支后仍可还原；旧数据库自动迁移，旧轮次按空 runtime 兼容。
- 系统诊断。
- 浏览器本地偏好持久化。
- 默认折叠的动态索引审计面板，显示已追踪 / 历史未追踪计数、置信度、来源模型、问题、质量门、证据和时间。
- 动态审计记录可直接打开对应章节原文，并逐条确认、内联修正或带原因拒绝。
- 审核不提供危险的无预览批量删除；静态索引关联记录只改变模型审计状态。

已经移除或弱化：

- 用户笔记系统。
- 三栏式拥挤布局。
- 分支对比与合并功能。

现状判断：

- Web 端已经从调试控制台升级为可用的产品原型。
- 页面仍需要继续做信息密度优化。
- 一些模型配置项和索引状态还需要更明确的“当前是否生效”提示。

### 6. 本地模型接入

已完成：

- Qwen3-Embedding-0.6B 本地目录加载。
- Qwen3-Reranker-0.6B 本地目录加载。
- 默认模型名自动映射本地目录。
- `HF_HOME`、`SENTENCE_TRANSFORMERS_HOME`、`TORCH_HOME`、`BOOKRECALL_MODEL_DIR` 项目内路径管理。
- `HF_HUB_DISABLE_XET=1`，减少 Windows 下载卡顿。
- `models/` 加入 `.gitignore`。
- LM Studio / OpenAI-compatible endpoint 输入入口。
- Endpoint 优先，填了 endpoint 就不加载 GGUF。
- Web 服务会缓存已加载的 Embedding / Reranker，并对共享 GPU 推理加锁。
- `/api/models/check` 可执行真实 Embedding 编码、Reranker 打分和本地 Qwen `/v1/models` 探测。
- GGUF 自检只验证文件路径和大小，避免与 LM Studio 同时加载 4B 模型争抢 6GB 显存。

已验证：

- PyTorch 可识别 `NVIDIA GeForce RTX 3060 Laptop GPU`。
- Qwen3-Embedding-0.6B 可离线加载并输出 1024 维向量。
- Qwen3-Embedding-0.6B 与 Qwen3-Reranker-0.6B 本地目录结构完整。

现状判断：

- 模型文件管理和分组件自检已经可用。
- 自检会返回本地解析路径、CUDA 设备、向量维度 / Reranker 分数、耗时和缓存复用状态。
- 还缺更友好的模型下载向导。

### 7. CLI

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
| `eval-retrieval` | 可用 |
| `eval-agent` | 可用 |

现状判断：

- CLI 足够支撑开发和排障。
- `eval-retrieval` 已支持 Reranker 模型、候选数、batch、字符和 token 上限参数。
- 普通 `ask` 仍没有 reranker 和 local planner 参数，主要交互配置集中在 Web。

### 8. 测试

当前验证：

```text
python -m unittest discover tests
Ran 169 tests in 25.749s
OK
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
- 死亡事实问题的确定性搜索与错误总结拒绝。
- `grounded_v2` 动态索引准入和事件近重复合并。
- 动态索引审计元数据持久化和旧数据 `legacy_untracked` 统计。
- 动态索引审计查询、聚合统计、逐条审核 API 和 Web 人工治理。
- 审核事务、引用计数修复、孤立父记录清理、静态事件保护、乐观版本冲突和拒绝防复活。
- 回答 runtime 持久化、旧库迁移、会话复制保留和对话卡实际执行链路展示。
- JSON / JSONL 评测数据校验、Top1、Recall@K、MRR、证据词覆盖和阈值门禁。
- Agent 最终证据与防剧透越界评测。
- 完整专名优先于短主题 / 实体的自动精确检索。

现状判断：

- 后端测试已经能防止核心回归。
- 前端缺少自动化 UI 测试。
- 本地模型加载和性能目前主要靠手动验证。

## 仍未完成的关键差距

### 1. 结构化索引质量仍不够好

问题：

- 规则实体抽取容易抽出泛词。
- 关系索引仍偏共现。
- 事件链仍偏关键词。
- 全书 LLM 智能索引太慢，不适合导入阶段默认启用。
- 历史版本已经写入的低质量动态记录不会因升级质量门而自动消失。

当前方向：

- 不再追求导入时一次性全书智能结构化。
- 保留基础结构化索引作为低成本骨架。
- 用 embedding + reranker 在问答期找相关片段。
- 用本地 Qwen 对少量片段做按需结构化。
- 将高置信结果动态写回索引。
- 对动态写回执行事实对齐：死亡、获得、失去、背叛等结论必须在原文中有直接措辞。
- 关系类型必须有对应动作词；“共现/关联”不再进入动态关系索引。
- 动态实体仅保留问题锚点或被有效关系/事件引用的实体。
- 静态智能索引会把模型提供的错误关系证据修正为同章中真正支持该关系的原文句。

下一步：

- 为 `legacy_untracked` 历史记录建立安全接管和人工标注流程。
- 把质量门未准入候选的拒绝原因持久化，支持分析模型为什么没有写回。
- 继续保持逐条治理；在历史数据具备来源和引用检查前，不提供批量删除。

### 2. LangGraph 仍只是可选策略，不是完整工作流

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

### 3. 本地 LLM Planner 还需要更强约束

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

### 4. 评测数据集仍需扩充

已完成：

- 建立 `eval/` 目录、JSON / JSONL schema 和数据校验。
- `eval-retrieval` 对比 lexical、embedding、lexical-rerank、embedding-rerank。
- `eval-agent` 检查最终证据、工具路由和防剧透越界。
- 输出 Top1、Recall@K、MRR、证据词覆盖、P50/P95 和错误数。
- 支持 `--min-top1`、`--min-mrr`、`--fail-on-error` 回归门禁。
- 已标注“成为尊者条件”“太白云生怎么死”“自由残缺变”三个真实失败案例。

未完成：

- 当前只有 3 条案例，样本过小，不能代表全书质量。
- 仍需扩充到 30-100 条，覆盖首次出现、时间线、关系、主题、指代和防剧透。
- 尚未实现 exact-text 与 hybrid 作为独立裸检索基线。
- 尚未形成 CI 中的固定质量阈值。

### 5. 性能仍需优化

当前瓶颈：

- Qwen3-Embedding 首次加载慢。
- 向量索引构建需要编码全部 child chunk。
- Reranker 对长片段和较多候选打分慢。
- 本地 Qwen 生成结构化 JSON 慢。

已做优化：

- Two-Phase Indexing。
- 导入时不默认全书智能 LLM 索引。
- 向量构建真实进度。
- Web 进程内复用 Embedding / Reranker 模型实例。
- Reranker 默认候选降到 6、batch size 降到 1。
- Parent 输入改为 Child 居中 384 字，并把模型最大长度从 40960 限制到 512 tokens。
- 三条真实案例中，Reranker 优化后 P50 约 1.64 秒、P95 约 2.90 秒。

下一步：

- 本地 Qwen 对话模型仍需常驻或服务端复用。
- Embedding 全量构建仍需断点续建和吞吐调优。
- 增加“快速模式 / 精准模式”切换。
- 支持后台任务取消。
- 支持索引构建断点续建。

### 6. Web 产品化还不完整

问题：

- 一些面板信息仍偏工程化。
- 模型配置生效状态不够直观。
- 后台任务不能取消。
- 长任务失败后的恢复指引还不够清楚。
- `legacy_untracked` 历史动态数据还不能在页面中接管和治理。

下一步：

- 增加索引任务取消按钮和失败恢复入口。
- 增加旧动态数据接管和质量门拒绝原因视图。
- 对召回结果增加可展开的评分和重排说明。

### 7. 多格式导入尚未实现

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

### 8. 多书知识库还不完整

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

## 优先级路线图

### P0：建立可重复的召回与回答评测

目标是不再依赖单次人工观感判断“变聪明或变笨”。

已完成：

- 评测 schema、指标、文本 / JSON 报告和 CLI 门禁。
- lexical、embedding、embedding + rerank 与 Agent 工作流对比。
- 三个真实失败案例已进入起始回归集，当前 Agent 两条本地链路均达到 Top1 / MRR 1.000。

仍需完成：

- 建立 30-100 条真实问题集，覆盖首次出现、条件、死因、低频实体、时间线和主题变化。
- 标注正确章节、必要关键词和最小充分证据。
- 固定比较 lexical、embedding、embedding + rerank、exact-text 和 hybrid 路线。
- 在样本扩充后确定 CI 质量阈值；当前 3 条案例的 1.000 不可外推。

### P0：补齐索引生命周期与可观测性

已完成：

- 模型自检 API 和 Web 结果卡片，分别验证 Embedding、Reranker 和本地 LLM。
- 自检显示模型名解析后的本地路径、设备、关键输出、耗时和缓存状态。
- Runtime 状态显示 Web 进程已缓存的 Embedding / Reranker 实例数。
- 向量索引状态显示实际模型、后端、chunk 数和是否匹配推荐 Qwen3 Embedding。
- 召回与回答 API 返回实际检索链路，Reranker 可选加载失败不再完全不可见。
- 每轮实际 runtime 已写入会话并在回答卡展示，历史会话不再退化为当前配置的猜测值。
- 动态记录已经持久化置信度、来源问题、来源模型、质量门版本、证据、状态和时间。
- 存储层可以分别统计已追踪记录与 `legacy_untracked` 历史记录。
- 动态审计查询与逐条审核 API、索引页治理面板、事务清理和静态索引保护已经完成。

仍需完成：

- 增加 `legacy_untracked` 接管和质量门未准入候选的拒绝原因持久化。
- 增加索引任务取消、失败状态落盘和断点续建。

### P1：把 Agent 升级为可恢复图工作流

- 将 query understanding、retrieval、rerank、tool execution、answer validation 和 memory writeback 拆成独立节点。
- 使用 LangGraph checkpoint 持久化执行状态。
- 支持中断恢复、超时降级和 human-in-the-loop。
- 在 Web 中展示节点级状态，而不只是文本工具轨迹。
- 保留确定性安全路由，不能为了统一成图而把所有判断重新交给 4B Planner。

### P1：性能与资源治理

- 已完成 Embedding / Reranker Web 常驻复用、GPU 推理锁和 Reranker 长度 / batch 调优。
- 继续实现本地 LLM 常驻复用。
- 提供“快速 / 平衡 / 精准”三档配置，并展示预估耗时。
- 对超大书记录索引阶段耗时、吞吐、峰值显存和失败位置。

### P2：产品扩展

- 优先增加 EPUB 和 Markdown 导入，PDF 单独设计版面清洗链路。
- 支持同一书籍分组内的跨书检索与系列实体消歧。
- 增加桌面打包、多用户权限和备份 / 恢复方案。
- 在核心质量稳定前，不优先扩展泛化知识库功能。

## 当前最重要的工程事实

- 当前测试数量：`169`，`2026-07-15` 全量通过。
- 前端：`vue-tsc --noEmit && vite build` 通过。
- 默认 embedding：`Qwen/Qwen3-Embedding-0.6B`。
- 默认 reranker：`Qwen/Qwen3-Reranker-0.6B`。
- 默认 rerank candidates：`6`。
- 默认 rerank batch size：`1`。
- 默认 rerank 输入上限：`384` 字 / `512` tokens。
- 本地模型目录：`D:\BookRecall\models`。
- 模型缓存目录：`D:\BookRecall\.cache\huggingface\sentence-transformers`。
- 向量索引目录：`D:\BookRecall\.bookrecall\vectors`。
- 数据库路径：`D:\BookRecall\.bookrecall\bookrecall.db`。
- 旧 BGE 模型缓存已清理。
- 旧 BGE 向量索引可能仍存在，需按书重建。

本机书库数量、chunk 数和动态记录数属于运行时数据，不作为仓库完成度指标写入本文档。应通过 Web Runtime、Library Lab 或存储层审计查询查看，避免状态文档随本机数据变化而失真。

当前质量门事实：

- 动态准入阈值为 `0.68`。
- 实体需要被问题锚定，或被通过验证的关系 / 事件引用。
- 模型给出的别名必须实际出现在证据原文中。
- “共现 / 关联”不作为有效动态关系写入。
- 死亡、获得、失去、背叛等高风险事实必须有直接证据。
- 同章同类型近重复事件会合并；更完整证据会升级旧事件。
- 新动态记录的审计与逐条人工治理已经完成；旧未追踪数据接管尚未完成。

## 当前工作区注意事项

- 工作区存在大量未提交改动。
- 不要随意回退用户已有改动。
- 不要提交 `models/`、`.cache/`、`.bookrecall/`。
- 不要把 API key、token、`.env`、证书加入仓库。
- 不要直接清空 `.bookrecall`；它同时包含书库、索引、阅读进度和会话数据。
- 修改召回、排序、事实路由或动态写回后，必须补真实失败案例的回归测试。
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
