import {
  api, state, isAdmin, isCurrent, setView, skeletonPage, pageHead, kpiCard, sparkline,
  stackedChart, typeLegend, emptyState, icon, esc, attr, num, compact, bytes, pct,
  agentGlyph, renderAgentMini, agentStatus, relTime, fmtDate, fmtDuration, jobStatus
} from '../core.js';

export async function render(params, token) {
  setView(skeletonPage('总览'));
  try {
    const data = await api.get('/api/overview?days=14');
    if (!isCurrent(token)) return;
    draw(data);
  } catch (error) {
    if (!isCurrent(token)) return;
    setView('<div class="page"><div class="panel"><div class="panel-body">' + emptyState('总览加载失败', error.message, 'alert-circle') + '<div style="text-align:center"><button class="btn btn-secondary btn-sm" data-action="reload-page">重新加载</button></div></div></div></div>');
  }
}

function draw(data) {
  const totals = data.totals || {};
  const types = data.type_counts || {};
  const daily = data.daily || [];
  const agents = data.agents || [];
  const recent = data.recent_sessions || [];
  const tools = data.top_tools || [];
  const jobs = data.jobs || [];
  const sparks = daily.map(function (row) { return row.total; });
  const agentEventTotal = agents.reduce(function (sum, agent) { return sum + Number(agent.event_count || 0); }, 0);
  const health = totals.source_errors > 0 ? 'warning' : 'ok';
  const storageTotal = Number(totals.db_size || 0) + Number(totals.backup_size || 0);
  const storageRatio = storageTotal ? Math.min(100, Number(totals.db_size || 0) * 100 / storageTotal) : 0;

  const kpis = '<div class="kpi-grid">' +
    kpiCard({ label: '会话总数', value: num(totals.sessions), icon: 'message', color: 'var(--blue)', spark: sparkline(sparks, '#2f63c8'), foot: '<span class="kpi-trend up">+' + num(totals.events_24h) + '</span> 近 24h 事件' }) +
    kpiCard({ label: '规范化事件', value: num(totals.events), icon: 'layers', color: 'var(--accent)', spark: sparkline(daily.map(function (row) { return row.types ? row.types.user_message + row.types.assistant_message : 0; }), '#0b7a63'), foot: '消息 <b>' + num(Number(types.user_message || 0) + Number(types.assistant_message || 0)) + '</b> · 工具 <b>' + num(Number(types.tool_call || 0) + Number(types.tool_result || 0)) + '</b>' }) +
    kpiCard({ label: '工具调用', value: num(types.tool_call), icon: 'tool', color: 'var(--amber)', spark: sparkline(daily.map(function (row) { return row.types ? row.types.tool_call : 0; }), '#a8720f'), foot: '成功率 <b class="text-accent">' + pct(totals.tool_success_rate) + '</b> · 结果 <b>' + num(types.tool_result) + '</b>' }) +
    kpiCard({ label: '数据体积', value: bytes(storageTotal), icon: 'hard-drive', color: 'var(--purple)', spark: '', foot: '数据库 <b>' + bytes(totals.db_size) + '</b> · 备份 <b>' + bytes(totals.backup_size) + '</b>' }) +
    '</div>';

  const legend = agents.filter(function (agent) { return Number(agent.event_count || 0) > 0; }).map(function (agent) {
    return '<span class="legend-item"><span class="legend-swatch" style="--legend-color:' + attr(agent.color) + '"></span>' + esc(agent.name) + ' <span class="mono dim">' + compact(agent.event_count) + '</span></span>';
  }).join('');

  const activity = '<section class="panel">' +
    '<div class="panel-head"><div><h2 class="panel-title">采集活动</h2><p class="panel-sub">近 14 天事件流 · 按 Agent 堆叠</p></div><div class="panel-actions"><div class="chart-legend">' + legend + '</div></div></div>' +
    '<div class="panel-body">' + stackedChart(daily, agents, { height: 244 }) + '</div>' +
    '</section>';

  const toolMax = Math.max.apply(null, tools.map(function (tool) { return Number(tool.calls || 0); }).concat([1]));
  const topTools = '<section class="panel">' +
    '<div class="panel-head"><div><h2 class="panel-title">工具热度</h2><p class="panel-sub">调用次数与成功 / 异常分布</p></div><div class="panel-actions"><a class="btn btn-ghost btn-xs" href="#/analytics">分析详情' + icon('arrow-right') + '</a></div></div>' +
    '<div class="panel-body" style="padding-top:12px">' +
    (tools.length ? '<div class="tools-list">' + tools.map(function (tool) {
      const width = Math.max(4, Math.round(Number(tool.calls || 0) * 100 / toolMax));
      const errorRate = Number(tool.calls || 0) ? Math.round(Number(tool.errors || 0) * 100 / Number(tool.calls)) : 0;
      return '<div class="tool-stat"><span class="mono">' + esc(tool.tool_name) + '</span><div class="tool-stat-bar"><i style="width:' + width + '%;background:' + (errorRate > 20 ? 'var(--rose)' : 'var(--amber)') + '"></i></div><b>' + num(tool.calls) + '</b><span class="tag ' + (errorRate > 20 ? 'rose' : '') + '">' + (errorRate > 20 ? errorRate + '% ERR' : 'OK') + '</span></div>';
    }).join('') + '</div>' : emptyState('暂无工具调用', '采集到带工具调用的会话后，这里会展示热度与异常率。', 'tool')) +
    '</div></section>';

  const jobRows = jobs.length ? jobs.slice(0, 5).map(function (job) {
    const status = jobStatus(job.status);
    const agent = agents.find(function (item) { return item.key === job.agent_key; }) || { key: job.agent_key, color: '#8a8478', name: job.agent_key };
    return '<div class="job-row' + (job.status === 'queued' ? ' idle' : '') + '">' + agentGlyph(agent) +
      '<div class="job-row-main"><div class="job-row-title"><strong>' + esc(agent.name) + '</strong><span class="status-dot ' + status.dot + '"></span><span class="tag">' + esc(job.trigger || 'manual') + '</span></div><div class="job-row-meta">' + esc(job.message || status.label) + ' · ' + relTime(job.started_at || job.queued_at) + '</div>' +
      (job.status === 'running' ? '<div class="progress-line"><i style="width:' + Number(job.progress || 0) + '%"></i></div>' : '') + '</div>' +
      '<div class="job-status">' + statusBadgeForJob(job.status) + '</div></div>';
  }).join('') : emptyState('暂无采集任务', '点击右上角「同步全部」启动第一次采集。', 'activity');

  const jobsPanel = '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">采集队列</h2><p class="panel-sub">任务进度与最近状态</p></div><div class="panel-actions"><a class="btn btn-ghost btn-xs" href="#/tasks">全部任务' + icon('arrow-right') + '</a></div></div><div class="panel-body" style="padding-top:6px"><div class="job-list">' + jobRows + '</div></div></section>';

  const storage = '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">存储与覆盖</h2><p class="panel-sub">本地 SQLite 档案库</p></div><span class="badge ' + (health === 'ok' ? 'accent' : 'amber') + '"><span class="dot"></span>' + (health === 'ok' ? '运行正常' : '需关注') + '</span></div>' +
    '<div class="panel-body"><div class="storage-visual"><div class="storage-ring"><svg viewBox="0 0 108 108"><circle cx="54" cy="54" r="44" fill="none" stroke="var(--surface-4)" stroke-width="8"/><circle cx="54" cy="54" r="44" fill="none" stroke="var(--accent)" stroke-width="8" stroke-linecap="round" stroke-dasharray="' + (2 * Math.PI * 44 * storageRatio / 100).toFixed(1) + ' 276.46"/></svg><div class="storage-center"><strong>' + bytes(storageTotal) + '</strong><span>TOTAL STORAGE</span></div></div>' +
    '<div class="storage-info"><div class="storage-row"><span>数据库</span><b>' + bytes(totals.db_size) + '</b></div><div class="storage-row"><span>备份</span><b>' + bytes(totals.backup_size) + '</b></div><div class="storage-row"><span>有效数据源</span><b>' + num(totals.active_sources) + '</b></div><div class="storage-row"><span>异常数据源</span><b class="' + (totals.source_errors ? 'text-danger' : '') + '">' + num(totals.source_errors) + '</b></div></div></div>' +
    '<div class="storage-note">输入 token <b class="mono" style="color:var(--text-soft)">' + compact(totals.tokens_input) + '</b> · 输出 <b class="mono" style="color:var(--text-soft)">' + compact(totals.tokens_output) + '</b><br>原始记录始终保留，规范化数据可重复导出。</div></div></section>';

  const agentStrip = agents.length ? '<div class="agent-strip">' + agents.map(renderAgentMini).join('') + '</div>' : '<div class="panel"><div class="panel-body">' + emptyState('尚未接入 Agent', '前往 Agent 接入页配置数据源', 'nodes') + '</div></div>';

  const recentRows = recent.length ? recent.map(function (session) {
    const agent = { key: session.agent_key, name: session.agent_name, color: session.agent_color };
    return '<tr><td><a href="#/sessions/' + session.id + '" class="session-title-cell">' + agentGlyph(agent) + '<span class="session-title-text"><strong>' + esc(session.title) + '</strong><span>' + esc(session.project || '未标注项目') + ' · ' + esc(session.model || '未知模型') + '</span></span></a></td>' +
      '<td><span class="badge" style="border-color:color-mix(in srgb, ' + attr(session.agent_color) + ' 25%, transparent);color:' + attr(session.agent_color) + '">' + esc(session.agent_name) + '</span></td>' +
      '<td><div class="cell-main">' + fmtDate(session.started_at) + '</div><div class="cell-sub">' + fmtDuration(session.started_at, session.ended_at) + '</div></td>' +
      '<td><div class="mini-counts"><span><i style="background:var(--blue)"></i>' + num(session.message_count) + '</span><span><i style="background:var(--amber)"></i>' + num(session.tool_call_count) + '</span></div></td>' +
      '<td class="num col-wide">' + compact(Number(session.tokens_input || 0) + Number(session.tokens_output || 0)) + '</td>' +
      '<td class="cell-actions"><a class="btn btn-ghost btn-xs" href="#/sessions/' + session.id + '">查看' + icon('arrow-right') + '</a></td></tr>';
  }).join('') : '<tr><td colspan="6" class="table-empty">暂无会话记录</td></tr>';

  const recentPanel = '<section class="panel"><div class="table-wrap"><table class="data-table fixed recent-table"><thead><tr><th>会话</th><th>Agent</th><th>开始时间</th><th>消息 / 工具</th><th class="num col-wide">Token</th><th></th></tr></thead><tbody>' + recentRows + '</tbody></table></div></section>';

  const actionButtons = '<button class="btn btn-ghost btn-sm" data-action="reload-page">' + icon('refresh') + '<span>刷新</span></button>' +
    (isAdmin() ? '<button class="btn btn-primary btn-sm" data-action="sync-all">' + icon('refresh') + '<span>同步全部</span></button>' : '') +
    '<a class="btn btn-secondary btn-sm" href="#/analytics">' + icon('chart') + '<span>统计分析</span></a>';

  const html = '<div class="page">' +
    pageHead({
      eyebrow: 'OVERVIEW / 14 DAYS',
      title: '采集总览',
      subtitle: '所有 Agent 的对话轨迹在这里汇合。统一规范为消息、工具调用与工具结果四类结构，可检索、可统计、可导出。',
      actions: actionButtons
    }) +
    kpis +
    '<div class="overview-grid"><div class="overview-stack">' + activity + topTools + '</div><div class="overview-stack">' + jobsPanel + storage + '</div></div>' +
    '<div class="section-head"><div><h2>Agent 接入状态</h2><p>当前工作站的采集覆盖面</p></div><div class="section-meta">' + num(agentEventTotal) + ' EVENTS ACROSS ' + agents.length + ' AGENTS · <a href="#/agents" style="color:var(--accent)">配置接入</a></div></div>' +
    agentStrip +
    '<div class="section-head"><div><h2>最近会话</h2><p>快速回到最有价值的上下文</p></div><div class="section-actions"><span class="section-meta">LAST 8 SESSIONS</span><a class="btn btn-ghost btn-xs" href="#/sessions">浏览全部' + icon('arrow-right') + '</a></div></div>' +
    recentPanel +
    '</div>';

  setView(html);
}

function statusBadgeForJob(status) {
  const meta = jobStatus(status);
  return '<span class="badge ' + meta.cls + '"><span class="dot"></span>' + esc(meta.label) + '</span>';
}
