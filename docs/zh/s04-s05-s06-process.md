# s04-s06: 子 Agent、技能加载、上下文压缩 流程详解

## s04: Subagent（子 Agent）

### 问题

所有对话堆在一个 messages 里，复杂任务上下文越来越脏。

### 解决

子 agent，独立 messages[]。

### 流程

```
主 agent_loop
  ↓
  LLM 返回 task 工具调用
  ↓
  run_subagent(prompt)     ← 进入子循环
    ↓
    sub_messages = []       ← 全新空 messages
    子 while 循环（最多 30 轮）
    子 LLM 自己调工具、自己决定 end_turn
    ↓
    返回 summary 文本       ← 子循环结束，中间过程全丢
  ↓
  summary 作为 tool_result 塞回主 messages
  ↓
  主循环继续
```

### 关键点

- **独立 messages** — sub_messages = []，不污染父 agent
- **受限工具** — CHILD_TOOLS 没有 task 工具，不能再派子 agent，防递归套娃
- **只返回 summary** — 中间调了多少工具、跑了多少轮，父 agent 不关心，只看结论
- **子 agent 自己决定结束** — 和主 agent 一样，stop_reason="end_turn" 时退出

### 核心代码

```python
def run_subagent(prompt: str) -> str:
    sub_messages = [{"role": "user", "content": prompt}]  # 全新空 messages
    for _ in range(30):  # 安全上限
        response = client.messages.create(
            model=MODEL, system=SUBAGENT_SYSTEM, messages=sub_messages,
            tools=CHILD_TOOLS, max_tokens=8000,
        )
        sub_messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break
        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                output = handler(**block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
        sub_messages.append({"role": "user", "content": results})
    # 只返回最终文本
    return "".join(b.text for b in response.content if hasattr(b, "text")) or "(no summary)"
```

---

## s05: Skill Loading（技能加载）

### 问题

把所有知识都塞 system prompt 里，token 浪费，上下文爆炸。

### 解决

两层注入，按需加载。

### 流程

```
启动时：扫描 skills/ 目录下所有 SKILL.md
    ↓
Layer 1（便宜）：system prompt 只放技能名和简介
    Skills available:
      - pdf: Process PDF files...           ← ~十几个 token
      - code-review: Review code changes... ← ~十几个 token
    ↓
LLM 需要某个技能时，调用 load_skill("pdf")
    ↓
Layer 2（按需）：完整技能内容通过 tool_result 注入 messages
    <skill name="pdf">
      完整的 PDF 处理指南...               ← 可能几千 token
    </skill>
```

### 技能文件结构

```
skills/
├── agent-builder/
│   └── SKILL.md          ← frontmatter (name, description) + body
├── code-review/
│   └── SKILL.md
├── mcp-builder/
│   └── SKILL.md
└── pdf/
    └── SKILL.md
```

### 关键点

- **菜单便宜，全文按需** — 几百个技能菜单也就几千 token，但全文放不下
- **SkillLoader 启动时扫描** — 全部读进内存，但只展示摘要
- **load_skill 是普通工具** — 和 bash、read_file 一样，走 dispatch map

### 和 s04 子 agent 的区别

- **s04 子 agent** — 派一个 LLM 去干活，消耗 token
- **s05 Skill** — 加载一段文档给 LLM 看，不额外调 LLM

---

## s06: Context Compact（上下文压缩）

### 问题

对话越来越长，token 成本爆炸，最终超出上下文窗口。

### 解决

三层压缩策略。

### 三层架构

```
每轮自动执行：
┌─────────────────────────────────┐
│ Layer 1: micro_compact          │
│ 旧 tool_result → "[Previous:   │
│ used bash]" 占位符              │
│ 只保留最近 3 条完整结果          │
└──────────────┬──────────────────┘
               ↓
        tokens > 50000?
        /            \
      否              是
       ↓               ↓
    继续        ┌──────────────────────┐
                │ Layer 2: auto_compact │
                │ 1. 保存完整对话到     │
                │    .transcripts/ 文件 │
                │ 2. 让 LLM 生成摘要    │
                │ 3. 用摘要替换整个     │
                │    messages           │
                └──────────────────────┘

LLM 主动触发：
┌──────────────────────────────────┐
│ Layer 3: compact 工具            │
│ 和 Layer 2 一样，但由 LLM 自己   │
│ 决定什么时候需要压缩              │
└──────────────────────────────────┘
```

### 压缩后 messages 变成

```python
messages = [
    {"role": "user", "content": "[Conversation compressed. Transcript: .transcripts/xxx.jsonl]\n\n摘要内容"},
    {"role": "assistant", "content": "Understood. I have the context from the summary. Continuing."},
]
```

### 核心代码

```python
def agent_loop(messages: list):
    while True:
        micro_compact(messages)                    # Layer 1: 每轮清旧结果
        if estimate_tokens(messages) > THRESHOLD:  # Layer 2: 超阈值自动压缩
            messages[:] = auto_compact(messages)
        response = client.messages.create(...)
        ...
        if manual_compact:                         # Layer 3: LLM 主动触发
            messages[:] = auto_compact(messages)
```

### 关键点

- **Layer 1 无感** — 每轮自动跑，LLM 不知道旧结果被替换了
- **Layer 2 有损** — 摘要会丢信息，但完整对话存在 .transcripts/ 文件里
- **Layer 3 主动** — LLM 觉得上下文太乱时可以自己调 compact 工具
- **`messages[:] =`** — 原地替换列表内容，保持引用不变
- **estimate_tokens** — 粗略估算：`len(str(messages)) // 4`（约 4 字符 = 1 token）
