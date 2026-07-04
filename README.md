# BookRecall · 书之回响

> 一个**对抗"阅读遗忘"的本地记忆 Agent**——你都读过，只是想不起来了。它帮你 1 秒找回人物首次登场、道具出现轨迹、某个观点前后变化，并给出**可追溯的原文证据**，绝不剧透你没读到的章节。

专为**长篇大部头**设计：已在真实 **840 万字、2340 章的网文《蛊真人》**上端到端跑通——2347 章解析、3.5 万切分块、10 万+ 实体出现记录，提问亚秒级返回。

---

## 为什么需要它

读完一部 1000 万字的长篇，过阵子你会卡在这些问题上：

- 「【星辰之匙】第一次出现在哪一章？」
- 「主角最后是怎么拿到星辰之匙的？」
- 「第 50 章那个黑衣人，后来还有出现过吗？」
- 「关于"自由意志"的观点前后有什么变化？」

普通 RAG 把全文切片做向量检索、再让大模型回答——但它答不好**强顺序性问题**（"第一次"），还会**剧透你没读到的部分**。BookRecall 的不同在于：

| 普通向量 RAG | BookRecall |
| --- | --- |
| 全文切片 → 语义近似 → 生成 | 实体索引 + 分层检索 + 元数据过滤 → **结构化记忆卡片** |
| 难以精准回答"第一次出现在哪" | 实体索引 **100% 精准**命中首次章节 |
| 不感知你的阅读进度，容易剧透 | **三重防剧透**：进度内才取证，越界证据一律剪枝 |
| 只给一段话 | 给【结论 + 原文摘录 + 章节定位 + 追问方向】的卡片 |
| 强依赖向量模型与 GPU | **零运行时依赖**，纯 Python 标准库即可跑 |

---

## 核心特性

- 🔍 **本地三层索引**：细粒度 child 块 + 章节级 parent 块 + 结构化实体索引，结构化索引专治"首次出现 / 出现轨迹"这类向量检索答不好的强顺序问题。
- ⚡ **倒排表加速检索（纯标准库）**：首次 2340 章 × 百 chunk 规模，从全库 O(n) 扫描升级为 O(命中)，打分语义不变、结果等价。
- 🧠 **可选本地小模型语义检索**：新增 `sentence-transformers` embedding 通道，推荐 `BAAI/bge-small-zh-v1.5`；向量索引独立保存在 `.bookrecall/vectors/`，没有可选依赖时自动保留零依赖倒排检索。
- 🤖 **LangAgent 架构**：手写轻量 **ReAct 状态机**——6 个可调用工具 + 可插拔决策策略（规则版默认 / LLM-ReAct 可选 / LangGraph 预留），支持"先查轨迹→聚焦末章→总结"这类多步推理。
- 🛡️ **三重防剧透**：工具内部进度限制 → `_clamp_max_chapter` 二次钳制 → `_prune_evidence` 兜底，**即便 LLM 乱传章节号也无法越界**。
- 🏷️ **实体别名**：`黑衣人|黑袍人,黑衣客`——提问用任一别名都能解析回规范名。
- 🧹 **高频 n-gram 实体自动挖掘**：强化符 `【】《》「」` + 全文 2/3/4-gram 词频，没有手工词表也能快速建索引。
- 💳 **结构化记忆卡片**：每次回答都是 `结论 + 证据片段 + 章节定位 + 追问建议`，JSON 与文本双格式，方便接入前端。
- 🌐 **零依赖本地 Web**：书库总览 / 实体索引 / 章节浏览 / 进度管理 / 问答卡片，一页搞定，无需 Streamlit。
- ☁️ **可选云端推理**：设了 OpenAI 兼容 API Key 即启用 LLM-ReAct 决策；没有则全程本地规则合成，**离线可用**。

---

## 架构

```text
                         ┌──────────────────────────┐
                         │       User Query         │
                         └────────────┬─────────────┘
                                      ▼
                         ┌──────────────────────────┐
                         │   LangAgent (ReAct Loop)  │
                         │   state + policy + tools  │
                         └─────┬──────────────┬───────┘
              decision/observe│              │ terminal
                               ▼              ▼
   ┌─────────────────────────────┐   ┌──────────────────────┐
   │   Local Indexing Layer       │   │  Cloud LLM (可选)    │
   │   • entity index (SQLite)    │   │  OpenAI 兼容接口      │
   │   • parent/child chunks      │   │  LLMReActPolicy 决策 │
   │   • inverted index retrieval │   └──────────────────────┘
   │   • metadata filter(进度)    │
   └─────────────────────────────┘
```

**三重防剧透**贯穿全链路：`progress_chapter` 在入口锁定，每次工具调用的 `max_chapter` 都不许超过它；任何越界证据在 `_prune_evidence` 被剪掉并标记 `spoiler_blocked`。

---

## 三步上手

> 前置：Python 3.11+。**无需 pip install 任何依赖**（云端推理为可选增强）。

```bash
git clone <repo-url> BookRecall && cd BookRecall

# 1) 给样书建索引（含精选实体词表）
python bookrecall.py build \
  --book-id sample \
  --input examples/sample_book.txt \
  --entities examples/sample_entities.txt

# 2) 设置阅读进度（防剧透以此为界）
python bookrecall.py set-progress --book-id sample --chapter 3

# 3) 提问
python bookrecall.py ask --book-id sample --question "星辰之匙第一次出现在哪一章？"
```

输出长这样：

```text
问题类型：实体首次出现
阅读进度保护：已限制到第 3 章
关联实体：星辰之匙
结论："星辰之匙"第一次出现于第 1 章。
证据定位：
- 第 1 章《灰塔来信》：林澈在灰塔的旧书库里翻出一封没有署名的信。信里第一次提到了【星辰之匙】...
你接下来还可以问：
- 星辰之匙后来还有出现过吗？
- 星辰之匙最后是怎么被拿到或使用的？
```

JSON 记忆卡片（接前端用）：

```bash
python bookrecall.py ask --book-id sample --format json \
  --question "黑袍人第一次出现在哪一章？"
```

---

## 命令一览

| 命令 | 作用 |
| --- | --- |
| `build` | 为一本书建立本地索引（章节解析 → 分层分块 → 实体索引 → 倒排表 → SQLite） |
| `ask` | 提问，输出结构化记忆卡片（`--format json` / `--progress N` 临时覆盖进度） |
| `set-progress` / `show-progress` | 记录 / 查看阅读进度 |
| `list-books` / `list-entities` | 查看书库与某书的实体索引 |
| `chapters` | 列出章节标题，核对章节解析是否正确 |
| `stats` | 查看索引规模（章节 / chunk / 实体 / 出现记录） |
| `models` | 探测本地小模型依赖与各书向量索引状态 |
| `embed-build` | 用本地 embedding 小模型为已有书籍构建向量索引 |
| `embed-search` | 直接用本地向量索引检索证据片段 |
| `clear` | 删除某本书的全部索引（需 `--yes` 二次确认，不删数据库文件） |
| `serve` | 启动零依赖本地 Web 界面 |

Web 界面：

```bash
python bookrecall.py serve --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000
```

---

## 可选：本地小模型语义检索

默认检索器仍是零依赖倒排检索。若本机已安装 `sentence-transformers` 与 `numpy`，可以为已有书籍构建本地 embedding 索引：

```bash
python bookrecall.py models

python bookrecall.py embed-build --book-id sample \
  --model BAAI/bge-small-zh-v1.5

python bookrecall.py embed-search --book-id sample \
  --query "黑袍人第一次出现在哪一章？" \
  --progress 3

python bookrecall.py ask --book-id sample \
  --retriever embedding \
  --question "黑袍人第一次出现在哪一章？"
```

在没有安装可选依赖时，`models` 会报告缺失项，`ask --retriever auto` 会自动回退到倒排检索：

```bash
python bookrecall.py ask --book-id sample \
  --retriever auto \
  --question "黑袍人第一次出现在哪一章？"
```

建议的本地小模型路线：

- `BAAI/bge-small-zh-v1.5`：中文长篇小说优先，体积小，适合 3060 6GB。
- `BAAI/bge-m3`：效果更强但更重，适合后续作为增强选项。
- 当前向量检索使用 `numpy` 精确余弦相似度，FAISS 不是必需依赖；后续可把 FAISS 接成更大规模索引后端。

---

## 实体词表

实体索引是回答"首次出现 / 轨迹"的基石。两种来源，准确度由高到低：

1. **手工词表**（推荐）：每行一个实体，支持 `标准名|别名1,别名2`：
   ```text
   星辰之匙|钥匙,星匙
   黑衣人|黑袍人,黑衣客
   自由意志
   ```
2. **自动挖掘**：`build` 时不传 `--entities`，系统自动从 `【】《》「」` 强调符 + 高频 n-gram 挖候选。看 `examples/蛊真人_entities_auto.txt` 即自动产物，`蛊真人_entities.txt` 是手工精选。

---

## 可选：云端推理

设了 API Key 即启用 `LLMReActPolicy`，由大模型决定调哪个工具 + 直接给出 final answer；没设则全程本地规则合成（离线可用）。

```bash
export BOOKRECALL_API_KEY="sk-..."
# 可选：
export BOOKRECALL_API_ENDPOINT="https://api.openai.com/v1/chat/completions"
export BOOKRECALL_MODEL="gpt-4o-mini"
```

> 兼容任何 OpenAI Chat Completions 格式的端点。决策失败 2 次自动回退规则版，不会卡死。

---

## 真实大书验证：《蛊真人》

840 万字、2340 个「第X节」标题的网文，是检验索引管线鲁棒性的硬样本：

```bash
python bookrecall.py build --book-id guzhenshuo \
  --input "book/蛊真人错别字修改版4.0.txt" \
  --entities examples/蛊真人_entities.txt
# → 章节数=2347、parent=6147、child=34791、实体=26、实体出现记录≈10 万

python bookrecall.py chapters --book-id guzhenshuo --limit 5
python bookrecall.py stats    --book-id guzhenshuo

# 首次出现（多步：别名解析 → lookup_first_appearance）
python bookrecall.py ask --book-id guzhenshuo --progress 100 --question "方源第一次出现在哪一节？"

# 防剧透：方源第3章才出现，进度设2 → 触发 spoiler_blocked，不暴露章节
python bookrecall.py ask --book-id guzhenshuo --progress 2  --question "方源第一次出现在哪一节？"

# 轨迹追踪（多步：别名 → lookup_timeline）
python bookrecall.py ask --book-id guzhenshuo --progress 100 --question "方源后来还有出现过吗？"
```

> 《蛊真人》的「第X节」标题用全角冒号 `：` 分隔、行首缩进，普通章节正则会全部漏判——`parser.py` 已针对网文格式做了容错。

---

## 项目结构

```text
src/bookrecall/
  agent/            # LangAgent：手写 ReAct 状态机
    core.py          #   ReAct 循环 + 三重防剧透 + 输出契约
    state.py         #   运行态
    tools.py         #   6 个工具 + 注册表 + LLM 函数描述
    render.py        #   记忆卡片渲染（契约不变）
    policies/        #   RuleBased(默认) / LLMReAct(可选) / LangGraph(预留)
  parser.py         # 章节解析（节/章/回/卷/篇/部/集，全角容错）
  chunking.py       # Parent/Child 分层切块
  entity_index.py   # 词表 + auto_discover(强调符+n-gram) + 出现记录
  retrieval.py      # 倒排表检索（纯标准库）
  storage.py        # SQLite 存储
  cloud.py          # 可选 OpenAI 兼容接口
  cli.py / web.py   # 命令行 / 零依赖 Web
tests/              # 38 个单测
examples/           # 样书 + 词表（手工精选 / 自动挖掘）
```

---

## 测试

```bash
python -m unittest discover -s tests -v   # 38 tests, all green
```

覆盖：章节解析误判防御 · 倒排表与全库扫描语义等价 · 6 工具的入参出参与防剧透 · LLM 文本解析器 · Web 全接口 · Agent 5 条回归基线。

---

## 路线图

`agent/` 已是 ReAct 状态机，但仍是**零依赖 MVP**。距离一个"完整的 Agent 工具项目"还有明确差距。详见 **[AGENT_STATUS.md](AGENT_STATUS.md)**：

- [x] 本地索引 / 倒排检索 / LangAgent ReAct / 防剧透 / 多步推理 / Web / CLI
- [x] 可选本地 embedding 通道（sentence-transformers + numpy 精确向量检索）
- [ ] FAISS 加速向量后端与大规模持久化调优
- [ ] LangGraph 原生编排（已预留接口）
- [ ] 原生 function-calling（当前用文本协议解析）
- [ ] 人物关系图 / 自动主题线索 / 章节级笔记
- [ ] Streamlit / 评论式前端

---

## 设计原则

1. **零运行时依赖优先**——纯 Python 标准库即可跑通核心链路，重型能力（向量模型、LangGraph、Streamlit）一律走"可选增强"通道。
2. **结构化优于生成**——能精确查的（首次出现、轨迹）绝不模糊生成；LLM 只用于真正需要综合因果的部分。
3. **防剧透是一等公民**——三重保险，越界证据一律丢弃并明示，宁可少答不可剧透。
4. **对外契约稳定**——`ask` / `ask_card` / 记忆卡片结构跨重构保持一致，上层调用方零改动。

---

## License

MIT
