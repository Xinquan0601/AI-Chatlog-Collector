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

Query：`event_type`（四类之一或 all）、`q`、`page`、`page_size`。

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
| range | 快捷范围：24h / 7d / 30d / 90d / all |
| from / to | 自定义起止时间（UTC ISO-8601，优先于 range） |
| agent / q / project / session_id | 筛选 |
| types | 逗号分隔的事件类型：`user_message,assistant_message,tool_call,tool_result`；省略表示全部 |

成功返回附件；超出行数上限返回 413 `export_too_large`。

```bash
curl -b cookies.txt -OJ \
  "http://127.0.0.1:8787/api/export?format=bundle&agent=codex&range=30d"
```

### GET /api/export/estimate

导出前的试算：参数与 `/api/export` 完全一致（`range`、`from`、`to`、`agent`、`q`、`project`、`session_id`、`types`），只统计、不生成文件。WebUI 的导出对话框用它实时显示条数与体积。

```json
{
  "ok": true,
  "data": {
    "total": 70,
    "by_type": {"user_message": 14, "assistant_message": 28, "tool_call": 14, "tool_result": 14},
    "sessions": 12,
    "bytes": 4838,
    "limit": 200000,
    "exceeded": false,
    "filters": {"agent": null, "from": "2026-11-01T00:00:00.000Z", "to": null, "types": ["user_message"], "session_id": null, "project": null}
  }
}
```

| 字段 | 说明 |
|---|---|
| total | 匹配的事件总数（等于 `by_type` 之和） |
| by_type | 四类事件的分别计数，未命中的类型为 0 |
| sessions | 命中的会话数 |
| bytes | 正文字节数加总，用于估算文件体积 |
| limit | 当前 `export_max_rows` 设置 |
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

## 设置 / 用户 / 日志  （管理员）

### GET /api/settings

返回全部配置、表计数、数据路径。

### PATCH /api/settings

可更新：`site_name、auto_sync_enabled、sync_interval_minutes、auto_retry_attempts、auto_backup_enabled、backup_interval_hours、backup_retention、default_page_size、timezone、keep_forever、export_max_rows`。

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
