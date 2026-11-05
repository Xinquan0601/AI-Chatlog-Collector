# API 参考

Base URL：`http://127.0.0.1:8787`  
所有响应：`{"ok": true, "data": ...}` 或 `{"ok": false, "error": {"code": "...", "message": "..."}}`。

## 通用约定

- 除「公开」接口外，需要登录 Cookie。
- 所有非 GET 请求必须带：`X-AgentTrail-Request: 1`。
- 请求体：`application/json`，最大 2MB。
- 分页参数：`page` 从 1 开始，`page_size` 最大 100。
- 时间参数：UTC ISO-8601，如 `2026-11-01T00:00:00Z`。
- 下载接口返回附件，不使用 JSON 包装。

### 认证示例

```bash
curl -c cookies.txt -H "Content-Type: application/json" -H "X-AgentTrail-Request: 1" \
  -d '{"username":"admin","password":"admin123"}' \
  http://127.0.0.1:8787/api/auth/login
```

后续请求：`curl -b cookies.txt ...`。

---

## 公开接口

### GET /api/health

健康检查。

### GET /api/auth/status

返回当前 Cookie 是否有效。

```json
{"authenticated": true, "user": {"id": 1, "username": "admin", "role": "admin"}}
```

### POST /api/auth/login

Body：`{"username":"...", "password":"..."}`。成功设置 HttpOnly Cookie。

---

## 认证 / 账号

### POST /api/auth/logout

销毁当前会话并清除 Cookie。

### POST /api/auth/change-password

Body：

```json
{"current_password":"...", "new_password":"至少8位"}
```

其他设备会话会失效，当前会话保留。

### GET /api/bootstrap

返回当前用户、权限、应用信息、公开配置、Agent 列表与事件类型字典。

---

## 总览与 Agent

### GET /api/overview?days=14

返回：`totals`、`type_counts`、`agents`、`daily`、`recent_sessions`、`jobs`、`top_tools`。

### GET /api/agents

每个 Agent 的采集配置、状态、会话 / 事件 / 工具计数、来源数和活动任务。

### PATCH /api/agents/{key}  （管理员）

Body 可包含：

```json
{
  "enabled": true,
  "roots": ["~/.codex/sessions"],
  "patterns": ["*.jsonl"],
  "auto_sync": true,
  "interval_minutes": 30,
  "max_attempts": 3
}
```

`roots` 中的路径会先按当前系统用户归一化：`C:/Users/Administrator/.claude/projects`、`/home/administrator/.codex/sessions` 都会保存为 `~/.claude/projects`、`~/.codex/sessions`；也支持 `%APPDATA%`、`$HOME` 等环境变量。

### POST /api/agents/{key}/sync  （管理员）

排队采集。返回 `202` 与 `job_id`。若该 Agent 已有任务，返回已有任务并附 `notice`。

### POST /api/agents/sync-all  （管理员）

为全部已启用 Agent 排队。

### POST /api/agents/{key}/reset  （管理员）

清空该 Agent 的源游标，下次重新扫描。**不会删除事件**。

---

## 会话与事件

### GET /api/sessions

Query：

| 参数 | 说明 |
|---|---|
| agent | Agent key，或省略 |
| q | 标题 / 项目 / cwd / 事件正文关键词 |
| from / to | 时间范围 |
| project | 精确项目 |
| page / page_size | 分页 |
| sort | started_desc / started_asc / events_desc / updated_desc / title_asc |

返回：`items / total / page / page_size / pages`。

### GET /api/sessions/{id}

Query：

| 参数 | 说明 |
|---|---|
| event_type | 事件类型，逗号分隔的任意组合：`user_message` / `assistant_message` / `tool_call` / `tool_result`；`all` 或省略表示不过滤 |
| q | 正文 / 工具名 / 工具入参关键词 |
| page / page_size | 分页 |

控制台默认只请求 `user_message,assistant_message`，工具调用与工具结果由勾选框按需加入。

返回：

- `session`：会话元数据与聚合计数；
- `events`：分页事件；正文超 12k 字符被截断，标记 `truncated`；
- `type_counts`：四类计数；
- `tools`：本会话工具使用排行。

### GET /api/events/{id}

返回单事件完整正文、工具入参、原始 JSON。用于「查看完整内容」。

### GET /api/search?q=...&limit=8

跨会话和事件正文搜索。返回 `sessions` 与 `events` 摘要。

---

## 统计

### GET /api/stats

Query：

| 参数 | 说明 |
|---|---|
| range | 7d / 30d / 90d / all |
| from / to | 自定义时间范围（优先于 range） |
| agent | Agent key |
| days | 趋势图天数，1–180 |

返回 `summary / type_counts / daily / by_agent / top_tools / top_projects / agents`。

---

## 导出

### GET /api/export

Query：

| 参数 | 说明 |
|---|---|
| format | csv / jsonl / json / bundle |
| agents | 逗号分隔的 Agent key，可多选，组内为「或」；省略或 `all` 表示全部。`agents` 优先于 `agent` |
| agent | 旧版单 Agent 过滤，等价于 `agents` 只有一个值 |
| range | 快捷范围：24h / 7d / 30d / 90d / all；仅在未提供 `from` 时生效 |
| from / to | 闭区间起止时间，可任意 ISO-8601 精度（服务端统一为 UTC 毫秒）；两侧可各自单独提供，`from` 优先于 `range`，`to` 独立生效 |
| types | 逗号分隔的事件类型，可多选，组内为「或」：`user_message,assistant_message,tool_call,tool_result`；省略表示全部 |
| q | 关键词，匹配 `content` 或 `tool_name`（不区分大小写） |
| project / session_id | 项目 / 会话过滤 |

未提供的条件不加限制；不同条件之间为「且」。成功返回附件。默认不设行数上限（`export_max_rows=0`）；只有手动设置上限且命中超过时返回 413 `export_too_large`。

```bash
curl -b cookies.txt -OJ \
  "http://127.0.0.1:8787/api/export?format=bundle&agents=codex,zcode&range=30d&types=user_message,assistant_message"
```

### GET /api/export/estimate

导出前的试算：参数与 `/api/export` 完全一致（`agents` / `agent`、`range`、`from`、`to`、`types`、`q`、`project`、`session_id`），只统计、不生成文件。WebUI 的导出对话框与「数据导出」页都用它实时显示条数与体积。

`by_agent` 只应用除 Agent 选择外的条件，因此每张 Agent 卡片都能显示「若选它」的命中数；`total` / `by_type` / `sessions` 则严格按 `agents` 过滤。下面示例中 `by_agent` 的数值可以大于 `total`，原因正是它忽略了 `agents` 选择（示例仅列前 3 项）。

```json
{
  "ok": true,
  "data": {
    "total": 42,
    "by_type": {"user_message": 14, "assistant_message": 28, "tool_call": 0, "tool_result": 0},
    "sessions": 7,
    "bytes": 36130,
    "agents": ["codex", "zcode"],
    "by_agent": [
      {"agent_key": "workbuddy", "events": 1783, "sessions": 33},
      {"agent_key": "opencode", "events": 625, "sessions": 29},
      {"agent_key": "deepseekharness", "events": 100, "sessions": 6}
    ],
    "limit": 0,
    "unlimited": true,
    "exceeded": false,
    "filters": {
      "agents": ["codex", "zcode"],
      "from": "2026-10-28T00:00:00.000Z",
      "to": "2026-11-03T23:59:59.999Z",
      "types": ["user_message", "assistant_message"],
      "session_id": null,
      "project": null
    }
  }
}
```

| 字段 | 说明 |
|---|---|
| total | 匹配的事件总数（等于 `by_type` 之和） |
| by_type | 四类事件的分别计数，未命中的类型为 0 |
| sessions | 命中的会话数 |
| bytes | 正文字节数加总，用于估算文件体积 |
| agents | 规范化后的 Agent 过滤列表；空数组表示不限 |
| by_agent | 每个 Agent 在「其余条件不变」时的命中事件数与会话数，按事件数降序 |
| filters | 服务端实际生效的筛选条件（日期已归一化为 UTC 毫秒） |
| limit | 当前 `export_max_rows` 设置，`0` 表示不限制 |
| unlimited | 是否不限制行数（`limit <= 0`） |
| exceeded | 是否超过单次导出上限（为 true 时 `/api/export` 返回 413） |

---

## 任务

### GET /api/tasks

Query：`status`、`agent`、`page`、`page_size`。返回 `items`、`active`、`counts`。

### GET /api/tasks/{id}

任务详情与统计快照。

### POST /api/tasks/{id}/retry  （管理员）

创建新的重试任务；旧任务保留。

---

## 备份  （管理员）

### GET /api/backups

返回备份列表、统计与自动备份配置。

### POST /api/backups

Body：`{"note":"升级前快照"}`。返回新备份记录。

### GET /api/backups/{id}/download

下载 `.db.gz`。

### DELETE /api/backups/{id}

删除备份文件与数据库记录。

---

## 数据导入  （管理员）

两种来源共用同一套运行记录与接口，服务端按文件自动识别，运行记录里的 `kind` 为 `legacy` 或 `archive`：

- **旧库迁移**：旧版 chatlog 导出库（`sessions` + `messages` + `export_meta`），以 `mode=ro&immutable=1` 只读打开，事件指纹为 `row:<row_id>`。
- **档案包合并**：本工具导出的 ZIP / CSV / JSONL / JSON，按包内 `fingerprint` 与本地事件比对，只写入缺失记录；用于把另一台机器采集的内容并入本机，重复执行保持幂等。只有 ZIP 带 `sessions.csv`（会话摘要），单文件 CSV / JSONL / JSON 仅含事件；导出时加在 `= + - @` 开头文本前的 `'` 保护前缀会在导入时还原。

### GET /api/import/candidates

返回档案库所在目录及其子目录下发现的来源：items[]（path / name / size_bytes / sessions / messages / modified_at / kind）。`.zip` 档案包额外给出 `events` 与 `sessions`。

### GET /api/import/preview?path=...

自动识别来源并返回预览。路径无效或不是可识别的导出文件时返回 400 `bad_source`。

旧库（`kind="legacy"`）：totals（sessions / messages / reasoning / tool_calls / tool_results / tokens）、sources[]（source、agent_key、supported、计数、时间范围）、warnings。

档案包（`kind="archive"`）：

| 字段 | 说明 |
|---|---|
| format | `bundle` / `csv` / `jsonl` / `json` |
| exported_at | 导出时间（来自 manifest，可能为空） |
| filters | manifest 里记录的导出筛选条件 |
| totals | `events、sessions、new_events、duplicate_events、new_sessions、merged_sessions、skipped、unknown_agents` |
| by_agent | 每个 Agent 的 events / sessions / new_events / duplicate_events |
| by_type | 四类事件的分布 |
| first_at / last_at | 事件时间范围（UTC ISO-8601） |
| warnings | 跳过原因、本机未接入的 Agent 等 |

### GET /api/import/runs?limit=20

返回导入记录 items[] 与当前运行中的 active_run_id。每条含 kind、status、progress、会话 / 事件统计、errors、warnings。

### POST /api/import/runs

Body：{"path":"D:/.../chatlog-export.db","include_reasoning":false,"dry_run":false}，档案包路径同样适用；`include_reasoning` 只对旧库生效。返回 202 与 run_id；已有导入运行时返回 409 `conflict`。

### GET /api/import/runs/{id}

单次导入的进度与统计：processed_sessions / total_sessions、imported_sessions、imported_events、duplicate_events、skipped_messages、errors、warnings、progress。

档案包运行时 `total_sessions` / `processed_sessions` 以事件为单位计数，`imported_sessions` 表示本次涉及的会话总数（新建 + 已有合并），`imported_events` 为实际写入的事件数。

### POST /api/import/runs/{id}/cancel

取消运行中的导入；该任务不在运行时返回 409。

状态：queued → running → success / partial / failed / canceled；服务重启时遗留的 queued / running 会被标记为 interrupted。

---

## 设置 / 用户 / 日志  （管理员）

### GET /api/settings

返回全部配置、表计数、数据路径。

### PATCH /api/settings

可更新：`site_name、auto_sync_enabled、sync_interval_minutes、auto_retry_attempts、auto_backup_enabled、backup_interval_hours、backup_retention、default_page_size、timezone、keep_forever、export_max_rows`。`export_max_rows` 取 0 表示不限制，正数上限为 1,000,000。

### GET /api/users

成员列表（不含密码哈希）。

### POST /api/users

Body：`username、password、role、must_change_password`。

### PATCH /api/users/{id}

Body：`role、active、password`（password 可省略）。不能停用 / 降级最后一个管理员或当前登录管理员。

### GET /api/logs

Query：`level、module、page、page_size`。

### GET /api/audit-logs

审计日志分页。

### GET /api/sources

采集源文件状态：路径、类型、状态、最近同步、导入数量、错误。

### GET /api/system

版本、Python、平台、路径、计数、数据库大小。

---

## 错误码

| HTTP | code | 说明 |
|---|---|---|
| 400 | bad_request | 参数不合法 |
| 401 | unauthorized / invalid_credentials | 未登录 / 密码错误 |
| 403 | forbidden / csrf | 权限不足 / 请求校验失败 |
| 404 | not_found | 资源不存在 |
| 413 | too_large / export_too_large | 请求体 / 导出过大 |
| 415 | unsupported_media_type | 非 JSON 请求体 |
| 429 | rate_limited | 登录尝试过多 |
| 500 | internal_error | 未处理异常（详情在系统日志） |
