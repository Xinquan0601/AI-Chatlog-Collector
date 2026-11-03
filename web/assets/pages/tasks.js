import {
  api, state, isAdmin, isCurrent, setView, skeletonPage, pageHead, emptyState, icon,
  esc, attr, num, agentGlyph, agentOptions, jobStatus, jobStatusBadge, fmtDate,
  fmtDuration, relTime, pagination, navigate, showToast, showModal, prettyJson
} from '../core.js';

export async function render(params, token, signal) {
  setView(skeletonPage('采集任务'));
  const query = {
    status: params.get('status') || 'all',
    agent: params.get('agent') || 'all',
    page: params.get('page') || '1'
  };
  try {
    const data = await load(query);
    if (!isCurrent(token)) return;
    draw(data, query, signal);
  } catch (error) {
    if (!isCurrent(token)) return;
    setView('<div class="page"><div class="panel"><div class="panel-body">' + emptyState('任务加载失败', error.message, 'alert-circle') + '<div style="text-align:center"><button class="btn btn-secondary btn-sm" data-action="reload-page">重新加载</button></div></div></div></div>');
  }
}

async function load(query) {
  return api.get('/api/tasks' + '?' + new URLSearchParams({
    status: query.status,
    agent: query.agent,
    page: query.page,
    page_size: '20'
  }).toString());
}

function draw(data, query, signal) {
  const items = data.items || [];
  const counts = data.counts || {};
  const active = Number(data.active || 0);
  const total = Number(data.total || 0);
  const filters = '<form id="task-filter-form" class="filter-bar"><div class="filter-field"><span class="filter-label">STATUS</span><select class="select" id="task-status"><option value="all">全部状态</option><option value="queued"' + (query.status === 'queued' ? ' selected' : '') + '>排队中</option><option value="running"' + (query.status === 'running' ? ' selected' : '') + '>采集中</option><option value="success"' + (query.status === 'success' ? ' selected' : '') + '>已完成</option><option value="partial"' + (query.status === 'partial' ? ' selected' : '') + '>部分完成</option><option value="failed"' + (query.status === 'failed' ? ' selected' : '') + '>失败</option></select></div>' +
    '<div class="filter-field"><span class="filter-label">AGENT</span><select class="select" id="task-agent">' + agentOptions(query.agent) + '</select></div>' +
    '<div class="filter-actions"><button class="btn btn-primary btn-sm" type="submit">' + icon('filter') + '<span>应用</span></button>' +
    '<button class="btn btn-ghost btn-sm" type="button" data-task-refresh>' + icon('refresh') + '<span>刷新</span></button></div></form>';

  const rows = items.length ? items.map(function (job) {
    const agent = { key: job.agent_key, name: job.agent_name, color: job.agent_color };
    const isActive = job.status === 'running' || job.status === 'queued';
    return '<tr><td><span class="mono dim">#' + job.id + '</span></td>' +
      '<td><div class="stats-agent-row">' + agentGlyph(agent) + '<span class="cell-main">' + esc(job.agent_name) + '</span></div></td>' +
      '<td>' + jobStatusBadge(job.status) + '</td>' +
      '<td><span class="tag">' + esc(job.trigger || 'manual') + '</span></td>' +
      '<td>' + (isActive ? '<div class="task-progress"><div class="progress-line"><i style="width:' + Number(job.progress || 0) + '%"></i></div><span>' + Number(job.progress || 0) + '%</span></div>' : '<span class="attempts">' + Number(job.attempts || 0) + '/' + Number(job.max_attempts || 0) + ' 次尝试</span>') + '</td>' +
      '<td><div class="task-msg" title="' + attr(job.message || '') + '">' + esc(job.message || '—') + '</div>' + (job.error ? '<div class="task-error" title="' + attr(job.error) + '">' + esc(job.error) + '</div>' : '') + '</td>' +
      '<td><div class="cell-main">' + fmtDate(job.started_at || job.queued_at) + '</div><div class="cell-sub">' + fmtDuration(job.started_at, job.finished_at) + '</div></td>' +
      '<td class="cell-actions"><button class="btn btn-ghost btn-xs" data-task-detail="' + job.id + '">详情</button>' + (isAdmin() && ['failed', 'partial', 'success'].includes(job.status) ? '<button class="btn btn-ghost btn-xs" data-task-retry="' + job.id + '">重试</button>' : '') + '</td></tr>';
  }).join('') : '<tr><td colspan="8" class="table-empty">暂无任务记录</td></tr>';

  const summary = '<div class="task-summary">' +
    '<div class="task-stat"><span>运行中</span><b class="text-accent">' + num(active) + '</b><small class="dim">QUEUED + RUNNING</small></div>' +
    '<div class="task-stat"><span>成功 / 部分完成</span><b>' + num(Number(counts.success || 0) + Number(counts.partial || 0)) + '</b><small class="dim">COMPLETED</small></div>' +
    '<div class="task-stat"><span>失败</span><b class="' + (counts.failed ? 'text-danger' : '') + '">' + num(counts.failed || 0) + '</b><small class="dim">FAILED</small></div>' +
    '<div class="task-stat"><span>任务总数</span><b>' + num(total) + '</b><small class="dim">ALL RUNS</small></div></div>';

  const header = pageHead({
    eyebrow: 'PIPELINE / INGESTION JOBS',
    title: '采集任务',
    subtitle: '查看采集任务队列、增量进度与失败原因。任务由后台工作线程执行，失败会按配置自动重试；也可以手动重跑最近结束的任务。',
    actions: '<button class="btn btn-ghost btn-sm" data-task-refresh>' + icon('refresh') + '<span>刷新</span></button>' + (isAdmin() ? '<button class="btn btn-primary btn-sm" data-action="sync-all">' + icon('zap') + '<span>同步全部</span></button>' : '')
  });

  const html = '<div class="page">' + header + summary + filters +
    '<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>ID</th><th>Agent</th><th>状态</th><th>触发</th><th>进度</th><th>消息 / 异常</th><th>开始</th><th></th></tr></thead><tbody>' + rows + '</tbody></table></div></section>' +
    pagination(data, 'tasks', query) + '</div>';

  setView(html, function (root, sig) {
    root.addEventListener('submit', function (event) {
      const form = event.target.closest('#task-filter-form');
      if (!form) return;
      event.preventDefault();
      navigate('tasks', { status: root.querySelector('#task-status').value, agent: root.querySelector('#task-agent').value }, true);
    }, { signal: sig });

    root.addEventListener('click', function (event) {
      const refresh = event.target.closest('[data-task-refresh]');
      if (refresh) { event.preventDefault(); navigate('tasks', { status: query.status, agent: query.agent, page: query.page }, true); return; }
      const detail = event.target.closest('[data-task-detail]');
      if (detail) { event.preventDefault(); openTask(detail.dataset.taskDetail); return; }
      const retry = event.target.closest('[data-task-retry]');
      if (retry) {
        event.preventDefault();
        retryTask(retry.dataset.taskRetry, retry);
      }
    }, { signal: sig });
  });

  if (active > 0) {
    if (state.taskTimer) clearInterval(state.taskTimer);
    state.taskTimer = setInterval(async function () {
      if (!isCurrent(state.renderToken)) return;
      try {
        const fresh = await load(query);
        if (!isCurrent(state.renderToken)) return;
        draw(fresh, query, signal);
      } catch (error) { /* keep last state */ }
    }, 5000);
  } else if (state.taskTimer) {
    clearInterval(state.taskTimer);
    state.taskTimer = null;
  }
}

async function retryTask(id, button) {
  button.disabled = true;
  try {
    const result = await api.post('/api/tasks/' + id + '/retry', {});
    showToast('已创建重试任务 #' + result.job_id, 'success');
    navigate('tasks', {}, true);
  } catch (error) {
    button.disabled = false;
    showToast(error.message, 'error', '重试失败');
  }
}

async function openTask(id) {
  try {
    const data = await api.get('/api/tasks/' + id);
    const job = data.item;
    const stats = job.stats || {};
    const body = '<div class="meta-list"><div class="meta-item"><span class="meta-key">Agent</span><span class="meta-value">' + esc(job.agent_name) + '</span></div><div class="meta-item"><span class="meta-key">状态</span><span class="meta-value">' + esc(jobStatus(job.status).label) + ' · ' + Number(job.attempts || 0) + '/' + Number(job.max_attempts || 0) + ' 次尝试</span></div><div class="meta-item"><span class="meta-key">触发方式</span><span class="meta-value">' + esc(job.trigger || 'manual') + '</span></div><div class="meta-item"><span class="meta-key">排队 / 开始 / 结束</span><span class="meta-value mono">' + esc(job.queued_at || '—') + ' → ' + esc(job.started_at || '—') + ' → ' + esc(job.finished_at || '—') + '</span></div></div>' +
      (job.error ? '<div class="modal-section-title" style="margin-top:15px">错误详情</div><pre class="tool-json" style="color:#ff9aa8">' + esc(job.error) + '</pre>' : '') +
      '<div class="modal-section-title" style="margin-top:15px">任务统计</div><pre class="tool-json">' + esc(prettyJson(stats)) + '</pre>';
    showModal({
      title: '采集任务 #' + job.id,
      subtitle: job.message || '',
      wide: true,
      body: body,
      footer: '<button class="btn btn-ghost" type="button" data-modal-close>关闭</button>' + (isAdmin() && ['failed', 'partial', 'success'].includes(job.status) ? '<button class="btn btn-primary" type="button" data-modal-retry>重试此任务</button>' : ''),
      onMount: function (element, close) {
        const retry = element.querySelector('[data-modal-retry]');
        if (retry) retry.addEventListener('click', function () { close(); navigate('tasks', {}, true); retryTask(job.id, retry); });
      }
    });
  } catch (error) {
    showToast(error.message, 'error', '加载任务失败');
  }
}
