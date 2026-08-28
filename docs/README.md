# coding-agent

一个从零实现的本地编程智能体。TypeScript/Ink TUI 负责交互与展示，Python Core 负责 OpenAI 兼容模型调用、原生 tool calling、上下文管理、本地工具执行、批准和循环终止。

项目不使用 LangChain、OpenAI Agents SDK 或其他 Agent 框架，也不依赖服务端托管的文件与代码执行工具。

## 环境要求

- Python 3.12
- uv
- Node.js 22 或更高版本
- npm
- 支持原生 tool calling 的 OpenAI 兼容模型接口

## 安装

```bash
uv sync --dev
npm install
```

复制环境变量模板并在本地填写：

```bash
cp .env.example .env
```

程序不会自动读取或向模型暴露 `.env`。启动前将变量导入当前 shell：

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://example.com/v1"
export AGENT_MODEL="model-name"
```

## 运行

开发模式：

```bash
npm run dev -- --cwd /absolute/path/to/target-project
```

也可以覆盖模型或 Base URL：

```bash
npm run dev -- --cwd /path/to/project --model model-name --base-url https://example.com/v1
```

构建并运行：

```bash
npm run build
npm start -- --cwd /absolute/path/to/target-project
```

单独启动 Core 进行协议调试：

```bash
uv run python -m agent_coder
```

Core 从 stdin 读取 JSON Lines，并将协议事件写到 stdout；运行日志只允许写入 stderr。

## 基础工具

- `list_directory`：列出目录。
- `read_file`：按行读取 UTF-8 文件。
- `search_text`：搜索文件内容或名称。
- `write_file`：创建或完整替换文本文件。
- `apply_patch`：局部修改文件。
- `run_command`：执行带超时和输出限制的非交互命令。

所有路径都必须位于启动时指定的工作区。敏感文件、越界路径和部分外部副作用操作会被拒绝；安装依赖、删除文件等高风险操作需要用户批准一次。

## 测试

```bash
uv run pytest
npm run typecheck
npm run test:tui
```

## 文档

- `DESIGN.md`：总体设计。
- `PROTOCOL.md`：TUI 与 Core 协议。
- `TOOLS.md`：工具契约与安全策略。
- `AGENT_LOOP.md`：Agent 循环和上下文管理。
- `TUI.md`：终端界面设计。

