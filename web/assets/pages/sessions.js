import {
  api, state, isCurrent, setView, skeletonPage, pageHead, emptyState, icon,
  esc, attr, num, compact, agentGlyph, agentOptions, fmtDate, fmtDuration, relTime,
  localInput, fromLocal, pagination, navigate, showToast, toQuery
} from '../core.js';
import { openExportDialog } from '../export-dialog.js';

export async function render(params, token, signal) {
  setView(skeletonPage('会话记录'));
  const query = {
    agent: params.get('agent') || '',
    q: params.get('q') || '',
    from: params.get('from') || '',
    to: params.get('to') || '',
    sort: params.get('sort') || 'started_desc',
    page: params.get('page') || '1'
  };
  let data;
  try {
    data = await api.get('/api/sessions' + toQuery({
      agent: query.agent,
      q: query.q,
      from: query.from,
      to: query.to,
      sort: query.sort,
      page: query.page
    }));
  } catch (error) {
    if (!isCurrent(token)) return;
    setView('<div class="page"><div class="panel"><div class="panel-body">' + emptyState('会话列表加载失败', error.message, 'alert-circle') + '<div style="text-align:center"><button class="btn btn-secondary btn-sm" data-action="reload-page">重新加载</button></div></div></div></div>');
    return;
  }
  if (!isCurrent(token)) return;
  draw(data, query, signal);
}

function draw(data, query, signal) {
  const rows = data.items || [];
  const hasFilter = !!(query.agent || query.q || query.from || query.to);
  const bodyRows = rows.length ? rows.map(function (session) {
    const agent = { key: session.agent_key, name: session.agent_name, color: session.agent_color };
    return '<tr><td><a class="session-title-cell" href="#/sessions/' + session.id + '">' + agentGlyph(agent) +
      '<span class="session-title-text"><strong>' + esc(session.title) + '</strong><span>' + esc(session.project || '未标注项目') + ' · ' + esc(session.model || '未知模型') + '</span></span></a></td>' +
      '<td><span class="badge" style="border-color:color-mix(in srgb, ' + attr(session.agent_color) + ' 25%, transparent);color:' + attr(session.agent_color) + '">' + esc(session.agent_name) + '</span></td>' +
      '<td><div class="cell-main">' + fmtDate(session.started_at) + '</div><div class="cell-sub">' + fmtDuration(session.started_at, session.ended_at) + '</div></td>' +
      '<td class="col-wide"><div class="mini-counts"><span><i style="background:var(--blue)"></i>' + num(session.user_message_count || 0) + '</span><span><i style="background:var(--accent)"></i>' + num(session.assistant_message_count || 0) + '</span><span><i style="background:var(--amber)"></i>' + num(session.tool_call_count || 0) + '</span></div></td>' +
      '<td class="num">' + num(session.event_count || 0) + '</td>' +
      '<td class="num col-wide">' + compact(Number(session.tokens_input || 0) + Number(session.tokens_output || 0)) + '</td>' +
      '<td class="cell-actions"><a class="btn btn-ghost btn-xs" href="#/sessions/' + session.id + '">打开' + icon('arrow-right') + '</a></td></tr>';
  }).join('') : '<tr><td colspan="7" class="table-empty">' + (hasFilter ? '没有符合筛选条件的会话' : '暂无会话，请先同步 Agent 数据') + '</td></tr>';

  const activeRange = query.from ? (rangeLabel(query.from)) : 'all';
  const filterBar = '<form id="session-filter-form" class="filter-bar">' +
    '<div class="filter-field grow"><span class="filter-label">SEARCH</span><div class="input-wrap"><span class="input-icon">' + icon('search') + '</span><input class="input" id="session-search" name="q" value="' + attr(query.q) + '" placeholder="搜索标题、项目或消息正文…"></div></div>' +
    '<div class="filter-field"><span class="filter-label">AGENT</span><select class="select" id="session-agent">' + agentOptions(query.agent) + '</select></div>' +
    '<div class="filter-field"><span class="filter-label">FROM</span><input class="input" id="session-from" type="datetime-local" value="' + attr(localInput(query.from)) + '"></div>' +
    '<div class="filter-field"><span class="filter-label">TO</span><input class="input" id="session-to" type="datetime-local" value="' + attr(localInput(query.to)) + '"></div>' +
    '<div class="filter-field"><span class="filter-label">SORT</span><select class="select" id="session-sort">' +
    '<option value="started_desc"' + (query.sort === 'started_desc' ? ' selected' : '') + '>最新开始</option><option value="started_asc"' + (query.sort === 'started_asc' ? ' selected' : '') + '>最早开始</option><option value="events_desc"' + (query.sort === 'events_desc' ? ' selected' : '') + '>事件最多</option><option value="updated_desc"' + (query.sort === 'updated_desc' ? ' selected' : '') + '>最近更新</option></select></div>' +
    '<div class="filter-actions"><button class="btn btn-primary btn-sm" type="submit">' + icon('filter') + '<span>应用筛选</span></button>' +
    (hasFilter ? '<a class="btn btn-ghost btn-sm" href="#/sessions">清除</a>' : '') +
    '<button class="btn btn-ghost btn-sm" type="button" data-export-sessions>' + icon('download') + '<span>导出</span></button></div></form>';

  const quick = '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;flex-wrap:wrap"><div class="filter-chips"><button class="chip ' + (activeRange === '24h' ? 'active' : '') + '" data-range="24h">24 小时</button><button class="chip ' + (activeRange === '7d' ? 'active' : '') + '" data-range="7d">7 天</button><button class="chip ' + (activeRange === '30d' ? 'active' : '') + '" data-range="30d">30 天</button><button class="chip ' + (activeRange === 'all' ? 'active' : '') + '" data-range="all">全部时间</button></div><div class="section-meta mono">' + num(data.total) + ' SESSIONS · PAGE ' + data.page + '/' + data.pages + '</div></div>';

  const table = '<section class="panel"><div class="table-wrap"><table class="data-table fixed sessions-table"><thead><tr><th>会话</th><th>Agent</th><th>开始时间</th><th class="col-wide">用户 / 助手 / 工具</th><th class="num">事件</th><th class="num col-wide">Token</th><th></th></tr></thead><tbody>' + bodyRows + '</tbody></table></div></section>' +
    pagination(data, 'sessions', query);

  const header = pageHead({
    eyebrow: 'ARCHIVE / NORMALIZED TRACES',
    title: '会话记录',
    subtitle: '跨越不同 Agent 的统一会话索引。支持 Agent、时间范围、项目与正文搜索，按事件量或更新时间排序。',
    actions: '<a class="btn btn-secondary btn-sm" href="#/analytics">' + icon('chart') + '<span>统计分析</span></a><button class="btn btn-ghost btn-sm" data-action="reload-page">' + icon('refresh') + '<span>刷新</span></button>'
  });

  const emptyHint = !rows.length && !hasFilter ? '<div style="margin-top:14px">' + emptyState('档案库还是空的', '先在 Agent 接入页配置数据源，再触发一次全量同步。', 'message') + '<div style="text-align:center"><a class="btn btn-primary btn-sm" href="#/agents">前往 Agent 接入</a></div></div>' : '';

  setView('<div class="page">' + header + quick + filterBar + emptyHint + table + '</div>', function (root, sig) {
    root.addEventListener('submit', function (event) {
      const form = event.target.closest('#session-filter-form');
      if (!form) return;
      event.preventDefault();
      applyFilters(root, query);
    }, { signal: sig });

    root.addEventListener('click', function (event) {
      const range = event.target.closest('[data-range]');
      if (range) {
        event.preventDefault();
        applyRange(range.dataset.range, query);
        return;
      }
      const exportButton = event.target.closest('[data-export-sessions]');
      if (exportButton) {
        event.preventDefault();
        exportSessions(query);
      }
    }, { signal: sig });
  });
}

function applyFilters(root, current) {
  const q = root.querySelector('#session-search').value.trim();
  const agent = root.querySelector('#session-agent').value;
  const from = fromLocal(root.querySelector('#session-from').value);
  const to = fromLocal(root.querySelector('#session-to').value);
  const sort = root.querySelector('#session-sort').value;
  if (from && to && from > to) {
    showToast('开始时间不能晚于结束时间', 'error');
    return;
  }
  navigate('sessions', { agent: agent, q: q, from: from, to: to, sort: sort }, true);
}

function applyRange(range, current) {
  let from = '';
  if (range === '24h') from = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  if (range === '7d') from = new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString();
  if (range === '30d') from = new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString();
  navigate('sessions', { agent: current.agent, q: current.q, from: from, sort: current.sort }, true);
}

function rangeLabel(from) {
  const value = new Date(from).getTime();
  if (Number.isNaN(value)) return 'all';
  const hours = (Date.now() - value) / 3600000;
  if (hours <= 26) return '24h';
  if (hours <= 24 * 8) return '7d';
  if (hours <= 24 * 31) return '30d';
  return 'custom';
}

function exportSessions(query) {
  openExportDialog({
    context: { agent: query.agent, q: query.q, from: query.from, to: query.to },
    format: 'bundle',
    title: '导出会话事件',
    subtitle: '沿用当前筛选条件，可再调整时间范围与事件角色。'
  });
}
