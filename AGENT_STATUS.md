# BookRecall Agent 现状、差距与完善路径

> 本文档以**代码为准**，记录 LangAgent 当前的真实能力边界、距离一个"完整的 Agent 工具项目"的差距，以及分层的完善路径。配套 README 使用——README 面向访客，本文档面向想继续推进该项目的人。

更新时间：2026-07-03（LangAgent 重构完成、35 测试全绿、《蛊真人》端到端验证通过之后）。

---

## 一、当前已实现的功能

### 1.1 索引管线（建库阶段）

| 模块 | 文件 | 已实现 |
| --- | --- | --- |
| 章节解析 | `parser.py` | 识别 `第X章/节/回/卷/篇/部/集`；行首全角/半角空格、BOM、全角冒号 `：` 分隔均容错；行长上限防正文误判；无任何标题时回退"全文 1 章"；章节号按解析顺序递增。在《蛊真人》2340 个`第X节`上正确解析为 2347 章。 |
| 分层分块 | `chunking.py` | Parent（章节级，默认 1800 字 + 200 重叠）→ Child（细粒度，默认 260 字 + 40 重叠）两层窗口切块，句号/换行优先切界。 |
| 实体索引 | `entity_index.py` | 接受手工词表（`名\|别名1,别名2`）；`auto_discover_entities` 自动挖掘 = `【】《》「」` 强调符 + 全文 2/3/4-gram 词频（仅在连续 CJK 段上做 n-gram，停用词与纯数字过滤）；逐章节扫描实体出现，记录首次章节 + 全部出现章节 + 位置 + 摘录。 |
| 存储 | `storage.py` | SQLite 9 张表：`books / chapters / parent_chunks / child_chunks / entities / entity_mentions / entity_aliases / chapter_summaries / reader_state`；`get_stats` / `delete_book` / `get_chapter_titles` 等查询方法。章节摘要取正文前 140 字。 |

### 1.2 检索层（查询阶段）

| 能力 | 实现 | 边界 |
| --- | --- | --- |
| 倒排表 | `retrieval.py` 建一次 `{token: set[child_chunk_id]}` 常驻缓存，查询取 token 交集候选再打分 | **语义仅词法级**：汉字单字 + bigram + 英文词，无向量化。结果是"含相同字形片段"的命中，不是"语义相近"。 |
| 打分 | `overlap + density + phrase_bonus`（纯函数，不变） | 纯词频启发式，远不如向量余弦。同义词/换名提问召回会弱。 |
| 进度过滤 | `max_chapter` 在 SQL 层（旧）与候选过滤（新）双重兜底 | 仅按章节号过滤，无"段落级"粒度。 |
| 等价性 | 倒排表结果 == 全库扫描结果（有单测保证） | 无候选时退回全库扫描，行为与旧版一致。 |

### 1.3 Agent 层（`agent/` 包——核心升级）

这是 MVP 到"完整项目"的关键一跳：从**一步硬编码路由**升级为**带工具调用循环的 ReAct 状态机**。

**状态机**（`state.py`、`core.py`）

```
入口锁进度 → 建 registry → 选 policy → while not terminal and step<max_steps:
  decision = policy.next_action(state, registry)
  调 tool → ingest 结果 → _prune_evidence(防剧透)
→ finalize 成 MemoryCard
```

- `AgentState`：question / progress_chapter / intent / matched_entities / primary_entity / evidence / raw_hits / trace / called_tools / step / max_steps / answer / summary / suggestions。
- 步数硬上限：规则版 6、LLM 版 8，杜绝死循环。
- **三重防剧透**：①工具内部用 `progress_chapter` 做 `max_chapter` ②`_clamp_max_chapter` 对 LLM 传入的章节号二次钳制 ③`_prune_evidence` 兜底丢弃越界证据并置 `spoiler_blocked`。任意一轮 LLM 抽风也无法越界。

**6 个工具**（`tools.py`，全部 `progress_protected=True`，直接复用 storage/retrieval，返回 dict 而非 MemoryCard）

| 工具 | 入参 | 复用 |
| --- | --- | --- |
| `lookup_first_appearance` | entity | `get_entity` + `get_entity_mentions` + 摘要取标题 |
| `lookup_timeline` | entity, max_chapter? | `get_entity_mentions`，最多 3 条片段 |
| `search_evidence` | query, max_chapter? | `LocalRetriever.search` |
| `lookup_entity_aliases` | entity | `resolve_entity_name` + `list_entities_with_aliases` |
| `get_chapter_summary` | chapter | `get_chapter_summaries`，超进度返回 spoiler_blocked |
| `list_entities` | — | `list_entities` |

`ToolRegistry.describe_for_llm()` 把工具清单序列化为 LLM 可读的 JSON 描述。

**3 种可插拔决策策略**（`policies/`，共享接口 `next_action(state, registry)->Decision`）

- **`RuleBasedPolicy`（默认）**：确定性多步路由，按"已调用工具集合"做状态机迁移。五类意图各走 2–3 步——首次出现（别名→lookup_first_appearance）、轨迹（别名→timeline，问"怎么拿到"再聚焦 search）、因果（search→可选 timeline）、对比（search→get_chapter_summary）、语义（search→可选 cloud）。无观察回退、无死循环。
- **`LLMReActPolicy`（可选，需 API Key）**：构造含工具清单 + trace + 进度边界的 prompt，让模型用 `thought/action/arguments/final_answer` 文本格式输出下一步；纯标准库简易解析器解析；**连续 2 次解析失败自动回退规则版**。
- **`LangGraphPolicy`（预留）**：接口已对齐，构造时 `raise ImportError` 提示需 `pip install langgraph`；未来把 `next_action` 体内换成 `graph.invoke` 即可，核心循环零改动。

**输出契约**（`render.py`）：`MemoryCard` / `EvidenceCard` 与旧版字段一致；`ask` / `ask_card` / `render_text` / `render_json` / `to_payload` 签名行为不变——重构后 `test_agent.py` 的 5 条回归基线零修改全绿。

### 1.4 交付层

- **CLI**（`cli.py`）：`build / ask / set-progress / show-progress / list-books / list-entities / chapters / stats / clear（需 --yes）/ serve`。
- **Web**（`web.py`）：零依赖 `http.server`，接口 `/api/books`、`/api/books/{id}/entities`、`/api/books/{id}/chapters`、`/api/books/{id}/progress`、`/api/ask`、`/api/progress`、`/health`；单页前端含书库总览 / 实体索引 / 章节浏览折叠区 / 进度管理 / 问答卡片 / 追问按钮。
- **云端**（`cloud.py`）：`OpenAICompatibleReasoner`，`urllib` 调 OpenAI 兼容端点；`enabled` 属性决定 policy 路由。

### 1.5 测试与验证

- **35 个单测全绿**：`test_parser` / `test_retrieval_inverted`（倒排与全库语义等价）/ `test_agent`（5 条回归基线）/ `test_agent_tools`（6 工具）/ `test_policy_parse`（LLM 文本解析器）/ `test_web`（全接口）。
- **真实书端到端**：《蛊真人》建库 → chapters/stats 核对 → 首次出现 / 轨迹 / 防剧透 / 因果多步 / JSON 卡片 全链路通过。

---

## 二、距离"完整 Agent 工具项目"还差什么

按"真要做成一个可被社区接走的 Agent 工具"的标准，刻度化差距如下。✅ 已达成 / ⚠️ 部分达成 / ❌ 缺失。

### 2.1 检索层 ⚠️

- ❌ **无向量化语义检索**。当前是汉字 bigram 倒排表，召回本质是"字形重叠"。问"主角兵器"无法召回正文写"那把剑"的段落，无法处理同义/换名提问。
- ❌ **无 chunk 级召回**，倒排表打分只到 child 文本，未做 query 扩写、未做 MMR 去冗余。
- ⚠️ 章节摘要只是正文前 140 字，非语义摘要。

### 2.2 Agent 层 ⚠️（核心，但仍是"手写轻量版"）

- ⚠️ **多步推理靠 RuleBasedPolicy 的确定性状态机**，分支是写死的 5 类意图 × 固定步骤数，**不是真正的"看观察再决定下一步"**的动态规划。LLMReActPolicy 能动态决策，但：
  - ❌ **用纯文本协议解析**（要求模型输出 `thought/action/...`），未走 OpenAI **原生 function-calling**。对国产兼容端点或非标准模型输出格式有解析风险，靠 30 行简易解析器 + 2 次回退兜底。
  - ⚠️ max_steps 固定 6/8，无"质量够了就提前停"的自适应。
- ❌ **LangGraph 仅预留接口**，未真正用 `StateGraph` 编排，无 checkpointing / 中断恢复 / 人审介入（human-in-the-loop）。
- ❌ **无 agent 记忆跨会话**：每次 `ask` 独立，不记得你刚才问过什么、追过哪条线索（trace 只在单次调用内）。
- ❌ **无自我纠错**：检索召回为空时只是换说法重试的建议文字，没有"自动放宽 max_chapter / 改写 query 再检索"的真重试。
- ⚠️ **工具偏读**：6 个工具全是查询类，无"写"工具（如标记笔记、打标签、修正实体别名）。

### 2.3 索引层 ⚠️

- ❌ **无人物关系图**：实体只有"首次出现 / 出现章节"，无"方源——师徒——某师傅""方源——敌——某正道"这类关系边。
- ❌ **无主题/线索自动抽取**：无"自由意志""重生""复仇"这类主题线。
- ⚠️ **实体别名不完整**：词表里的别名才有效，自动挖掘不产出别名关系。
- ⚠️ 章节摘要非语义、无 chunk 重要性权重。

### 2.4 交互层 ⚠️

- ⚠️ Web 是零依赖单页，无多轮对话历史展示、无笔记、无高亮原文、无导出。
- ❌ 无 Streamlit / 评论式富前端。
- ⚠️ CLI 一次性问答，无交互式 REPL。

### 2.5 工程化 ❌（MVP 通病）

- ❌ **无打包发布**：仍 `python bookrecall.py` 直跑，未做 `pip install bookrecall` / Console entry。
- ❌ **无 CI**：35 测试只在本地跑。
- ⚠️ **无配置文件**：分块参数、检索 top_k 硬编码在 `config.py`，无用户 config。
- ❌ **无增量建索引**：改了实体词表要 `clear` 全删重建，840 万字重建代价高。
- ⚠️ **无观测**：无 agent 调用链日志、无检索召回率可视化。
- ❌ **`.bookrecall/` 未列入 .gitignore**（需确认）。

---

## 三、该如何完善

按"实现成本 / 价值"排优先级，分四层给出可落地路径。每条带示意落点。

### 优先级总览

| 序 | 项目 | 价值 | 成本 | 层 |
| --- | --- | --- | --- | --- |
| P0 | 接原生 function-calling + 降级链 | 极高 | 中 | Agent |
| P0 | 真向量语义检索（BGE+FAISS） | 极高 | 高 | 检索 |
| P1 | 跨会话 agent 记忆 | 高 | 中 | Agent |
| P1 | LangGraph 真编排 + checkpointing | 高 | 中 | Agent |
| P1 | 人物关系图 + 主题线索 | 高 | 高 | 索引 |
| P2 | 交互式 REPL + 多轮对话前端 | 中 | 中 | 交互 |
| P2 | 增量建索引 | 中 | 中 | 索引/工程 |
| P3 | 打包发布 + CI + 配置文件 | 中 | 低 | 工程 |
| P3 | 评测集 + 召回率观测 | 中 | 中 | 工程 |

---

### 3.1 Agent 层完善（最高优先级）

**P0-A：接原生 function-calling，保留降级链**

当前 `LLMReActPolicy` 用文本协议，应改为优先走 OpenAI `tools` 参数的原生 function-calling，回退到文本协议。改造落点：`cloud.py` 新增 `chat_with_tools(messages, tools)` 方法透传 `tool_calls`；`policies/llm_react.py` 的 `_parse_react` 改为优先解析 `response.tool_calls`，文本协议作降级。

```python
# cloud.py 增量示意
def chat_with_tools(self, messages, tools, tool_choice="auto"):
    payload = {"model": self.model, "messages": messages,
               "tools": tools, "tool_choice": tool_choice, "temperature": 0.2}
    # ... urllib 调用，返回 {content, tool_calls}
```

降级链保留不变（2 次失败回退规则版），这样任意 OpenAI 兼容端点都能稳。

**P1-A：跨会话 agent 记忆**

给 `AgentState` 增加可选 `session_id`，把 trace + 上轮答案持久化到 SQLite 新表 `agent_memory(book_id, user_id, session_id, turn, trace_json)`。`_init_state` 时回灌最近 N 轮 trace 进 LLM policy 的 prompt，使它能"接着上条线索继续问"。落点：`storage.py` 加表 + `core.py` 的 `_init_state` / `ask_card` 收尾处读写。

**P1-B：真正落地 LangGraphPolicy**

把 `policies/langgraph.py` 占位实现成：
- 节点 = 6 工具节点 + 1 个路由节点（复用 `RuleBasedPolicy.next_action` 的判定逻辑）。
- 用 `StateGraph[AgentState]`，`add_conditional_edges` 做路由。
- 启用 checkpointing，存到 SQLite，支持中断恢复 + 人审（human-in-the-loop）——让长链检索可被打断纠正。

```python
from langgraph.graph import StateGraph
builder = StateGraph(AgentState)
builder.add_node("router", route_node)          # 决定下一步工具
for t in registry.names():
    builder.add_node(t, tool_node(t))
builder.add_conditional_edges("router", lambda s: policy.next_action(s, registry).tool_call.name)
```

核心循环改成调 `graph.invoke(state)`，旧的 while 循环作为 `LangGraphPolicy` 的内部实现。**对外 `ask` 签名仍不变**。

**P1-C：自我纠错工具 + 写工具**

新增非查询工具：
- `expand_query(query)`：query 改写（同义词/删除实体），失败召回时自动重试。
- `relax_search(query)`：可放宽到更大 max_chapter（仍受全局进度硬上限钳制）。
- `add_note(book_id, chapter, text)`：在卡片里沉淀用户笔记，跨会话可见。

工具注册表已支持任意新增，`describe_for_llm` 自动序列化，无需改核心。

---

### 3.2 检索层完善

**P0-B：BGE + FAISS 真向量语义检索（可选依赖通道）**

把 `retrieval.py` 做成"可切换检索器"抽象：

```python
class Retriever(Protocol):
    def search(self, book_id, query, max_chapter=None) -> list[SearchHit]: ...

class LexicalRetriever:   # 现有倒排表版，零依赖默认
    ...
class EmbeddingRetriever: # bge-small-zh-v1.5 + FAISS，可选
    def __init__(self, model_name="BAAI/bge-small-zh-v1.5"):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)
        self._index = None  # faiss.IndexFlatIP，建库时建
```

`build` 时若检测到可选依赖就建向量索引（存 `.bookrecall/<book_id>/vectors.faiss`），否则落回倒排表。**SQLite 元数据过滤与防剧透逻辑完全复用**，只换打分层。`config.py` 增 `retriever: lexical|embedding`。

3060 6GB 上 `bge-small-zh` 构建约几分钟、占 ~1.2GB 显存，可接受；纯 CPU 也能跑，慢些。

**P2：chunk 级召回增强**

- query 扩写（实体别名回填进 query）。
- MMR 去冗余，避免 top_k 全是同一段。
- 章节摘要改用 LLM 抽取（建库时一次性，缓存进 `chapter_summaries`）。

---

### 3.3 索引层完善

**P1-D：人物关系图**

新增 EntityRelation 抽取工具。两种实现：
- 规则版（零依赖）：实体共现统计 + 简单关系词模板（"是 X 的徒弟" 等模式）。
- LLM 版（可选）：建库时按章节摘要批量喂模型抽 `(主体, 关系, 客体)` 三元组，存新表 `entity_relations`。
新增工具 `lookup_relations(entity)`，让能回答"方源和谁是对手/盟友"。

**P1-E：主题线索**

对全书做主题抽取（建库时一次 LLM 调用或 TF-IDF），存 `themes` 表挂到章节。新增工具 `search_theme(theme)`，回答"自由意志观点前后变化"时能跨章节聚合。

**P2-F：增量建索引**

`build --update-entities` 只重建实体索引不动 chunk；`build --append-chapters` 追加新章节。降低大书重建代价。

---

### 3.4 交互层完善

**P2-G：交互式 REPL + 多轮**

新增 `bookrecall.py chat --book-id X`，单进程内复用 agent，维护会话状态（trace 持续累积），上下箭头看历史，追问直接续。Web 端同理把 `state` 挂到 session。

**P2-H：富前端**

任选其一：
- Streamlit（最快）：复用 `/api/*`，加对话流、证据高亮、章节跳转、笔记侧边栏。
- 改造现有零依赖单页加多轮气泡 + fetch 流式。

---

### 3.5 工程化（P3，但易做易收效）

- **打包**：完善 `pyproject.toml` 的 `[project.scripts]`，发 PyPI，让 `pip install bookrecall` 即用。向量/Streamlit/LangGraph 进 `[project.optional-dependencies]`（当前已有 `full`，整理清晰）。
- **CI**：加 GitHub Actions 跑 `unittest discover`，绿才合并。
- **配置**：新增 `bookrecall.toml`，`ChunkSettings` / `SearchSettings` / 检索器选择 / 模型名都走配置。
- **评测**：建 `eval/` 放 20–50 个标准问答对（含防剧透正负例），跑 `bookrecall.py eval` 输出召回率/剧透率/平均步数。这是衡量后续改进收益的地基。
- **观测**：agent trace 可选写 `--debug-trace` 到文件，渲染成决策树可视化。
- **`.gitignore`** 确认 `.bookrecall/` 与 `book/` 是否入库。

---

## 四、一句话总结

BookRecall 当下是**一个工程化扎实、零依赖、真书验证过的 Agent MVP**——它已经把"对抗阅读遗忘"的核心价值链路（结构化索引 + 防剧透 + 多步 ReAct + 记忆卡片）完整跑通。但它是**手写轻量 ReAct**而非 LangGraph 编排、是**词法倒排**而非真向量、是**单页零依赖**而非富前端、是**直跑脚本**而非可安装包。完善路径清晰：Agent 层接 function-calling + 跨会话记忆 + 真 LangGraph，检索层接 BGE+FAISS 作可选通道，索引层补关系图与主题，交互层上 REPL/Streamlit，工程层补打包/CI/评测——每一步都在现有契约内增量演进，**对外 `ask`/`MemoryCard` 契约自始至终不变**。
