# 本地工具规范

## 1. 文档目的

本文定义 Agent Core 提供给模型的本地工具，包括参数、返回值、执行语义、安全边界和错误处理。当前提供十一个工具：

1. `list_directory`
2. `read_file`
3. `search_text`
4. `write_file`
5. `apply_patch`
6. `run_command`
7. `git_status`
8. `git_diff`
9. `move_path`
10. `delete_path`
11. `request_user_input`

工具由项目自行定义和执行，不依赖模型 API 服务端托管的文件或代码执行能力。

## 2. 设计原则

- 参数含义明确，避免同一参数承担多种语义。
- 工具输出结构稳定，模型能根据错误继续处理。
- 所有路径都以工作区根目录为安全边界。
- 默认返回足够完成任务的信息，但限制大文件和大输出。
- 修改操作尽量原子化，失败时不留下部分结果。
- 工具只报告事实，不替模型做任务完成判断。
- 用户批准只授权一次具体调用，不自动扩大权限。

## 3. 工具注册表

每个工具在注册表中包含：

| 字段 | 说明 |
| --- | --- |
| `name` | 提供给模型的唯一工具名 |
| `description` | 简短、面向模型的用途说明 |
| `parametersSchema` | JSON Schema 参数定义 |
| `handler` | 本地执行函数 |
| `riskEvaluator` | 根据参数确定风险级别 |
| `timeoutPolicy` | 超时策略 |
| `outputPolicy` | 输出长度和截断策略 |

注册时检查工具名唯一、Schema 合法且 handler 存在。未知工具不会尝试猜测或执行，而是返回 `unknown_tool`。

## 4. 通用执行流程

每个 tool call 按以下顺序处理：

```text
模型 tool call
   ↓
查找工具定义
   ↓
解析并校验 JSON 参数
   ↓
规范化路径与默认值
   ↓
风险评估
   ├─ 需要批准 → 等待用户决定
   └─ 无需批准
   ↓
执行工具
   ↓
限制并规范化输出
   ↓
生成 TUI 事件和模型 tool result
```

Core 必须同时生成：

- 面向 TUI 的结构化执行事件。
- 面向模型的紧凑 tool result。

两者来自同一个内部结果，但 TUI 可以收到 diff、耗时等额外展示信息。

## 5. 通用返回结构

工具成功：

```json
{
  "ok": true,
  "summary": "Read lines 1-80 from src/main.py",
  "data": {},
  "error": null,
  "meta": {
    "durationMs": 12,
    "truncated": false
  }
}
```

工具失败：

```json
{
  "ok": false,
  "summary": "File does not exist",
  "data": null,
  "error": {
    "code": "file_not_found",
    "message": "src/missing.py does not exist",
    "retryable": false,
    "details": {}
  },
  "meta": {
    "durationMs": 2,
    "truncated": false
  }
}
```

`summary` 必须简短且可以直接显示在 TUI 折叠卡片中。`data` 提供给模型；`details` 只包含有助于恢复的信息，不附带内部 traceback。

## 6. 通用错误码

| 错误码 | 含义 | 通常可重试 |
| --- | --- | --- |
| `unknown_tool` | 工具名未注册 | 否 |
| `invalid_arguments` | 参数 JSON 或类型错误 | 修正参数后可重试 |
| `path_outside_workspace` | 路径越过工作区边界 | 否 |
| `path_forbidden` | 命中敏感路径规则 | 否 |
| `file_not_found` | 文件不存在 | 修正路径后可重试 |
| `not_a_file` | 目标不是普通文件 | 修正路径后可重试 |
| `not_a_directory` | 目标不是目录 | 修正路径后可重试 |
| `unsupported_file_type` | 不支持的二进制或特殊文件 | 否 |
| `decode_error` | 文件无法按支持的编码读取 | 否 |
| `permission_denied` | 操作系统拒绝访问 | 否 |
| `conflict` | 文件已变化或补丁上下文不匹配 | 重新读取后可重试 |
| `approval_denied` | 用户拒绝执行 | 否 |
| `command_timeout` | 命令超时 | 视任务而定 |
| `command_failed` | 命令非零退出 | 视命令输出而定 |
| `cancelled` | 用户取消当前任务 | 否 |
| `internal_error` | 未预期工具异常 | 否 |

## 7. 路径规范与工作区边界

### 7.1 输入形式

模型传入的文件路径应为相对于工作区根目录的 POSIX 风格路径，例如 `src/main.py`。允许 `.` 表示工作区根目录。

第一版不鼓励模型传绝对路径；如果收到绝对路径，只有在规范化后仍位于工作区内时才允许。

### 7.2 规范化流程

1. 将相对路径拼接到工作区根目录。
2. 解析 `.`、`..` 和符号链接影响。
3. 对已有目标使用真实路径检查。
4. 对待创建目标检查最近的已有父目录真实路径。
5. 确认最终路径位于工作区根目录内。
6. 命中禁止路径时拒绝。

只进行字符串前缀比较是不安全的，例如 `/work/project-other` 不能被视为 `/work/project` 内部路径。

### 7.3 默认禁止路径

以下路径默认禁止模型读取或写入：

- `.env` 和 `.env.*`，但允许 `.env.example`。
- `.git/objects`、`.git/credentials` 等 Git 内部和凭据数据。
- 常见私钥文件，例如 `id_rsa`、`id_ed25519`、`*.pem`、`*.key`。
- 明确命名为 secrets、credentials 或 tokens 的本地配置。

`.gitignore`、源码和普通项目配置可以读取。禁止规则是安全底线，不因模型请求而自动解除。

## 8. 风险等级

工具调用分为三个风险等级：

| 风险 | 处理方式 | 示例 |
| --- | --- | --- |
| `low` | 可直接执行 | 读取文件、搜索、列目录、运行只读检查 |
| `medium` | 可执行并明确展示变更 | 写入普通项目文件、应用补丁、运行测试 |
| `high` | 执行前请求用户批准 | 删除、安装依赖、覆盖关键配置、网络或 Git 外部操作 |

风险由工具和实际参数共同决定。工具名不是唯一依据，例如 `run_command` 运行 `git status` 是低风险，运行依赖安装命令是高风险。

## 9. `list_directory`

### 9.1 用途

列出一个目录的直接子项，帮助模型了解项目结构。该工具不递归扫描整个仓库。

### 9.2 参数 Schema

```json
{
  "type": "object",
  "properties": {
    "path": {
      "type": "string",
      "description": "相对于工作区的目录路径，默认是 ."
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 500,
      "default": 200
    },
    "includeHidden": {
      "type": "boolean",
      "default": false
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### 9.3 返回数据

```json
{
  "path": ".",
  "entries": [
    {"name": "src", "path": "src", "type": "directory"},
    {"name": "pyproject.toml", "path": "pyproject.toml", "type": "file", "size": 1200}
  ],
  "totalEntries": 2,
  "truncated": false
}
```

### 9.4 行为规则

- 默认按目录优先、名称字典序排列。
- 不跟随目录符号链接进行递归，因为本工具不递归。
- 隐藏项在 `includeHidden=false` 时不返回，但 `.gitignore` 可作为例外显示。
- 命中禁止路径的子项可以显示名称，但不暴露其内容和目标信息。
- 超过 `limit` 时设置 `truncated=true`。

### 9.5 风险

正常调用为低风险。请求列出禁止路径时直接拒绝，不进入批准流程。

## 10. `read_file`

### 10.1 用途

按行读取单个文本文件，并返回稳定行号，供模型分析或为补丁定位上下文。

### 10.2 参数 Schema

```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string"},
    "startLine": {
      "type": "integer",
      "minimum": 1,
      "default": 1
    },
    "endLine": {
      "type": "integer",
      "minimum": 1
    }
  },
  "required": ["path"],
  "additionalProperties": false
}
```

### 10.3 返回数据

```json
{
  "path": "src/main.py",
  "startLine": 1,
  "endLine": 3,
  "totalLines": 120,
  "content": "1: from app import run\n2: \n3: run()",
  "truncated": false,
  "contentHash": "sha256:..."
}
```

### 10.4 行为规则

- 第一版按 UTF-8 解码，允许带 UTF-8 BOM。
- 返回内容包含 1-based 行号。
- `endLine` 未提供时读取到单次默认上限，而不是无条件读到文件末尾。
- 建议默认最多 400 行、最多 40,000 字符；先达到任一限制即停止。
- 空文件返回空 `content`，不视为错误。
- 目录、设备文件和无法识别的二进制文件拒绝读取。
- 返回 `contentHash`，供后续修改检测文件是否已变化。

### 10.5 风险

普通源码读取为低风险；敏感路径直接拒绝。

## 11. `search_text`

### 11.1 用途

在工作区中搜索文本或文件名，底层优先使用 `rg`，不可用时使用 Python 回退实现。

### 11.2 参数 Schema

```json
{
  "type": "object",
  "properties": {
    "query": {"type": "string", "minLength": 1},
    "path": {"type": "string", "default": "."},
    "mode": {
      "type": "string",
      "enum": ["content", "files"],
      "default": "content"
    },
    "isRegex": {"type": "boolean", "default": false},
    "caseSensitive": {"type": "boolean", "default": false},
    "glob": {"type": "string"},
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 500,
      "default": 100
    }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

### 11.3 返回数据

内容搜索：

```json
{
  "mode": "content",
  "matches": [
    {
      "path": "src/pricing.py",
      "line": 42,
      "column": 5,
      "text": "return calculate_total(items)"
    }
  ],
  "matchCount": 1,
  "truncated": false
}
```

文件名搜索返回 `paths` 字符串数组。

### 11.4 行为规则

- 默认按普通文本搜索，不将用户输入解释为正则表达式。
- 默认忽略 `.git`、`.venv`、`node_modules`、`dist`、`build`、缓存目录和二进制文件。
- 尊重项目 `.gitignore`；实现时应明确底层 `rg` 参数。
- `glob` 只用于限制范围，不能越过禁止路径规则。
- 正则表达式非法时返回 `invalid_arguments`。
- 达到结果或字符上限时设置 `truncated=true`。

### 11.5 风险

正常调用为低风险；搜索禁止路径时直接拒绝。

## 12. `write_file`

### 12.1 用途

创建新文本文件，或在确有必要时完整替换已有文本文件。局部修改应优先使用 `apply_patch`。

### 12.2 参数 Schema

```json
{
  "type": "object",
  "properties": {
    "path": {"type": "string"},
    "content": {"type": "string"},
    "expectedHash": {
      "type": "string",
      "description": "覆盖已有文件时建议提供 read_file 返回的哈希"
    },
    "createParents": {"type": "boolean", "default": false}
  },
  "required": ["path", "content"],
  "additionalProperties": false
}
```

### 12.3 返回数据

```json
{
  "path": "src/new_module.py",
  "created": true,
  "bytesWritten": 280,
  "contentHash": "sha256:...",
  "diff": "--- /dev/null\n+++ b/src/new_module.py\n@@ ...",
  "diffTruncated": false
}
```

### 12.4 行为规则

- 仅写入 UTF-8 文本。
- 新建文件可直接执行，路径仍需通过安全校验。
- 覆盖已有文件时，如果提供 `expectedHash` 且当前哈希不同，返回 `conflict`。
- 已有文件没有 `expectedHash` 时可执行，但 TUI 应明确标记为完整覆盖。
- `createParents=false` 且父目录不存在时返回错误。
- 使用同目录临时文件写入，刷新后原子替换目标，避免部分写入。
- 保留已有普通文件的权限位；新文件使用安全默认权限。
- 生成 unified diff，超长时截断并明确标记。
- 写入成功后重新读取或计算哈希验证结果。

### 12.5 风险

- 新建普通源码文件：中风险，无需单独批准，但必须展示 diff。
- 覆盖普通源码文件：中风险，无需单独批准，但必须展示 diff。
- 覆盖锁文件、核心配置或异常大的文件：高风险，需要批准。
- 禁止路径：直接拒绝。

## 13. `apply_patch`

### 13.1 用途

对一个或多个现有文本文件进行可检查的局部修改。它是 Agent 修改已有代码的首选工具。

### 13.2 补丁格式

第一版采用项目自定义、明确标记文件边界的 patch 文本格式，语义接近 unified diff。必须支持：

- 更新已有文件。
- 新建文件。
- 删除文件仅在用户批准后执行。

第一版可以不支持文件重命名；重命名需要使用创建新文件与删除旧文件两个明确步骤。

### 13.3 参数 Schema

```json
{
  "type": "object",
  "properties": {
    "patch": {
      "type": "string",
      "minLength": 1
    }
  },
  "required": ["patch"],
  "additionalProperties": false
}
```

### 13.4 返回数据

```json
{
  "files": [
    {
      "path": "src/pricing.py",
      "action": "updated",
      "addedLines": 4,
      "removedLines": 2,
      "contentHash": "sha256:..."
    }
  ],
  "diff": "--- a/src/pricing.py\n+++ b/src/pricing.py\n@@ ...",
  "diffTruncated": false
}
```

### 13.5 行为规则

- 先完整解析并验证补丁，再修改任何文件。
- 补丁中的每个路径都必须单独通过工作区检查。
- 上下文不匹配时返回 `conflict`，提示模型重新读取文件。
- 多文件补丁必须尽量保持事务性：验证全部目标和补丁后再写入。
- 写入使用临时文件与原子替换。
- 任一文件写入失败时，尽可能恢复已替换文件；实现应测试该行为。
- 修改后生成实际 diff，而不是直接信任模型提交的 patch 文本。
- 删除文件属于高风险操作，未批准时整个调用不执行。

### 13.6 风险

- 更新或新建普通源码：中风险，展示 diff。
- 删除文件、修改敏感配置、修改大量文件：高风险，需要批准。
- 修改禁止路径：直接拒绝。

## 14. `run_command`

### 14.1 用途

在工作区内执行非交互式本地命令，用于查看状态、运行测试、构建、格式化和其他开发任务。

### 14.2 参数 Schema

```json
{
  "type": "object",
  "properties": {
    "command": {
      "type": "string",
      "minLength": 1
    },
    "cwd": {
      "type": "string",
      "default": "."
    },
    "timeoutMs": {
      "type": "integer",
      "minimum": 1000,
      "maximum": 120000,
      "default": 30000
    }
  },
  "required": ["command"],
  "additionalProperties": false
}
```

### 14.3 返回数据

```json
{
  "command": "uv run pytest",
  "cwd": ".",
  "exitCode": 0,
  "stdout": "18 passed in 0.42s",
  "stderr": "",
  "timedOut": false,
  "cancelled": false,
  "truncated": false,
  "durationMs": 610
}
```

### 14.4 执行规则

- 使用平台约定的非交互式 shell 执行命令，第一目标平台为 macOS/Linux。
- 固定 `cwd` 为经过验证的工作区内部目录。
- 使用受控环境变量；继承运行所需环境，但移除或避免输出敏感值。
- 不提供交互式 stdin；需要输入的命令应失败或超时，而不是永久等待。
- 同时捕获 stdout 和 stderr，并支持实时发送受限的 `tool_output_delta`。
- 达到超时后先请求进程组正常终止，短暂等待后强制终止整个进程组。
- 用户取消时使用相同的进程组清理逻辑。
- 非零退出码返回 `ok=false` 和 `command_failed`，同时保留 stdout、stderr 和退出码供模型分析。
- 输出保留头部和尾部，中间省略，并设置 `truncated=true`。
- 第一版单次只运行一个命令，不支持后台常驻进程。

### 14.5 风险分类

低风险、可直接执行的典型命令：

- `pwd`、`ls`、`git status`、`git diff`
- 测试、类型检查、lint 和只读构建检查
- 读取版本号或帮助信息

中风险、可执行并展示结果的典型命令：

- 格式化器和可能修改源码的代码修复命令
- 普通构建命令生成工作区内构建产物

高风险、必须批准的典型命令：

- 安装、升级或删除依赖
- 删除或批量移动文件
- 修改 Git 历史、创建提交或切换工作树状态
- 网络请求、上传、发布、部署和 Git push
- 修改权限、执行下载得到的脚本
- 命令中包含明显危险的 shell 重定向、命令替换或破坏性选项

始终禁止：

- 访问工作区之外的路径。
- 请求提权，例如 `sudo`。
- 读取或打印 API Key、环境变量全集和凭据文件。
- 启动无法由 Core 跟踪和清理的后台进程。
- 用户批准也不能解除这些底线。

风险识别无法证明任意 shell 字符串安全，因此实现时采用保守策略：无法分类的命令按高风险处理。

## 15. `git_status`

以不经过 shell 的只读 Git 子进程返回当前分支、是否干净及每个变更路径的暂存区/工作区状态。可选布尔参数 `includeUntracked` 默认为 `true`。非 Git 仓库返回 `not_git_repository`，风险为低。

## 16. `git_diff`

读取有界、无颜色的 Git diff。`scope` 可为 `worktree`（默认）、`staged` 或 `all`；可选 `path` 限定工作区内路径，`contextLines` 范围为 0–20，`maxChars` 范围为 1,000–120,000。结果包含 `diff`、`empty` 和 `truncated`，风险为低。

## 17. `move_path`

参数为 `source`、`destination` 和可选的 `createParents`。源和目标必须位于工作区内，目标已存在时拒绝覆盖；移动目录时也拒绝把目录移入自身。风险为中。

## 18. `delete_path`

参数为 `path` 和可选布尔值 `recursive`。工作区根目录始终不可删除；文件、符号链接和空目录可直接处理，非空目录必须显式设置 `recursive: true`。删除不可撤销，风险为高，每次调用都需要用户批准。

## 19. `request_user_input`

参数仅包含 1–2,000 字符的非空字符串 `question`。它是 Agent 控制工具，不进入本地工具 handler：Core 发送 `user_input_required` 后暂停当前循环，收到回答或取消后生成对应 tool result，再继续模型请求。风险为低。

## 20. 输出限制

建议第一版默认值：

| 输出 | 默认限制 |
| --- | --- |
| `list_directory` | 200 项 |
| `read_file` | 400 行且 40,000 字符 |
| `search_text` | 100 条且 40,000 字符 |
| 单个 diff | 60,000 字符 |
| 命令 stdout + stderr | 40,000 字符 |
| tool result 总长度 | 60,000 字符 |

截断规则：

- 文件和搜索结果从尾部截断，并返回继续读取所需的位置。
- 命令输出保留头尾，因为错误摘要经常位于尾部。
- diff 保留文件列表和每个文件的开头，明确提示完整 diff 未发送给模型。
- 截断必须可见，不能伪装成完整结果。

## 21. 批准流程

1. 工具完成参数校验和风险评估。
2. Core 发送 `approval_required`，包含操作摘要、原因和参数。
3. TUI 只允许用户选择“允许一次”或“拒绝”。
4. 等待期间 Agent 循环暂停，但用户可以取消整个 turn。
5. 用户允许后，Core 再次确认 turn 仍活动，然后执行原始参数。
6. 用户拒绝后，生成 `approval_denied` tool result 返回模型。
7. 模型可以选择低风险替代方案或结束任务，但不能把拒绝解释为允许。

批准请求中的参数必须与实际执行参数完全一致。如果参数发生变化，必须重新评估并重新请求批准。

## 22. 并发规则

- 第一版按模型返回顺序串行执行多个 tool calls。
- 串行执行便于展示、批准、取消和文件冲突处理。
- 即使两个读取工具理论上可以并行，第一版也不做并发优化。
- 同一时刻最多有一个本地子进程命令。

## 23. 审计信息

每次工具调用在内存运行记录中保存：

- session ID、turn ID、step 和 tool call ID。
- 工具名称和脱敏后的参数。
- 风险等级与批准决定。
- 开始时间、结束时间和耗时。
- 成功或错误码。
- 变更文件列表和 diff 摘要。

第一版不要求持久化完整审计日志。若启用调试日志，必须默认脱敏并放入 `.gitignore` 覆盖的目录。

## 24. 测试要求

### 24.1 通用测试

- 未知工具和非法参数。
- 路径中的 `..`、绝对路径和符号链接越界。
- 禁止路径读取和写入。
- 输出达到限制时明确截断。
- 用户拒绝、等待时取消和执行时取消。
- 工具异常被转换为稳定错误，不泄漏 traceback 给模型。

### 24.2 文件工具测试

- 空文件、Unicode、无尾部换行和大文件。
- 新建、覆盖、哈希冲突和父目录不存在。
- 补丁上下文匹配与冲突。
- 多文件补丁预校验失败时没有任何文件变化。
- 修改后 diff 与实际文件一致。

### 24.3 命令工具测试

- 成功退出、非零退出、超时和取消。
- stdout、stderr 同时产生大量输出时不会死锁。
- 进程创建子进程后仍能整体终止。
- 交互式命令不会无限等待。
- 工作目录不能越界。
- 风险命令触发批准，未知命令保守处理。
