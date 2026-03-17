---
name: dida365-skill
description: "滴答清单/TickTick 任务管理工具——创建、查看、完成、删除任务和项目。Use when user says: 创建任务、添加待办、今日任务、查看清单、完成任务、滴答清单、TickTick、TODO、待办事项、任务管理、项目管理、标记完成、删除任务、设置截止日期、任务优先级、新建项目、收集箱、inbox。不要用于与任务管理无关的通用编程或问答任务。"
allowed-tools: "Bash"
license: MIT
compatibility: "Requires Python 3.8+. Uses Python stdlib only. Needs Dida365 OAuth token. Claude Code only."
metadata:
  author: fanxing
  version: 2.2.0
---

## 首次使用：先准备授权信息

如果用户还没有配置 `DIDA_CLIENT_ID` 和 `DIDA_CLIENT_SECRET`，先不要直接运行 `auth`。

先引导用户到开发者后台创建应用：

1. 打开 `https://developer.dida365.com/manage`
2. 点击 `New App`
3. 随便填写一个应用名即可创建
4. 创建后点击该应用的 `Edit`
5. 进入 `App Setting`
6. 复制其中的 `Client ID` 和 `Client Secret`
7. 将 `OAuth redirect URL` 设置为 `http://localhost:18365/callback`

然后把授权信息写入 skill 目录下的 `.env`：

```bash
cd ~/.claude/skills/dida365-skill
cp .env.example .env
```

在 `.env` 中填写：

```dotenv
DIDA_CLIENT_ID=你的_client_id
DIDA_CLIENT_SECRET=你的_client_secret
```

若用户已经有 `client_id / client_secret`，跳过本节，直接进入 OAuth 授权。

## 首次使用：OAuth 授权

若运行命令报 "未找到 access token" 错误，需先完成 OAuth 授权：

1. 确保 `.env` 文件已配置 `DIDA_CLIENT_ID` 和 `DIDA_CLIENT_SECRET`
2. 运行授权命令：

```bash
cd ~/.claude/skills/dida365-skill && python3 index.py auth
```

3. 在浏览器中打开输出的授权链接，完成授权后 token 自动保存

**重要**: 开发者应用的 Redirect URI 必须设置为 `http://localhost:18365/callback`

若当前是远程服务器环境，可在本机浏览器完成授权后，从重定向地址中复制 `code`，再执行：

```bash
cd ~/.claude/skills/dida365-skill && python3 index.py auth --code <authorization_code>
```

## Intent Decision Tree

```
用户想要…
├─ 查看所有项目/清单 ──────→ projects
├─ 查看项目元数据 ─────────→ project-info {projectId}
├─ 查看某项目内的任务 ────→ project {projectId}
├─ 查看某一个任务详情 ────→ task {projectId} {taskId}
├─ 创建新项目 ────────────→ create-project {name}
├─ 更新项目名称 ──────────→ update-project {projectId} --name xxx
├─ 删除项目 ──────────────→ delete-project {projectId}
├─ 查看今日待办 ──────────→ today
├─ 查看未来几天到期 ──────→ upcoming {days}
├─ 查看某时间段到期 ──────→ due-range {start} {end}
├─ 查看某时间段已完成 ────→ completed {start} {end} [--project id]
├─ 按项目/日期/优先级/标签筛选 → filter-tasks ...
├─ 查看收集箱 ────────────→ inbox
├─ 创建新任务 ────────────→ create-task {title} [--project id] [--due date] [--priority N]
├─ 创建带高级字段任务 ────→ create-task-raw
├─ 创建 checklist 任务 ───→ create-checklist {title} --project {id} --items "子项1|子项2"
├─ 更新任务属性 ──────────→ update-task {taskId} --project {pid} [--title/--content/--due/--priority/--tags]
├─ 更新带高级字段任务 ────→ update-task-raw {taskId}
├─ 完成任务 ──────────────→ complete-task {projectId} {taskId}
├─ 删除任务 ──────────────→ delete-task {projectId} {taskId}
├─ 移动任务 ──────────────→ move-task {fromProjectId} {toProjectId} {taskId}
├─ 检查连接 ──────────────→ check
└─ 重新授权 ──────────────→ auth
```

## 核心工作流

所有命令的 cwd 必须是 skill 目录：

```bash
cd ~/.claude/skills/dida365-skill && python3 index.py <command> [args]
```

除非是在开发这个 skill 本身，否则不要写临时 Python 片段访问 API；优先把用户意图映射到现有 CLI 子命令。

### 典型操作序列

**查看并完成任务：** 先用 `projects` 获取项目 ID → `project {id}` 查看任务列表 → `complete-task {projectId} {taskId}` 完成

**创建任务：** 直接 `create-task` 即可，不指定 `--project` 会放入默认项目

**精确回读单个任务：** 用 `task {projectId} {taskId}`。这是核对截止日期、完成时间、checklist 子项的首选命令。

`task` 输出会展示 checklist 子项的 `status=` 原始状态值，便于核对子项状态。

若任务已从项目里删除，但 OpenAPI 直查接口仍短暂返回缓存对象，`task` 会直接报错提示“可能已删除”，而不是把这类幽灵结果当成真实任务展示。

**查询已完成任务：** 不要用 `project`。`project` 主要看当前项目下的未完成任务；已完成任务请用 `completed`。

**高级 OpenAPI 参数：** 当需要 `desc`、`isAllDay`、`startDate`、`timeZone`、`reminders`、`repeatFlag`、`sortOrder`、复杂 `items` 字段时，不要猜命令行 flag，直接用 `create-task-raw` / `update-task-raw` 并通过 stdin 传 JSON。

## 命令参考

| 命令 | 参数 | 说明 |
|------|------|------|
| `auth` | `[--code xxx]` | OAuth 授权流程，支持手动粘贴授权码 |
| `check` | | 验证 token 和连接 |
| `projects` | | 列出所有项目（含 ID） |
| `project-info` | `{projectId}` | 项目元数据，接近 OpenAPI `/project/{projectId}` |
| `project` | `{projectId}` | 项目详情及其所有任务 |
| `task` | `{projectId} {taskId}` | 单个任务详情，适合精确核对 |
| `create-project` | `{name} [--color x]` | 创建项目 |
| `update-project` | `{id} --name xxx` | 更新项目 |
| `delete-project` | `{id}` | 删除项目 |
| `create-task` | `{title} [--project id] [--content desc] [--due YYYY-MM-DD] [--priority 0/1/3/5] [--tags t1,t2]` | 创建任务 |
| `create-task-raw` | `stdin JSON` | 用 OpenAPI 风格 JSON 创建高级任务 |
| `create-checklist` | `{title} --project {id} --items "a\|b\|c" [--content x] [--due x] [--priority N]` | 创建带子项的 checklist 任务 |
| `update-task` | `{taskId} --project {pid} [--title x] [--content x] [--due x] [--priority N] [--tags x]` | 更新任务 |
| `update-task-raw` | `{taskId}` + `stdin JSON` | 用 OpenAPI 风格 JSON 更新高级任务 |
| `complete-task` | `{projectId} {taskId}` | 标记完成 |
| `delete-task` | `{projectId} {taskId}` | 删除任务，并校验其已从项目可见结果中消失 |
| `move-task` | `{fromProjectId} {toProjectId} {taskId}` | 移动任务 |
| `today` | | 今日到期的未完成任务 |
| `upcoming` | `[days] [--project id]` | 未来 N 天到期的未完成任务，默认 7 天 |
| `due-range` | `{start} {end} [--project id]` | 指定日期区间内到期的未完成任务 |
| `completed` | `{start} {end} [--project id]` | 指定日期区间内已完成任务 |
| `filter-tasks` | `[--project id] [--start date] [--end date] [--priority 0,3] [--tags a,b] [--status 0,2]` | 高级筛选 |
| `inbox` | | 收集箱中的任务 |

## 参数说明

- `--priority`: 0=无, 1=低, 3=中, 5=高
- `--due`: 日期格式 YYYY-MM-DD（如 2026-03-20）
- `--tags`: 逗号分隔标签（如 "工作,重要"）
- `--items`: checklist 子项，用 `|` 分隔，并整体加引号，如 `"子项1|子项2"`
- `--content`: 任务描述。长文本可通过 stdin 传入：`printf '内容' | python3 index.py create-task "标题"`
- 常用查询：`python3 index.py upcoming 10`
- 指定区间：`python3 index.py due-range 2026-03-17 2026-03-26`
- 已完成区间：`python3 index.py completed 2026-03-17 2026-03-17 --project <projectId>`

## 高级 JSON 模式

当普通 flag 不够时，直接传 OpenAPI 风格 JSON。

创建高级任务：

```bash
cat <<'JSON' | python3 index.py create-task-raw
{
  "title": "高级任务",
  "projectId": "<projectId>",
  "desc": "checklist 描述",
  "isAllDay": true,
  "startDate": "2026-03-22T00:00:00+0800",
  "dueDate": "2026-03-22T23:59:59+0800",
  "timeZone": "Asia/Shanghai",
  "reminders": ["TRIGGER:PT0S"],
  "repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1",
  "sortOrder": 1000,
  "items": [
    {
      "title": "子项1",
      "status": 0,
      "isAllDay": true,
      "timeZone": "Asia/Shanghai",
      "sortOrder": 10
    }
  ]
}
JSON
```

更新高级任务：

```bash
cat <<'JSON' | python3 index.py update-task-raw TASK_ID
{
  "projectId": "<projectId>",
  "desc": "更新后的描述",
  "reminders": ["TRIGGER:P0DT9H0M0S", "TRIGGER:PT0S"]
}
JSON
```

核对高级字段时优先使用 `task {projectId} {taskId}`。

## 日期语义

- `--due YYYY-MM-DD` 是“按任务时区解释的日期”。
- 回读时优先使用 `task` 命令，不要只看 API 原始时间串。
- `project`/`today`/`upcoming`/`due-range` 都会按任务时区显示日期。

## 错误恢复

| 错误 | 原因 | 恢复 |
|------|------|------|
| 未找到 access token | 未完成 OAuth 授权 | `python3 index.py auth` |
| HTTP 401 | token 已失效或刷新失败 | `python3 index.py auth` 重新授权 |
| HTTP 404 project/task | ID 错误 | `python3 index.py projects` 获取正确 ID |
| 缺少 DIDA_CLIENT_ID | .env 未配置 | 复制 .env.example 为 .env 并填入凭据 |
| 想查看已完成但 `project` 里没有 | `project` 不适合查已完成 | 改用 `completed` 或 `task` |
| 想创建 checklist 但 `create-task` 不够 | 需要子项列表 | 改用 `create-checklist` |
| 普通命令不支持 OpenAPI 某个字段 | 需要高级字段 | 改用 `create-task-raw` / `update-task-raw` |
| `task` 报“OpenAPI 仍返回缓存对象” | 任务可能已删除但直查接口未立即一致 | 以 `project` / `filter-tasks` 结果为准 |

## 输出说明

- 读取命令返回格式化文本，直接展示给用户
- 写入命令返回操作结果摘要（名称 + ID）
- 任务列表中显示优先级、截止日期、标签和任务 ID
