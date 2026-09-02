# coding-agent

`coding-agent` 是一个运行在本地终端中的编程智能体。你可以像使用 Codex CLI 或 OpenCode 一样，用自然语言描述编程任务；它会自主查看项目、搜索代码、修改文件、执行测试，并根据执行结果继续处理，直到给出最终答复。

项目采用 TypeScript/Ink 构建终端界面，Python 实现模型调用、Agent 循环和本地工具。运行时使用支持原生 tool calling 的 OpenAI 兼容接口，不依赖 LangChain、OpenAI Agents SDK 等 Agent 框架，也不使用服务端托管的文件或代码执行工具。

## 基本功能

### 终端交互界面

- 在 TUI 中提交自然语言编程任务。
- 流式显示模型回复和当前运行状态。
- 使用 `markdansi` 渲染 GFM，支持标题、粗体、斜体、行内代码、带语法高亮的代码块、表格、嵌套列表、引用和链接等格式。
- 展示文件读取、搜索、修改和命令执行过程。
- 展示文件 diff、命令输出、耗时和错误信息。
- 对高风险操作显示完整参数并请求用户确认。
- Agent 在缺少必要信息时可暂停并直接向用户提问。
- 支持取消正在执行的任务。
- 在同一次运行中继续追问或提交下一项任务。
- 自动恢复当前工作区最近使用的对话。
- 输入 `/session` 新建或切换当前工作区的历史对话。

### 自主编程循环

Agent 会按需重复执行以下过程：

1. 理解用户任务并调用模型。
2. 接收模型生成的一个或多个 tool calls。
3. 在本地校验并执行工具。
4. 把工具结果返回模型。
5. 根据文件内容、diff 或测试结果继续处理。
6. 完成任务后输出变更和验证总结。

程序设置了最大执行步数、重复调用检测、空响应处理和取消机制，避免无限循环。

### 本地工具

| 工具 | 作用 |
| --- | --- |
| `list_directory` | 查看目录中的文件和子目录 |
| `read_file` | 按行读取 UTF-8 文本文件 |
| `search_text` | 搜索文件内容或文件名 |
| `write_file` | 创建文件或完整替换文件内容 |
| `apply_patch` | 对一个或多个文件进行局部修改 |
| `run_command` | 执行测试、构建和其他非交互式命令 |
| `git_status` | 读取结构化 Git 工作区状态 |
| `git_diff` | 查看工作区、暂存区或指定路径的 Git diff |
| `move_path` | 在工作区内移动或重命名文件和目录 |
| `delete_path` | 删除文件、符号链接或目录，执行前要求确认 |
| `request_user_input` | 暂停当前任务并向用户询问必要信息 |

### 安全控制

- 所有文件操作限制在启动时指定的工作区内。
- 拒绝通过 `..`、绝对路径或符号链接访问工作区外部。
- 默认禁止读取 `.env`、私钥和常见凭据文件。
- 命令设置超时和输出长度限制。
- API Key 不会加入模型上下文，也不会传给 Agent 执行的命令。
- 安装依赖、删除文件、修改 Git 状态等高风险操作需要用户批准一次。
- `sudo`、Git push、凭据输出和明显越界命令始终禁止。

安全检查用于降低误操作风险，但它不是完整的操作系统沙箱。请使用测试项目或版本控制，并在批准高风险命令前认真检查参数。

## 环境要求

- macOS 或 Linux
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 22 或更高版本
- npm
- 支持 Chat Completions 原生 tool calling 和流式输出的 OpenAI 兼容模型接口

## 快速开始

### 1. 安装依赖

进入仓库目录：

```bash
cd coding-agent
```

安装并锁定 Python 环境：

```bash
uv sync --dev
```

安装 TUI 依赖：

```bash
npm install
```

### 2. 配置模型

程序会自动读取工作区根目录下的 `.env`，也支持使用已导出的环境变量：

```bash
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://your-provider.example/v1"
export AGENT_MODEL="your-model-name"
```

| 环境变量 | 必填 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 是 | OpenAI 兼容接口的 API Key |
| `OPENAI_BASE_URL` | 否 | API 地址，默认 `https://api.openai.com/v1` |
| `AGENT_MODEL` | 是 | 需要调用的模型名称 |

仓库提供了 [.env.example](../.env.example) 作为变量模板。你可以在准备运行 `coding-agent` 的目标项目中创建 `.env`：

```dotenv
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-provider.example/v1
AGENT_MODEL=your-model-name
```

Core 只从 `.env` 选取上述三个配置项，不会把文件中的其他变量导入进程环境。配置优先级为：命令行参数 > 已导出的环境变量 > 工作区 `.env` > 默认值。不要提交包含真实凭据的 `.env`。

### 3. 启动

推荐安装为本机命令：

```bash
npm run install:command
```

安装完成后，进入需要处理的项目并直接启动：

```bash
cd /absolute/path/to/target-project
coding-agent
```

Agent 默认把执行 `coding-agent` 时的当前目录作为工作区，不需要显式传入路径。仍可通过参数覆盖工作区、模型或 API 地址：

```bash
coding-agent \
  --cwd /absolute/path/to/target-project \
  --model your-model-name \
  --base-url https://your-provider.example/v1
```

开发模式无需安装全局命令：

```bash
npm run dev
```

该命令同样默认使用当前目录。若在 `coding-agent` 仓库中运行，它会把智能体自身的仓库作为工作区；开发时可用 `--cwd` 指定其他测试项目：

```bash
npm run dev -- --cwd /absolute/path/to/target-project
```

生产构建：

```bash
npm run build
npm start -- --cwd /absolute/path/to/target-project
```

查看命令参数：

```bash
coding-agent --help
```

## 使用示例

启动后，在输入框中描述一个边界清晰、可以验证的任务，例如：

```text
阅读这个项目，修复价格计算中的浮点精度问题，补充相关测试并运行测试套件。
```

或者：

```text
为现有 Todo CLI 增加 priority 字段，更新帮助信息和测试，不要改变现有命令的行为。
```

Agent 会在界面中展示读取文件、搜索代码、应用补丁和运行测试的过程。如果某一步需要安装依赖、删除文件或执行其他高风险命令，界面会暂停并等待批准。

### 对话历史

对话按工作区分别保存。再次从同一目录启动 `coding-agent` 时，会自动恢复最近使用的对话及其模型上下文。在聊天输入区输入：

```text
/session
```

随后可使用方向键选择历史对话、按 `Enter` 切换，或按 `N` 新建对话。切换仅允许在 Agent 空闲时进行。

在空闲输入区键入 `/` 会打开命令列表。继续输入可按前缀过滤命令，使用 `↑` / `↓` 选择，按 `Enter` 执行，按 `Esc` 关闭。列表只展示当前版本真实可用的命令。

历史文件位于当前工作区的 `.coding-agent/sessions/`。其中可能包含你输入的任务、模型回复、工具参数和工具结果，因此目录默认权限受限、被加入本项目的 `.gitignore`，Agent 的文件工具不能进入该目录，命令策略也会拒绝对它的直接引用。若目标项目已有自己的 `.gitignore`，建议也加入：

```gitignore
.coding-agent/
```

## 键盘操作

| 快捷键 | 作用 |
| --- | --- |
| `Enter` | 提交任务、确认批准选项或回答 Agent 的问题 |
| `Ctrl+Enter` | 在任务或问题回答中插入换行；部分终端可能不支持 |
| `Y` | 在批准面板中选择“允许一次” |
| `N` | 在批准面板中选择“拒绝” |
| `Esc` | 关闭面板、拒绝待批准操作或取消 Agent 的问题 |
| `Ctrl+C` | 运行中先取消任务；空闲时退出程序 |
| `/session` | 打开对话选择面板 |
| `/` | 打开并过滤命令列表 |
| `↑` / `↓` | 在对话选择面板中移动 |
| `N` | 在对话选择面板中新建对话 |
| `Esc` | 关闭对话选择面板、拒绝批准或取消当前任务 |

如果当前终端无法区分 `Ctrl+Enter`，可以从外部编辑器复制多行任务并粘贴到输入框。

## 命令行参数

| 参数 | 说明 |
| --- | --- |
| `--cwd PATH` | 覆盖 Agent 工作区；不传时使用当前所在目录 |
| `--model NAME` | 覆盖 `AGENT_MODEL` |
| `--base-url URL` | 覆盖 `OPENAI_BASE_URL` |
| `--help` | 显示帮助信息 |

通常只需先 `cd` 到目标项目再运行 `coding-agent`。在智能体自身仓库内调试时，建议用 `--cwd` 指向单独的测试项目。

## 可执行文件说明

`npm run install:command` 会生成可执行的 TUI 构建产物，并通过 npm link 在当前 Node.js 环境中安装一个 `coding-agent` 命令。可以用下面的命令确认安装位置：

```bash
command -v coding-agent
```

该命令入口是单一可执行启动器，但不是完全自包含的原生二进制：运行时仍需要本机的 Node.js、uv、项目 Python 环境及本仓库目录。移动或删除仓库后，需要重新安装命令。

如果使用 nvm 切换 Node.js 版本，npm 的全局命令目录也可能发生变化；切换后重新执行 `npm run install:command` 即可。

## 开发与测试

运行 Python 测试：

```bash
uv run pytest
```

运行 TUI 协议测试：

```bash
npm run test:tui
```

执行 TypeScript 类型检查和构建：

```bash
npm run typecheck
npm run build
```

单独启动 Python Core 进行 JSON Lines 协议调试：

```bash
uv run python -m agent_coder
```

Core 从 stdin 读取协议消息，将事件写入 stdout，并把普通日志写入 stderr。

## 项目结构

```text
coding-agent/
├── apps/tui/                  # TypeScript / React / Ink TUI
├── src/agent_coder/           # Python Agent Core
│   ├── agent.py               # Agent 主循环
│   ├── model.py               # OpenAI 兼容模型适配
│   ├── server.py              # JSON Lines 协议服务
│   ├── sessions.py            # 工作区对话历史持久化
│   └── tools/                 # 本地工具与安全策略
├── tests/                     # Python 测试
├── docs/                      # 使用与设计文档
├── pyproject.toml
├── uv.lock
├── package.json
└── package-lock.json
```

## 常见问题

### 启动后提示缺少 `OPENAI_API_KEY`

确认当前工作区的 `.env` 包含正确变量，或者变量已经导入启动 TUI 的同一个 shell：

```bash
test -f .env && echo ".env exists"
echo "$OPENAI_API_KEY"
```

不要把真实 Key 粘贴到日志、截图、README 或演示视频中。

### 模型不调用工具或接口返回格式错误

确认当前模型与网关支持 Chat Completions 的原生 function/tool calling，并支持流式 tool call 参数。仅提供普通文本生成接口的模型无法驱动本项目。

### 命令被要求批准

这是正常的安全行为。界面会展示工具名称、命令、路径和风险原因。选择“允许一次”只批准当前 tool call；参数变化后需要重新批准。

### Agent 无法访问某个文件

文件必须位于 `--cwd` 指定的工作区内，并且不能属于默认禁止的凭据或敏感路径。请不要通过放宽规则让模型读取 API Key。

### Agent 修改后测试仍然失败

工具失败会返回模型继续处理，但模型能力和上下文仍会影响结果。可以在同一会话中补充约束、指出失败现象，或取消任务后用更明确的目标重新开始。

## 当前限制

- 一次只能执行一个用户任务。
- 当前没有删除、重命名或搜索历史对话的界面。
- 恢复后的聊天区只重建用户和 Agent 文本；旧工具详情仍保留在模型上下文中，但不重新展开成工具卡片。
- 当前主要支持 UTF-8 文本文件。
- 命令工具只适合非交互式、可终止的开发命令。
- 暂不支持 Windows、浏览器工具、多智能体或插件系统。
- 不自动创建 Git commit，也不会自动 push、发布或部署。
- 不同 OpenAI 兼容服务对流式 tool calling 的实现可能存在差异。

## 设计文档

- [总体设计](./DESIGN.md)
- [TUI 与 Core 通信协议](./PROTOCOL.md)
- [本地工具规范](./TOOLS.md)
- [Agent 循环设计](./AGENT_LOOP.md)
- [TUI 交互设计](./TUI.md)
