import {
  api, state, isCurrent, setView, skeletonPage, pageHead, emptyState, icon,
  esc, attr, num, compact, pct, agentGlyph, agentOptions, eventMeta, fmtDate,
  stackedChart, donutSvg, typeLegend, kpiCard, legendSwatch, navigate, showToast,
  localInput, fromLocal, toQuery
} from '../core.js';
import { openExportDialog } from '../export-dialog.js';

const RANGES = [
  { key: '7d', label: '7 天', days: 7 },
  { key: '30d', label: '30 天', days: 30 },
  { key: '90d', label: '90 天', days: 90 },
  { key: 'all', label: '全部', days: 90 }
];

export async function render(params, token, signal) {
  setView(skeletonPage('统计分析'));
  const query = {
    range: params.get('range') || (params.get('from') ? 'custom' : '30d'),
    agent: params.get('agent') || 'all',
    from: params.get('from') || '',
    to: params.get('to') || ''
  };
  let data;
  try {
    const apiQuery = toQuery({
      range: query.range === 'custom' ? '' : query.range,
      agent: query.agent,
      from: query.from,
      to: query.to,
      days: query.range === 'all' ? 90 : (query.range === '7d' ? 7 : query.range === '90d' ? 90 : 30)
    });
    data = await api.get('/api/stats' + apiQuery);
  } catch (error) {
    if (!isCurrent(token)) return;
    setView('<div class="page"><div class="panel"><div class="panel-body">' + emptyState('统计加载失败', error.message, 'alert-circle') + '<div style="text-align:center"><button class="btn btn-secondary btn-sm" data-action="reload-page">重新加载</button></div></div></div></div>');
    return;
  }
  if (!isCurrent(token)) return;
  draw(data, query, signal);
}

function draw(data, query, signal) {
  const summary = data.summary || {};
  const agents = data.agents || [];
  const byAgent = data.by_agent || [];
  const tools = data.top_tools || [];
  const projects = data.top_projects || [];
  const daily = data.daily || [];
  const typeCounts = {
    user_message: Number(summary.user_messages || 0),
    assistant_message: Number(summary.assistant_messages || 0),
    tool_call: Number(summary.tool_calls || 0),
    tool_result: Number(summary.tool_results || 0)
  };
  const totalTypes = typeCounts.user_message + typeCounts.assistant_message + typeCounts.tool_call + typeCounts.tool_result;
  const donutItems = Object.keys(typeCounts).map(function (key) {
    const meta = eventMeta(key);
    return { label: meta.label, count: typeCounts[key], color: meta.color };
  });
  const activeRange = query.range === 'custom' ? 'custom' : query.range;
  const rangeLabel = (RANGES.find(function (item) { return item.key === activeRange; }) || { label: '自定义' }).label;
  const agentLabel = query.agent !== 'all' ? (agents.find(function (item) { return item.key === query.agent; }) || {}).name : '全部 Agent';

  const chips = '<div class="filter-chips">' + RANGES.map(function (item) {
    return '<button class="chip ' + (activeRange === item.key ? 'active' : '') + '" data-analytics-range="' + item.key + '">' + item.label + '</button>';
  }).join('') + (activeRange === 'custom' ? '<span class="chip active">自定义</span>' : '') + '</div>';

  const filterBar = '<form id="analytics-filter-form" class="filter-bar">' +
    '<div class="filter-field"><span class="filter-label">AGENT</span><select class="select" id="analytics-agent">' + agentOptions(query.agent) + '</select></div>' +
    '<div class="filter-field"><span class="filter-label">FROM</span><input class="input" id="analytics-from" type="datetime-local" value="' + attr(localInput(query.from)) + '"></div>' +
    '<div class="filter-field"><span class="filter-label">TO</span><input class="input" id="analytics-to" type="datetime-local" value="' + attr(localInput(query.to)) + '"></div>' +
    '<div class="filter-actions"><button class="btn btn-primary btn-sm" type="submit">' + icon('filter') + '<span>应用</span></button>' +
    (query.from || query.agent !== 'all' ? '<a class="btn btn-ghost btn-sm" href="#/analytics">重置</a>' : '') +
    '<button class="btn btn-ghost btn-sm" type="button" data-export-analytics>' + icon('download') + '<span>导出</span></button></div></form>';

  const kpis = '<div class="kpi-grid">' +
    kpiCard({ label: '事件总量', value: num(summary.events), icon: 'layers', color: 'var(--accent)', foot: '范围 · <b>' + esc(agentLabel) + '</b> / ' + esc(rangeLabel) }) +
    kpiCard({ label: '覆盖会话', value: num(summary.sessions), icon: 'message', color: 'var(--blue)', foot: '平均 <b>' + num(summary.avg_events_per_session) + '</b> 事件 / 会话' }) +
    kpiCard({ label: '工具调用', value: num(summary.tool_calls), icon: 'tool', color: 'var(--amber)', foot: '结果 <b>' + num(summary.tool_results) + '</b> · 失败 <b class="' + (summary.tool_failures ? 'text-danger' : '') + '">' + num(summary.tool_failures) + '</b>' }) +
    kpiCard({ label: '工具成功率', value: summary.tool_success_rate === null ? '—' : pct(summary.tool_success_rate), icon: 'circle-check', color: 'var(--purple)', foot: 'Token <b>' + compact(Number(summary.tokens_input || 0) + Number(summary.tokens_output || 0)) + '</b> 输入+输出' }) +
    '</div>';

  const legend = agents.filter(function (agent) { return byAgent.some(function (row) { return row.agent_key === agent.key; }); }).map(function (agent) {
    return '<span class="legend-item">' + legendSwatch(agent.color) + esc(agent.name) + '</span>';
  }).join('');

  const activity = '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">事件趋势</h2><p class="panel-sub">按 Agent 分解的每日规范化事件</p></div><div class="panel-actions"><div class="chart-legend">' + legend + '</div></div></div><div class="panel-body">' + stackedChart(daily, agents, { height: 250 }) + '</div></section>';

  const typePanel = '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">结构分布</h2><p class="panel-sub">四类规范结构占比</p></div><span class="badge neutral">' + num(totalTypes) + ' EVENTS</span></div><div class="panel-body"><div class="donut-layout">' + donutSvg(donutItems, totalTypes, 'EVENTS') + '<div class="donut-legend">' + typeLegend(typeCounts) + '</div></div>' +
    '<div class="analytics-kpi-row"><div class="analytics-kpi"><span>消息总量</span><b>' + compact(typeCounts.user_message + typeCounts.assistant_message) + '</b><small>USER + ASSISTANT</small></div><div class="analytics-kpi"><span>工具链路</span><b>' + compact(typeCounts.tool_call + typeCounts.tool_result) + '</b><small>CALL + RESULT</small></div></div></div></section>';

  const maxAgentEvents = Math.max.apply(null, byAgent.map(function (row) { return Number(row.events || 0); }).concat([1]));
  const agentPanel = '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">Agent 分布</h2><p class="panel-sub">事件规模与会话覆盖</p></div><span class="badge neutral">' + byAgent.length + ' ACTIVE</span></div><div class="panel-body">' +
    (byAgent.length ? '<div class="agent-bars">' + byAgent.map(function (row) {
      return '<div class="agent-bar-row">' + agentGlyph(row.agent_key) + '<span class="agent-bar-name">' + esc(row.name) + '</span><div class="agent-bar-track"><i style="width:' + Math.max(3, Math.round(Number(row.events || 0) * 100 / maxAgentEvents)) + '%;--bar-color:' + attr(row.color) + '"></i></div><span class="agent-bar-value">' + compact(row.events) + '</span></div>' +
        '<div class="agent-bar-sub"><span>' + num(row.sessions) + ' 会话 · 平均 ' + num(row.avg_events) + '</span><span class="mono">' + compact(Number(row.tokens_input || 0) + Number(row.tokens_output || 0)) + ' tok</span></div>';
    }).join('') + '</div>' : emptyState('暂无 Agent 数据', '当前筛选范围内没有采集事件。', 'nodes')) + '</div></section>';

  const toolMax = Math.max.apply(null, tools.map(function (tool) { return Number(tool.calls || 0); }).concat([1]));
  const toolsPanel = '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">工具排行</h2><p class="panel-sub">调用量与异常分布</p></div><span class="badge neutral">TOP ' + Math.min(12, tools.length) + '</span></div><div class="panel-body flush">' +
    (tools.length ? '<div class="table-wrap"><table class="data-table fixed tools-table"><thead><tr><th>工具</th><th class="num">调用</th><th>占比</th><th class="num">异常</th></tr></thead><tbody>' + tools.map(function (tool) {
      return '<tr><td class="tool-name-cell">' + esc(tool.tool_name) + '</td><td class="num">' + num(tool.calls) + '</td><td><div class="bar-track"><i style="width:' + Math.max(4, Math.round(Number(tool.calls || 0) * 100 / toolMax)) + '%"></i></div></td><td class="num ' + (Number(tool.errors || 0) ? 'text-danger' : '') + '">' + num(tool.errors || 0) + '</td></tr>';
    }).join('') + '</tbody></table></div>' : emptyState('暂无工具调用', '当前筛选范围内没有工具事件。', 'tool')) + '</div></section>';

  const projectsPanel = '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">项目覆盖</h2><p class="panel-sub">事件最活跃的工作区</p></div><a class="btn btn-ghost btn-xs" href="#/sessions">浏览会话' + icon('arrow-right') + '</a></div><div class="panel-body flush">' +
    (projects.length ? '<div class="table-wrap"><table class="data-table fixed projects-table"><thead><tr><th>项目</th><th class="num">会话</th><th class="num">事件</th></tr></thead><tbody>' + projects.map(function (project) {
      return '<tr><td class="cell-main">' + esc(project.project) + '</td><td class="num">' + num(project.sessions) + '</td><td class="num">' + num(project.events) + '</td></tr>';
    }).join('') + '</tbody></table></div>' : emptyState('暂无项目信息', '来源记录未提供工作区元数据。', 'folder')) + '</div></section>';

  const formats = [
    { key: 'bundle', title: '分析包 ZIP', desc: '事件 CSV、会话摘要、汇总、字典与 manifest，适合直接分析或归档。', ext: '.zip' },
    { key: 'csv', title: '事件明细 CSV', desc: '扁平表结构，兼容 Excel / pandas / BI 工具。', ext: '.csv' },
    { key: 'jsonl', title: '规范化 JSONL', desc: '一行一个事件对象，适合日志管线与流式处理。', ext: '.jsonl' }
  ];
  const formatCards = formats.map(function (format) {
    return '<button class="format-card ' + (state.analyticsFormat === format.key ? 'selected' : '') + '" type="button" data-format="' + format.key + '"><div class="format-top"><strong>' + format.title + '</strong><span class="format-icon">' + icon(format.key === 'bundle' ? 'archive' : format.key === 'csv' ? 'file-text' : 'code') + '</span></div><p>' + format.desc + '</p><div class="format-ext mono">' + format.ext + '</div></button>';
  }).join('');

  const exportPanel = '<section class="panel export-panel"><div class="panel-head"><div><h2 class="panel-title">导出分析数据</h2><p class="panel-sub">当前筛选：' + esc(agentLabel) + ' · ' + esc(rangeLabel) + ' · ' + num(summary.events) + ' 条事件</p></div><span class="badge accent"><span class="dot"></span>READY</span></div><div class="panel-body"><div class="export-grid">' + formatCards + '</div><div class="export-actions"><div class="export-hint">导出会保留统一后的四类事件结构，并附带工具调用关联 ID 与 <b>fingerprint</b>。时间字段统一为 UTC ISO-8601，便于跨时区分析。</div><button class="btn btn-primary" type="button" data-export-analytics>' + icon('download') + '<span>导出 ' + (state.analyticsFormat === 'bundle' ? '分析包' : state.analyticsFormat === 'csv' ? 'CSV' : 'JSONL') + '</span></button></div></div></section>';

  const header = pageHead({
    eyebrow: 'ANALYTICS / UNIFIED EVENT MODEL',
    title: '统计分析',
    subtitle: '从会话总量、消息结构到工具调用成功率，以统一口径观察多个 Agent 的工作负载。',
    actions: '<a class="btn btn-secondary btn-sm" href="#/sessions">' + icon('message') + '<span>查看会话</span></a><button class="btn btn-ghost btn-sm" data-action="reload-page">' + icon('refresh') + '<span>刷新</span></button>'
  });

  setView('<div class="page">' + header + chips + filterBar + kpis +
    '<div class="analytics-top">' + activity + typePanel + '</div>' +
    '<div class="overview-grid equal">' + agentPanel + toolsPanel + '</div>' +
    '<div class="overview-grid"><div class="overview-stack">' + projectsPanel + '</div><div class="overview-stack">' + exportPanel + '</div></div>' +
    '</div>', function (root, sig) {
      root.addEventListener('submit', function (event) {
        const form = event.target.closest('#analytics-filter-form');
        if (!form) return;
        event.preventDefault();
        const agent = root.querySelector('#analytics-agent').value;
        const from = fromLocal(root.querySelector('#analytics-from').value);
        const to = fromLocal(root.querySelector('#analytics-to').value);
        if (from && to && from > to) { showToast('开始时间不能晚于结束时间', 'error'); return; }
        navigate('analytics', { agent: agent, from: from, to: to, range: from ? 'custom' : (query.range === 'custom' ? '30d' : query.range) }, true);
      }, { signal: sig });

      root.addEventListener('click', function (event) {
        const range = event.target.closest('[data-analytics-range]');
        if (range) {
          event.preventDefault();
          const item = RANGES.find(function (entry) { return entry.key === range.dataset.analyticsRange; });
          navigate('analytics', { agent: query.agent, range: item ? item.key : '30d' }, true);
          return;
        }
        const format = event.target.closest('[data-format]');
        if (format) {
          state.analyticsFormat = format.dataset.format;
          root.querySelectorAll('[data-format]').forEach(function (element) { element.classList.toggle('selected', element === format); });
          const label = state.analyticsFormat === 'bundle' ? '分析包' : state.analyticsFormat === 'csv' ? 'CSV' : 'JSONL';
          root.querySelectorAll('[data-export-analytics] span:last-child').forEach(function (element) { element.textContent = '导出 ' + label; });
          return;
        }
        const exportButton = event.target.closest('[data-export-analytics]');
        if (exportButton) {
          event.preventDefault();
          exportData(query);
        }
      }, { signal: sig });
    });
}

function exportData(query) {
  openExportDialog({
    context: { agent: query.agent === 'all' ? '' : query.agent, from: query.from, to: query.to },
    format: state.analyticsFormat || 'bundle',
    range: query.from ? 'custom' : (query.range || 'all'),
    title: '导出分析数据',
    subtitle: '按角色与时间范围导出规范事件，可直接用于 Excel、pandas 或归档。'
  });
}
