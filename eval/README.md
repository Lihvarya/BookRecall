# BookRecall 召回评测集

`eval-retrieval` 用标注问题集评估“裸检索器是否找到了正确章节和证据”。`eval-agent` 使用同一数据集评估规则 Agent 的工具路由、最终证据和防剧透边界。两者默认都不调用生成式 LLM，可以把粗召回退化与 Agent 路由问题分开诊断。

## 数据格式

推荐使用 UTF-8 JSONL，每行一个案例：

```json
{"id":"death-cause","book_id":"gu","query":"某角色怎么死的？","relevant_chapters":[1287],"evidence_terms":["死了","尸躯"],"max_chapter":1287,"tags":["death","fact"]}
```

字段：

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `id` | 是 | 数据集内唯一的稳定 ID |
| `book_id` | 是 | 本地书籍 ID；可被 CLI `--book-id` 覆盖 |
| `query` | 是 | 用户问题 |
| `relevant_chapters` | 是 | 能直接支撑答案的章节号，可有多个 |
| `evidence_terms` | 否 | Top K 证据应覆盖的关键原文词 |
| `max_chapter` | 否 | 阅读进度上限，相关章节不能超过该值 |
| `tags` | 否 | 问题类型标签 |
| `note` | 否 | 标注说明，不参与评分 |

## 运行

只评测不需要模型的倒排检索：

```powershell
.\.venv\Scripts\python.exe bookrecall.py eval-retrieval `
  --dataset eval\bookrecall_regression.example.jsonl `
  --book-id _4.0 `
  --retrievers lexical `
  --top-k 4
```

对比完整本地召回链路：

```powershell
.\.venv\Scripts\python.exe bookrecall.py eval-retrieval `
  --dataset eval\bookrecall_regression.example.jsonl `
  --book-id _4.0 `
  --retrievers lexical,embedding,embedding-rerank `
  --top-k 4 `
  --format json
```

加入回归门禁：

```powershell
.\.venv\Scripts\python.exe bookrecall.py eval-retrieval `
  --dataset eval\bookrecall_regression.example.jsonl `
  --book-id _4.0 `
  --retrievers lexical `
  --min-top1 0.60 `
  --min-mrr 0.75 `
  --fail-on-error
```

门禁失败时命令退出码为 `1`；数据格式错误或书籍不存在时退出码为 `2`。模型路线不可用会计入错误，不会静默回退成倒排检索。

评测实际 Agent 工作流：

```powershell
.\.venv\Scripts\python.exe bookrecall.py eval-agent `
  --dataset eval\bookrecall_regression.example.jsonl `
  --book-id _4.0 `
  --retrievers lexical `
  --top-k 4 `
  --min-top1 0.60 `
  --fail-on-error `
  --fail-on-spoiler
```

`eval-agent` 不配置 session、本地 LLM 或云端模型，因此不会写会话和动态结构化索引。它主要验证确定性问题分流、工具组合、最终证据章节和阅读进度裁剪。

示例文件只包含三个已知失败类型，用于验证工具链。正式回归门禁仍需要逐步扩充到 30-100 条人工确认案例。
