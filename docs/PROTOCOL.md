# TUI 与 Agent Core 通信协议

## 1. 文档目的

本文定义 TypeScript TUI 与 Python Agent Core 之间的进程通信协议。协议需要满足以下目标：

- 支持模型文本流式输出和长时间运行的工具调用。
- 支持用户批准、拒绝和取消操作。
- 保证事件能够关联到正确的会话、任务和工具调用。
- 协议可记录、回放，并可脱离真实 TUI 或模型进行测试。
- Python 日志或依赖库输出不会破坏协议数据。

本文只定义本地父子进程协议，不定义 OpenAI API 的网络格式。

## 2. 传输方式

TUI 是父进程，负责启动并管理 Agent Core 子进程：

```text
TUI stdin / keyboard
        │
        ▼
TypeScript TUI ──stdin──> Python Agent Core
TypeScript TUI <─stdout── Python Agent Core
                         └─stderr──> 日志文件或 TUI 调试输出
```

传输规则：

1. 使用 UTF-8 编码。
2. 使用 JSON Lines，每行必须是一个完整 JSON 对象。
3. 一条消息内部不得包含未转义的换行符。
4. TUI 写入 Core 的标准输入；Core 写入 TUI 的标准输出。
5. Core 的日志、traceback 和第三方库日志只能写入标准错误。
6. 双方都应逐行读取，不能假设一次读取只包含一条消息。
7. 写出一条消息后应及时刷新缓冲区。

## 3. 协议版本

初始协议版本为 `1`。所有消息必须包含 `protocolVersion`：

```json
{
  "protocolVersion": 1,
  "type": "initialize",
  "messageId": "msg_01J...",
  "timestamp": "2026-08-27T08:00:00.000Z",
  "payload": {}
}
```

版本处理规则：

- 主版本不匹配时，Core 返回 `protocol_version_mismatch` 并退出。
- 同一版本内允许增加可选字段。
- 接收方必须忽略自己不认识的可选字段。
- 不允许静默改变已有字段的含义或类型。

## 4. 消息信封

所有消息使用统一信封：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `protocolVersion` | integer | 是 | 当前固定为 `1` |
| `type` | string | 是 | 事件类型 |
| `messageId` | string | 是 | 当前消息的唯一 ID |
| `timestamp` | string | 是 | UTC ISO 8601 时间 |
| `sessionId` | string | 条件 | 初始化完成后的消息必填 |
| `turnId` | string | 条件 | 与用户任务相关的消息必填 |
| `toolCallId` | string | 条件 | 与工具调用相关的消息必填 |
| `payload` | object | 是 | 事件数据，可为空对象 |

ID 约定：

- `sessionId`：一次 Core 进程生命周期。
- `conversationId`：当前工作区内一份可跨进程恢复的对话记录，通过相关消息的 payload 传递。
- `turnId`：一次用户提交到最终完成、失败或取消。
- `toolCallId`：采用模型返回的 tool call ID；如果上游没有提供，由 Core 生成。
- `messageId`：每条协议消息都生成新的唯一 ID。

ID 只要求唯一和不可混淆，不将时间、模型或用户信息编码到 ID 中。

## 5. 连接生命周期

正常生命周期如下：

```text
TUI                           Core
 │──── initialize ───────────>│
 │<─── initialized ───────────│
 │──── submit_task ──────────>│
 │<─── turn_started ──────────│
 │<─── assistant_delta ───────│ 0..n
 │<─── tool_requested ────────│ 0..n
 │<─── approval_required / ───│ 可选
 │<─── user_input_required    │
 │──── response ─────────────>│
 │<─── tool_started ──────────│ 普通本地工具
 │<─── tool_finished ─────────│
 │<─── turn_finished ─────────│
 │──── submit_task ──────────>│ 可继续下一轮
 │──── shutdown ─────────────>│
 │<─── shutdown_complete ─────│
```

约束：

- `initialize` 必须是 TUI 发送的第一条消息。
- 收到 `initialized` 前不得发送 `submit_task`。
- 同一会话最多存在一个活动 turn。
- turn 结束后保留会话历史，可继续提交新任务。
- 未正常初始化时发生的错误不包含 `sessionId`。

## 6. TUI 发给 Core 的消息

### 6.1 `initialize`

启动并校验工作区与模型配置。

```json
{
  "protocolVersion": 1,
  "type": "initialize",
  "messageId": "msg_init_1",
  "timestamp": "2026-08-27T08:00:00.000Z",
  "payload": {
    "workspaceRoot": "/absolute/path/to/project",
    "model": "model-name",
    "baseUrl": "https://example.com/v1",
    "options": {
      "maxSteps": 30,
      "commandTimeoutMs": 30000
    }
  }
}
```

规则：

- `workspaceRoot` 必须是绝对路径。
- API Key 不通过协议发送，Core 自己从环境变量读取。
- TUI 可以传递非敏感选项；Core 负责最终校验和应用默认值。

### 6.2 `submit_task`

提交新的用户任务。

```json
{
  "protocolVersion": 1,
  "type": "submit_task",
  "messageId": "msg_task_1",
  "timestamp": "2026-08-27T08:01:00.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "text": "修复计算总价时的精度问题并运行测试"
  }
}
```

规则：

- `text` 去除首尾空白后不得为空。
- `turnId` 由 TUI 创建，Core 检查其在当前会话中没有被使用。
- 当前已有活动 turn 时，Core 返回 `turn_already_running`。

### 6.3 `approval_response`

响应待确认的高风险工具调用。

```json
{
  "protocolVersion": 1,
  "type": "approval_response",
  "messageId": "msg_approval_1",
  "timestamp": "2026-08-27T08:01:10.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "toolCallId": "call_7",
  "payload": {
    "decision": "allow_once"
  }
}
```

`decision` 第一版只允许：

- `allow_once`：仅允许当前调用。
- `deny`：拒绝当前调用。

第一版不提供“本会话始终允许”，防止权限范围被误解或扩大。

### 6.4 `cancel_turn`

请求取消当前任务：

```json
{
  "protocolVersion": 1,
  "type": "cancel_turn",
  "messageId": "msg_cancel_1",
  "timestamp": "2026-08-27T08:01:20.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "reason": "user_requested"
  }
}
```

Core 收到后应中断模型请求、正在等待的批准或本地子进程，并最终发送 `turn_cancelled`。取消是异步请求，TUI 不能在发出后立即假设任务已经结束。

### 6.4.1 `user_input_response`

回答或取消 Agent 的提问。消息必须携带当前 `sessionId`、`turnId` 和对应 `toolCallId`。回答使用 `{"answer":"..."}`；取消问题使用 `{"cancelled":true}`。回答去除首尾空白后不能为空，最长 10,000 字符。

### 6.5 `list_sessions`

请求当前工作区的对话列表。payload 为空，Core 返回 `sessions_listed`。仅允许在初始化完成后调用。

### 6.6 `switch_session`

```json
{
  "protocolVersion": 1,
  "type": "switch_session",
  "messageId": "msg_switch_1",
  "timestamp": "2026-08-27T08:05:00.000Z",
  "sessionId": "sess_1",
  "payload": {"conversationId": "conv_0123456789abcdef0123456789abcdef"}
}
```

只能切换 `list_sessions` 返回的当前工作区对话。存在活动 turn 时返回 `turn_already_running`。

### 6.7 `create_session`

请求在当前工作区新建空白 Conversation 并立即切换。payload 为空；存在活动 turn 时拒绝。

### 6.8 `shutdown`

请求正常关闭 Core：

```json
{
  "protocolVersion": 1,
  "type": "shutdown",
  "messageId": "msg_shutdown_1",
  "timestamp": "2026-08-27T08:10:00.000Z",
  "sessionId": "sess_1",
  "payload": {}
}
```

如果有活动 turn，Core 先取消 turn，再清理子进程并发送 `shutdown_complete`。

## 7. Core 发给 TUI 的消息

### 7.1 `initialized`

```json
{
  "protocolVersion": 1,
  "type": "initialized",
  "messageId": "msg_initialized_1",
  "timestamp": "2026-08-27T08:00:00.100Z",
  "sessionId": "sess_1",
  "payload": {
    "workspaceRoot": "/absolute/path/to/project",
    "model": "model-name",
    "conversationId": "conv_0123456789abcdef0123456789abcdef",
    "conversationTitle": "修复价格计算精度问题",
    "transcript": [
      {"role": "user", "content": "修复价格计算精度问题"},
      {"role": "assistant", "content": "已完成。"}
    ],
    "capabilities": {
      "streaming": true,
      "toolCalling": true,
      "approvals": true
    }
  }
}
```

只有工作区、模型配置、工具注册表和协议均通过校验后才能发送。

### 7.1.1 `sessions_listed`

返回按 `updatedAt` 倒序排列的会话摘要：

```json
{
  "type": "sessions_listed",
  "payload": {
    "activeConversationId": "conv_0123456789abcdef0123456789abcdef",
    "sessions": [{
      "id": "conv_0123456789abcdef0123456789abcdef",
      "title": "修复价格计算精度问题",
      "createdAt": "2026-08-27T08:00:00.000000Z",
      "updatedAt": "2026-08-27T08:04:00.000000Z",
      "messageCount": 1
    }]
  }
}
```

### 7.1.2 `conversation_switched` 与 `conversation_created`

切换或新建成功后返回 `conversationId`、`conversationTitle` 和 `transcript`。`transcript` 只包含可显示的 user/assistant 文本；完整 tool messages 仍由 Core 从持久化历史恢复给模型。TUI 收到事件后替换当前聊天区并关闭选择面板。

### 7.1.3 `conversation_updated`

首次用户消息使默认标题发生变化时发送，payload 包含 `conversationId` 和新的 `conversationTitle`，TUI 据此刷新顶部标题。

### 7.2 `turn_started`

```json
{
  "protocolVersion": 1,
  "type": "turn_started",
  "messageId": "msg_turn_started_1",
  "timestamp": "2026-08-27T08:01:00.010Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "text": "修复计算总价时的精度问题并运行测试"
  }
}
```

### 7.3 `agent_status`

报告当前 Agent 阶段，用于状态栏，不作为聊天内容展示。

```json
{
  "protocolVersion": 1,
  "type": "agent_status",
  "messageId": "msg_status_1",
  "timestamp": "2026-08-27T08:01:00.100Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "status": "requesting_model",
    "step": 1
  }
}
```

`status` 允许：`requesting_model`、`streaming_response`、`running_tool`、`waiting_for_approval`、`finalizing`。

### 7.4 `assistant_message_started`

一轮模型输出可能包含文本和工具调用。此事件为流式文本创建容器：

```json
{
  "protocolVersion": 1,
  "type": "assistant_message_started",
  "messageId": "msg_asst_start_1",
  "timestamp": "2026-08-27T08:01:01.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "assistantMessageId": "asst_1",
    "step": 1
  }
}
```

### 7.5 `assistant_delta`

```json
{
  "protocolVersion": 1,
  "type": "assistant_delta",
  "messageId": "msg_delta_1",
  "timestamp": "2026-08-27T08:01:01.050Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "assistantMessageId": "asst_1",
    "text": "我先检查相关实现。"
  }
}
```

TUI 按收到顺序追加相同 `assistantMessageId` 的文本。

### 7.6 `assistant_message_finished`

```json
{
  "protocolVersion": 1,
  "type": "assistant_message_finished",
  "messageId": "msg_asst_end_1",
  "timestamp": "2026-08-27T08:01:01.200Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "assistantMessageId": "asst_1",
    "text": "我先检查相关实现。"
  }
}
```

完整 `text` 用于校验流式拼接结果；即使没有文本，也允许发送空字符串。

### 7.7 `tool_requested`

```json
{
  "protocolVersion": 1,
  "type": "tool_requested",
  "messageId": "msg_tool_req_1",
  "timestamp": "2026-08-27T08:01:01.210Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "toolCallId": "call_7",
  "payload": {
    "step": 1,
    "name": "read_file",
    "arguments": {
      "path": "src/pricing.py",
      "startLine": 1,
      "endLine": 200
    },
    "risk": "low"
  }
}
```

只有工具名称存在且参数已成功解析后才发送。参数校验失败仍发送该事件，随后发送失败的 `tool_finished`。

### 7.8 `approval_required`

```json
{
  "protocolVersion": 1,
  "type": "approval_required",
  "messageId": "msg_approval_req_1",
  "timestamp": "2026-08-27T08:01:02.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "toolCallId": "call_8",
  "payload": {
    "name": "run_command",
    "summary": "安装项目依赖",
    "reason": "该命令会修改依赖目录和锁文件",
    "arguments": {
      "command": "npm install"
    }
  }
}
```

Core 在收到对应 `approval_response` 前不得执行工具。等待期间不启动新的模型请求。

### 7.8.1 `user_input_required`

```json
{
  "protocolVersion": 1,
  "type": "user_input_required",
  "messageId": "msg_question_1",
  "timestamp": "2026-08-27T08:01:02.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "toolCallId": "call_9",
  "payload": {"question": "输出应使用 JSON 还是 YAML？"}
}
```

TUI 显示问题输入面板，并以 `user_input_response` 回答。等待期间不得提交新任务；用户仍可取消整个 turn。

### 7.9 `tool_started`

```json
{
  "protocolVersion": 1,
  "type": "tool_started",
  "messageId": "msg_tool_start_1",
  "timestamp": "2026-08-27T08:01:02.100Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "toolCallId": "call_7",
  "payload": {
    "name": "read_file"
  }
}
```

### 7.10 `tool_output_delta`

仅供长时间命令输出使用：

```json
{
  "protocolVersion": 1,
  "type": "tool_output_delta",
  "messageId": "msg_tool_delta_1",
  "timestamp": "2026-08-27T08:01:02.200Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "toolCallId": "call_9",
  "payload": {
    "stream": "stdout",
    "text": "tests/test_price.py ."
  }
}
```

该事件只用于界面实时反馈；写入模型历史的仍是最终、经过限制的工具结果。

### 7.11 `file_diff`

```json
{
  "protocolVersion": 1,
  "type": "file_diff",
  "messageId": "msg_diff_1",
  "timestamp": "2026-08-27T08:01:03.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "toolCallId": "call_10",
  "payload": {
    "path": "src/pricing.py",
    "diff": "--- a/src/pricing.py\n+++ b/src/pricing.py\n@@ ...",
    "addedLines": 3,
    "removedLines": 1,
    "truncated": false
  }
}
```

### 7.12 `tool_finished`

成功示例：

```json
{
  "protocolVersion": 1,
  "type": "tool_finished",
  "messageId": "msg_tool_end_1",
  "timestamp": "2026-08-27T08:01:03.100Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "toolCallId": "call_7",
  "payload": {
    "name": "read_file",
    "ok": true,
    "durationMs": 8,
    "summary": "Read lines 1-120 from src/pricing.py",
    "result": {
      "content": "...",
      "truncated": false
    },
    "error": null
  }
}
```

失败示例：

```json
{
  "protocolVersion": 1,
  "type": "tool_finished",
  "messageId": "msg_tool_end_2",
  "timestamp": "2026-08-27T08:01:03.200Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "toolCallId": "call_8",
  "payload": {
    "name": "read_file",
    "ok": false,
    "durationMs": 2,
    "summary": "File does not exist",
    "result": null,
    "error": {
      "code": "file_not_found",
      "message": "src/missing.py does not exist",
      "retryable": false
    }
  }
}
```

### 7.13 `context_updated`

当 Core 截断或压缩上下文时发送摘要事件：

```json
{
  "protocolVersion": 1,
  "type": "context_updated",
  "messageId": "msg_context_1",
  "timestamp": "2026-08-27T08:02:00.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "action": "tool_output_truncated",
    "summary": "Command output was limited to 20000 characters"
  }
}
```

第一版只做状态提示，不在 TUI 中展示内部推理内容。

### 7.14 `turn_finished`

```json
{
  "protocolVersion": 1,
  "type": "turn_finished",
  "messageId": "msg_turn_end_1",
  "timestamp": "2026-08-27T08:03:00.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "status": "completed",
    "finalText": "已修复价格计算并通过 18 项测试。",
    "steps": 6,
    "toolCalls": 9,
    "changedFiles": ["src/pricing.py", "tests/test_pricing.py"],
    "durationMs": 120000
  }
}
```

`status` 第一版固定为 `completed`。失败和取消使用独立事件。

### 7.15 `turn_failed`

```json
{
  "protocolVersion": 1,
  "type": "turn_failed",
  "messageId": "msg_turn_fail_1",
  "timestamp": "2026-08-27T08:03:00.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "error": {
      "code": "max_steps_exceeded",
      "message": "Agent reached the maximum of 30 steps",
      "retryable": false
    },
    "steps": 30,
    "toolCalls": 41
  }
}
```

### 7.16 `turn_cancelled`

```json
{
  "protocolVersion": 1,
  "type": "turn_cancelled",
  "messageId": "msg_turn_cancelled_1",
  "timestamp": "2026-08-27T08:03:00.000Z",
  "sessionId": "sess_1",
  "turnId": "turn_1",
  "payload": {
    "reason": "user_requested"
  }
}
```

### 7.17 `error`

用于不一定导致当前 turn 结束的协议或运行错误：

```json
{
  "protocolVersion": 1,
  "type": "error",
  "messageId": "msg_error_1",
  "timestamp": "2026-08-27T08:03:00.000Z",
  "sessionId": "sess_1",
  "payload": {
    "code": "invalid_message",
    "message": "payload.text is required",
    "fatal": false,
    "relatedMessageId": "msg_task_2"
  }
}
```

### 7.18 `shutdown_complete`

Core 已停止接收任务、清理子进程和刷新日志后发送，随后以退出码 `0` 退出。

## 8. 错误代码

协议级错误至少包括：

| 错误码 | 含义 | 是否致命 |
| --- | --- | --- |
| `invalid_json` | 一行不是合法 JSON | 否，连续出现可致命 |
| `invalid_message` | 信封或 payload 不符合要求 | 否 |
| `protocol_version_mismatch` | 协议版本不兼容 | 是 |
| `not_initialized` | 初始化前发送了业务消息 | 否 |
| `already_initialized` | 重复初始化 | 否 |
| `session_mismatch` | session ID 不匹配 | 否 |
| `turn_already_running` | 已有活动任务 | 否 |
| `unknown_turn` | turn ID 不存在 | 否 |
| `unknown_tool_call` | tool call ID 不存在 | 否 |
| `approval_not_pending` | 当前调用不等待确认 | 否 |
| `user_input_not_pending` | 当前调用不等待用户回答 | 否 |
| `core_internal_error` | Core 无法继续运行 | 是 |

## 9. 顺序与一致性规则

1. 单个进程连接中的事件顺序就是权威顺序，不额外引入 sequence number。
2. 一个 `assistant_message_started` 必须对应一个 `assistant_message_finished`，除非 turn 被取消或失败。
3. 一个 `tool_requested` 最终必须对应一个 `tool_finished`，包括参数错误、拒绝和取消。
4. `tool_started` 只能出现在 `tool_requested` 之后。
5. 需要批准时，`approval_required` 位于 `tool_requested` 与 `tool_started` 之间。
6. 每个 turn 最终必须且只能出现 `turn_finished`、`turn_failed` 或 `turn_cancelled` 之一。
7. 终态事件发送后，不得再发送属于该 turn 的 delta、工具或状态事件。
8. TUI 收到未知事件时记录警告并忽略，不直接崩溃。

## 10. 背压与输出限制

- Core 对每个工具结果设定发送上限，避免 stdout 被大输出占满。
- `tool_output_delta` 可以合并发送，不保证每个原始字节都实时到达。
- TUI 应持续消费 Core stdout，不能因为渲染速度阻塞读取。
- TUI 内存中只保留受控长度的命令实时输出；完整结果仍受 Core 限制。
- 超过上限时在事件中设置 `truncated: true`，不能静默丢弃。

## 11. 进程退出语义

| Core 退出码 | 含义 |
| --- | --- |
| `0` | 正常关闭 |
| `2` | 配置或初始化失败 |
| `3` | 协议版本不兼容 |
| `4` | 无法恢复的 Core 内部错误 |

如果 Core 在没有发送终态事件时退出，TUI 将活动 turn 标记为失败，并显示标准错误的最后一段脱敏摘要。

## 12. 安全要求

- 协议中不得发送 API Key。
- 日志记录协议事件时必须对潜在敏感字段做脱敏。
- TUI 不信任 Core 返回的 ANSI 控制序列；工具输出默认作为普通文本渲染。
- Core 不信任 TUI 提供的路径，仍需自行验证工作区边界。
- `approval_response` 必须同时匹配 session、turn 和 tool call，不能按“最近一次请求”模糊关联。

## 13. 测试要求

至少覆盖以下协议测试：

- 正常初始化、执行任务和关闭。
- 文本被拆成任意大小的读取块时仍能逐行解析。
- 一次读取包含多条 JSON Lines。
- 非法 JSON 后仍可继续处理下一条消息。
- 初始化前提交任务。
- 活动 turn 中重复提交任务。
- 批准、拒绝和取消待确认工具。
- 工具运行期间取消任务。
- Core 异常退出后 TUI 正确进入失败状态。
- 未知可选字段和未知事件不会导致崩溃。
- 每个 turn 恰好产生一个终态事件。
