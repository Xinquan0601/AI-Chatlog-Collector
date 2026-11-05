import { api, download, queryString, ApiError } from './api.js';
import { icon, hydrateIcons } from './icons.js';

export { api, download, queryString, ApiError, icon };
export { hydrateIcons } from './icons.js';

export const view = document.getElementById('view');

export const state = {
  user: null,
  permissions: { admin: false },
  settings: {},
  agents: [],
  eventTypes: {},
  boot: null,
  route: null,
  renderToken: 0,
  currentSignal: null,
  analyticsFormat: 'bundle',
  activeJobs: 0,
  importTimer: null,
  jobTimer: null,
  taskTimer: null,
  searchTimer: null
};

export function isAdmin() {
  return !!(state.permissions && state.permissions.admin);
}

export function isCurrent(token) {
  return token === state.renderToken;
}

export function $(selector, root) {
  return (root || document).querySelector(selector);
}

export function $$(selector, root) {
  return Array.prototype.slice.call((root || document).querySelectorAll(selector));
}

export function esc(value) {
  return String(value === undefined || value === null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function attr(value) {
  return esc(value).replace(/\n/g, '&#10;');
}

export function num(value) {
  return new Intl.NumberFormat('zh-CN').format(Number(value || 0));
}

export function compact(value) {
  const n = Number(value || 0);
  if (Math.abs(n) >= 100000000) return (n / 100000000).toFixed(1).replace(/\.0$/, '') + '亿';
  if (Math.abs(n) >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, '') + '万';
  if (Math.abs(n) >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
  return String(Math.round(n));
}

export function bytes(value) {
  let n = Number(value || 0);
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return (i === 0 ? n.toFixed(0) : n.toFixed(1).replace(/\.0$/, '')) + ' ' + units[i];
}

export function pct(value, digits) {
  if (value === null || value === undefined) return '—';
  return Number(value).toFixed(digits === undefined ? 1 : digits).replace(/\.0$/, '') + '%';
}

export function truncate(text, length) {
  const value = String(text || '');
  const limit = length || 80;
  return value.length > limit ? value.slice(0, limit) + '…' : value;
}

function tz() {
  return state.settings.timezone || 'Asia/Shanghai';
}

function dateValue(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDate(value) {
  const d = dateValue(value);
  if (!d) return '—';
  try {
    return new Intl.DateTimeFormat('zh-CN', { timeZone: tz(), month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(d).replace(/\//g, '.');
  } catch (error) {
    return d.toLocaleString('zh-CN');
  }
}

export function fmtDay(value) {
  const d = dateValue(value);
  if (!d) return '—';
  try {
    return new Intl.DateTimeFormat('zh-CN', { timeZone: tz(), month: 'long', day: 'numeric' }).format(d);
  } catch (error) {
    return d.toLocaleDateString('zh-CN');
  }
}

export function fmtFull(value) {
  const d = dateValue(value);
  if (!d) return '—';
  try {
    return new Intl.DateTimeFormat('zh-CN', { timeZone: tz(), year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(d).replace(/\//g, '-');
  } catch (error) {
    return d.toLocaleString('zh-CN');
  }
}

export function fmtTime(value) {
  const d = dateValue(value);
  if (!d) return '—';
  try {
    return new Intl.DateTimeFormat('zh-CN', { timeZone: tz(), hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
  } catch (error) {
    return d.toLocaleTimeString('zh-CN');
  }
}

export function relTime(value) {
  const d = dateValue(value);
  if (!d) return '—';
  const diff = Date.now() - d.getTime();
  if (diff < 0) return fmtDate(value);
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return minutes + ' 分钟前';
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + ' 小时前';
  const days = Math.floor(hours / 24);
  if (days === 1) return '昨天';
  if (days < 7) return days + ' 天前';
  return fmtDate(value);
}

export function fmtDuration(start, end) {
  const a = dateValue(start);
  const b = dateValue(end);
  if (!a || !b) return '—';
  const seconds = Math.max(0, Math.round((b - a) / 1000));
  if (seconds < 60) return seconds + 's';
  if (seconds < 3600) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return mins + 'm' + (secs ? ' ' + secs + 's' : '');
  }
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  return hours + 'h' + (mins ? ' ' + mins + 'm' : '');
}

export function localInput(value) {
  const d = dateValue(value);
  if (!d) return '';
  const pad = function (n) { return String(n).padStart(2, '0'); };
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

export function fromLocal(value) {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '' : d.toISOString();
}

export function initial(name) {
  const value = String(name || '?').trim();
  return value.slice(0, 2).toUpperCase();
}

const AGENT_INITIALS = {
  codex: 'CX',
  claudecode: 'CC',
  opencode: 'OC',
  workbuddy: 'WB',
  deepseekharness: 'DS',
  zcode: 'ZC'
};

export function agentInitials(key) {
  return AGENT_INITIALS[key] || String(key || 'AG').slice(0, 2).toUpperCase();
}

export function agentMeta(key) {
  const found = (state.agents || []).find(function (agent) { return agent.key === key; });
  return found || { key: key, name: key || '未知 Agent', color: '#8a8478', vendor: '', status: 'idle' };
}

export function agentName(key) {
  return agentMeta(key).name;
}

export function agentGlyph(agentOrKey, extraClass) {
  const key = typeof agentOrKey === 'string' ? agentOrKey : (agentOrKey && agentOrKey.key);
  const meta = typeof agentOrKey === 'string' ? agentMeta(agentOrKey) : agentOrKey;
  const color = (meta && meta.color) || agentMeta(key).color || '#8a8478';
  return '<span class="agent-glyph ' + (extraClass || '') + '" style="--agent-color:' + attr(color) + '" title="' + attr((meta && meta.name) || key) + '">' + esc(agentInitials(key)) + '</span>';
}

export function agentOptions(selected, includeAll) {
  let html = includeAll === false ? '' : '<option value="all">全部 Agent</option>';
  (state.agents || []).forEach(function (agent) {
    html += '<option value="' + attr(agent.key) + '"' + (selected === agent.key ? ' selected' : '') + '>' + esc(agent.name) + '</option>';
  });
  return html;
}

const AGENT_STATUS = {
  ok: { label: '采集正常', cls: 'accent', dot: 'ok' },
  warning: { label: '需要关注', cls: 'amber', dot: 'warning' },
  error: { label: '采集异常', cls: 'rose', dot: 'error' },
  syncing: { label: '正在采集', cls: 'blue', dot: 'running' },
  idle: { label: '等待同步', cls: 'neutral', dot: 'idle' },
  disabled: { label: '已停用', cls: 'neutral', dot: 'disabled' }
};

export function agentStatus(status) {
  return AGENT_STATUS[status] || AGENT_STATUS.idle;
}

export function statusBadge(status, label) {
  const meta = AGENT_STATUS[status] || AGENT_STATUS.idle;
  return '<span class="badge ' + meta.cls + '"><span class="dot"></span>' + esc(label || meta.label) + '</span>';
}

const JOB_STATUS = {
  queued: { label: '排队中', cls: 'blue', dot: 'queued' },
  running: { label: '采集中', cls: 'blue', dot: 'running' },
  success: { label: '已完成', cls: 'accent', dot: 'ok' },
  partial: { label: '部分完成', cls: 'amber', dot: 'warning' },
  failed: { label: '失败', cls: 'rose', dot: 'error' },
  cancelled: { label: '已取消', cls: 'neutral', dot: 'disabled' }
};

export function jobStatus(status) {
  return JOB_STATUS[status] || JOB_STATUS.queued;
}

export function jobStatusBadge(status) {
  const meta = jobStatus(status);
  return '<span class="badge ' + meta.cls + '"><span class="dot"></span>' + esc(meta.label) + '</span>';
}

const EVENT_FALLBACK = {
  user_message: { label: '用户消息', short: 'USER', color: '#2f63c8' },
  assistant_message: { label: '助手消息', short: 'ASSISTANT', color: '#0b7a63' },
  tool_call: { label: '工具调用', short: 'TOOL CALL', color: '#a8720f' },
  tool_result: { label: '工具结果', short: 'TOOL RESULT', color: '#6b4bd6' }
};

export function eventMeta(type) {
  const fromServer = state.eventTypes && state.eventTypes[type];
  return Object.assign({}, EVENT_FALLBACK[type] || EVENT_FALLBACK.user_message, fromServer || {});
}

export function eventTypeLabel(type) {
  return eventMeta(type).label;
}

export function eventIconName(type) {
  if (type === 'tool_call') return 'tool';
  if (type === 'tool_result') return 'file-text';
  if (type === 'assistant_message') return 'bot';
  return 'user-round';
}

export function toQuery(params) {
  const search = new URLSearchParams();
  Object.keys(params || {}).forEach(function (key) {
    const value = params[key];
    if (value === undefined || value === null || value === '') return;
    search.set(key, String(value));
  });
  const text = search.toString();
  return text ? '?' + text : '';
}

export function hrefFor(path, params) {
  return '#/' + path + toQuery(params);
}

export function navigate(path, params, replace) {
  const target = hrefFor(path, params);
  if (replace) {
    history.replaceState(null, '', target);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  } else if (location.hash === target) {
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  } else {
    location.hash = target;
  }
}

export function currentParams() {
  return new URLSearchParams(location.hash.split('?')[1] || '');
}

let viewAbort = null;

export function setView(html, setup) {
  if (viewAbort) viewAbort.abort();
  viewAbort = new AbortController();
  view.innerHTML = html;
  hydrateIcons(view);
  if (setup) {
    try { setup(view, viewAbort.signal); } catch (error) { console.error(error); }
  }
  fitCharts(view);
}

// Charts are generated as strings, so their width is only known after mounting.
// Re-draw each one against its real track width (and again after a resize).
export function fitCharts(root) {
  const scope = root && root.querySelectorAll ? root : document;
  scope.querySelectorAll('[data-chart]').forEach(function (host) {
    const job = chartJobs.get(host.dataset.chart);
    if (!job) return;
    const width = Math.round(host.clientWidth);
    if (!width || width < 240) return;
    const svg = host.querySelector('svg');
    if (!svg) return;
    const current = Number((svg.getAttribute('viewBox') || '').split(/[\s,]+/)[2]) || 0;
    if (Math.abs(current - width) < 8) return;
    host.innerHTML = drawStackedChart(job.daily, job.agents, job.options, width);
  });
}

if (typeof window !== 'undefined') {
  let chartRaf = 0;
  window.addEventListener('resize', function () {
    if (chartRaf) cancelAnimationFrame(chartRaf);
    chartRaf = requestAnimationFrame(function () { chartRaf = 0; fitCharts(document); });
  });
}

export function skeletonPage(headline) {
  return '<div class="page">' +
    '<div class="page-head"><div class="page-head-main"><div class="skeleton skeleton-text" style="width:110px"></div><div class="skeleton skeleton-title" style="margin-top:10px"></div><div class="skeleton skeleton-text" style="width:320px"></div></div></div>' +
    '<div class="kpi-grid">' + new Array(4).fill('<div class="skeleton skeleton-card"></div>').join('') + '</div>' +
    '<div class="overview-grid"><div class="skeleton" style="height:320px"></div><div class="skeleton" style="height:320px"></div></div>' +
    '<div class="skeleton" style="height:240px"></div>' +
    '<div class="sr-only">' + esc(headline || '加载中') + '</div></div>';
}

export function emptyState(title, description, iconName) {
  return '<div class="empty-state"><div class="empty-icon">' + icon(iconName || 'archive') + '</div><h3>' + esc(title) + '</h3><p>' + esc(description || '') + '</p></div>';
}

export function errorPage(message, actionLabel) {
  return '<div class="page"><div class="panel"><div class="panel-body"><div class="empty-panel">' + emptyState('加载失败', message || '请稍后重试', 'alert-circle') + '<button class="btn btn-secondary btn-sm" data-action="reload-page">' + icon('refresh') + '<span>' + esc(actionLabel || '重新加载') + '</span></button></div></div></div></div>';
}

export function pageHead(options) {
  return '<div class="page-head"><div class="page-head-main">' +
    '<div class="page-eyebrow"><span class="pulse-dot"></span>' + options.eyebrow + '</div>' +
    '<h1 class="page-title">' + options.title + '</h1>' +
    (options.subtitle ? '<p class="page-sub">' + options.subtitle + '</p>' : '') +
    '</div>' +
    (options.actions ? '<div class="page-actions">' + options.actions + '</div>' : '') +
    '</div>';
}

export function kpiCard(options) {
  const color = options.color || 'var(--accent)';
  return '<article class="kpi-card" style="--kpi-color:' + attr(color) + ';--kpi-bg:color-mix(in srgb, ' + attr(color) + ' 10%, transparent);--kpi-glow:color-mix(in srgb, ' + attr(color) + ' 13%, transparent)">' +
    '<div class="kpi-top"><span class="kpi-label">' + esc(options.label) + '</span><span class="kpi-icon">' + icon(options.icon || 'activity') + '</span></div>' +
    '<div class="kpi-value">' + options.value + (options.unit ? '<small>' + esc(options.unit) + '</small>' : '') + '</div>' +
    '<div class="kpi-foot">' + (options.foot || '') + '</div>' +
    (options.spark || '') +
    '</article>';
}

export function sparkline(values, color) {
  const data = (values || []).map(function (value) { return Number(value || 0); });
  if (!data.length) return '';
  const width = 70;
  const height = 25;
  const max = Math.max.apply(null, data.concat([1]));
  const step = data.length > 1 ? width / (data.length - 1) : width;
  const points = data.map(function (value, index) {
    const x = index * step;
    const y = height - 3 - (value / max) * (height - 6);
    return [x, y];
  });
  const line = points.map(function (point, index) { return (index === 0 ? 'M' : 'L') + point[0].toFixed(1) + ' ' + point[1].toFixed(1); }).join(' ');
  const area = line + ' L' + width + ' ' + height + ' L0 ' + height + ' Z';
  return '<svg class="kpi-spark" viewBox="0 0 ' + width + ' ' + height + '" fill="none" aria-hidden="true"><path d="' + area + '" fill="' + attr(color || 'var(--accent)') + '" opacity=".08"/><path d="' + line + '" stroke="' + attr(color || 'var(--accent)') + '" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" opacity=".75"/></svg>';
}

export function legendSwatch(color) {
  return '<span class="legend-swatch" style="--legend-color:' + attr(color) + '"></span>';
}

const chartJobs = new Map();
let chartSeq = 0;

// Estimated track width before the chart is in the DOM. fitCharts() replaces it with
// the measured width right after mount, so tick text always renders at ~10px.
function estimateChartWidth() {
  if (typeof window === 'undefined') return 760;
  const narrow = window.innerWidth < 760;
  return Math.max(280, Math.min(880, window.innerWidth - (narrow ? 60 : 420)));
}

export function stackedChart(daily, agents, options) {
  const opts = options || {};
  const id = 'chart-' + (++chartSeq);
  chartJobs.set(id, { daily: daily, agents: agents, options: opts });
  return '<div class="chart-host" data-chart="' + id + '">' + drawStackedChart(daily, agents, opts, opts.width || estimateChartWidth()) + '</div>';
}

function drawStackedChart(daily, agents, options, width) {
  const opts = options || {};
  const height = opts.height || 260;
  const margin = { top: 14, right: 12, bottom: 26, left: 38 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const rows = daily || [];
  if (!rows.length || rows.every(function (row) { return !row.total; })) {
    return '<div class="chart-empty">所选范围内暂无采集事件</div>';
  }
  const visibleAgents = (agents || []).filter(function (agent) {
    return rows.some(function (row) { return row.agents && row.agents[agent.key]; });
  });
  const maxTotal = Math.max.apply(null, rows.map(function (row) { return row.total || 0; }).concat([1]));
  const magnitude = Math.pow(10, Math.floor(Math.log10(maxTotal)));
  const niceMax = Math.ceil(maxTotal / magnitude) * magnitude || 1;
  const groupW = plotW / rows.length;
  const barW = Math.min(18, Math.max(5, groupW * 0.48));
  const labelEvery = Math.max(1, Math.ceil(rows.length / 8));
  let svg = '<svg class="chart-svg" viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="采集活动堆叠图">';
  for (let i = 0; i <= 4; i += 1) {
    const y = margin.top + (plotH * i / 4);
    const value = Math.round(niceMax * (4 - i) / 4);
    svg += '<line class="grid-line" x1="' + margin.left + '" y1="' + y.toFixed(1) + '" x2="' + (width - margin.right) + '" y2="' + y.toFixed(1) + '"/>';
    svg += '<text x="' + (margin.left - 7) + '" y="' + (y + 3).toFixed(1) + '" text-anchor="end">' + compact(value) + '</text>';
  }
  rows.forEach(function (row, index) {
    const x = margin.left + groupW * index + (groupW - barW) / 2;
    let cursor = margin.top + plotH;
    const tooltip = [row.day];
    let hasAny = false;
    visibleAgents.forEach(function (agent) {
      const value = (row.agents && row.agents[agent.key]) || 0;
      if (value <= 0) return;
      hasAny = true;
      const h = (value / niceMax) * plotH;
      cursor -= h;
      tooltip.push(agent.name + ': ' + num(value));
      svg += '<rect class="bar-seg" x="' + x.toFixed(1) + '" y="' + cursor.toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + Math.max(1, h).toFixed(1) + '" rx="1.5" fill="' + attr(agent.color) + '" opacity=".82"/>';
    });
    if (hasAny) {
      svg += '<rect class="hit" x="' + (margin.left + groupW * index).toFixed(1) + '" y="' + margin.top + '" width="' + groupW.toFixed(1) + '" height="' + plotH + '"><title>' + esc(tooltip.join('\n')) + '</title></rect>';
    }
    if (index % labelEvery === 0 || index === rows.length - 1) {
      const label = String(row.day || '').slice(5).replace('-', '/');
      svg += '<text x="' + (margin.left + groupW * index + groupW / 2).toFixed(1) + '" y="' + (height - 8) + '" text-anchor="middle">' + esc(label) + '</text>';
    }
  });
  svg += '<line class="axis-line" x1="' + margin.left + '" y1="' + (height - margin.bottom) + '" x2="' + (width - margin.right) + '" y2="' + (height - margin.bottom) + '"/>';
  svg += '</svg>';
  return '<div class="chart-shell">' + svg + '</div>';
}

export function donutSvg(items, total, centerLabel) {
  const size = 144;
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  const sum = Number(total || 0);
  let offset = 0;
  let circles = '';
  (items || []).forEach(function (item) {
    const value = Number(item.count || 0);
    if (!value || !sum) return;
    const length = Math.max(0, (value / sum) * circumference - 2.5);
    circles += '<circle cx="72" cy="72" r="' + radius + '" fill="none" stroke="' + attr(item.color) + '" stroke-width="13" stroke-linecap="round" stroke-dasharray="' + length.toFixed(2) + ' ' + circumference.toFixed(2) + '" stroke-dashoffset="' + (-offset).toFixed(2) + '"/>';
    offset += (value / sum) * circumference;
  });
  if (!circles) {
    circles = '<circle cx="72" cy="72" r="' + radius + '" fill="none" stroke="var(--surface-4)" stroke-width="13"/>';
  }
  return '<div class="donut-wrap"><svg viewBox="0 0 ' + size + ' ' + size + '" aria-hidden="true">' + circles + '</svg><div class="donut-center"><strong>' + compact(sum) + '</strong><span>' + esc(centerLabel || 'TOTAL') + '</span></div></div>';
}

export function typeLegend(counts) {
  const labels = { user_message: '用户消息', assistant_message: '助手消息', tool_call: '工具调用', tool_result: '工具结果' };
  const total = Object.keys(labels).reduce(function (sum, key) { return sum + Number(counts[key] || 0); }, 0);
  return Object.keys(labels).map(function (key) {
    const meta = eventMeta(key);
    const value = Number(counts[key] || 0);
    const percent = total ? Math.round(value * 100 / total) : 0;
    return '<div class="donut-legend-row"><span style="background:' + attr(meta.color) + '"></span><span>' + labels[key] + '</span><b>' + num(value) + ' · ' + percent + '%</b></div>';
  }).join('');
}

export function pagination(data, path, params) {
  if (!data || data.pages <= 1) return '';
  const current = data.page;
  const pages = data.pages;
  const values = Object.assign({}, params || {});
  delete values.page;
  const link = function (page, label, options) {
    const opts = options || {};
    return '<a class="pager-btn ' + (opts.active ? 'active' : '') + ' ' + (opts.disabled ? 'disabled' : '') + '" ' +
      (opts.disabled ? 'aria-disabled="true" tabindex="-1"' : 'href="' + hrefFor(path, Object.assign({}, values, { page: page })) + '"') + '>' + label + '</a>';
  };
  let buttons = '';
  const candidates = new Set([1, pages, current - 1, current, current + 1]);
  const sorted = Array.from(candidates).filter(function (page) { return page >= 1 && page <= pages; }).sort(function (a, b) { return a - b; });
  let previous = 0;
  sorted.forEach(function (page) {
    if (previous && page - previous > 1) buttons += '<span class="pager-btn" style="border:0;pointer-events:none">…</span>';
    buttons += link(page, String(page), { active: page === current });
    previous = page;
  });
  return '<div class="pager"><div class="pager-info">共 <b class="mono" style="color:var(--text-soft)">' + num(data.total) + '</b> 条 · 第 ' + current + ' / ' + pages + ' 页</div><div class="pager-controls">' +
    link(Math.max(1, current - 1), icon('chevron-left'), { disabled: current <= 1 }) +
    buttons +
    link(Math.min(pages, current + 1), icon('chevron-right'), { disabled: current >= pages }) +
    '</div></div>';
}

export function formatRichText(text) {
  let value = esc(text || '');
  const blocks = [];
  value = value.replace(/\x60{3}([A-Za-z0-9_+-]*)\n([\s\S]*?)\x60{3}/g, function (match, language, code) {
    blocks.push('<pre><code data-lang="' + esc(language || '') + '">' + code.replace(/\s+$/, '') + '</code></pre>');
    return '\u0001' + (blocks.length - 1) + '\u0001';
  });
  value = value.replace(/\x60([^\x60\n]+)\x60/g, function (match, code) {
    blocks.push('<code>' + code + '</code>');
    return '\u0001' + (blocks.length - 1) + '\u0001';
  });
  value = value.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  value = value.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>');
  value = value.replace(/^&gt; (.*)$/gm, '<blockquote>$1</blockquote>');
  value = value.replace(/\u0001(\d+)\u0001/g, function (match, index) { return blocks[Number(index)] || ''; });
  return value;
}

export function prettyJson(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch (error) { return value; }
  }
  try { return JSON.stringify(value, null, 2); } catch (error) { return String(value); }
}

export function toolSummary(input) {
  if (input === null || input === undefined) return '';
  let value = input;
  if (typeof value === 'string') {
    try { value = JSON.parse(value); } catch (error) { return '<span class="tool-arg">' + esc(truncate(value, 160)) + '</span>'; }
  }
  if (typeof value !== 'object' || Array.isArray(value)) return '<span class="tool-arg">' + esc(truncate(prettyJson(value), 160)) + '</span>';
  const keys = Object.keys(value).slice(0, 4);
  if (!keys.length) return '';
  return keys.map(function (key) {
    let display = value[key];
    if (typeof display === 'object') display = JSON.stringify(display);
    return '<span class="tool-arg"><b>' + esc(key) + '</b> ' + esc(truncate(String(display), 64)) + '</span>';
  }).join('');
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(String(text || ''));
    return true;
  } catch (error) {
    const area = document.createElement('textarea');
    area.value = String(text || '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    area.remove();
    return ok;
  }
}

let toastCounter = 0;

export function showToast(message, type, title) {
  const kind = type || 'info';
  const root = document.getElementById('toast-root');
  const id = 'toast-' + (++toastCounter);
  const iconName = kind === 'success' ? 'circle-check' : (kind === 'error' ? 'alert-circle' : 'info');
  const html = '<div class="toast ' + kind + '" id="' + id + '"><span class="toast-icon">' + icon(iconName) + '</span><div class="toast-content"><div class="toast-title">' + esc(title || (kind === 'success' ? '操作成功' : kind === 'error' ? '操作失败' : '提示')) + '</div><div class="toast-msg">' + esc(message || '') + '</div></div><button class="toast-close" aria-label="关闭">' + icon('x') + '</button></div>';
  root.insertAdjacentHTML('beforeend', html);
  const element = document.getElementById(id);
  const remove = function () {
    if (!element || element.classList.contains('leaving')) return;
    element.classList.add('leaving');
    setTimeout(function () { element.remove(); }, 220);
  };
  element.querySelector('.toast-close').addEventListener('click', remove);
  setTimeout(remove, kind === 'error' ? 6500 : 3800);
}

let modalInstance = null;

export function showModal(options) {
  closeModal();
  const root = document.getElementById('modal-root');
  const wide = options.wide ? ' modal-wide' : '';
  const html = '<div class="modal-backdrop" data-modal-backdrop><div class="modal' + wide + '" role="dialog" aria-modal="true" aria-label="' + attr(options.title || '对话框') + '">' +
    '<div class="modal-head"><div><h3>' + esc(options.title || '') + '</h3>' + (options.subtitle ? '<p>' + esc(options.subtitle) + '</p>' : '') + '</div><button class="icon-btn" type="button" data-modal-close aria-label="关闭">' + icon('x') + '</button></div>' +
    '<div class="modal-body">' + (options.body || '') + '</div>' +
    (options.footer ? '<div class="modal-foot">' + options.footer + '</div>' : '') +
    '</div></div>';
  root.innerHTML = html;
  document.body.style.overflow = 'hidden';
  const backdrop = root.querySelector('[data-modal-backdrop]');
  const instance = {
    root: root,
    element: root.querySelector('.modal'),
    close: function () {
      backdrop.remove();
      document.body.style.overflow = '';
      modalInstance = null;
      if (options.onClose) options.onClose();
    }
  };
  modalInstance = instance;
  backdrop.addEventListener('click', function (event) {
    if (event.target === backdrop || event.target.closest('[data-modal-close]')) instance.close();
  });
  hydrateIcons(backdrop);
  if (options.onMount) options.onMount(instance.element, instance.close);
  return instance;
}

export function closeModal() {
  if (modalInstance) {
    const instance = modalInstance;
    modalInstance = null;
    const backdrop = instance.element.parentElement;
    if (backdrop) backdrop.remove();
    document.body.style.overflow = '';
    if (instance.onClose) instance.onClose();
  }
}

export function confirmDialog(options) {
  return new Promise(function (resolve) {
    let settled = false;
    const instance = showModal({
      title: options.title || '请确认操作',
      subtitle: options.subtitle,
      body: '<p class="modal-text">' + (options.body || '') + '</p>',
      footer: '<button class="btn btn-ghost" type="button" data-modal-close>取消</button><button class="btn ' + (options.danger ? 'btn-danger' : 'btn-primary') + '" type="button" data-confirm>' + esc(options.confirmText || '确认') + '</button>',
      onClose: function () {
        if (!settled) { settled = true; resolve(false); }
      }
    });
    instance.element.querySelector('[data-confirm]').addEventListener('click', function () {
      settled = true;
      instance.close();
      resolve(true);
    });
  });
}

export function renderAgentMini(agent) {
  const status = agentStatus(agent.status);
  return '<a class="agent-mini" href="#/sessions?agent=' + attr(agent.key) + '">' +
    agentGlyph(agent) +
    '<div class="agent-mini-main"><div class="agent-mini-title">' + esc(agent.name) + '<span class="status-dot ' + status.dot + '"></span></div><div class="agent-mini-sub">' + esc(agent.vendor) + ' · ' + (agent.last_event_at ? relTime(agent.last_event_at) : '暂无数据') + '</div></div>' +
    '<div class="agent-mini-stat"><b>' + compact(agent.event_count || 0) + '</b><span>EVENTS</span></div>' +
    '</a>';
}

export function statusLabel(status) {
  return agentStatus(status).label;
}

