import {
  api, state, isCurrent, setView, skeletonPage, pageHead, emptyState, icon, esc, attr,
  num, agentGlyph, agentOptions, agentStatus, fmtFull, fmtDate, relTime, pagination,
  navigate, showModal, prettyJson, showToast
} from '../core.js';

const TABS = [
  { key: 'system', label: '系统日志', icon: 'file-text' },
  { key: 'audit', label: '审计日志', icon: 'shield' },
  { key: 'sources', label: '源文件状态', icon: 'server' }
];

export async function render(params, token, signal) {
  setView(skeletonPage('日志与审计'));
  const tab = TABS.some(function (item) { return item.key === params.get('tab'); }) ? params.get('tab') : 'system';
  const query = {
    tab: tab,
    level: params.get('level') || 'all',
    module: params.get('module') || '',
    agent: params.get('agent') || 'all',
    page: params.get('page') || '1'
  };
  let data;
  try {
    if (tab === 'system') data = await api.get('/api/logs?' + new URLSearchParams({ level: query.level, module: query.module, page: query.page, page_size: '30' }).toString());
    else if (tab === 'audit') data = await api.get('/api/audit-logs?' + new URLSearchParams({ page: query.page, page_size: '30' }).toString());
    else data = await api.get('/api/sources?' + new URLSearchParams({ agent: query.agent, page: query.page, page_size: '20' }).toString());
  } catch (error) {
    if (!isCurrent(token)) return;
    setView('<div class="page"><div class="panel"><div class="panel-body">' + emptyState('日志加载失败', error.message, 'alert-circle') + '<div style="text-align:center"><button class="btn btn-secondary btn-sm" data-action="reload-page">重新加载</button></div></div></div></div>');
    return;
  }
  if (!isCurrent(token)) return;
  draw(tab, query, data, signal);
}

function draw(tab, query, data, signal) {
  const items = data.items || [];
  const tabs = '<div class="tabs">' + TABS.map(function (item) {
    return '<a class="tab ' + (tab === item.key ? 'active' : '') + '" href="#/logs?tab=' + item.key + '">' + esc(item.label) + '</a>';
  }).join('') + '</div>';

  let filters = '';
  let table = '';
  let title = '系统日志';
  let subtitle = '服务运行、采集器、调度器与备份模块的诊断信息。';

  if (tab === 'system') {
    title = '系统日志';
    subtitle = '采集与后台任务的可观测性入口。错误日志会保留异常堆栈摘要，便于定位适配器问题。';
    filters = '<form id="log-filter-form" class="filter-bar"><div class="filter-field"><span class="filter-label">LEVEL</span><select class="select" id="log-level"><option value="all">全部级别</option><option value="info"' + (query.level === 'info' ? ' selected' : '') + '>INFO</option><option value="warning"' + (query.level === 'warning' ? ' selected' : '') + '>WARNING</option><option value="error"' + (query.level === 'error' ? ' selected' : '') + '>ERROR</option></select></div><div class="filter-field"><span class="filter-label">MODULE</span><select class="select" id="log-module"><option value="">全部模块</option>' + ['collector', 'jobs', 'scheduler', 'server', 'auth', 'backup', 'export'].map(function (module) { return '<option value="' + module + '"' + (query.module === module ? ' selected' : '') + '>' + module + '</option>'; }).join('') + '</select></div><div class="filter-actions"><button class="btn btn-primary btn-sm" type="submit">' + icon('filter') + '<span>筛选</span></button></div></form>';
    const rows = items.length ? items.map(function (log, index) {
      return '<tr data-log-index="' + index + '" style="cursor:pointer"><td><span class="log-time">' + fmtFull(log.created_at) + '</span></td><td><span class="log-level ' + esc(log.level) + '"><span class="status-dot ' + (log.level === 'error' ? 'error' : log.level === 'warning' ? 'warning' : 'ok') + '"></span>' + esc(log.level) + '</span></td><td><span class="log-module">' + esc(log.module) + '</span></td><td><span class="log-message">' + esc(log.message) + '</span></td><td class="cell-actions"><button class="icon-btn sm" data-log-open="' + index + '" aria-label="详情">' + icon('more') + '</button></td></tr>';
    }).join('') : '<tr><td colspan="5" class="table-empty">暂无系统日志</td></tr>';
    table = '<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>时间</th><th>级别</th><th>模块</th><th>消息</th><th></th></tr></thead><tbody>' + rows + '</tbody></table></div></section>' + pagination(data, 'logs', query);
  } else if (tab === 'audit') {
    title = '审计日志';
    subtitle = '登录、配置变更、导出、备份与用户管理操作的不可变审计轨迹。';
    const rows = items.length ? items.map(function (log, index) {
      return '<tr data-audit-index="' + index + '" style="cursor:pointer"><td><span class="log-time">' + fmtFull(log.created_at) + '</span></td><td><div class="cell-main">' + esc(log.username || 'system') + '</div><div class="cell-sub mono">' + esc(log.ip || '—') + '</div></td><td><span class="tag">' + esc(log.action) + '</span></td><td><span class="log-message">' + esc(log.target || '—') + '</span></td><td class="cell-actions"><button class="icon-btn sm" data-audit-open="' + index + '" aria-label="详情">' + icon('more') + '</button></td></tr>';
    }).join('') : '<tr><td colspan="5" class="table-empty">暂无审计事件</td></tr>';
    table = '<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>时间</th><th>操作者 / IP</th><th>动作</th><th>目标</th><th></th></tr></thead><tbody>' + rows + '</tbody></table></div></section>' + pagination(data, 'logs', query);
  } else {
    title = '源文件状态';
    subtitle = '每个被发现的原始数据源及其增量游标状态。重置游标可在 Agent 配置中执行。';
    filters = '<form id="source-filter-form" class="filter-bar"><div class="filter-field"><span class="filter-label">AGENT</span><select class="select" id="source-agent">' + agentOptions(query.agent) + '</select></div><div class="filter-actions"><button class="btn btn-primary btn-sm" type="submit">' + icon('filter') + '<span>筛选</span></button></div></form>';
    const rows = items.length ? items.map(function (source) {
      const status = source.status === 'ok' ? 'ok' : source.status === 'warning' ? 'warning' : source.status === 'error' ? 'error' : 'idle';
      return '<tr><td><div class="stats-agent-row">' + agentGlyph(source.agent_key) + '<div><div class="cell-main">' + esc(source.agent_name) + '</div><div class="cell-sub mono">' + esc(source.source_type) + '</div></div></div></td>' +
        '<td><div class="source-path" title="' + attr(source.path) + '">' + esc(source.path) + '</div></td>' +
        '<td><span class="badge ' + (status === 'ok' ? 'accent' : status === 'warning' ? 'amber' : status === 'error' ? 'rose' : 'neutral') + '"><span class="dot"></span>' + esc(source.status) + '</span></td>' +
        '<td><div class="cell-main">' + (source.last_synced_at ? relTime(source.last_synced_at) : '—') + '</div><div class="cell-sub">' + fmtFull(source.last_synced_at) + '</div></td>' +
        '<td class="num">' + num(source.events_imported) + '</td>' +
        '<td class="cell-sub">' + esc(source.last_error || '—') + '</td></tr>';
    }).join('') : '<tr><td colspan="6" class="table-empty">尚未发现数据源</td></tr>';
    table = '<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>Agent</th><th>来源路径</th><th>状态</th><th>最近同步</th><th class="num">已导入</th><th>错误</th></tr></thead><tbody>' + rows + '</tbody></table></div></section>' + pagination(data, 'logs', query);
  }

  const header = pageHead({
    eyebrow: 'OBSERVABILITY / AUDIT TRAIL',
    title: title,
    subtitle: subtitle,
    actions: '<button class="btn btn-ghost btn-sm" data-action="reload-page">' + icon('refresh') + '<span>刷新</span></button>'
  });

  setView('<div class="page">' + header + '<div class="toolbar-row">' + tabs + '<span class="section-meta mono">' + num(data.total) + ' RECORDS · PAGE ' + data.page + '/' + data.pages + '</span></div>' + filters + table + '</div>', function (root, sig) {
    root.addEventListener('submit', function (event) {
      const logForm = event.target.closest('#log-filter-form');
      if (logForm) {
        event.preventDefault();
        navigate('logs', { tab: 'system', level: root.querySelector('#log-level').value, module: root.querySelector('#log-module').value }, true);
        return;
      }
      const sourceForm = event.target.closest('#source-filter-form');
      if (sourceForm) {
        event.preventDefault();
        navigate('logs', { tab: 'sources', agent: root.querySelector('#source-agent').value }, true);
      }
    }, { signal: sig });

    root.addEventListener('click', function (event) {
      const system = event.target.closest('[data-log-open], [data-log-index]');
      if (system && tab === 'system') {
        const index = Number(system.dataset.logOpen !== undefined ? system.dataset.logOpen : system.dataset.logIndex);
        if (items[index]) openLog(items[index]);
        return;
      }
      const audit = event.target.closest('[data-audit-open], [data-audit-index]');
      if (audit && tab === 'audit') {
        const index = Number(audit.dataset.auditOpen !== undefined ? audit.dataset.auditOpen : audit.dataset.auditIndex);
        if (items[index]) openAudit(items[index]);
      }
    }, { signal: sig });
  });
}

function openLog(log) {
  showModal({
    title: '系统日志详情',
    subtitle: log.module + ' · ' + fmtFull(log.created_at),
    wide: true,
    body: '<div class="meta-list"><div class="meta-item"><span class="meta-key">级别</span><span class="meta-value">' + esc(log.level) + '</span></div><div class="meta-item"><span class="meta-key">消息</span><span class="meta-value">' + esc(log.message) + '</span></div></div><pre class="tool-json" style="margin-top:12px">' + esc(log.detail_json || '（无附加详情）') + '</pre>',
    footer: '<button class="btn btn-ghost" type="button" data-modal-close>关闭</button>'
  });
}

function openAudit(log) {
  showModal({
    title: '审计事件详情',
    subtitle: (log.username || 'system') + ' · ' + log.action,
    wide: true,
    body: '<div class="meta-list"><div class="meta-item"><span class="meta-key">时间</span><span class="meta-value mono">' + fmtFull(log.created_at) + '</span></div><div class="meta-item"><span class="meta-key">目标</span><span class="meta-value">' + esc(log.target || '—') + '</span></div><div class="meta-item"><span class="meta-key">IP</span><span class="meta-value mono">' + esc(log.ip || '—') + '</span></div></div><pre class="tool-json" style="margin-top:12px">' + esc(prettyJson(log.detail_json || '{}')) + '</pre>',
    footer: '<button class="btn btn-ghost" type="button" data-modal-close>关闭</button>'
  });
}
