# s03: TodoWrite 完整流程详解

## 问题

LLM 没有计划就瞎干，复杂任务容易跑偏丢步骤。

## 解决

加 `TodoManager`，LLM 自己规划、自己更新进度，代码在旁边盯着催。

## 关键组件

### 1. TodoManager 类

```python
class TodoManager:
    def update(self, items: list) -> str:
        # 校验：最多 20 条，同时只能 1 个 in_progress
        # status 只能是 pending/in_progress/completed
        self.items = validated
        return self.render()   # ← 返回渲染后的文本

    def render(self) -> str:
        # 渲染成 [ ] [>] [x] 格式
        # [>] #1: Create hello.py
        # [ ] #2: Test hello.py
        # (0/2 completed)
```

### 2. Nag Reminder（催促机制）

```python
rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
if rounds_since_todo >= 3:
    results.insert(0, {"type": "text", "text": "<reminder>Update your todos.</reminder>"})
```

连续 3 轮没更新 todo，代码自动往 tool_result 前面插一条提醒。LLM 看到后会去更新 todo。

## 完整 messages 流程

假设用户输入：`Create hello.py and test it`

```
messages = [

  ── 你输入 prompt ──
  {"role": "user", "content": "Create hello.py and test it"}

  ── 第 1 轮：LLM 先规划 todo ──
  {"role": "assistant", "content": [
      ToolUseBlock(name="todo", input={"items": [
          {"id": "1", "text": "Create hello.py", "status": "in_progress"},
          {"id": "2", "text": "Test hello.py", "status": "pending"},
      ]})
  ]}
  {"role": "user", "content": [
      {"type": "tool_result", "content":
          "[>] #1: Create hello.py\n[ ] #2: Test hello.py\n(0/2 completed)"}
  ]}
  → rounds_since_todo = 0

  ── 第 2 轮：LLM 创建文件 ──
  {"role": "assistant", "content": [
      ToolUseBlock(name="write_file", input={"path": "hello.py", "content": "print('Hello')"})
  ]}
  {"role": "user", "content": [
      {"type": "tool_result", "content": "Wrote 14 bytes"}
  ]}
  → rounds_since_todo = 1

  ── 第 3 轮：LLM 跑测试 ──
  {"role": "assistant", "content": [
      ToolUseBlock(name="bash", input={"command": "python hello.py"})
  ]}
  {"role": "user", "content": [
      {"type": "tool_result", "content": "Hello"}
  ]}
  → rounds_since_todo = 2

  ── 第 4 轮：LLM 还是没更新 todo ──
  {"role": "assistant", "content": [
      ToolUseBlock(name="bash", input={"command": "cat hello.py"})
  ]}
  {"role": "user", "content": [
      ↓↓↓ 计数器到 3，代码偷偷插入提醒 ↓↓↓
      {"type": "text", "text": "<reminder>Update your todos.</reminder>"},
      {"type": "tool_result", "content": "print('Hello')"}
  ]}
  → rounds_since_todo = 3

  ── 第 5 轮：LLM 看到提醒，更新 todo ──
  {"role": "assistant", "content": [
      ToolUseBlock(name="todo", input={"items": [
          {"id": "1", "text": "Create hello.py", "status": "completed"},
          {"id": "2", "text": "Test hello.py", "status": "completed"},
      ]})
  ]}
  {"role": "user", "content": [
      {"type": "tool_result", "content":
          "[x] #1: Create hello.py\n[x] #2: Test hello.py\n(2/2 completed)"}
  ]}
  → rounds_since_todo = 0（归零）

  ── 第 6 轮：LLM 说完了 ──
  {"role": "assistant", "content": [
      TextBlock(text="Done! Created and tested hello.py.")
  ]}
  → stop_reason = "end_turn"，循环结束

]
```

## 调用链路

```
LLM 返回 ToolUseBlock(name="todo", input={items: [...]})
    ↓
handler = TOOL_HANDLERS["todo"]           # 拿到 lambda
    ↓
handler(**block.input)                    # 调用 TODO.update(items)
    ↓
TODO.update(items)                        # 校验 + 存储
    ↓
return self.render()                      # 渲染成 [>] [ ] [x] 文本
    ↓
output = "[>] #1: Create hello.py\n..."   # 返回给 agent_loop
    ↓
results.append({"type": "tool_result", "content": output})
    ↓
messages.append({"role": "user", "content": results})
    ↓
下一轮 LLM 看到 tool_result，知道当前进度
```

## 核心认识

- **TodoManager 只是容器**：不帮 LLM 规划，LLM 自己决定拆几步、怎么拆
- **Nag reminder 是代码塞的**：放在 role: "user" 里，LLM 以为是用户在催它
- **update() 内部调 render()**：返回格式化文本作为 tool_result
- **循环没变**：只是在 tool_result 前面偷偷插提醒
