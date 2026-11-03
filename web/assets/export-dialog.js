import {
  api, download, eventMeta, eventTypeLabel, eventIconName, esc, attr, icon,
  num, bytes, localInput, fromLocal, showModal, showToast
} from './core.js';

const EVENT_ORDER = ['user_message', 'assistant_message', 'tool_call', 'tool_result'];

const FORMATS = [
  { key: 'bundle', label: '分析包', hint: 'ZIP', desc: '事件 CSV + 摘要 + 字典' },
  { key: 'csv', label: 'CSV', hint: '.csv', desc: '表格，兼容 Excel' },
  { key: 'jsonl', label: 'JSONL', hint: '.jsonl', desc: '一行一个事件' },
  { key: 'json', label: 'JSON', hint: '.json', desc: '单个数组文件' }
];

const RANGES = [
  { key: 'all', label: '全部时间', note: 'ALL TIME', hours: 0 },
  { key: '24h', label: '最近 24 小时', note: 'LAST 24H', hours: 24 },
  { key: '7d', label: '最近 7 天', note: 'LAST 7D', hours: 24 * 7 },
  { key: '30d', label: '最近 30 天', note: 'LAST 30D', hours: 24 * 30 },
  { key: 'custom', label: '自定义…', note: 'CUSTOM', hours: -1 }
];

function rangeFromHours(hours) {
  return new Date(Date.now() - hours * 3600 * 1000).toISOString();
}

/**
 * Open the export dialog.
 * @param {object} options
 * @param {object} [options.context] agent/q/project/from/to/session_id inherited from the current view
 * @param {string} [options.format] initial format key
 * @param {string[]} [options.types] initially selected event types
 * @param {string} [options.scope] human readable scope note (e.g. 仅本会话)
 * @param {string} [options.title]
 */
export function openExportDialog(options) {
  const config = options || {};
  const context = config.context || {};
  const initialTypes = Array.isArray(config.types) && config.types.length ? config.types : EVENT_ORDER.slice();
  const selected = new Set(initialTypes);
  let formatKey = config.format || 'bundle';
  const initialRange = context.from || context.to ? 'custom' : (config.range || 'all');
  let rangeKey = initialRange;
  let estimate = null;
  let loading = false;
  let requestSeq = 0;
  let timer = null;

  const scopeBits = [];
  if (context.session_id) scopeBits.push('仅当前会话');
  if (context.agent) scopeBits.push('Agent：' + context.agent);
  if (context.project) scopeBits.push('项目：' + context.project);
  if (context.q) scopeBits.push('关键词：' + context.q);
  const scopeLine = config.scope || (scopeBits.length ? scopeBits.join(' · ') : '范围：全部已采集会话');

  const rangeChips = RANGES.map(function (range) {
    return '<button class="chip" type="button" data-export-range="' + range.key + '">' + esc(range.label) + '</button>';
  }).join('');

  const roleCards = EVENT_ORDER.map(function (type) {
    const meta = eventMeta(type);
    return '<label class="role-card" data-role-card="' + type + '" style="--role-color:' + attr(meta.color) + '">' +
      '<input type="checkbox" value="' + type + '" ' + (selected.has(type) ? 'checked' : '') + '>' +
      '<span class="role-card-icon">' + icon(eventIconName(type)) + '</span>' +
      '<span class="role-card-main"><span class="role-card-name">' + esc(eventTypeLabel(type)) + '</span>' +
      '<span class="role-card-count mono" data-role-count="' + type + '">—</span></span>' +
      '</label>';
  }).join('');

  const formatCards = FORMATS.map(function (format) {
    return '<button class="format-card compact" type="button" data-export-format="' + format.key + '">' +
      '<span class="format-top"><strong>' + esc(format.label) + '</strong><span class="format-icon">' + icon(format.key === 'bundle' ? 'archive' : format.key === 'csv' ? 'file-text' : 'code') + '</span></span>' +
      '<span class="format-desc">' + esc(format.desc) + '</span>' +
      '<span class="format-ext mono">' + esc(format.hint) + '</span></button>';
  }).join('');

  const body =
    '<div class="export-dialog">' +
      '<p class="export-scope">' + icon('filter') + '<span>' + esc(scopeLine) + '</span></p>' +
      '<section class="export-block">' +
        '<div class="export-block-head"><span class="filter-label">时间范围</span><span class="export-block-note mono" data-export-range-note>ALL TIME</span></div>' +
        '<div class="filter-chips" data-export-ranges>' + rangeChips + '</div>' +
        '<div class="export-dates" data-export-dates>' +
          '<div class="filter-field"><span class="filter-label">FROM</span><input class="input" type="datetime-local" data-export-from value="' + attr(localInput(context.from || '')) + '"></div>' +
          '<div class="filter-field"><span class="filter-label">TO</span><input class="input" type="datetime-local" data-export-to value="' + attr(localInput(context.to || '')) + '"></div>' +
        '</div>' +
      '</section>' +
      '<section class="export-block">' +
        '<div class="export-block-head"><span class="filter-label">事件角色</span>' +
          '<div class="export-block-tools"><button class="link-btn" type="button" data-export-roles="all">全选</button><button class="link-btn" type="button" data-export-roles="none">清空</button></div></div>' +
        '<div class="export-roles" data-export-roles-wrap>' + roleCards + '</div>' +
        '<p class="export-warn" data-export-warn hidden></p>' +
      '</section>' +
      '<section class="export-block">' +
        '<div class="export-block-head"><span class="filter-label">文件格式</span></div>' +
        '<div class="export-formats" data-export-formats>' + formatCards + '</div>' +
      '</section>' +
      '<div class="export-summary" data-export-summary>' +
        '<span class="export-summary-dot"></span><span data-export-summary-text>正在统计可导出的事件…</span>' +
      '</div>' +
    '</div>';

  const instance = showModal({
    title: config.title || '导出数据',
    subtitle: config.subtitle || '按时间范围与事件角色导出规范事件，时间统一为 UTC ISO-8601。',
    wide: true,
    body: body,
    footer: '<button class="btn btn-ghost" type="button" data-modal-close>取消</button>' +
      '<button class="btn btn-primary" type="button" data-export-commit>' + icon('download') + '<span>导出</span></button>'
  });

  const root = instance.element;
  const fromInput = root.querySelector('[data-export-from]');
  const toInput = root.querySelector('[data-export-to]');
  const datesWrap = root.querySelector('[data-export-dates]');
  const noteElement = root.querySelector('[data-export-range-note]');
  const summaryText = root.querySelector('[data-export-summary-text]');
  const summaryChip = root.querySelector('[data-export-summary]');
  const warnElement = root.querySelector('[data-export-warn]');
  const commitButton = root.querySelector('[data-export-commit]');

  if (rangeKey === 'custom' && !fromInput.value) {
    fromInput.value = localInput(rangeFromHours(24 * 30));
  }
  if (rangeKey === 'custom' && !toInput.value) {
    toInput.value = localInput(new Date().toISOString());
  }

  function paintRange() {
    root.querySelectorAll('[data-export-range]').forEach(function (chip) {
      chip.classList.toggle('active', chip.dataset.exportRange === rangeKey);
    });
    root.querySelectorAll('[data-export-range="' + rangeKey + '"]').forEach(function (chip) {
      chip.setAttribute('aria-pressed', 'true');
    });
    const custom = rangeKey === 'custom';
    datesWrap.hidden = !custom;
    const preset = RANGES.find(function (item) { return item.key === rangeKey; });
    noteElement.textContent = preset ? preset.note : 'CUSTOM';
  }

  function paintRoles() {
    root.querySelectorAll('[data-export-roles-wrap] input[type="checkbox"]').forEach(function (box) {
      const type = box.value;
      box.checked = selected.has(type);
      const card = box.closest('.role-card');
      if (card) card.classList.toggle('active', box.checked);
    });
  }

  function paintFormats() {
    root.querySelectorAll('[data-export-format]').forEach(function (card) {
      card.classList.toggle('selected', card.dataset.exportFormat === formatKey);
    });
  }

  function currentFilters() {
    let from = context.from || '';
    let to = context.to || '';
    if (rangeKey !== 'custom') {
      const preset = RANGES.find(function (item) { return item.key === rangeKey; });
      from = preset && preset.hours ? rangeFromHours(preset.hours) : '';
      to = '';
    } else {
      from = fromLocal(fromInput.value);
      to = fromLocal(toInput.value);
    }
    return {
      agent: context.agent || '',
      q: context.q || '',
      project: context.project || '',
      session_id: context.session_id || '',
      from: from,
      to: to,
      types: EVENT_ORDER.filter(function (type) { return selected.has(type); })
    };
  }

  function paramsFor(filters) {
    const params = new URLSearchParams();
    if (filters.agent) params.set('agent', filters.agent);
    if (filters.q) params.set('q', filters.q);
    if (filters.project) params.set('project', filters.project);
    if (filters.session_id) params.set('session_id', filters.session_id);
    if (filters.from) params.set('from', filters.from);
    if (filters.to) params.set('to', filters.to);
    if (filters.types.length && filters.types.length < EVENT_ORDER.length) params.set('types', filters.types.join(','));
    return params;
  }

  function paintSummary() {
    const filters = currentFilters();
    if (filters.types.length === 0) {
      summaryChip.className = 'export-summary warn';
      summaryText.textContent = '至少选择一个事件角色';
      commitButton.disabled = true;
      return;
    }
    if (filters.from && filters.to && filters.from > filters.to) {
      summaryChip.className = 'export-summary warn';
      summaryText.textContent = '开始时间晚于结束时间';
      commitButton.disabled = true;
      return;
    }
    if (loading) {
      summaryChip.className = 'export-summary';
      summaryText.textContent = '正在统计…';
      commitButton.disabled = true;
      return;
    }
    if (!estimate) {
      summaryChip.className = 'export-summary';
      summaryText.textContent = '正在统计可导出的事件…';
      commitButton.disabled = true;
      return;
    }
    if (!estimate.total) {
      summaryChip.className = 'export-summary warn';
      summaryText.textContent = '当前条件下没有可导出的事件';
      commitButton.disabled = true;
      return;
    }
    const tooLarge = estimate.exceeded;
    summaryChip.className = 'export-summary' + (tooLarge ? ' warn' : '');
    summaryText.innerHTML = tooLarge
      ? '共 <b>' + num(estimate.total) + '</b> 条事件，超过单次上限 ' + num(estimate.limit) + '，请缩小范围'
      : '将导出 <b>' + num(estimate.total) + '</b> 条事件 · ' + num(estimate.sessions) + ' 个会话 · 约 ' + bytes(estimate.bytes);
    warnElement.hidden = !tooLarge;
    if (tooLarge) warnElement.textContent = '提示：缩小时间范围或减少角色，或由管理员在「系统设置」中提高「单次导出行数上限」。';
    commitButton.disabled = tooLarge;
    EVENT_ORDER.forEach(function (type) {
      const countElement = root.querySelector('[data-role-count="' + type + '"]');
      if (countElement) countElement.textContent = num((estimate.by_type && estimate.by_type[type]) || 0);
    });
  }

  async function refresh() {
    const filters = currentFilters();
    if (filters.types.length === 0 || (filters.from && filters.to && filters.from > filters.to)) {
      estimate = null;
      loading = false;
      paintSummary();
      return;
    }
    const seq = ++requestSeq;
    loading = true;
    paintSummary();
    try {
      const params = paramsFor(filters);
      const result = await api.get('/api/export/estimate?' + params.toString());
      if (seq !== requestSeq) return;
      estimate = result;
    } catch (error) {
      if (seq !== requestSeq) return;
      estimate = null;
      summaryChip.className = 'export-summary warn';
      summaryText.textContent = '统计失败：' + error.message;
      commitButton.disabled = true;
      return;
    } finally {
      if (seq === requestSeq) loading = false;
    }
    paintSummary();
  }

  function scheduleRefresh() {
    clearTimeout(timer);
    timer = setTimeout(refresh, 220);
  }

  root.addEventListener('click', function (event) {
    const rangeChip = event.target.closest('[data-export-range]');
    if (rangeChip) {
      rangeKey = rangeChip.dataset.exportRange;
      if (rangeKey === 'custom') {
        if (!fromInput.value) fromInput.value = localInput(rangeFromHours(24 * 30));
        if (!toInput.value) toInput.value = localInput(new Date().toISOString());
      }
      paintRange();
      scheduleRefresh();
      return;
    }
    const roleToggle = event.target.closest('[data-export-roles]');
    if (roleToggle) {
      const mode = roleToggle.dataset.exportRoles;
      selected.clear();
      if (mode === 'all') EVENT_ORDER.forEach(function (type) { selected.add(type); });
      paintRoles();
      scheduleRefresh();
      return;
    }
    const formatCard = event.target.closest('[data-export-format]');
    if (formatCard) {
      formatKey = formatCard.dataset.exportFormat;
      paintFormats();
      return;
    }
    if (event.target.closest('[data-export-commit]')) {
      commit();
    }
  });

  root.addEventListener('change', function (event) {
    const box = event.target.closest('[data-export-roles-wrap] input[type="checkbox"]');
    if (box) {
      if (box.checked) selected.add(box.value); else selected.delete(box.value);
      paintRoles();
      scheduleRefresh();
      return;
    }
    if (event.target === fromInput || event.target === toInput) {
      if (rangeKey !== 'custom') rangeKey = 'custom';
      paintRange();
      scheduleRefresh();
    }
  });

  async function commit() {
    const filters = currentFilters();
    if (!filters.types.length) {
      showToast('至少选择一个事件角色', 'error');
      return;
    }
    const original = commitButton.innerHTML;
    commitButton.disabled = true;
    commitButton.innerHTML = '<span class="spinner tiny"></span><span>导出中…</span>';
    try {
      const params = paramsFor(filters);
      params.set('format', formatKey);
      if (rangeKey === '24h' || rangeKey === '7d' || rangeKey === '30d') params.set('range', rangeKey);
      const fallback = 'agenttrail-export-' + new Date().toISOString().slice(0, 10);
      const filename = await download('/api/export?' + params.toString(), fallback);
      showToast('已生成：' + filename, 'success', '导出完成');
      instance.close();
    } catch (error) {
      showToast(error.message, 'error', '导出失败');
      commitButton.disabled = false;
      commitButton.innerHTML = original;
    }
  }

  paintRange();
  paintRoles();
  paintFormats();
  refresh();
  return instance;
}
