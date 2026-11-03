import {
  state, setView, pageHead, icon, esc, num, compact
} from '../core.js';

const ADAPTERS = [
  { key: 'codex', name: 'Codex', source: '~/.codex/sessions/**/*.jsonl', format: 'rollout JSONL', mapping: 'response_item.message / function_call / output' },
  { key: 'claudecode', name: 'Claude Code', source: '~/.claude/projects/**/*.jsonl', format: 'projects JSONL', mapping: 'message.content blocks · tool_use / tool_result' },
  { key: 'opencode', name: 'OpenCode', source: '~/.local/share/opencode/opencode.db', format: 'SQLite (read-only)', mapping: 'session_v2 + session_message + content blocks' },
  { key: 'workbuddy', name: 'WorkBuddy', source: '~/.workbuddy/projects/**/*.jsonl', format: 'CodeBuddy JSONL', mapping: 'message / user / assistant + tool blocks' },
  { key: 'deepseekharness', name: 'DeepSeek Harness', source: '~/.dsh/sessions/**/session.v4.jsonl.zstd', format: 'v4 event stream · zstd', mapping: 'user/message · assistant/message · tool/result' },
  { key: 'zcode', name: 'ZCode', source: '~/.zcode/sessions/**/*.json(l)', format: 'generic JSON / JSONL', mapping: 'role/content · tool_calls · tool_use / tool_result' }
];

export async function render(params, token, signal) {
  const agents = state.agents || [];
  const version = state.boot && state.boot.app ? state.boot.app.version : '1.0.0';
  const coverage = agents.filter(function (agent) { return agent.enabled; }).length;

  const arch = '<div class="arch-map">' +
    layer('CLIENT', '浏览器', ['WebUI SPA · 原生 ES Modules', 'Hash Router · 无外部 CDN', 'HttpOnly Cookie 会话'], 'blue') +
    layer('API', 'HTTP 服务', ['REST JSON API', '鉴权 / 同源校验 / 限流', '静态资源与下载'], 'accent') +
    layer('DOMAIN', '服务层', ['Collector Registry', 'Normalization Pipeline', 'Job Manager / Scheduler'], '') +
    layer('SERVICES', '能力层', ['Incremental Sync', 'Dedup / Fingerprint', 'Analytics / Search', 'Export / Backup / Audit'], '') +
    layer('STORAGE', '持久化', ['SQLite WAL · 主库', 'gzip 快照 · 备份', 'ZIP / CSV / JSONL · 导出'], 'blue') +
    '</div>';

  const flow = '<div class="flow-steps">' +
    '<div class="flow-step"><span>01 · DISCOVER</span><strong>发现数据源</strong><p>按 Agent 配置的根路径与 glob 模式枚举本地文件 / SQLite。</p></div>' +
    '<div class="flow-step"><span>02 · READ</span><strong>增量读取</strong><p>JSONL 使用字节游标；SQLite / zstd 使用时间或文件指纹。</p></div>' +
    '<div class="flow-step"><span>03 · NORMALIZE</span><strong>统一规范化</strong><p>适配器把异构记录映射为四类事件与会话元数据。</p></div>' +
    '<div class="flow-step"><span>04 · PERSIST</span><strong>幂等落库</strong><p>fingerprint 唯一索引去重，事务内更新会话聚合。</p></div>' +
    '<div class="flow-step"><span>05 · OBSERVE</span><strong>检索与导出</strong><p>搜索、统计、分页浏览、审计与可下载分析包。</p></div>' +
    '</div>';

  const adapters = '<div class="table-wrap"><table class="data-table"><thead><tr><th>Agent</th><th>默认数据源</th><th>格式</th><th>规范化映射</th><th>状态</th></tr></thead><tbody>' +
    ADAPTERS.map(function (adapter) {
      const agent = agents.find(function (item) { return item.key === adapter.key; }) || {};
      return '<tr><td><div class="stats-agent-row"><span class="agent-glyph" style="--agent-color:' + esc(agent.color || '#8a8478') + '">' + esc((adapter.name || '').slice(0, 2).toUpperCase()) + '</span><span class="cell-main">' + esc(adapter.name) + '</span></div></td><td class="mono" style="font-size:10.5px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(adapter.source) + '</td><td><span class="tag">' + esc(adapter.format) + '</span></td><td class="cell-sub">' + esc(adapter.mapping) + '</td><td>' + (agent.enabled ? '<span class="badge accent"><span class="dot"></span>已接入</span>' : '<span class="badge neutral">未启用</span>') + '</td></tr>';
    }).join('') + '</tbody></table></div>';

  const eventCards = ['user_message', 'assistant_message', 'tool_call', 'tool_result'].map(function (type, index) {
    const labels = { user_message: '用户消息', assistant_message: '助手消息', tool_call: '工具调用', tool_result: '工具结果' };
    const details = {
      user_message: 'input_text · text · 清理注入上下文',
      assistant_message: 'output_text · text · tokens / model',
      tool_call: 'tool_name · call_id · input JSON',
      tool_result: 'call_id · content · success / error'
    };
    return '<div class="model-card"><h4>' + String(index + 1).padStart(2, '0') + ' · ' + labels[type] + '</h4><p>' + details[type] + '</p></div>';
  }).join('');

  const model = '<div class="model-grid">' +
    '<div class="model-card"><h4>agents</h4><p>适配器与采集配置：启用状态、根路径、匹配模式、间隔、重试次数、最近扫描 / 成功 / 错误时间。</p></div>' +
    '<div class="model-card"><h4>sessions</h4><p>按 Agent + 外部会话 ID 唯一。保存标题、项目、模型、起止时间、四类计数、token 与来源路径。</p></div>' +
    '<div class="model-card"><h4>events</h4><p>核心规范表。type 限定四类；保留 tool_call_id、tool_input、tool_status、fingerprint、原始 JSON 引用。</p></div>' +
    '<div class="model-card"><h4>sources</h4><p>每个文件或数据库的增量游标、大小、mtime、最近同步、导入量与错误信息。</p></div>' +
    '<div class="model-card"><h4>jobs</h4><p>任务状态机：queued → running → success / partial / failed。记录重试次数、进度与统计快照。</p></div>' +
    '<div class="model-card"><h4>access &amp; ops</h4><p>users / web_sessions / audit_logs / system_logs / backups / settings，构成访问控制与运维闭环。</p></div>' +
    '</div>';

  const pages = '<div class="doc-grid">' +
    doc('总览', '采集脉搏、活动堆叠图、Agent 覆盖、最近会话与队列状态。', 'grid') +
    doc('会话记录', '按 Agent / 时间 / 正文筛选，分页浏览与一键导出。', 'message') +
    doc('会话详情', '四类事件时间线、工具入参/结果、完整内容按需加载。', 'layers') +
    doc('统计分析', '时间趋势、结构分布、Agent / 工具排行与自定义范围。', 'chart') +
    doc('Agent 接入', '启用、路径、匹配模式、调度间隔、重试与游标重置。', 'nodes') +
    doc('采集任务', '队列、进度、自动重试与失败详情。', 'activity') +
    doc('备份 / 日志', 'SQLite 快照、下载、恢复流程、审计与源文件状态。', 'database') +
    doc('系统设置', '调度、备份、导出上限、成员与安全策略。', 'settings')
    + '</div>';

  const html = '<div class="page">' +
    pageHead({
      eyebrow: 'DOCS / SYSTEM DESIGN',
      title: '架构与文档',
      subtitle: '迹录是一个本地优先、单进程、无外部服务依赖的 AI 聊天记录管理器。本页给出运行架构、数据模型、适配器映射与主要交互流程。',
      actions: '<span class="badge accent">v' + esc(version) + '</span><span class="badge neutral">' + coverage + ' AGENTS ENABLED</span>'
    }) +
    '<div class="doc-hero panel"><span class="doc-hero-icon">' + icon('book') + '</span><div><h2>从原始轨迹到可分析资产</h2><p>采集层只读访问各 Agent 的本地记录；规范化层将差异收敛为统一的四类事件；服务层负责增量同步、去重、任务调度与失败重试；WebUI 与 REST API 提供检索、统计、导出与运维能力。原始文件永远不会被修改。</p></div></div>' +
    '<div class="overview-grid equal">' +
    '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">运行架构</h2><p class="panel-sub">单机部署 · Python 标准库 + SQLite</p></div><span class="badge neutral">5 LAYERS</span></div><div class="panel-body">' + arch + '</div></section>' +
    '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">采集流水线</h2><p class="panel-sub">从数据源到可导出事件</p></div><span class="badge accent">IDEMPOTENT</span></div><div class="panel-body">' + flow + '<div class="storage-note" style="margin-top:14px">失败任务会在同一任务内按指数退避自动重试；重试次数耗尽后保留错误详情，可手动重跑。单 Agent 同时只允许一个在途任务，避免游标竞争。</div></div></section>' +
    '</div>' +
    '<div class="section-head"><div><h2>Agent 适配器</h2><p>默认位置可通过 Agent 配置覆盖，支持任意兼容目录</p></div><div class="section-meta">' + num(agents.length) + ' REGISTERED</div></div>' +
    '<section class="panel"><div class="panel-body" style="padding-top:6px">' + adapters + '</div></section>' +
    '<div class="overview-grid equal" style="margin-top:14px">' +
    '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">统一事件模型</h2><p class="panel-sub">所有 Agent 最终收敛为四类结构</p></div><span class="badge accent">4 TYPES</span></div><div class="panel-body">' + '<div class="model-grid" style="grid-template-columns:1fr 1fr">' + eventCards + '</div>' + '</div></section>' +
    '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">数据模型</h2><p class="panel-sub">SQLite 关系表与索引</p></div><span class="badge neutral">WAL · FK ON</span></div><div class="panel-body">' + model + '</div></section>' +
    '</div>' +
    '<div class="section-head"><div><h2>主要页面与交互</h2><p>同一套导航与筛选语义贯穿所有页面</p></div><div class="section-meta">WEBUI MAP</div></div>' +
    pages +
    '<div class="overview-grid equal" style="margin-top:14px">' +
    '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">导出格式</h2><p class="panel-sub">为后续分析准备的数据包</p></div></div><div class="panel-body"><div class="model-list"><div class="model-card"><h4>ANALYSIS BUNDLE .ZIP</h4><p>events.csv · sessions.csv · summary.json · manifest.json · data_dictionary.md。推荐用于长期归档、交叉分析与复现。</p></div><div class="model-card"><h4>EVENTS CSV / JSONL</h4><p>扁平事件导出。字段包含 agent、session_id、event_type、timestamp、content、tool_call_id、tool_input_json、fingerprint、source_path。</p></div><div class="model-card"><h4>FILTER-AWARE</h4><p>导出继承 Agent、时间范围、事件类型、关键词与会话筛选；对话框中还可指定自定义起止日期、勾选四类事件角色，并先试算条数再下载。工具调用与结果通过 tool_call_id 关联。</p></div></div></div></section>' +
    '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">安全与运维边界</h2><p class="panel-sub">本机部署下的最小安全基线</p></div><span class="badge accent">LOCAL-FIRST</span></div><div class="panel-body"><div class="meta-list">' +
    ops('访问控制', '管理员 / 只读成员；写操作仅管理员，导出对所有登录成员开放。') +
    ops('会话安全', 'HttpOnly + SameSite=Strict Cookie；登录限流；写请求要求同源校验头。') +
    ops('数据持久化', 'SQLite WAL、外键、事务写入；备份使用在线 Backup API。') +
    ops('失败恢复', '任务级指数退避；源文件错误隔离；游标可重置并依赖 fingerprint 幂等。') +
    ops('日志与审计', '系统日志记录采集与调度；审计日志记录登录、配置、导出、备份、用户变更。') +
    '</div><div class="storage-note" style="margin-top:14px">当前为单机部署模型。若需多人远程访问，请在反向代理层启用 HTTPS、限制来源网段，并设置 <b class="mono">AGENTTRAIL_SECURE_COOKIE=1</b>。</div></div></section>' +
    '</div></div>';

  setView(html);
}

function layer(kind, label, nodes, tone) {
  return '<div class="arch-layer"><div class="arch-layer-label"><b>' + esc(kind) + '</b><span>' + esc(label) + '</span></div><div class="arch-nodes">' + nodes.map(function (node, index) {
    return (index ? '<span class="arch-arrow">→</span>' : '') + '<span class="arch-node ' + (tone || '') + '">' + esc(node) + '</span>';
  }).join('') + '</div></div>';
}

function doc(title, description, iconName) {
  return '<div class="doc-link"><span>' + icon(iconName) + '</span><strong>' + esc(title) + '</strong><small>' + esc(description) + '</small></div>';
}

function ops(title, description) {
  return '<div class="meta-item"><span class="meta-key">' + esc(title) + '</span><span class="meta-value">' + esc(description) + '</span></div>';
}
