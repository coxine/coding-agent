# Agent 循环设计

## 1. 文档目的

本文定义 Python Agent Core 如何组织消息、调用模型、解析流式 tool calling、执行工具、处理上下文、判断终止并恢复错误。该循环是编程智能体最重要的自行实现部分。

第一版只支持单个活动对话内串行 turn：用户完成一个任务后，可以在同一上下文中继续追问，也可以在 Agent 空闲时切换其他持久化对话，但不能并行执行多个任务。

## 2. 核心概念

### 2.1 Core Session 与 Conversation

一次 Agent Core 进程生命周期对应协议中的 `sessionId`。一个工作区可以保存多个 Conversation，每个 Conversation 有独立 `conversationId` 和消息历史。Core Session 保存运行时配置、工具注册表与当前活动 Conversation；Conversation 保存：

- 对话消息历史。
- 标题、创建时间和更新时间。

Conversation 持久化在工作区 `.coding-agent/sessions/`。启动时加载更新时间最新的一项；恢复时使用当前配置重新生成 system message，再追加保存的 user、assistant 与 tool 消息。持久化点包括用户消息加入后、一个 assistant tool-call 批次取得全部 tool result 后，以及最终 assistant 消息完成后，避免写入缺少对应 tool result 的中间结构。

### 2.2 Turn

用户提交一条任务后创建一个 turn，直到进入以下唯一终态之一：

- `completed`
- `failed`
- `cancelled`

Turn 保存：

- 用户原始任务。
- 当前状态。
- Agent step 数。
- tool call 数。
- 连续错误计数。
- 重复调用检测记录。
- 变更文件集合。
- 取消信号。
- 开始时间和结束时间。

### 2.3 Step

一次完整模型请求及其响应称为一个 Agent step。一个 step 可以返回：

- 纯文本最终回答。
- 文本加一个或多个 tool calls。
- 只有一个或多个 tool calls。
- 无效或不完整响应。

工具调用本身不增加 step；执行完当前响应中的全部工具后，下一次模型请求才进入下一个 step。

### 2.4 Message

内部消息统一为以下角色：

- `system`
- `user`
- `assistant`
- `tool`

模型供应商返回值必须先转换为内部消息，再进入 Agent 逻辑。模型适配层负责把内部消息转换回目标 OpenAI 兼容格式。

## 3. 模块职责

```text
TurnController
├── ContextManager
├── ModelClient
│   ├── RequestBuilder
│   └── StreamParser
├── ToolRegistry
│   ├── ArgumentValidator
│   ├── RiskEvaluator
│   └── ToolExecutor
├── LoopGuard
└── EventEmitter
```

### `TurnController`

拥有 Agent 主循环，维护 turn 状态并协调其他模块。

### `ContextManager`

维护内部消息、估算上下文预算、截断工具输出并生成请求消息。

### `ModelClient`

处理 OpenAI 兼容请求、流式响应、错误分类和有限重试，不负责决定是否执行工具。

### `ToolRegistry`

查找、校验、评估和执行工具，返回统一结果。

### `LoopGuard`

检查最大步数、重复调用、连续失败、取消和其他终止条件。

### `EventEmitter`

将内部状态转为 `PROTOCOL.md` 定义的 JSON Lines 事件。

### `SessionStore`

校验、列举、加载并原子保存 Conversation。它还生成供 TUI 重建聊天区使用的 user/assistant 文本投影；工具消息保留在模型历史中，但不会展开为旧工具卡片。

## 4. 状态机

Turn 状态机：

```text
created
  │
  ▼
preparing_context
  │
  ▼
requesting_model ◄──────────────────────────┐
  │                                         │
  ├─ 文本增量 ─> streaming_response         │
  │                    │                    │
  │                    └─ 响应完成 ─────────┤
  │                                         │
  ├─ tool calls ─> validating_tools         │
  │                    │                    │
  │                    ├─> waiting_approval │
  │                    │                    │
  │                    └─> running_tools ───┘
  │
  ├─ 最终文本 ─> finalizing ─> completed
  ├─ 不可恢复错误 ───────────> failed
  └─ 用户取消 ───────────────> cancelled
```

所有非终态都可以响应取消。终态不可再次转移。

## 5. 系统提示词组成

系统提示词由固定模板和运行时信息组成：

1. 身份：本地编程智能体。
2. 目标：完成用户任务，并在必要时使用工具获取事实和验证修改。
3. 工作区：当前根目录及路径规则。
4. 工具使用规则：先读取再修改、修改后验证、不得虚构工具结果。
5. 安全规则：禁止越界和读取凭据；被拒绝后不得绕过批准。
6. 结束规则：完成后给出简洁总结、变更和验证结果。
7. 环境摘要：操作系统、shell、可用基础命令，不包含敏感环境变量。

提示词不包含：

- API Key 或完整环境变量。
- 未经读取的项目内容。
- 隐藏的内部推理要求。
- 与当前 Agent 能力不符的工具说明。

工具参数和 Schema 通过 API 的 tool definitions 传递，不在提示词中重复整份 JSON Schema。

## 6. 内部消息模型

建议的内部结构：

```text
SystemMessage
  role = system
  content

UserMessage
  role = user
  content
  turn_id

AssistantMessage
  role = assistant
  content
  tool_calls[]
  provider_metadata

ToolMessage
  role = tool
  tool_call_id
  tool_name
  content
```

约束：

- 包含 tool calls 的 assistant message 必须在所有对应 tool messages 之前。
- 每个 tool call 必须恰好有一个 tool message，包括拒绝、参数错误和取消。
- tool result 的 `content` 是序列化后的紧凑 JSON。
- TUI 展示事件不直接作为模型消息写入历史。
- 供应商特有字段放入 metadata，不渗透到 Agent 主循环。

## 7. 主循环

概念伪代码：

```python
async def run_turn(user_text):
    turn = create_turn(user_text)
    append_user_message(user_text)
    emit_turn_started(turn)

    while True:
        check_cancelled(turn)
        loop_guard.check_before_step(turn)
        turn.step += 1

        request_messages = context_manager.build_request(messages)
        emit_status("requesting_model", turn.step)

        response = await model_client.stream_chat(
            messages=request_messages,
            tools=tool_registry.schemas,
            cancellation=turn.cancellation,
        )

        assistant_message = normalize_response(response)
        append_assistant_message(assistant_message)

        if assistant_message.tool_calls:
            for call in assistant_message.tool_calls:
                result = await execute_or_request_input(call, turn)
                append_tool_message(call, result)
            continue

        if has_meaningful_text(assistant_message):
            finalize_completed(turn, assistant_message.content)
            return

        handle_empty_response(turn)
```

实际实现必须用 `try/finally` 确保取消时清理模型请求和本地子进程，并确保 turn 最终只发送一个终态事件。

## 8. 模型请求

每次请求至少包含：

- 经过预算处理的消息列表。
- 十一个工具的原生 tool calling 定义。
- 模型名称。
- 流式响应开关。
- 供应商支持时的最大输出限制。

第一版默认让模型自动决定是否调用工具，不使用强制 tool choice。

请求前检查：

- turn 仍活动且未取消。
- 消息角色顺序合法。
- 没有缺失对应结果的历史 tool call。
- 上下文预算在限制内。
- 当前 step 未超过上限。

## 9. 流式响应解析

OpenAI 兼容服务的流式细节可能不同，模型适配层需要将供应商事件规范化为：

- 文本增量。
- tool call 名称增量。
- tool call 参数 JSON 字符串增量。
- 响应完成。
- usage 信息，可选。
- 错误。

### 9.1 文本

- 第一个文本增量到达前发送 `assistant_message_started`。
- 每个规范化文本片段发送 `assistant_delta`。
- Core 同时在内存中拼接完整文本。
- 响应结束后发送 `assistant_message_finished`。
- TUI 中已展示的流式文本不等于已提交历史；只有完整响应规范化成功后才追加 assistant message。

### 9.2 Tool calls

一个响应可以包含多个并行索引的 tool call 增量。解析器按供应商提供的 index 或 ID 分别累积：

```text
index 0: id + name + arguments fragments
index 1: id + name + arguments fragments
```

响应完成后才解析完整 arguments JSON。不能在参数仍流式到达时执行工具。

解析结果要求：

- 工具名非空。
- tool call ID 唯一。
- arguments 是 JSON object。
- 保留模型给出的调用顺序。

JSON 解析失败时，为该 tool call 生成 `invalid_arguments` 结果并返回模型，不猜测缺失括号或擅自修复参数。

### 9.3 文本与工具共存

如果模型同时返回说明文本和 tool calls：

- 文本正常展示并保存在 assistant message 中。
- 该文本不视为最终回答。
- 按顺序执行 tool calls，然后进入下一 step。

## 10. Tool call 执行

每个 tool call 的执行步骤：

1. 发送 `tool_requested`。
2. 查找工具并校验参数。
3. 计算风险等级。
4. 如需批准，发送 `approval_required` 并等待决定。
5. `request_user_input` 发送 `user_input_required` 并等待用户回答，不调用 handler。
6. 被拒绝时生成 `approval_denied` 结果，不调用 handler。
7. 允许执行时发送 `tool_started`。
8. 执行 handler；命令可发送受限的实时输出。
9. 文件修改成功时发送 `file_diff`。
10. 发送 `tool_finished`。
11. 将紧凑结果作为 tool message 加入模型历史。

第一版串行执行同一响应中的多个 tool calls。若前一个工具修改了文件，后一个工具看到的是修改后的工作区。

如果用户在多个调用之间取消，尚未执行的调用也要生成 `cancelled` tool result，确保消息结构完整，然后 turn 进入 `cancelled`，但不再发起模型请求。

## 11. 完成判定

模型没有返回 tool calls，并且存在非空文本时，默认认为当前 turn 完成。

完成前 Core 进行结构性检查：

- 当前没有等待执行的 tool call。
- 当前没有运行中的工具或命令子进程。
- 最近模型响应已完整结束。
- turn 没有取消或失败。

Core 不用关键词判断“完成”，也不要求模型调用额外的 `finish` 工具。这样保持协议简单，并兼容更多 OpenAI 兼容模型。

`turn_finished.finalText` 使用最后一条没有 tool calls 的 assistant 文本。

## 12. 终止条件

### 12.1 正常完成

模型返回有意义的文本且没有 tool calls。

### 12.2 最大步数

默认 `maxSteps=30`。准备发起下一次模型请求前检查；达到上限后返回 `max_steps_exceeded`。

最大步数用于限制失控循环，不代表任务的理想长度。TUI 可显示当前 step，但第一版不允许运行中提高上限。

### 12.3 重复调用

为每次工具调用生成稳定指纹：

```text
fingerprint = tool_name + canonical_json(arguments)
```

如果连续三次出现完全相同的指纹，并且中间没有文件变化、命令结果变化或新的用户输入，判定为无进展循环，终止为 `repeated_tool_call`。

以下情况不算完全重复：

- 读取同一文件但行范围不同。
- 同一测试命令在文件修改后再次执行。
- 前一次调用失败，而相关工作区状态已经改变。

### 12.4 连续模型错误

模型请求经过内部重试仍失败时，连续错误计数加一。默认连续两次 step 无法得到有效响应后终止。

### 12.5 连续空响应

模型既没有文本也没有 tool calls 时视为空响应。允许追加一条简短内部纠正消息并重试一次；再次为空则以 `empty_model_response` 失败。

### 12.6 用户取消

任何非终态收到取消信号后：

- 停止继续读取模型流。
- 取消网络请求。
- 终止活动命令进程组。
- 结束批准等待。
- 不再发起新的模型请求。
- 发送 `turn_cancelled`。

### 12.7 不可恢复内部错误

状态不一致、历史消息结构损坏或工具注册表失效等错误直接使 turn 失败；如果会话也不再可信，Core 随后退出。

## 13. 模型错误与重试

模型客户端将错误分为：

| 类型 | 示例 | 策略 |
| --- | --- | --- |
| `authentication` | 401、无效 Key | 不重试，turn 失败 |
| `permission` | 403、模型不可用 | 不重试，turn 失败 |
| `rate_limit` | 429 | 有限重试并退避 |
| `server_error` | 5xx | 有限重试并退避 |
| `network` | 连接重置、临时 DNS 错误 | 有限重试并退避 |
| `timeout` | 请求超时 | 有限重试 |
| `invalid_response` | 无法解析响应 | 最多重试一次 |
| `context_length` | 超过模型上下文 | 先压缩，再重试一次 |
| `cancelled` | 用户取消 | 不重试 |

建议默认最多三次网络尝试，退避约为 0.5 秒、1 秒、2 秒，并加入少量随机抖动。重试不能越过用户取消。

流式响应已经向 TUI 输出部分文本后发生网络错误时：

- 将当前 assistant draft 标记为中断，不写入正式历史。
- TUI 保留可见文本并标记“响应中断”。
- 可以重新请求当前 step，但新响应使用新的 assistant message ID。
- 不把两次响应片段拼成一条正式模型消息。

## 14. 工具错误处理

工具错误通常不终止 turn，而是作为 tool result 返回模型。模型可以根据稳定错误码恢复：

- `file_not_found`：重新列目录或搜索。
- `conflict`：重新读取文件后生成新补丁。
- `command_failed`：分析 stdout/stderr 后修改。
- `approval_denied`：选择低风险替代方案或向用户说明。
- `command_timeout`：缩小测试范围或调整一次允许范围内的超时。

以下情况可以直接终止 turn：

- 用户取消。
- 工具执行破坏了 Core 的状态不变量。
- 相同不可恢复错误连续出现并触发无进展保护。

## 15. 上下文管理

### 15.1 基本策略

第一版采用按需读取和受限历史，不使用向量数据库。上下文由以下部分组成：

```text
固定系统提示词
+ 会话中必要的用户与 assistant 消息
+ tool call 与对应的紧凑 tool result
+ 当前 turn 的完整近期消息
```

### 15.2 预算

Core 配置模型上下文上限和预留输出空间：

```text
输入预算 = 模型上下文上限 - 最大模型输出 - 安全余量
```

如果供应商没有可靠 tokenizer，第一版使用保守字符估算，并将估算逻辑集中在 `ContextManager`，方便之后替换。

### 15.3 优先级

从最不可删除到最可压缩：

1. 系统提示词和工具定义。
2. 当前用户原始任务。
3. 尚未闭合的 assistant tool calls 与 tool results。
4. 当前 turn 最近若干 step。
5. 当前 turn 较早的大型工具结果。
6. 已完成的历史 turn。

### 15.4 第一版压缩方式

- 工具自身先限制文件、搜索、命令和 diff 输出。
- 较早的超长 tool result 替换为确定性摘要，保留工具名、参数摘要、成功状态、关键路径、退出码和截断说明。
- 已完成旧 turn 只保留用户请求和最终回答的简短内容；不进行额外模型摘要调用。
- 当前 turn 最近消息保持原样，避免摘要遗漏正在解决的问题。

第一版不让模型总结自己的隐藏推理，只压缩可观察的工具事实和已完成对话。

### 15.5 结构完整性

上下文裁剪必须以消息组为单位：

- 不能保留 assistant tool call 而删除对应 tool result。
- 不能单独保留 tool message。
- 同一 assistant message 中的多个 tool calls 及其结果作为一个不可拆组。

## 16. 工作区状态与变更跟踪

Turn 维护 `changedFiles` 集合：

- `write_file` 和 `apply_patch` 成功后加入文件路径。
- 删除成功时仍保留路径并标记删除。
- 命令可能修改文件，但 Core 第一版不尝试自动推断所有副作用。
- 若工作区是 Git 仓库，可以用只读 `git diff` 辅助展示，但不能把 Git 当成文件正确性的唯一来源。

Agent 最终总结中的变更列表来源于实际工具结果，而不是只信任模型陈述。

## 17. 最终结果

正常完成时，Core 发送：

- 最后一条 assistant 最终文本。
- step 和工具调用数量。
- 实际记录的变更文件列表。
- turn 总耗时。

系统提示词要求最终文本尽量包含：

- 做了什么。
- 修改了哪些关键文件。
- 运行了什么验证及结果。
- 尚未解决的限制或风险。

Core 不强制解析最终文本的自然语言结构，避免因为格式差异误判任务失败。

## 18. 可观察性

Core 记录以下非敏感信息：

- session、turn、step 和 tool call ID。
- 状态转换。
- 模型请求耗时、重试次数和可用 usage。
- 工具名称、风险、耗时和结果状态。
- 终止原因。

默认日志不记录：

- API Key 和认证头。
- 完整环境变量。
- 完整文件内容和命令输出。
- 用户未要求持久化的完整会话。

## 19. 状态不变量

实现和测试必须维护：

1. 一个 session 最多一个活动 turn。
2. 一个 turn 只进入一个终态。
3. step 单调递增且不超过配置上限。
4. 每个 assistant tool call 恰好对应一个 tool message。
5. 未批准的高风险工具不会执行。
6. 取消后不再产生新的模型请求或工具执行。
7. Core stdout 只包含合法协议 JSON Lines。
8. 模型历史始终满足目标 OpenAI 兼容接口的角色顺序。
9. TUI 已展示事件和 Core 内部历史可以通过 ID 关联，但两者不混用。

## 20. 测试要求

### 20.1 单元测试

- 状态机合法与非法转移。
- 文本增量拼接。
- 多 tool call 参数增量交错到达。
- 非法 tool arguments。
- 最大步数和重复调用指纹。
- 网络错误分类与退避次数。
- 上下文预算、裁剪优先级和 tool call 组完整性。
- turn 终态只能发送一次。

### 20.2 模拟模型集成测试

使用预设响应序列，不访问真实 API：

- 直接返回最终文本。
- 读取文件后返回最终文本。
- 多次读取、修改、运行测试后完成。
- 测试失败后修改并再次测试。
- 同一响应返回多个工具。
- 文本和工具同时返回。
- 参数 JSON 分片与响应中断。
- 重复工具调用触发保护。
- 用户在模型请求、批准等待和命令执行阶段取消。

### 20.3 真实 API 冒烟测试

真实 API 测试默认不进入普通测试套件，只在提供环境变量时运行。至少验证：

- 模型能识别并调用一个读取工具。
- tool result 能正确回传并得到最终回答。
- 流式文本和原生 tool call 能被当前模型适配器解析。
