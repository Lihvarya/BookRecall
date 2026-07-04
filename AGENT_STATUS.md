# BookRecall Agent 状态说明

本文档面向继续开发 BookRecall 的人。

它不讲愿景，不讲宣传，只回答三个问题：

1. 这个 Agent 现在已经实现了什么
2. 它离“完整 Agent 产品”还差什么
3. 接下来最值得优先补哪一部分

更新时间：`2026-07-05`

## 一句话判断

BookRecall 现在已经不是“一个纯脚本 demo”，而是一个可运行、可测试、可扩展的本地阅读记忆 Agent MVP。

但它还不是一个完整的 Agent 产品。它目前更准确的定位是：

- 一个以本地索引为核心的阅读回忆引擎
- 一个带 ReAct 状态机的可控问答层
- 一个已经开始支持本地 embedding 和外部 LLM 的 Agent 控制台

## 当前已实现

## 1. 数据与索引层

已完成：

- 章节解析
  - 支持中文网文常见章节格式
  - 支持无章节标题时回退到整本单章
- 分层切块
  - parent chunk
  - child chunk
- 结构化实体索引
  - 实体名
  - 别名
  - 首次出现章节
  - 全部出现章节
  - 出现摘录
- 结构化关系索引第一版
  - 同章共现实体对
  - 关系首次出现章节
  - 关系证据摘录
  - 基于关键词的粗分类：冲突、同伴/协作、师徒/传承、亲缘/家族、共现/关联
  - `lookup_relations` 可返回关系起点、互动推进、近期状态和总体变化提示
- 结构化主题索引第一版
  - 自动发现常见主题词
  - 支持 `--themes` 手工主题词表
  - 主题首次出现章节
  - 主题线索证据摘录
  - `search_theme` 可返回最多三段阶段摘要与总体演化提示
- 结构化事件链索引第一版
  - 基于实体共现和事件关键词抽取事件节点
  - 事件类型粗分类：获得/失去、冲突/危机、揭示/真相、选择/决定、协作/同行
  - 记录事件章节、摘要、原文摘录和关联实体
  - `search_events` 可按实体或问题检索已读范围内的关键事件链
- SQLite 存储
  - `books`
  - `chapters`
  - `parent_chunks`
  - `child_chunks`
  - `entities`
  - `entity_mentions`
  - `entity_aliases`
  - `relations`
  - `relation_mentions`
  - `themes`
  - `theme_aliases`
  - `theme_mentions`
  - `events`
  - `event_entities`
  - `chapter_summaries`
  - `reader_state`
  - `agent_memory`

这部分已经足够支撑“第一次出现”“后来还有没有出现”“当前读到哪了”这种核心阅读回忆问题。

## 2. 检索层

已完成：

- 默认倒排检索器 `LocalRetriever`
- 可选本地 embedding 检索器 `EmbeddingRetriever`
- 向量索引持久化到 `.bookrecall/vectors/`
- embedding 索引支持双后端
  - `numpy` 精确相似度
  - `faiss` 内积索引（环境可用时启用）
- `ask --retriever lexical|embedding|auto`
- `models / embed-build / embed-search`

现状判断：

- 倒排检索已经能稳定服务核心问题
- embedding 检索已经真正接通，不是占位接口
- 当前已经支持 `faiss` 可选后端
- 如果环境没有 `faiss`，会自动回退到 `numpy`
- 下一步重点不再是“是否接入 FAISS”，而是更大规模检索优化、rerank 和 query rewrite

## 3. Agent 层

已完成：

- 手写 ReAct 状态机
- `AgentState`
- 工具注册表 `ToolRegistry`
- 9 个工具：
  - `lookup_first_appearance`
  - `lookup_timeline`
  - `lookup_relations`
  - `search_theme`
  - `search_events`
  - `search_evidence`
  - `lookup_entity_aliases`
  - `get_chapter_summary`
  - `list_entities`
- 两种策略：
  - `RuleBasedPolicy`
  - `LLMReActPolicy`
- `LLMReActPolicy` 已升级为原生 tool calling 优先，文本协议回退
- 会话级记忆已接入
  - `ask` / `ask_card` 支持 `session_id`
  - 同一会话下可复用最近几轮的主实体线索
  - 最近轮次会写入 SQLite `agent_memory`
  - `LLMReActPolicy` 已可读取最近会话摘要作为上下文
- `LangGraphPolicy` 预留接口

这意味着当前 Agent 已经具备“先解析实体，再查轨迹，再补证据，最后组织答案”的多步能力，而不再只是一个单次函数调用。

## 4. 防剧透机制

已完成三重防剧透：

- 用户阅读进度作为全局上限
- 工具调用时对 `max_chapter` 二次钳制
- 结果出栈前再次裁掉越界证据

这部分是当前项目最扎实、也最有差异化的能力之一。

## 5. 输出契约

已完成：

- `MemoryCard`
- `EvidenceCard`
- 文本渲染
- JSON 渲染
- 对外结构稳定

这让 CLI、Web、未来前端都能复用同一套问答结果。

## 6. 交付层

已完成：

- CLI
- 本地 Web 控制台
- Web API
- 本地 embedding 状态面板
- 外部 API 设置面板
- 结构化索引总览面板
- 网页端粘贴正文建索引
- 网页端构建本地向量索引
- Web 前端已拆分为 `index.html / app.css / app.js`

当前网页端已经支持：

- 粘贴正文创建书籍索引
- 填写实体词表和主题词表
- 可选覆盖同名 `book_id`
- 选择书籍
- 设置阅读进度
- 输入会话 ID，支持连续追问
- 提问
- 查看索引规模统计
- 查看实体索引、主题线索、事件链和关系索引
- 构建当前书的本地 embedding 向量索引
- 查看会话历史
- 查看本轮工具 trace
- 使用快捷提问模板覆盖首次出现、轨迹、关系、主题和事件问题
- 切换检索器
- 查看本地模型依赖
- 查看向量索引状态
- 配置 DeepSeek / OpenAI-compatible API
- 通过独立静态资源维护页面结构、样式和交互逻辑

## 7. 测试

当前测试已覆盖：

- 章节解析
- 倒排检索
- embedding 索引构建与检索
- Agent 核心问答
- Agent 工具层
- 人物关系索引、存储、工具和 Agent 问答链路
- 人物关系阶段摘要
- 主题线索索引、存储、工具和 Agent 问答链路
- 主题线索阶段摘要
- 事件链索引、存储、工具和 Agent 问答链路
- LLM ReAct 文本解析
- Web API

当前状态：

- `70 unittest tests`
- 全绿

## 还没完成的关键部分

下面这些不是“锦上添花”，而是它从 MVP 走向完整 Agent 产品时最关键的缺口。

## 1. LangGraph 还没真正落地

现状：

- 代码里有 LangGraph 预留接口
- 但执行流仍然是手写 while-loop ReAct

这意味着现在还没有：

- graph 级状态编排
- checkpoint
- 中断恢复
- human-in-the-loop
- 更复杂的流程图式控制

判断：

- 这不影响当前可用性
- 但会限制后续复杂 Agent 能力

## 2. 原生 function-calling 已接入，但还没完全做深

现状：

- `LLMReActPolicy` 现在优先走原生 OpenAI-compatible tool calling
- 如果供应商不返回 `tool_calls`，会自动回退到原有文本协议解析
- 当前已经有本地测试覆盖优先链路和回退链路

结果是：

- 稳定性比之前好一层
- 但还缺少真实多供应商回归验证
- 还没有把 tool calling trace 更细地暴露到 Web 调试界面

所以这项工作从“未实现”进入了“已接入，但还需扩展和验证”阶段。

## 3. 跨会话记忆已完成第一版，下一步是把它产品化

现状：

- 当前的阅读进度会持久化
- Agent 现在已经会在 `session_id` 维度持久化最近问答
- 当本轮问题没有显式实体时，可以沿用同会话最近一轮的主实体继续追问

仍然缺失：

- 用户长期偏好
- 会话摘要压缩与清理策略
- Web 端对话历史面板
- 会话 trace 可视化与回放

这意味着它已经从“单轮问答引擎”进入“基础多轮助手”阶段，但还不是完整的长期协作助手。

## 4. 知识结构层已有关系、主题和事件链第一版，但还不够深

当前实体索引已经很好用，人物关系、主题线索和事件链也已经完成第一版：

- build 阶段会基于同章共现生成 `relations` 和 `relation_mentions`
- Agent 已有 `relation_lookup` 意图
- 工具层已有 `lookup_relations`
- 可以按“关系起点 / 互动推进 / 近期状态”初步回答“谁和谁是什么关系、后来如何变化”
- build 阶段会生成 `themes` 和 `theme_mentions`
- Agent 已有 `theme_explore` 意图
- 工具层已有 `search_theme`
- 可以按“线索起点 / 发展推进 / 近期变化”初步回答“某个主题/观点前后有什么变化”
- build 阶段会生成 `events` 和 `event_entities`
- Agent 已有 `event_chain` 意图
- 工具层已有 `search_events`
- 可以按“事件节点 / 章节定位 / 关联实体”初步回答“某条主线涉及哪些关键事件”

但它还不是完整知识图谱，仍然缺：

- 高质量人物关系图
- 地点关系图
- 道具关系图
- 高质量主题线索图
- 更高质量的因果事件图

所以现在能很好回答：

- “第一次出现在哪一章”
- “后来还出现过吗”
- “谁和谁是什么关系”（阶段摘要版）
- “某个主题前后有什么变化”（阶段摘要版）
- “某个实体涉及哪些关键事件”（事件节点版）

但还不够擅长回答：

- “谁和谁的关系为什么会发生深层转折”
- “这个观点在前中后期为什么会深层演化”
- “这条主线为什么一步步因果推进到当前结果”

## 5. 向量检索已进入“双后端可用”阶段，但还不是最终形态

现状：

- 已有 `sentence-transformers` 接入
- 已支持本地索引构建与检索
- 已支持在问答中切换
- 已支持 `numpy / faiss` 双后端

未完成：

- 更大规模向量检索优化
- query rewrite
- rerank
- MMR 去冗余

这意味着它现在已经“能用”，但还没到“最强版本”。

## 6. Web 端已进入“可调试的多轮控制台”阶段，但还不是完整产品前端

现状：

- Web 控制台已经明显强于早期版本
- 已有书库、Agent、模型、外部 API 三区域
- 已支持从网页端粘贴正文创建本地结构化索引
- 已支持从网页端构建当前书本地向量索引
- 已支持会话 ID、会话历史和本轮工具轨迹展示
- 已支持索引统计、主题线索、事件链、关系索引浏览
- 已支持常见 Agent 问题快捷模板
- Web 前端已从 `web.py` 大字符串拆分为 `index.html / app.css / app.js`

但仍缺：

- 原文高亮跳转
- 多本书分组管理
- 更细致的设置持久化

所以它现在更像“开发者控制台 + 可用的多轮问答前端”，还不是成熟消费级产品。

## 7. 工程化还有欠账

还没完成的部分包括：

- CI
- 发布流程
- 更正式的配置文件体系
- 增量重建索引
- 性能基准
- 评测集
- 运行观测

这些不会立刻影响 demo，但会影响项目长期维护和协作。

## 当前最优先的方向

如果按“投入产出比”排序，我建议下一步优先顺序是：

1. FAISS / 更强的 embedding 检索后端
2. 事件链、主题线索层和人物关系质量提升
3. 会话摘要压缩与长期偏好记忆
4. Web 端原文高亮跳转
5. LangGraph 真落地
6. tool calling 的真实供应商回归验证

原因很简单：

- 会话记忆能直接把它从“单轮工具”推进成“持续协作助手”
- FAISS 和结构层能提升复杂问题质量
- Web 多轮与 trace 能显著提升产品完成度
- LangGraph 适合在流程更复杂时接入，而不是现在为了“名义上用了”而硬接
- tool calling 现在已经接入，下一步重点是把它放到真实供应商上验证边界

## 适合当前版本解决的问题

当前版本已经比较适合：

- 回忆小说人物首次出现
- 查询某实体在已读范围内的出现轨迹
- 找某个事件、道具、概念的相关证据片段
- 在防剧透前提下回顾前文
- 用本地 embedding 改善语义召回
- 用外部大模型做复杂总结

## 暂时不适合的问题

当前版本还不太适合：

- 极复杂的人物关系推理
- 很强的章节间因果链自动抽取
- 长期对话式读书陪伴
- 直接替代通用聊天系统
- 无索引情况下即时读完整本超大长文

## 可以如何理解这个项目的阶段

如果把项目分成四个阶段：

1. 索引原型
2. 可用 Agent MVP
3. 完整阅读 Agent
4. 产品化平台

那么 BookRecall 当前处于：

`2 -> 3` 之间

它已经跨过了“能不能跑”的阶段，正在进入“能不能更聪明、更稳定、更像真正 Agent 产品”的阶段。

## 建议的下一批具体任务

如果继续开发，可以直接从这一批开始：

- 增加会话级 `agent_memory` 持久化
- 为 Web 端增加对话历史和调试 trace 面板
- 在 embedding 通道后面接 FAISS
- 提升 `lookup_relations` 的质量：从阶段摘要升级到事件级关系抽取、转折识别和关系图谱
- 提升 `search_theme` 的质量：从阶段摘要升级到多主题对比、观点转折识别和 LLM 深层总结

## 总结

BookRecall 现在已经实现了：

- 本地阅读索引
- 可控 ReAct Agent
- 防剧透机制
- CLI 与 Web 双入口
- 本地 embedding 接入
- 外部 OpenAI-compatible API 接入

还没有完成的核心是：

- 原生 function-calling 优先链路
- 跨会话记忆
- LangGraph 真正执行流
- 高质量关系图谱与主题层
- 更强向量检索后端
- 更完整产品级前端

所以它已经是一个“可用的阅读回忆 Agent MVP”，但还不是“完整的阅读 Agent 产品”。
