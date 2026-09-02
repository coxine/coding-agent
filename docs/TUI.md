# TUI 交互设计

## 1. 文档目的

本文定义 TypeScript 终端用户界面的布局、组件、状态、快捷键和异常展示。TUI 的产品形态参考 Codex CLI 与 OpenCode，但第一版只实现支撑基本 Agent 工作流所必需的界面。

TUI 不包含模型决策、工具安全判断或文件执行逻辑。它是 Agent Core 协议事件的可视化客户端，并负责收集用户输入、批准决定和取消请求。

## 2. 技术边界

### 2.1 技术选择

第一版计划采用：

- TypeScript
- React
- Ink 作为终端组件渲染层
- Node.js 子进程 API 启动 Python Agent Core
- npm 管理依赖、脚本和锁文件

Ink 是普通 TUI 渲染库，不是 Agent 框架；Agent 的重要逻辑仍全部位于本项目的 Python Core。

### 2.2 TUI 负责

- 渲染会话和运行状态。
- 获取多行用户输入。
- 启动、监控和关闭 Core 子进程。
- 解析 Core stdout 的 JSON Lines。
- 将用户操作编码为协议消息。
- 显示工具参数、结果摘要、命令输出和文件 diff。
- 显示批准对话框。
- 处理键盘取消、退出和折叠操作。

### 2.3 TUI 不负责

- 构造模型消息。
- 调用 OpenAI API。
- 校验模型工具参数。
- 判断工具风险。
- 执行文件或命令操作。
- 判断 Agent 是否完成。
- 保存 API Key。

## 3. 基本界面布局

宽度充足时：

```text
┌─ coding-agent ──────────────────────────────────────────────┐
│ model-name  •  ~/project  •  step 4  •  Running tool       │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ You                                                          │
│ 修复价格计算精度问题，并运行相关测试。                       │
│                                                              │
│ Agent                                                        │
│ 我先检查价格计算和现有测试。                                 │
│                                                              │
│ ▾ read_file  src/pricing.py                       ✓ 8 ms     │
│   Read lines 1-120 from src/pricing.py                       │
│                                                              │
│ ▸ search_text  "calculate_total"                  ✓ 12 ms   │
│                                                              │
│ ▾ apply_patch                                     ✓ 20 ms   │
│   src/pricing.py  +4 -2                                      │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ > 输入任务，Enter 发送，Ctrl+Enter 换行                       │
├──────────────────────────────────────────────────────────────┤
│ esc cancel  •  ctrl+c exit  •  ctrl+o details                │
└──────────────────────────────────────────────────────────────┘
```

布局包含：

1. Header：产品名。
2. Context Bar：模型、工作区、step 和状态。
3. Transcript：消息和工具记录。
4. Composer：用户输入区。
5. Footer：当前可用快捷键和临时提示。

## 4. 响应式布局

TUI 必须适配终端大小变化：

- 宽度小于 80 列时，Context Bar 分两行显示。
- 工具参数和长路径使用省略号，但展开详情可查看完整内容。
- 宽度不足时不显示装饰性边框，优先保留正文。
- 高度不足时 Transcript 滚动，Header、Composer 和 Footer 保持可见。
- 终端小于最低可用尺寸时显示简短提示，不尝试渲染错位布局。

建议最低尺寸：60 列 × 15 行。窗口恢复后自动恢复正常界面。

## 5. 页面模式

第一版只有一个主会话页面，但有以下互斥交互模式：

### 5.1 `composing`

默认模式。用户编辑任务，Agent 空闲时可提交。

### 5.2 `running`

Agent 正在请求模型、输出文本或执行普通工具。输入框保留但禁止提交第二个任务；界面提示可按 Esc 取消。

### 5.3 `approval`

高风险工具等待决定。批准面板获得焦点，普通输入区暂时不可编辑。

### 5.4 `details`

查看选中工具的完整参数、结果、命令输出或 diff。关闭后回到原模式。

### 5.5 `fatal_error`

Core 无法启动或已经异常退出。显示错误摘要，并允许退出或重新启动 Core；第一版不在此状态自动重试。

### 5.6 `session_picker`

用户在 Composer 输入 `/session` 后进入。面板列出当前工作区的历史对话，显示标题、更新时间、turn 数和当前项标记。方向键移动，Enter 切换，`N` 新建，Esc 关闭。Agent 运行或等待批准时不能打开或切换。

### 5.7 `command_palette`

用户在空闲 Composer 中键入 `/` 后显示。面板从真实注册的斜杠命令中按已输入前缀过滤；方向键移动，Enter 执行，Esc 清空命令输入并关闭。选择 `/rename` 后继续在 Composer 输入名称。未知的 `/...` 不发送给模型，而是显示本地错误通知。

### 5.8 `question`

Agent 需要补充信息时显示独立问题面板并接管输入焦点。Enter 提交非空回答，Ctrl+Enter 插入换行，Esc 取消该问题，Ctrl+C 取消整个 turn。

### 5.9 `status`

用户在空闲 Composer 中执行 `/status` 后显示只读状态面板。面板展示模型、目录、Conversation ID、Core Session ID、消息数、API 返回的最近一次输入/输出 token、Session 累计 token 和附加 metadata；Enter 或 Esc 关闭。Token 不在本地估算，缺失 usage 时明确提示供应商未返回。

## 6. 组件结构

```text
App
├── Header
├── ContextBar
├── Transcript
│   ├── UserMessage
│   ├── AssistantMessage
│   ├── ToolCallCard
│   │   ├── ToolHeader
│   │   ├── ToolSummary
│   │   ├── CommandOutput
│   │   └── DiffView
│   └── SystemNotice
├── Composer
├── ApprovalDialog
├── SessionPicker
├── CommandPalette
├── DetailsPanel
└── Footer
```

### 6.1 `Header`

- 显示项目名 `coding-agent`。
- 第一版不加入 logo 动画或复杂欢迎页。

### 6.2 `ContextBar`

显示：

- 模型名称。
- 工作区短路径。
- 当前 step。
- 当前状态。

不显示 API Key、Base URL 中的凭据参数或完整环境配置。

### 6.3 `Transcript`

- 按协议事件顺序呈现会话。
- 默认自动跟随最新内容。
- 用户主动向上滚动后暂停自动跟随，并提示存在新内容。
- turn 完成后仍保留当前 session 的历史。
- 初始化或切换对话时，用 Core 提供的 transcript 重建用户与 Agent 文本。
- 历史工具消息不重新生成工具卡片；它们只保留在 Core 的模型上下文中。

### 6.4 `Composer`

- 支持 Unicode 和多行文本。
- 空白输入不能提交。
- Agent 活动时显示只读提示，不接受第二个任务。
- 提交后清空输入，并在 Transcript 中立即显示用户消息。
- 发送失败时恢复原输入，避免任务内容丢失。

### 6.5 `ToolCallCard`

工具调用使用统一卡片，不为每个工具设计完全不同的页面。

折叠状态显示：

- 状态图标。
- 工具名。
- 一个关键参数摘要。
- 执行耗时。
- 成功或错误摘要。

展开状态显示：

- 完整、格式化后的参数。
- 风险等级和批准状态。
- 受限的结果内容。
- 命令退出码、超时和截断标记。
- 文件变更统计和 diff。

### 6.6 `ApprovalDialog`

必须显示：

- 工具名称。
- 操作摘要。
- 为什么需要批准。
- 完整关键参数，例如命令、路径和工作目录。
- “允许一次”和“拒绝”两个选项。

批准面板不得只显示模糊描述，例如“允许危险操作”。用户需要看到实际将执行的内容。

### 6.7 `DetailsPanel`

用于查看完整工具详情，不离开主会话。内容只读，可滚动。第一版不提供在 TUI 中直接编辑 diff 或命令参数。

## 7. 消息展示

### 7.1 用户消息

- 标记为 `You`。
- 保留用户换行。
- 不进行 Markdown 代码执行或终端控制序列解释。

### 7.2 Agent 消息

- 标记为 `Agent`。
- 流式增量追加到当前 assistant message。
- 支持受控的基础 Markdown：标题、粗体、斜体、删除线、行内代码、代码块、表格、列表、引用、分隔线和链接文本。
- Markdown 由 `markdansi` 的 GFM parser 和终端 renderer 处理，不在 TUI 内自行维护 Markdown 语法解析器。
- fenced code block 根据语言标记进行 ANSI 语法高亮；未标语言、未知语言或高亮失败时回退到原始文本。
- GitHub 风格表格支持左对齐、居中和右对齐，并按终端显示宽度对齐中英文字符。
- 渲染宽度跟随 Ink 窗口宽度；表格默认截断过长单元格，代码块默认换行。
- 流式输出中的未闭合 Markdown 标记按普通文本显示，后续增量补全后再应用样式。
- 不解析 HTML，不执行链接、代码或 Markdown 中的任何控制指令。
- 第一版不渲染 HTML、图片、跨行/跨列表格或单元格内的块级 Markdown。
- 响应中断时保留已有文本，并显示“响应中断，正在重试”或失败标记。

### 7.3 系统通知

系统通知用于展示：

- 初始化结果。
- 上下文或输出被截断。
- 任务取消。
- Core 重试和可恢复错误。

系统通知与 Agent 自然语言回复使用不同颜色和标签，避免用户误认为它来自模型。

## 8. 工具状态展示

工具卡片状态：

| 状态 | 图标建议 | 含义 |
| --- | --- | --- |
| `requested` | `○` | 已解析模型请求 |
| `waiting_approval` | `?` | 等待用户决定 |
| `running` | 动态 spinner | 正在执行 |
| `succeeded` | `✓` | 执行成功 |
| `failed` | `✗` | 执行失败 |
| `denied` | `⊘` | 用户拒绝 |
| `cancelled` | `■` | 因 turn 取消而停止 |

颜色只是辅助信息，状态必须同时通过文本或符号表达，避免仅依赖颜色。

关键参数摘要建议：

| 工具 | 折叠摘要 |
| --- | --- |
| `list_directory` | 目录路径 |
| `read_file` | 文件路径和行范围 |
| `search_text` | 查询词和搜索路径 |
| `write_file` | 文件路径和创建/覆盖 |
| `apply_patch` | 修改文件数和行数 |
| `run_command` | 命令和工作目录 |

## 9. Diff 展示

- 使用 unified diff。
- 新增行使用 `+`，删除行使用 `-`，上下文行保留前缀空格。
- 颜色增强新增和删除，但关闭颜色后仍可阅读。
- 默认折叠超长 diff，只显示文件统计和前若干行。
- `file_diff.truncated=true` 时明确提示内容不完整。
- 多文件变更按文件拆分可折叠区块。
- TUI 只展示 Core 返回的实际 diff，不自行重新计算文件差异。

## 10. 命令输出展示

- 命令运行期间显示 spinner、已运行时间和实时输出尾部。
- stdout 与 stderr 可以使用不同标签，但不能依赖颜色作为唯一区分。
- 命令结束后显示退出码、耗时、是否超时和是否被截断。
- 默认折叠成功且输出较长的命令。
- 失败命令自动展开最后一段 stderr/stdout，帮助用户和演示观看者理解恢复过程。
- 过滤 ANSI 控制字符，避免工具输出控制 TUI 光标或伪造界面。

## 11. 快捷键

第一版快捷键：

| 快捷键 | 作用 | 可用模式 |
| --- | --- | --- |
| `Enter` | 提交输入 | `composing` |
| `/session` | 打开对话选择面板 | `composing` |
| `/rename <名称>` | 修改当前对话展示名称 | `composing` |
| `/status` | 打开当前对话状态面板 | `composing` |
| `/` | 唤起命令列表；继续输入可过滤 | `composing` |
| `↑` / `↓` | 选择历史对话 | `session_picker` |
| `Enter` | 切换到选中对话 | `session_picker` |
| `N` | 新建并切换到空白对话 | `session_picker` |
| `Esc` | 关闭面板 | `session_picker` |
| `Ctrl+Enter` | 插入换行 | `composing` |
| `Esc` | 取消当前 turn；关闭详情 | `running` / `details` |
| `Ctrl+C` | 第一次取消活动 turn，空闲时退出 | 全局 |
| `Ctrl+O` | 打开或关闭选中工具详情 | 非 `approval` |
| `↑` / `↓` | 移动选择或滚动 | `approval` / `details` |
| `Tab` | 切换批准选项 | `approval` |
| `Enter` | 确认批准选择 | `approval` |
| `Enter` | 提交回答 | `question` |
| `Ctrl+Enter` | 在回答中插入换行 | `question` |
| `Esc` | 取消当前问题并让 Agent 继续 | `question` |
| `Enter` / `Esc` | 关闭状态面板 | `status` |

为避免误退出：

- turn 活动时第一次 `Ctrl+C` 只发送取消请求。
- 收到 `turn_cancelled` 后再次 `Ctrl+C` 才退出。
- Core 无响应时，Footer 提示再次按 `Ctrl+C` 强制退出；强制退出前 TUI 仍需尝试终止子进程。

具体终端对组合键的编码可能不同；如 `Ctrl+Enter` 无法稳定识别，可先在外部编辑器编写多行任务后粘贴输入。

## 12. TUI 状态模型

建议使用单一 reducer 管理状态，避免协议事件在多个组件中各自修改数据。

概念状态：

```text
AppState
├── connection
│   ├── status
│   ├── sessionId
│   └── fatalError
├── configuration
│   ├── workspaceRoot
│   └── model
├── activeTurnId
├── turns[]
│   ├── userMessage
│   ├── assistantMessages[]
│   ├── toolCalls[]
│   └── terminalStatus
├── composer
│   └── text
├── interactionMode
├── pendingApproval
├── selectedToolCallId
└── viewport
```

Reducer 输入只允许：

- 通过校验的 Core 协议事件。
- 本地 UI action，例如编辑、滚动和选择。

组件不直接修改共享状态。

## 13. 协议事件到 UI 的映射

| 协议事件 | UI 行为 |
| --- | --- |
| `initialized` | 连接状态变为 ready，显示模型和工作区 |
| `status_report` | 显示只读状态面板和上下文占用 |
| `turn_started` | 创建 turn 记录，禁用任务提交 |
| `agent_status` | 更新 Context Bar 状态和 step |
| `assistant_message_started` | 创建空 Agent 消息块 |
| `assistant_delta` | 追加文本并刷新显示 |
| `assistant_message_finished` | 固化消息内容 |
| `tool_requested` | 创建工具卡片 |
| `approval_required` | 切换到 approval 模式 |
| `user_input_required` | 切换到 question 模式并显示问题 |
| `tool_started` | 工具卡片进入 running |
| `tool_output_delta` | 更新命令实时输出尾部 |
| `file_diff` | 绑定 diff 到对应工具卡片 |
| `tool_finished` | 固化工具结果和耗时 |
| `context_updated` | 添加低优先级系统通知 |
| `turn_finished` | turn 完成，恢复 composing |
| `turn_failed` | 展示错误，恢复 composing |
| `turn_cancelled` | 标记取消，恢复 composing |
| `error` | 根据 fatal 决定通知或 fatal_error |
| `shutdown_complete` | 正常结束 TUI 进程 |

收到与当前 session 不匹配的事件时，不更新 UI 状态，而是记录协议错误。

## 14. Core 子进程管理

### 14.1 启动

TUI 使用 npm 脚本确定项目内 Python Core 入口，并通过 `uv run` 启动。启动后立即发送 `initialize`。

启动阶段展示明确状态：

- `Starting Agent Core`
- `Validating workspace`
- `Ready`

在超时时间内未收到 `initialized`，进入 `fatal_error`，并展示可执行的排查提示。

### 14.2 stdout

- 使用增量缓冲区按换行拆分 JSON Lines。
- 单行 JSON 不完整时保留到下一次读取。
- 单次读取包含多行时逐行处理。
- 空行可忽略。
- 非法行作为协议错误处理，不把原始内容直接注入聊天区域。

### 14.3 stderr

- 只保留受限长度的末尾内容供调试。
- 默认不混入 Transcript。
- Core 异常退出时显示脱敏摘要。
- 开发模式可把 stderr 写入未入库日志文件。

### 14.4 退出

正常退出流程：

1. TUI 发送 `shutdown`。
2. 等待 `shutdown_complete`。
3. 等待子进程退出。
4. 恢复终端光标和输入模式。
5. TUI 退出。

如果 Core 未在宽限期内退出，TUI 终止整个子进程组，并仍需恢复终端状态。

## 15. 输入与焦点

- 正常情况下 Composer 持有输入焦点。
- ApprovalDialog 打开后必须独占焦点，防止按键同时写入 Composer。
- DetailsPanel 打开后方向键用于滚动详情。
- turn 运行时可预留用户输入文本，但不允许提交。
- resize、流式输出和 spinner 更新不能打断当前编辑内容。

## 16. 滚动与长会话

- Transcript 使用有限视口，不一次渲染所有历史内容。
- 自动跟随仅在用户位于底部时生效。
- 用户向上滚动后显示“有新输出”的轻量提示。
- 工具实时输出只保留最近窗口，最终结果由 `tool_finished` 固化。
- 第一版可以对非常早的已完成 turn 做 UI 折叠，但不能删除 Core 会话历史。

## 17. 错误体验

### 配置错误

显示缺失的变量名和设置方式，不回显任何已存在的 Secret。

### API 错误

可恢复重试用系统通知显示；认证、权限和模型不存在等错误以 turn 失败卡片显示。

### 工具错误

错误保留在对应 ToolCallCard 内。模型继续运行时，不弹出阻塞式错误框。

### 协议错误

单条可恢复错误显示简短通知并记录调试信息；状态可能不一致或 Core 退出时进入 `fatal_error`。

### 用户取消

发送取消后状态显示 `Cancelling…`，收到 `turn_cancelled` 前不允许提交新任务。

## 18. 颜色与可访问性

- 自动检测终端颜色能力。
- 支持 `NO_COLOR` 环境变量。
- 颜色不是状态的唯一载体。
- 保持正文与背景有足够对比度。
- 用户输入、Agent 文本、工具输出和系统通知使用稳定标签区分。
- Unicode 图标不可用时回退到 ASCII，例如 `[ok]`、`[x]`、`[...]`。

## 19. 安全展示

- 对工具输出移除终端控制字符，仅允许受控的换行和制表符。
- 不自动识别或打开工具输出中的链接。
- 对超长单行进行可视换行，防止布局被撑坏。
- 批准操作展示 Core 提供的原始关键参数，不由 TUI 重新概括后替代。
- TUI 日志不得包含 API Key、认证头或未脱敏环境变量。

## 20. 第一版视觉范围

第一版追求清晰、稳定和适合录制演示，不追求完整复刻 Codex/OpenCode：

必须有：

- 清晰的用户与 Agent 消息区分。
- 流式文本。
- 工具卡片及运行状态。
- 命令输出摘要。
- 文件 diff。
- 批准面板。
- 取消和错误状态。

暂不做：

- 主题选择器。
- 会话侧边栏。
- 鼠标交互。
- 文件树浏览器。
- 内置编辑器。
- 命令面板和自定义斜杠命令。
- 会话搜索与持久化恢复。

## 21. 测试要求

### 21.1 Reducer 测试

- 每类协议事件产生正确状态。
- 流式文本正确拼接。
- 工具事件按 tool call ID 关联。
- turn 终态后恢复输入。
- 取消等待期间不能提交新任务。
- 未知事件和 session 不匹配不会破坏状态。

### 21.2 组件测试

- 窄终端和低高度布局。
- ToolCallCard 各状态和折叠切换。
- 批准面板的允许与拒绝。
- diff 和命令输出截断提示。
- 无颜色与 ASCII 回退模式。

### 21.3 协议客户端测试

- JSON 消息被拆包或粘包时正确解析。
- 非法 JSON 被隔离。
- Core stderr 不进入协议解析器。
- Core 异常退出转换为 fatal 状态。
- shutdown 超时后正确清理子进程。

### 21.4 端到端测试

使用假的 Core 子进程输出固定 JSON Lines，验证：

- 初始化并提交任务。
- Agent 文本流式显示。
- 工具调用、diff 和测试失败结果展示。
- 批准一次与拒绝。
- 执行中取消。
- turn 完成后继续提交第二个任务。

## 22. 最小演示路径

用于验收和视频录制的 TUI 流程：

1. 启动后显示模型与工作区。
2. 用户输入真实修改任务。
3. Agent 流式说明将先检查代码。
4. 多个读取和搜索工具以折叠卡片显示。
5. `apply_patch` 完成后展示文件统计和 diff。
6. `run_command` 实时显示测试；第一次失败。
7. Agent 读取错误、再次补丁并重新测试。
8. 测试通过，Agent 输出总结，状态恢复为 idle。

该路径覆盖 TUI 的全部基本价值，同时能直接展示 Agent Core 的真实循环，而不是只展示生成结果。
