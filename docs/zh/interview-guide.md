# Agent 12 课面试指南

> 基于 learn-claude-code 项目，从零到一构建类 Claude Code Agent 的核心知识。

## 核心认知

**Agent = LLM + 工具 + 循环。** 不是框架，不是 prompt chain，不是拖拽工作流。

- LLM 是大脑，负责决策
- 工具是手脚，负责执行
- 循环是心跳，驱动整个过程
- 你的代码是 harness（缰绳），给 LLM 提供工作环境

> The model is the agent. The code is the harness.

## 核心循环（从 s01 到 s12 始终不变）

```python
def agent_loop(messages):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM,
            messages=messages, tools=TOOLS,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return  # LLM 说完了

        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = TOOL_HANDLERS[block.name](**block.input)
                results.append({"type": "tool_result",
                    "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})
```

12 节课都在这个循环上叠加机制，循环本身从不改变。

---

## 逐课精华

### s01: Agent Loop — 一个循环就是一个 Agent

**问题：** LLM 能推理但碰不到真实世界，不能读文件、跑命令、看报错。没有循环，每次工具调用都得人工粘结果。

**解决：** while 循环 + bash 工具 + stop_reason 判断退出。

**核心原理：**
- `stop_reason="tool_use"` → LLM 想调工具，继续循环
- `stop_reason="end_turn"` → LLM 说完了，退出
- LLM 只决策不执行，返回 ToolUseBlock 说"我想跑 ls"，你的代码 subprocess.run() 真正执行
- messages 是你的代码维护的列表，LLM 无状态，每次把完整历史传过去

**面试一句话：** Agent 就是一个 while 循环，LLM 决定做什么、什么时候停，代码负责执行。

---

### s02: Tool Use — 加工具不改循环

**问题：** 只有 bash 时，所有操作都走 shell。cat 截断不可预测，sed 遇特殊字符崩，每次 bash 调用都是不受约束的安全面。

**解决：** dispatch map（字典路由）+ 专用工具 + 路径沙箱。

**核心原理：**
- `TOOL_HANDLERS = {"bash": handler, "read_file": handler, ...}` — 按名字查函数
- `handler = TOOL_HANDLERS.get(block.name)` — 一行代码替代 if/elif 链
- `safe_path()` — 限制文件操作只能在工作目录内，防止 LLM 读写系统文件
- 加新工具 = 加一个 handler 函数 + 往字典和 TOOLS 各加一条

**面试一句话：** 字典路由做工具分发，专用工具替代万能 bash 做安全沙箱，循环不变。

---

### s03: TodoWrite — 先规划再执行

**问题：** LLM 没有计划就瞎干，复杂任务容易跑偏丢步骤。

**解决：** TodoManager 让 LLM 自己规划进度 + Nag reminder 催促更新。

**核心原理：**
- TodoManager 只是容器（存储 + 渲染），不帮 LLM 规划，LLM 自己决定拆几步
- `update()` 内部调 `render()` 返回 `[>] [ ] [x]` 格式文本，作为 tool_result 喂回
- 连续 3 轮没更新 todo，代码偷偷在 tool_result 前插入 `<reminder>Update your todos.</reminder>`
- reminder 放在 `role: "user"` 里，LLM 以为是用户在催它
- 同一时间只允许 1 个 in_progress 任务，防止 LLM 并行乱来

**面试一句话：** LLM 自己规划任务，代码提供存储和催促机制，规划能力取决于模型本身。

---

### s04: Subagent — 隔离上下文

**问题：** 所有对话堆在一个 messages 里，复杂任务上下文越来越脏。

**解决：** 子 agent 用独立的 messages[]，完成后只返回 summary。

**核心原理：**
- `sub_messages = []` — 全新空 messages，不污染父 agent
- 子 agent 就是 agent_loop 再跑一遍，同一个模式
- 子 agent 没有 task 工具，不能再派子 agent（防递归套娃）
- 只返回最终文本 summary，中间过程全丢弃
- 同步执行，会阻塞主循环（和 s09 teammate 的区别）

**面试一句话：** 派人去调查，只带结论回来，笔记不堆你桌上。

---

### s05: Skill Loading — 按需加载知识

**问题：** 把所有知识都塞 system prompt 里，token 浪费，上下文爆炸。

**解决：** 两层注入 — 菜单便宜，全文按需。

**核心原理：**
- Layer 1（便宜）：system prompt 只放技能名和一句简介（~十几 token/技能）
- Layer 2（按需）：LLM 调 `load_skill("pdf")` 时，完整内容通过 tool_result 注入
- SkillLoader 启动时扫描 `skills/*/SKILL.md`，全部读进内存但只展示摘要
- SKILL.md 用 YAML frontmatter 存 name/description，body 是全文内容
- load_skill 就是普通工具，走 dispatch map

**面试一句话：** 餐厅菜单只写菜名，你点了才端上来，不点的不占桌子。

---

### s06: Context Compact — 战略性遗忘

**问题：** 对话越来越长，token 成本爆炸，最终超出上下文窗口。

**解决：** 三层压缩策略。

**核心原理：**
- Layer 1（micro_compact）：每轮自动执行，旧 tool_result 替换成 `"[Previous: used bash]"`，只保留最近 3 条完整结果
- Layer 2（auto_compact）：token 估算超 50000 时触发，保存完整对话到 `.transcripts/` 文件，让 LLM 生成摘要替换整个 messages
- Layer 3（compact 工具）：LLM 自己觉得上下文太乱时主动调用
- 压缩后 messages 只剩 2 条（摘要 + "Understood"），从头开始但保留关键信息
- `estimate_tokens = len(str(messages)) // 4`（粗略估算，约 4 字符 = 1 token）

**面试一句话：** 三层压缩：占位符替换 → 自动摘要 → 手动触发，让 agent 能无限工作。

---

### s07: Task System — 状态存文件

**问题：** s03 的 todo 存在内存里，上下文压缩后就丢了。

**解决：** 任务持久化到 JSON 文件 + 依赖图。

**核心原理：**
- 每个任务一个文件：`.tasks/task_N.json`，压缩上下文也不丢
- 依赖图：`blockedBy` / `blocks` 字段，完成 task 1 自动从 task 2 的 blockedBy 里移除
- CRUD 四个工具：task_create / task_update / task_list / task_get
- 和 s03 TodoManager 的区别：内存 vs 文件，无依赖 vs 有依赖

**面试一句话：** 状态存文件，不怕上下文压缩丢失，依赖图自动解锁下游任务。

---

### s08: Background Tasks — 不阻塞主循环

**问题：** bash 是阻塞的，跑个慢命令（npm test、pip install）整个 agent 卡住等。

**解决：** 后台线程执行命令，通知队列回传结果。

**核心原理：**
- `threading.Thread` 里跑 `subprocess.run()`（线程里创建子进程）
- 立即返回 task_id，主循环继续调 LLM
- 命令跑完结果塞进 `_notification_queue`
- 每轮循环开头 `drain_notifications()`，有结果就注入 messages
- 线程负责不阻塞，子进程负责执行 shell 命令

**面试一句话：** 火后不管，主循环继续工作，结果通过通知队列异步回传。

---

### s09: Agent Teams — 多 agent 协作

**问题：** 一个 agent 处理不了大型任务，需要分工协作。

**解决：** 持久化命名 agent + JSONL 文件邮箱通信。

**核心原理：**
- 每个 teammate 一个独立线程，跑自己的 agent_loop
- JSONL 邮箱通信：`name.jsonl`，append 写入，drain 读取（读完清空）
- `config.json` 记录团队成员：name、role、status（working/idle/shutdown）
- LLM 自己决定 spawn 几个 teammate、叫什么、干什么
- 和 s04 子 agent 区别：持久存在（不用完即毁）、双向通信（不只返回 summary）、独立线程（不阻塞主循环）

**面试一句话：** 每个 teammate 是独立线程里的 agent_loop，通过 JSONL 文件邮箱收发消息。

---

### s10: Team Protocols — 协作规则

**问题：** teammate 跑起来后没有协议，不知道怎么安全关闭，也没有计划审批机制。

**解决：** 两个协议，都用 request_id 关联请求和响应。

**核心原理：**
- 关闭协议：Lead → shutdown_request(request_id) → teammate → shutdown_response(request_id, approve) → Lead
- 计划审批：teammate → plan_approval(plan) → Lead → plan_approval(request_id, approve, feedback) → teammate
- 状态机：`pending → approved / rejected`
- request_id 解决异步匹配问题：同时给多人发请求，靠 request_id 区分谁回的哪个
- `_tracker_lock = threading.Lock()` 保证多线程安全

**面试一句话：** 同一个 request_id 关联模式驱动所有协议，状态机追踪请求生命周期。

---

### s11: Autonomous Agents — 自己找活干

**问题：** teammate 干完活就 idle 了，等着 lead 分配新任务，不会主动找活干。

**解决：** 空闲循环 + 任务板轮询 + 自动认领。

**核心原理：**
- 工作阶段：正常 agent_loop
- 空闲阶段：每 5 秒轮询一次（最长 60 秒），检查两个来源：
  1. 邮箱有新消息 → 回去工作
  2. `.tasks/` 有无主任务 → 认领 → 回去工作
  3. 超时 60 秒无活 → shutdown
- `claim_task()` 用锁保护（`_claim_lock`），防止多个 agent 同时认领同一个任务
- 身份注入：上下文压缩后重新注入 `<identity>` 块，防止 agent 忘了自己是谁

**面试一句话：** agent 不等命令，自己轮询任务板找活干，从被动变主动。

---

### s12: Worktree Isolation — 目录隔离

**问题：** 多个 agent 在同一个目录干活，互相覆盖文件。

**解决：** git worktree 给每个任务创建独立目录。

**核心原理：**
- `git worktree add` 创建独立工作目录 + 独立分支
- 任务是控制面（`.tasks/`），worktree 是执行面（`.worktrees/`），用 task_id 绑定
- `worktree_run(name, command)` — 在指定 worktree 目录里执行命令（cwd 指向 worktree）
- EventBus 事件总线：append-only 的 `events.jsonl`，记录 worktree 生命周期（create/remove/keep）
- 完成后两种选择：`worktree_remove`（丢弃）或 `worktree_keep`（保留）

**面试一句话：** 任务管目标，worktree 管目录，用 task_id 绑定，互不污染。

---

## 架构演进总览

```
s01  循环          ← 最小 agent
s02  +多工具       ← 安全分发
s03  +规划         ← 先想后做
s04  +子agent      ← 上下文隔离
s05  +技能加载     ← 按需知识
s06  +压缩         ← 无限工作
s07  +任务持久化   ← 不怕丢失
s08  +后台执行     ← 不阻塞
s09  +团队协作     ← 多 agent
s10  +协议         ← 安全协调
s11  +自主         ← 主动找活
s12  +目录隔离     ← 互不污染
```

## 面试高频 5 问

1. **Agent 是什么？** → while 循环 + LLM 决策 + 代码执行工具，30 行代码就能实现
2. **怎么加工具？** → dispatch map 字典路由，加 handler + schema，循环不变
3. **上下文太长怎么办？** → 三层压缩：占位符替换 → 自动摘要 → 手动触发
4. **多 agent 怎么通信？** → JSONL 文件邮箱，append 写入，drain 读取
5. **怎么做隔离？** → 上下文隔离（s04 子 agent），文件系统隔离（s12 git worktree）

## SDK 区分

| | Anthropic Python SDK | Claude Agent SDK |
|---|---|---|
| 包名 | `anthropic` | `claude-agent-sdk` |
| 做什么 | 调 API：发消息、拿回复 | 造 Agent：循环、工具、子 agent 全封装好 |
| 类比 | 方向盘 | 整辆车 |

这个项目用 Anthropic Python SDK（方向盘），手写所有机制（自己造车）。学完再看 Claude Agent SDK 源码就全懂了。
