import {
  api, state, isCurrent, setView, skeletonPage, pageHead, emptyState, icon,
  esc, attr, num, bytes, fmtDate, relTime, fmtDuration, navigate, showToast,
  confirmDialog
} from '../core.js';

const RUN_STATUS = {
  queued: { label: '排队中', cls: 'blue' },
  running: { label: '导入中', cls: 'blue' },
  success: { label: '已完成', cls: 'accent' },
  partial: { label: '部分完成', cls: 'amber' },
  failed: { label: '失败', cls: 'rose' },
  canceled: { label: '已取消', cls: 'neutral' },
  interrupted: { label: '已中断', cls: 'neutral' }
};

let chosenPath = '';

export async function render(params, token, signal) {
  setView(skeletonPage('旧数据导入'));
  try {
    const results = await Promise.all([
      api.get('/api/import/candidates'),
      api.get('/api/import/runs?limit=12')
    ]);
    if (!isCurrent(token)) return;
    draw(results[0], results[1], token, signal);
  } catch (error) {
    if (!isCurrent(token)) return;
    setView('<div class="page"><div class="panel"><div class="panel-body">' +
      emptyState('无法读取导入信息', error.message, 'alert-circle') +
      '<div style="text-align:center"><button class="btn btn-secondary btn-sm" data-action="reload-page">重新加载</button></div></div></div></div>');
  }
}

function runMeta(status) {
  return RUN_STATUS[status] || { label: status || '未知', cls: 'neutral' };
}

function runBadge(status) {
  const meta = runMeta(status);
  return '<span class="badge ' + meta.cls + '"><span class="dot"></span>' + esc(meta.label) + '</span>';
}

function draw(candidates, runsData, token, signal) {
  const items = (candidates && candidates.items) || [];
  const runs = (runsData && runsData.items) || [];
  const activeId = runsData && runsData.active_run_id ? Number(runsData.active_run_id) : 0;
  const activeRun = runs.filter(function (run) { return run.id === activeId; })[0] || null;

  if (!chosenPath) {
    chosenPath = (activeRun && activeRun.source_path) || (runs[0] && runs[0].source_path) || (items[0] && items[0].path) || '';
  }
  const sourceRows = items.length ? items.map(function (item) {
    return '<label class="intake-source' + (item.path === chosenPath ? ' active' : '') + '">' +
      '<input type="radio" name="intake-file" value="' + attr(item.path) + '"' + (item.path === chosenPath ? ' checked' : '') + '>' +
      '<div class="intake-source-body"><div class="intake-source-name">' + esc(item.name) + '</div>' +
      '<div class="intake-source-meta">' + num(item.sessions) + ' 个会话 · ' + num(item.messages) + ' 条消息 · ' + bytes(item.size_bytes) + ' · ' + relTime(item.modified_at) + '</div></div>' +
      '<span class="tag accent">预览</span></label>';
  }).join('') : '<p class="field-help">data/ 目录下没有发现 chatlog 导出库，可以直接填写文件路径。</p>';

  const runRows = runs.length ? runs.map(function (run) {
    const busy = run.status === 'queued' || run.status === 'running';
    const progress = '<div class="task-progress"><div class="progress-line"><i style="width:' + Number(run.progress || 0) + '%"></i></div><span>' + Number(run.progress || 0) + '%</span></div>';
    const errors = (run.errors || []).concat(run.warnings || []);
    const detail = errors.length ? '<div class="task-error" title="' + attr(errors.join(' · ')) + '">' + esc(errors[0]) + '</div>' : '';
    const action = busy
      ? '<button class="btn btn-ghost btn-xs" data-import-cancel="' + run.id + '">取消</button>'
      : '<button class="btn btn-ghost btn-xs" data-import-rerun="' + run.id + '">重新导入</button>';
    return '<tr><td><span class="mono dim">#' + run.id + '</span></td>' +
      '<td><div class="cell-main" title="' + attr(run.source_path) + '">' + esc(run.source_label || run.source_path) + '</div>' +
      '<div class="cell-sub">' + fmtDate(run.created_at) + '</div></td>' +
      '<td>' + runBadge(run.status) + '</td>' +
      '<td>' + (busy ? progress : '<span class="mono">' + num(run.imported_sessions) + ' 会话</span><div class="cell-sub">' + num(run.imported_events) + ' 事件</div>') + detail + '</td>' +
      '<td><span class="mono">' + num(run.duplicate_events) + '</span><div class="cell-sub">去重跳过</div></td>' +
      '<td><div class="cell-main">' + fmtDuration(run.started_at, run.finished_at) + '</div><div class="cell-sub">' + esc(run.created_by || '—') + '</div></td>' +
      '<td class="cell-actions">' + action + '</td></tr>';
  }).join('') : '<tr><td colspan="7" class="table-empty">还没有导入记录</td></tr>';

  const busy = activeRun && (activeRun.status === 'queued' || activeRun.status === 'running');
  const header = pageHead({
    eyebrow: 'ARCHIVE INTAKE / LEGACY CHATLOG',
    title: '旧数据导入',
    subtitle: '把旧版 chatlog 导出库中的会话迁入当前档案库。导入按旧库行号生成指纹，重复执行只会补齐新增内容。',
    actions: '<button class="btn btn-ghost btn-sm" data-import-refresh>' + icon('refresh') + '<span>刷新</span></button>'
  });

  const html = '<div class="page">' + header +
    '<div class="backup-hero"><div class="backup-hero-icon">' + icon('upload') + '</div>' +
    '<div class="backup-hero-copy"><h2>入库登记台</h2><p>选择旧工具导出的 .db 文件，先预览来源与规模，再决定是否同时导入推理片段。导入在后台线程执行，可以随时取消；已写入的会话不会重复。</p></div>' +
    '<div class="backup-actions"><span class="tag">' + (items.length ? items.length + ' 个候选文件' : '手动指定路径') + '</span></div></div>' +
    '<div class="intake-grid"><section class="panel"><div class="panel-head"><div><h2 class="panel-title">来源文件</h2><p class="panel-sub">自动发现 data/ 下的导出库，也可以填写绝对路径。</p></div><span class="badge neutral">SQLite</span></div>' +
    '<div class="panel-body"><div class="intake-sources" id="intake-candidates">' + sourceRows + '</div>' +
    '<form class="intake-path" id="intake-path-form"><input class="input" id="intake-path" placeholder="D:/.../chatlog-export.db" value="' + attr(chosenPath) + '" spellcheck="false">' +
    '<button class="btn btn-secondary btn-sm" type="submit">' + icon('search') + '<span>预览</span></button></form>' +
    '<p class="field-help">只读取导出库，不会修改源文件。仅支持包含 sessions 与 messages 表的 chatlog 导出格式。</p></div></section>' +
    '<aside class="panel"><div class="panel-head"><div><h2 class="panel-title">导入选项</h2><p class="panel-sub">选项在启动时固定，重新导入会沿用上次设置。</p></div></div>' +
    '<div class="panel-body"><div class="inline-switch"><div class="inline-switch-copy"><strong>同时导入推理过程</strong><span>旧库中共 26,862 条推理片段，默认跳过；开启后会作为带 reasoning 标记的助手消息写入。</span></div><label class="switch"><input id="intake-reasoning" type="checkbox"><span></span></label></div>' +
    '<div class="inline-switch"><div class="inline-switch-copy"><strong>预演模式</strong><span>只统计将要写入的会话与事件，不落库，用于确认映射是否正确。</span></div><label class="switch"><input id="intake-dry-run" type="checkbox"><span></span></label></div>' +
    '<div class="form-actions"><button class="btn btn-primary btn-sm" data-import-start' + (busy ? ' disabled' : '') + '>' + icon('play') + '<span>' + (busy ? '导入进行中' : '开始导入') + '</span></button></div>' +
    '<div id="intake-progress">' + (activeRun ? progressHtml(activeRun) : '') + '</div>' +
    '<p class="field-help">来源映射：opencode-local 与 opencode-linux 合并进 opencode，vscodecopilot 独立接入；未知来源会被跳过并记入警告。</p></div></aside></div>' +
    '<section class="panel" id="intake-preview-panel" hidden></section>' +
    '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">导入记录</h2><p class="panel-sub">最近 12 次导入，包含预演与中断记录。</p></div></div>' +
    '<div class="table-wrap"><table class="data-table"><thead><tr><th>ID</th><th>来源</th><th>状态</th><th>会话 / 事件</th><th>去重</th><th>耗时</th><th></th></tr></thead><tbody>' + runRows + '</tbody></table></div></section>' +
    '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">入库规则</h2><p class="panel-sub">迁移只做格式转换，不重写消息内容。</p></div></div><div class="panel-body"><div class="flow-steps">' +
    '<div class="flow-step"><span>01 SELECT</span><strong>选择导出库</strong><p>读取 export_meta 与 sessions 表，确认来源与规模。</p></div>' +
    '<div class="flow-step"><span>02 MAP</span><strong>映射来源</strong><p>旧 source 映射到当前 Agent，子会话与项目路径保留在元数据里。</p></div>' +
    '<div class="flow-step"><span>03 DEDUPE</span><strong>按行号去重</strong><p>以旧库 row_id 作为事件指纹，重复导入不会产生副本。</p></div>' +
    '</div></div></section></div>';

  setView(html, function (root, sig) {
    const pathInput = root.querySelector('#intake-path');
    const previewPanel = root.querySelector('#intake-preview-panel');
    const runsById = {};
    runs.forEach(function (run) { runsById[run.id] = run; });

    function options() {
      return {
        include_reasoning: !!root.querySelector('#intake-reasoning').checked,
        dry_run: !!root.querySelector('#intake-dry-run').checked
      };
    }

    function setActiveSource() {
      root.querySelectorAll('.intake-source').forEach(function (label) {
        const radio = label.querySelector('input');
        label.classList.toggle('active', !!radio && radio.checked);
      });
    }

    async function loadPreview() {
      if (!chosenPath) {
        showToast('请先选择或填写导出文件路径', 'error');
        return;
      }
      previewPanel.hidden = false;
      previewPanel.innerHTML = '<div class="panel-head"><div><h2 class="panel-title">来源预览</h2><p class="panel-sub">正在读取导出文件…</p></div></div>' +
        '<div class="panel-body"><div class="progress-line"><i style="width:35%"></i></div></div>';
      try {
        const data = await api.get('/api/import/preview?path=' + encodeURIComponent(chosenPath));
        if (!isCurrent(token)) return;
        previewPanel.innerHTML = previewHtml(data);
      } catch (error) {
        previewPanel.innerHTML = '<div class="panel-head"><div><h2 class="panel-title">来源预览</h2><p class="panel-sub">读取失败</p></div></div>' +
          '<div class="panel-body">' + emptyState('无法预览该文件', error.message, 'alert-circle') + '</div>';
      }
    }

    root.addEventListener('change', function (event) {
      const radio = event.target.closest('input[name="intake-file"]');
      if (!radio) return;
      chosenPath = radio.value;
      if (pathInput) pathInput.value = chosenPath;
      setActiveSource();
      loadPreview();
    }, { signal: sig });

    root.addEventListener('submit', function (event) {
      const form = event.target.closest('#intake-path-form');
      if (!form) return;
      event.preventDefault();
      chosenPath = (pathInput && pathInput.value || '').trim();
      loadPreview();
    }, { signal: sig });

    root.addEventListener('click', async function (event) {
      const refresh = event.target.closest('[data-import-refresh]');
      if (refresh) { event.preventDefault(); navigate('import', {}, true); return; }
      const start = event.target.closest('[data-import-start]');
      if (start) {
        event.preventDefault();
        await startImport(root, token, chosenPath, options(), start);
        return;
      }
      const cancel = event.target.closest('[data-import-cancel]');
      if (cancel) {
        event.preventDefault();
        cancel.disabled = true;
        try {
          await api.post('/api/import/runs/' + cancel.dataset.importCancel + '/cancel', {});
          showToast('已请求取消导入', 'success');
        } catch (error) {
          cancel.disabled = false;
          showToast(error.message, 'error', '取消失败');
        }
        return;
      }
      const rerun = event.target.closest('[data-import-rerun]');
      if (rerun) {
        event.preventDefault();
        const run = runsById[Number(rerun.dataset.importRerun)];
        if (!run) return;
        const ok = await confirmDialog({
          title: '重新导入 #' + run.id,
          body: '将以相同选项再次读取 ' + esc(run.source_label) + '，已存在的会话与事件会自动去重。',
          confirmText: '重新导入'
        });
        if (!ok) return;
        await startImport(root, token, run.source_path, run.options || {}, rerun);
      }
    }, { signal: sig });

    if (chosenPath) loadPreview();
    if (busy && activeId) pollRun(root, token, activeId);
  });
}

function previewHtml(data) {
  const totals = data.totals || {};
  const sources = data.sources || [];
  const sizeLine = bytes(data.size_bytes) + (data.exported_at ? ' · 导出于 ' + relTime(data.exported_at) : '') + (data.tool_version ? ' · v' + esc(data.tool_version) : '');
  const rows = sources.map(function (item) {
    const agent = item.supported
      ? '<span class="cell-main">' + esc(item.agent_key) + '</span>'
      : '<span class="tag rose">无对应 Agent</span>';
    const range = item.first_at ? fmtDate(item.first_at) + ' — ' + fmtDate(item.last_at) : '—';
    return '<tr><td><span class="mono">' + esc(item.source) + '</span>' + (item.supported ? '' : '<div class="cell-sub">导入时跳过</div>') + '</td>' +
      '<td>' + agent + '</td>' +
      '<td class="num">' + num(item.sessions) + '</td>' +
      '<td class="num">' + num(item.messages) + '</td>' +
      '<td class="num">' + num(item.tool_calls) + ' / ' + num(item.tool_results) + '</td>' +
      '<td class="num">' + num(item.reasoning) + '</td>' +
      '<td><span class="cell-sub">' + range + '</span></td></tr>';
  }).join('');
  const warnings = (data.warnings || []).length
    ? '<p class="intake-note">' + data.warnings.map(function (item) { return esc(item); }).join('<br>') + '</p>'
    : '';
  return '<div class="panel-head"><div><h2 class="panel-title">来源预览 · ' + esc(data.name) + '</h2>' +
    '<p class="panel-sub mono">' + esc(data.path) + '</p></div><span class="tag">' + sizeLine + '</span></div>' +
    '<div class="panel-body"><div class="task-summary">' +
    '<div class="task-stat"><span>会话</span><b class="text-accent">' + num(totals.sessions) + '</b><small class="dim">SESSIONS</small></div>' +
    '<div class="task-stat"><span>消息</span><b>' + num(totals.messages) + '</b><small class="dim">MESSAGES</small></div>' +
    '<div class="task-stat"><span>工具调用 / 结果</span><b>' + num(totals.tool_calls) + ' / ' + num(totals.tool_results) + '</b><small class="dim">TOOL EVENTS</small></div>' +
    '<div class="task-stat"><span>推理片段</span><b>' + num(totals.reasoning) + '</b><small class="dim">DEFAULT SKIP</small></div></div>' +
    warnings +
    '<div class="table-wrap" style="margin-top:14px"><table class="data-table"><thead><tr><th>旧来源</th><th>Agent</th><th>会话</th><th>消息</th><th>工具调用 / 结果</th><th>推理</th><th>时间范围</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
    (sources.length ? '' : emptyState('导出库中没有会话', '确认文件是否来自旧版 chatlog 工具。', 'database')) + '</div>';
}

function progressHtml(run) {
  const status = runMeta(run.status);
  const percent = Number(run.progress || 0);
  const notes = (run.errors || []).concat(run.warnings || []).slice(0, 3);
  return '<div class="intake-progress"><div class="intake-progress-head"><span>任务 #' + run.id + ' · ' + esc(status.label) + '</span><b>' + percent + '%</b></div>' +
    '<div class="progress-line"><i style="width:' + percent + '%"></i></div>' +
    '<div class="intake-stats">' +
    '<div class="intake-stat"><span>已处理会话</span><b>' + num(run.processed_sessions) + ' / ' + num(run.total_sessions) + '</b></div>' +
    '<div class="intake-stat"><span>写入事件</span><b>' + num(run.imported_events) + '</b></div>' +
    '<div class="intake-stat"><span>去重跳过</span><b>' + num(run.duplicate_events) + '</b></div>' +
    '<div class="intake-stat"><span>跳过消息</span><b>' + num(run.skipped_messages) + '</b></div></div>' +
    (notes.length ? '<p class="intake-note">' + notes.map(function (item) { return esc(item); }).join('<br>') + '</p>' : '') + '</div>';
}

async function startImport(root, token, path, opts, button) {
  if (!path) {
    showToast('请先选择或填写导出文件路径', 'error');
    return;
  }
  if (button) button.disabled = true;
  try {
    const result = await api.post('/api/import/runs', {
      path: path,
      include_reasoning: !!opts.include_reasoning,
      dry_run: !!opts.dry_run
    });
    showToast('导入任务 #' + result.run_id + ' 已启动', 'success');
    if (result.item) {
      const box = root.querySelector('#intake-progress');
      if (box) box.innerHTML = progressHtml(result.item);
    }
    pollRun(root, token, result.run_id);
  } catch (error) {
    if (button) button.disabled = false;
    showToast(error.message, 'error', '启动失败');
  }
}

function pollRun(root, token, runId) {
  if (state.importTimer) clearInterval(state.importTimer);
  const box = root.querySelector('#intake-progress');
  async function tick() {
    if (!isCurrent(token)) {
      clearInterval(state.importTimer);
      state.importTimer = null;
      return;
    }
    try {
      const data = await api.get('/api/import/runs/' + runId);
      if (!isCurrent(token)) return;
      const run = data.item;
      if (box) box.innerHTML = progressHtml(run);
      if (run.status !== 'queued' && run.status !== 'running') {
        clearInterval(state.importTimer);
        state.importTimer = null;
        const ok = run.status === 'success' || run.status === 'partial';
        showToast(ok ? '导入完成：' + num(run.imported_events) + ' 条事件' : '导入结束：' + runMeta(run.status).label, ok ? 'success' : 'error');
        setTimeout(function () { if (isCurrent(token)) navigate('import', {}, true); }, 800);
      }
    } catch (error) {
      // Keep the last known progress; the next tick retries.
    }
  }
  tick();
  state.importTimer = setInterval(tick, 1200);
}
