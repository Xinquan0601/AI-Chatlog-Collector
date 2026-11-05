import {
  api, state, isCurrent, setView, emptyState, icon, esc, attr, num, compact,
  agentGlyph, agentMeta, eventMeta, eventTypeLabel, eventIconName, fmtDate, fmtFull,
  fmtTime, fmtDuration, relTime, formatRichText, prettyJson, toolSummary, pagination,
  hrefFor, toQuery, navigate, showToast, showModal, copyText, truncate
} from '../core.js';
import { openExportDialog } from '../export-dialog.js';
import { renderMarkdown } from '../markdown.js';

const EVENT_TYPES = ['user_message', 'assistant_message', 'tool_call', 'tool_result'];
const DEFAULT_EVENT_TYPES = ['user_message', 'assistant_message'];

function parseEventTypes(raw) {
  if (!raw) return DEFAULT_EVENT_TYPES.slice();
  if (raw === 'all') return EVENT_TYPES.slice();
  const picked = raw.split(',').map(function (item) { return item.trim(); }).filter(function (item) {
    return EVENT_TYPES.indexOf(item) >= 0;
  });
  if (!picked.length) return DEFAULT_EVENT_TYPES.slice();
  return EVENT_TYPES.filter(function (type) { return picked.indexOf(type) >= 0; });
}

function isDefaultSelection(types) {
  return types.length === DEFAULT_EVENT_TYPES.length && DEFAULT_EVENT_TYPES.every(function (type) {
    return types.indexOf(type) >= 0;
  });
}

// The default selection ("only messages") is the implicit state, so it stays out of the URL.
function eventTypeParam(types) {
  if (types.length >= EVENT_TYPES.length) return 'all';
  if (isDefaultSelection(types)) return '';
  return types.join(',');
}

export async function render(params, token, signal, id) {
  setView('<div class="page">' + skeletonDetail() + '</div>');
  const query = {
    types: parseEventTypes(params.get('event_type')),
    q: params.get('q') || '',
    page: params.get('page') || '1',
    event_id: params.get('event_id') || ''
  };
  let data;
  try {
    data = await api.get('/api/sessions/' + id + toQuery({
      event_type: query.types.join(','),
      q: query.q,
      page: query.page,
      page_size: 30
    }));
  } catch (error) {
    if (!isCurrent(token)) return;
    setView('<div class="page"><div class="panel"><div class="panel-body">' + emptyState('会话加载失败', error.message, 'alert-circle') + '<div style="text-align:center"><a class="btn btn-secondary btn-sm" href="#/sessions">返回会话列表</a></div></div></div></div>');
    return;
  }
  if (!isCurrent(token)) return;
  draw(data, query, id, signal);
}

function skeletonDetail() {
  return '<div class="skeleton" style="height:100px;margin-bottom:14px"></div><div class="skeleton" style="height:76px;margin-bottom:14px"></div><div class="detail-layout"><div class="skeleton" style="height:440px"></div><div class="skeleton" style="height:300px"></div></div>';
}

function draw(data, query, id, signal) {
  const session = data.session || {};
  const eventsData = data.events || { items: [], total: 0, page: 1, pages: 1 };
  const counts = data.type_counts || {};
  const tools = data.tools || [];
  const events = eventsData.items || [];
  const agent = { key: session.agent_key, name: session.agent_name, color: session.agent_color };
  const totalMessages = Number(counts.user_message || 0) + Number(counts.assistant_message || 0);
  const totalEvents = EVENT_TYPES.reduce(function (sum, key) { return sum + Number(counts[key] || 0); }, 0);
  const allTypesSelected = query.types.length === EVENT_TYPES.length;
  const defaultSelection = isDefaultSelection(query.types);

  const meta = session.metadata || {};
  const header = '<div class="detail-head"><a class="back-link" href="' + hrefFor('sessions', { agent: session.agent_key }) + '">' + icon('arrow-left') + '<span>返回会话列表</span></a>' +
    '<div class="detail-title-row">' + agentGlyph(agent) +
    '<div class="detail-title-main"><h1>' + esc(session.title || '未命名会话') + '</h1><div class="detail-meta"><span>' + esc(session.agent_name) + '</span><span class="sep"></span><span>' + esc(session.project || '未标注项目') + '</span><span class="sep"></span><span>' + fmtFull(session.started_at) + '</span><span class="sep"></span><span>' + fmtDuration(session.started_at, session.ended_at) + '</span>' + (session.model ? '<span class="sep"></span><span class="mono">' + esc(session.model) + '</span>' : '') + '</div></div>' +
    '<div class="detail-actions"><button class="btn btn-ghost btn-sm" data-copy-link title="复制当前链接">' + icon('copy') + '<span>复制链接</span></button><button class="btn btn-primary btn-sm" data-export-session="' + session.id + '" data-export-title="' + attr(session.title || '') + '">' + icon('download') + '<span>导出本会话</span></button></div></div></div>';

  const summary = '<div class="summary-strip">' +
    '<div class="summary-cell"><span>用户消息</span><b>' + num(counts.user_message) + '</b></div>' +
    '<div class="summary-cell"><span>助手消息</span><b>' + num(counts.assistant_message) + '</b></div>' +
    '<div class="summary-cell"><span>工具调用</span><b>' + num(counts.tool_call) + '</b></div>' +
    '<div class="summary-cell"><span>工具结果</span><b>' + num(counts.tool_result) + '</b></div>' +
    '<div class="summary-cell"><span>Token 合计</span><b>' + compact(Number(session.tokens_input || 0) + Number(session.tokens_output || 0)) + '</b></div></div>';

  const typeToggles = '<div class="type-filter" role="group" aria-label="按事件类型筛选">' + EVENT_TYPES.map(function (type) {
    const checked = query.types.indexOf(type) >= 0;
    const meta = eventMeta(type);
    return '<label class="type-toggle' + (checked ? ' active' : '') + '" style="--type-color:' + attr(meta.color) + '">' +
      '<input type="checkbox" value="' + type + '"' + (checked ? ' checked' : '') + '>' +
      '<span class="type-toggle-box" aria-hidden="true">' + icon('check') + '</span>' +
      '<span class="type-toggle-label">' + esc(eventTypeLabel(type)) + '</span>' +
      '<span class="type-toggle-count mono">' + num(counts[type] || 0) + '</span>' +
      '</label>';
  }).join('') + '</div>';

  const typeActions = '<div class="type-filter-actions"><button class="link-btn" type="button" data-type-preset="all"' + (allTypesSelected ? ' disabled' : '') + '>全选</button><button class="link-btn" type="button" data-type-preset="messages"' + (defaultSelection ? ' disabled' : '') + '>仅消息</button></div>';

  const toolbar = '<div class="timeline-toolbar"><div class="type-filter-wrap">' + typeToggles + typeActions + '</div>' +
    '<form id="event-filter-form" class="timeline-search"><div class="input-wrap"><span class="input-icon">' + icon('search') + '</span><input class="input" id="event-search" value="' + attr(query.q) + '" placeholder="搜索本会话内容…"></div></form></div>';

  let emptyTitle = '本会话暂无事件';
  let emptyDesc = '采集器可能只获取到会话元数据。';
  if (query.q) {
    emptyTitle = '没有匹配的事件';
    emptyDesc = '尝试调整关键词，或勾选更多事件类型。';
  } else if (!allTypesSelected) {
    emptyTitle = '当前筛选下没有事件';
    emptyDesc = '勾选更多事件类型，即可查看工具调用与结果。';
  }
  const feed = events.length ? events.map(function (event) { return renderEvent(event); }).join('') : emptyState(emptyTitle, emptyDesc, 'message');

  const pageParams = { event_type: eventTypeParam(query.types), q: query.q };
  const paginationHtml = pagination(eventsData, 'sessions/' + id, pageParams);

  const timeline = '<section class="panel timeline-panel">' + toolbar +
    '<div class="event-feed">' + feed + '</div>' + (paginationHtml ? '<div style="padding:0 16px 14px">' + paginationHtml + '</div>' : '') + '</section>';

  const metaPanel = '<section class="panel meta-card"><div class="panel-head"><div><h2 class="panel-title">会话信息</h2><p class="panel-sub">来源元数据与聚合指标</p></div></div><div class="panel-body"><div class="meta-list">' +
    metaItem('来源 Agent', session.agent_name) +
    metaItem('项目', session.project || '—') +
    metaItem('模型', session.model || '—') +
    metaItem('开始时间', fmtFull(session.started_at)) +
    metaItem('结束时间', fmtFull(session.ended_at)) +
    metaItem('持续时长', fmtDuration(session.started_at, session.ended_at)) +
    metaItem('消息 / 事件', num(totalMessages) + ' / ' + num(totalEvents)) +
    metaItem('Token 输入 / 输出', num(session.tokens_input) + ' / ' + num(session.tokens_output)) +
    metaItem('外部会话 ID', session.external_id, true) +
    metaItem('源文件', session.source_path || '—', true) +
    metaItem('工作目录', session.cwd || '—', true) +
    (meta.agent_preset ? metaItem('Agent 预设', meta.agent_preset) : '') +
    (meta.originator ? metaItem('来源客户端', meta.originator) : '') +
    '</div></div></section>';

  const toolsPanel = '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">工具使用</h2><p class="panel-sub">本会话调用的工具</p></div></div><div class="panel-body">' +
    (tools.length ? '<div class="tools-list">' + tools.map(function (tool) {
      const max = Math.max.apply(null, tools.map(function (item) { return Number(item.calls || 0); }).concat([1]));
      return '<div class="tool-stat"><span>' + esc(tool.tool_name) + '</span><div class="tool-stat-bar"><i style="width:' + Math.max(4, Math.round(Number(tool.calls || 0) * 100 / max)) + '%"></i></div><b>' + num(tool.calls) + '</b></div>';
    }).join('') + '</div>' : '<p class="modal-text">本会话没有工具调用。</p>') +
    '</div></section>';

  const exportPanel = '<section class="panel"><div class="panel-body"><div style="display:flex;gap:9px;align-items:flex-start"><span class="backup-file-icon">' + icon('archive') + '</span><div><h3 class="panel-title" style="margin-bottom:4px">分析与归档</h3><p class="modal-text">导出本会话的全部规范事件、会话摘要与数据字典，适合离线分析。</p></div></div><button class="btn btn-secondary btn-sm btn-block" style="margin-top:12px" data-export-session="' + session.id + '" data-export-title="' + attr(session.title || '') + '">' + icon('download') + '<span>导出本会话</span></button></div></section>';

  const html = '<div class="page">' + header + summary + '<div class="detail-layout"><div class="detail-main">' + timeline + '</div><aside class="detail-side">' + metaPanel + toolsPanel + exportPanel + '</aside></div></div>';

  setView(html, function (root, sig) {
    root.addEventListener('click', function (event) {
      const preset = event.target.closest('[data-type-preset]');
      if (preset) {
        event.preventDefault();
        const types = preset.dataset.typePreset === 'all' ? EVENT_TYPES.slice() : DEFAULT_EVENT_TYPES.slice();
        navigate('sessions/' + id, { event_type: eventTypeParam(types), q: query.q }, true);
        return;
      }
      const open = event.target.closest('[data-event-open]');
      if (open) { event.preventDefault(); openEvent(open.dataset.eventOpen); return; }
      const copy = event.target.closest('[data-event-copy]');
      if (copy) { event.preventDefault(); copyEvent(copy.dataset.eventCopy); return; }
      const exportButton = event.target.closest('[data-export-session]');
      if (exportButton) { event.preventDefault(); exportSession(exportButton.dataset.exportSession, exportButton.dataset.exportTitle || ''); return; }
      const copyLink = event.target.closest('[data-copy-link]');
      if (copyLink) {
        event.preventDefault();
        copyText(location.href).then(function (ok) { showToast(ok ? '会话链接已复制' : '复制失败，请手动复制地址栏链接', ok ? 'success' : 'error'); });
        return;
      }
      const target = event.target.closest('[data-scroll-event]');
      if (target) {
        const element = root.querySelector('[data-event-id="' + target.dataset.scrollEvent + '"]');
        if (element) element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, { signal: sig });

    root.addEventListener('change', function (event) {
      const box = event.target.closest('.type-filter input[type="checkbox"]');
      if (!box) return;
      const next = query.types.slice();
      const index = next.indexOf(box.value);
      if (box.checked && index < 0) {
        next.push(box.value);
      } else if (!box.checked && index >= 0) {
        if (next.length === 1) {
          box.checked = true;
          showToast('至少保留一个事件类型', 'error');
          return;
        }
        next.splice(index, 1);
      }
      const ordered = EVENT_TYPES.filter(function (type) { return next.indexOf(type) >= 0; });
      navigate('sessions/' + id, { event_type: eventTypeParam(ordered), q: query.q }, true);
    }, { signal: sig });

    root.addEventListener('submit', function (event) {
      const form = event.target.closest('#event-filter-form');
      if (!form) return;
      event.preventDefault();
      const q = root.querySelector('#event-search').value.trim();
      navigate('sessions/' + id, { event_type: eventTypeParam(query.types), q: q }, true);
    }, { signal: sig });

    if (query.event_id) {
      const target = root.querySelector('[data-event-id="' + query.event_id + '"]');
      if (target) {
        setTimeout(function () {
          target.scrollIntoView({ behavior: 'smooth', block: 'center' });
          target.style.outline = '1px solid var(--accent-line)';
          target.style.borderRadius = '12px';
        }, 80);
      } else {
        openEvent(query.event_id);
      }
    }
  });
}

function metaItem(key, value, mono) {
  return '<div class="meta-item"><span class="meta-key">' + esc(key) + '</span><span class="meta-value ' + (mono ? 'mono' : '') + '">' + esc(value) + '</span></div>';
}

function renderEvent(event) {
  const type = event.type;
  const meta = eventMeta(type);
  const iconName = eventIconName(type);
  const content = event.content || '';
  const typeLabel = eventTypeLabel(type);
  const tokens = Number(event.tokens_input || 0) + Number(event.tokens_output || 0);
  const toolStatus = event.tool_status || '';
  let body = '';

  if (type === 'tool_call') {
    const input = event.tool_input !== undefined && event.tool_input !== null ? event.tool_input : event.tool_input_json;
    const hasInput = input !== null && input !== undefined && String(prettyJson(input)).length > 0;
    body = '<div class="tool-name-row"><span class="tool-name">' + esc(event.tool_name || 'tool') + '</span>' + toolStatusBadge(toolStatus) + '</div>' +
      (hasInput ? '<div class="tool-args">' + toolSummary(input) + '</div><details class="tool-details"><summary>查看入参 JSON</summary><pre class="tool-json">' + esc(prettyJson(input)) + '</pre></details>' : '<p class="modal-text">该工具没有记录入参。</p>');
  } else if (type === 'tool_result') {
    body = '<div class="tool-name-row">' + (event.tool_name ? '<span class="tool-name">' + esc(event.tool_name) + '</span>' : '<span class="tag">TOOL RESULT</span>') + toolStatusBadge(toolStatus) + '</div>' +
      (content ? '<div class="event-content">' + formatRichText(content) + '</div>' : '<p class="modal-text">工具未返回文本输出。</p>');
  } else if (isMessageType(type)) {
    body = '<div class="event-content md">' + renderMarkdown(content || '（空消息）') + '</div>';
  } else {
    body = '<div class="event-content">' + formatRichText(content || '（空消息）') + '</div>';
  }

  const footParts = ['<span>' + fmtTime(event.timestamp) + '</span>', '<span class="mono">#' + esc(event.source_event_id || event.id) + '</span>'];
  if (tokens) footParts.push('<span class="mono">' + num(tokens) + ' tok</span>');
  if (event.truncated) footParts.push('<span class="truncated-note">内容已截断 · ' + num(event.content_length) + ' 字符</span>');

  return '<div class="event ' + esc(type) + '" data-event-id="' + event.id + '">' +
    '<div class="event-rail"><div class="event-node">' + icon(iconName) + '</div><div class="event-line"></div></div>' +
    '<div class="event-card"><div class="event-head"><span class="event-type-label" style="color:' + attr(meta.color) + '">' + esc(typeLabel) + '</span>' +
    (event.tool_name && type !== 'tool_result' ? '<span class="tag">' + esc(event.tool_name) + '</span>' : '') +
    '<span class="event-time">' + fmtTime(event.timestamp) + '</span></div>' +
    '<div class="event-body">' + body + '</div>' +
    '<div class="event-foot">' + footParts.join('') +
    (event.truncated ? '<button class="btn-link" data-event-open="' + event.id + '">查看完整内容</button>' : '<button class="btn-link" data-event-copy="' + event.id + '">复制</button>') +
    '</div></div></div>';
}

function isMessageType(type) {
  return type === 'user_message' || type === 'assistant_message';
}

function toolStatusBadge(status) {
  if (!status) return '';
  const lower = String(status).toLowerCase();
  if (['success', 'completed', 'ok'].includes(lower)) return '<span class="badge accent">' + esc(status) + '</span>';
  if (['error', 'failed'].includes(lower)) return '<span class="badge rose">' + esc(status) + '</span>';
  return '<span class="badge neutral">' + esc(status) + '</span>';
}

async function openEvent(eventId) {
  try {
    const data = await api.get('/api/events/' + eventId);
    const event = data.item;
    const input = event.tool_input !== undefined && event.tool_input !== null ? event.tool_input : event.tool_input_json;
    const raw = event.raw !== undefined && event.raw !== null ? event.raw : event.raw_json;
    const content = event.content || '';
    const contentHtml = isMessageType(event.type)
      ? '<div class="event-content md markdown-full" id="event-full-content">' + renderMarkdown(content || '（空）') + '</div>'
      : '<div class="full-content" id="event-full-content">' + esc(content || '（空）') + '</div>';
    const body = '<div class="meta-list" style="margin-bottom:12px"><div class="meta-item"><span class="meta-key">事件类型</span><span class="meta-value">' + esc(eventTypeLabel(event.type)) + ' · ' + esc(event.agent_key) + '</span></div><div class="meta-item"><span class="meta-key">时间</span><span class="meta-value mono">' + fmtFull(event.timestamp) + '</span></div>' + (event.tool_name ? '<div class="meta-item"><span class="meta-key">工具</span><span class="meta-value mono">' + esc(event.tool_name) + ' · ' + esc(event.tool_status || '—') + '</span></div>' : '') + '</div>' +
      '<div class="modal-section-title">完整内容 · ' + num(content.length) + ' 字符</div>' + contentHtml +
      (input !== null && input !== undefined ? '<details class="tool-details"><summary>工具入参 JSON</summary><pre class="tool-json">' + esc(prettyJson(input)) + '</pre></details>' : '') +
      (raw !== null && raw !== undefined ? '<details class="tool-details"><summary>原始记录</summary><pre class="tool-json">' + esc(prettyJson(raw)) + '</pre></details>' : '');
    const instance = showModal({
      title: '事件完整内容',
      subtitle: '大字段不会进入列表接口，展开后按需加载。',
      wide: true,
      body: body,
      footer: '<button class="btn btn-ghost" type="button" data-modal-close>关闭</button><button class="btn btn-primary" type="button" data-copy-event>' + icon('copy') + '<span>复制内容</span></button>',
      onMount: function (element) {
        element.querySelector('[data-copy-event]').addEventListener('click', function () {
          const text = content || element.querySelector('#event-full-content').textContent;
          copyText(text).then(function (ok) { showToast(ok ? '事件内容已复制' : '复制失败', ok ? 'success' : 'error'); });
        });
      }
    });
  } catch (error) {
    showToast(error.message, 'error', '加载事件失败');
  }
}

async function copyEvent(eventId) {
  try {
    const data = await api.get('/api/events/' + eventId);
    const ok = await copyText(data.item.content || '');
    showToast(ok ? '事件内容已复制' : '复制失败', ok ? 'success' : 'error');
  } catch (error) {
    showToast(error.message, 'error');
  }
}

function exportSession(id, title) {
  openExportDialog({
    context: { session_id: String(id) },
    scope: '仅本会话' + (title ? '：' + title : ''),
    format: 'bundle',
    title: '导出本会话',
    subtitle: '只包含这一个会话的事件，可按角色筛选后再导出。'
  });
}
