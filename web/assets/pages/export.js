import {
  api, state, view, isCurrent, setView, skeletonPage, pageHead, icon,
  esc, attr, num, bytes, download, showToast, agentGlyph,
  eventMeta, eventIconName
} from '../core.js';

/* ==========================================================================
   数据导出 · Export workspace
   --------------------------------------------------------------------------
   默认值、快捷选项联动、日期校验与筛选规则集中在本文件顶部，UI 与帮助面板
   都从这些定义渲染，避免文案与行为脱节。
   ========================================================================== */

const EVENT_ORDER = ['user_message', 'assistant_message', 'tool_call', 'tool_result'];

const RANGE_PRESETS = [
  { key: 'all', label: '全部日期', days: 0, hint: '不限' },
  { key: '1d', label: '最近一天', days: 1, hint: '今天' },
  { key: '7d', label: '最近一周', days: 7, hint: '含今天 7 天' },
  { key: '30d', label: '最近一个月', days: 30, hint: '含今天 30 天' }
];

const MODE_PRESETS = [
  { key: 'full', label: '全量内容', hint: '4 类消息全选' },
  { key: 'messages', label: '仅消息', hint: '用户 + 助手' }
];

const FORMATS = [
  { key: 'bundle', name: '分析包', ext: 'ZIP', icon: 'archive', desc: '事件 / 会话 / manifest / 数据字典' },
  { key: 'csv', name: 'CSV', ext: '.csv', icon: 'file-text', desc: 'Excel 直接打开的表格' },
  { key: 'jsonl', name: 'JSONL', ext: '.jsonl', icon: 'code', desc: '逐行 JSON，流式处理' },
  { key: 'json', name: 'JSON', ext: '.json', icon: 'file', desc: '完整数组，脚本读取' }
];

const DEFAULTS = {
  range: 'all',
  types: EVENT_ORDER.slice(),
  mode: 'full',
  format: 'bundle',
  keyword: ''
};

/* ---------------------------------------------------------------- date utils */

function pad2(value) {
  return String(value).padStart(2, '0');
}

function localDateKey(date) {
  return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate());
}

function todayKey() {
  return localDateKey(new Date());
}

function shiftDateKey(key, days) {
  const date = new Date(key + 'T12:00:00');
  date.setDate(date.getDate() + days);
  return localDateKey(date);
}

function dayStartIso(key) {
  const date = new Date(key + 'T00:00:00');
  return Number.isNaN(date.getTime()) ? '' : date.toISOString();
}

function dayEndIso(key) {
  const date = new Date(key + 'T23:59:59.999');
  return Number.isNaN(date.getTime()) ? '' : date.toISOString();
}

function isDateKey(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  // Reject impossible dates such as 2026-02-30 that the browser would silently roll over.
  const date = new Date(value + 'T12:00:00');
  return !Number.isNaN(date.getTime()) && localDateKey(date) === value;
}

function formatLocalStamp(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate()) +
    ' ' + pad2(date.getHours()) + ':' + pad2(date.getMinutes()) + ':' + pad2(date.getSeconds()) +
    '.' + String(date.getMilliseconds()).padStart(3, '0');
}

function presetDateRange(key) {
  const preset = RANGE_PRESETS.find(function (item) { return item.key === key; });
  if (!preset) return { from: '', to: '' };
  if (preset.key === 'all') return { from: '', to: '' };
  const today = todayKey();
  return { from: shiftDateKey(today, -(preset.days - 1)), to: today };
}

/* ------------------------------------------------------------------- state */

function createState(params) {
  const allKeys = (state.agents || []).map(function (agent) { return agent.key; });
  const agents = new Set(allKeys);
  const urlAgents = (params.get('agents') || '').split(',').filter(Boolean);
  // "all" (or no value) means no restriction, matching the API.
  if (urlAgents.length && urlAgents.indexOf('all') < 0) {
    agents.clear();
    urlAgents.forEach(function (key) {
      if (allKeys.indexOf(key) >= 0) agents.add(key);
    });
  }
  const types = new Set(DEFAULTS.types);
  const urlTypes = (params.get('types') || '').split(',').filter(function (key) {
    return EVENT_ORDER.indexOf(key) >= 0;
  });
  if (urlTypes.length) {
    types.clear();
    urlTypes.forEach(function (key) { types.add(key); });
  }
  let from = params.get('from') || '';
  let to = params.get('to') || '';
  if (from && !isDateKey(from)) from = '';
  if (to && !isDateKey(to)) to = '';
  const range = from || to ? 'custom' : DEFAULTS.range;
  const fmt = params.get('format') || DEFAULTS.format;
  return {
    agents: agents,
    allAgents: allKeys,
    types: types,
    from: from,
    to: to,
    range: range,
    format: FORMATS.some(function (item) { return item.key === fmt; }) ? fmt : DEFAULTS.format,
    keyword: params.get('q') || '',
    estimate: null,
    error: '',
    loading: false,
    exporting: false,
    seq: 0,
    timer: null
  };
}

function selectedTypes(st) {
  return EVENT_ORDER.filter(function (key) { return st.types.has(key); });
}

function modeOf(st) {
  const picked = selectedTypes(st);
  if (picked.length === EVENT_ORDER.length) return 'full';
  if (picked.length === 2 && picked.indexOf('user_message') >= 0 && picked.indexOf('assistant_message') >= 0) return 'messages';
  return 'custom';
}

function rangeBounds(st) {
  // Both bounds are inclusive: start = local 00:00:00.000, end = local 23:59:59.999.
  if (st.from && !isDateKey(st.from)) return { error: '起始日期格式无效，请重新选择。' };
  if (st.to && !isDateKey(st.to)) return { error: '结束日期格式无效，请重新选择。' };
  if (st.from && st.to && st.from > st.to) return { error: '起始日期不能晚于结束日期。' };
  return {
    from: st.from ? dayStartIso(st.from) : '',
    to: st.to ? dayEndIso(st.to) : '',
    error: ''
  };
}

function validationError(st) {
  if (!(state.agents || []).length) return '尚未发现可导出的 Agent，请先完成一次采集。';
  if (!st.agents.size) return '请至少选择一个 AGENT。';
  if (!st.types.size) return '请至少选择一类消息（用户消息 / 助手消息 / 工具调用 / 工具结果）。';
  const bounds = rangeBounds(st);
  if (bounds.error) return bounds.error;
  if (st.estimate && st.estimate.exceeded) {
    return '命中 ' + num(st.estimate.total) + ' 条，超过单次导出上限 ' + num(st.estimate.limit) + ' 条，请缩小范围。';
  }
  return '';
}

function queryParams(st) {
  const params = new URLSearchParams();
  const agents = Array.from(st.agents);
  if (agents.length && agents.length !== st.allAgents.length) params.set('agents', agents.join(','));
  const types = selectedTypes(st);
  if (types.length && types.length !== EVENT_ORDER.length) params.set('types', types.join(','));
  const bounds = rangeBounds(st);
  if (bounds.from) params.set('from', bounds.from);
  if (bounds.to) params.set('to', bounds.to);
  const keyword = (st.keyword || '').trim();
  if (keyword) params.set('q', keyword);
  return params;
}

/* ------------------------------------------------------------------ render */

export async function render(params, token, signal) {
  setView(skeletonPage('数据导出'));
  const st = createState(params);
  if (!isCurrent(token)) return;

  const agents = state.agents || [];
  const agentCards = agents.length
    ? agents.map(function (agent) {
        const color = agent.color || '#8a8478';
        return '<label class="pick-card agent-pick" data-agent-card="' + attr(agent.key) + '" style="--pick-color:' + attr(color) + '">' +
          '<input type="checkbox" data-agent-toggle="' + attr(agent.key) + '"' + (st.agents.has(agent.key) ? ' checked' : '') + '>' +
          '<span class="pick-check">' + icon('check') + '</span>' +
          agentGlyph(agent, 'pick-glyph') +
          '<span class="pick-main">' +
            '<span class="pick-name"><span class="pick-name-text">' + esc(agent.name || agent.key) + '</span></span>' +
            '<span class="pick-meta"><b data-agent-count="' + attr(agent.key) + '">—</b> 条事件 · <span data-agent-sessions="' + attr(agent.key) + '">—</span> 会话' +
            (agent.enabled ? '' : '<span class="pick-off">已停用</span>') + '</span>' +
          '</span>' +
        '</label>';
      }).join('')
    : '<div class="pick-empty">' + icon('info') + '<span>还没有发现 Agent。完成一次采集后即可在此选择数据来源。</span></div>';

  const typeCards = EVENT_ORDER.map(function (key) {
    const meta = eventMeta(key);
    return '<label class="pick-card type-pick" data-type-card="' + attr(key) + '" style="--pick-color:' + attr(meta.color) + '">' +
      '<input type="checkbox" data-type-toggle="' + attr(key) + '"' + (st.types.has(key) ? ' checked' : '') + '>' +
      '<span class="pick-check">' + icon('check') + '</span>' +
      '<span class="pick-icon">' + icon(eventIconName(key)) + '</span>' +
      '<span class="pick-main">' +
        '<span class="pick-name">' + esc(meta.label) + '</span>' +
        '<span class="pick-meta"><b data-type-count="' + attr(key) + '">—</b> 条命中</span>' +
      '</span>' +
    '</label>';
  }).join('');

  const html = '<div class="page export-page">' +
    pageHead({
      eyebrow: 'EXPORT · DATASET BUILDER',
      title: '数据导出',
      subtitle: '按 Agent、日期与消息类型组合筛选规范事件，导出为可分析的数据集。默认不设限制：全部 Agent、全部日期、全部消息类型。',
      actions: '<button class="btn btn-secondary btn-sm" type="button" data-export-reset>' + icon('rotate-ccw') + '<span>恢复默认</span></button>' +
        '<button class="btn btn-primary btn-sm" type="button" data-export-run>' + icon('download') + '<span>开始导出</span></button>'
    }) +
    '<div class="export-layout">' +
      '<div class="export-column">' +
        '<section class="panel export-panel">' +
          '<div class="panel-head"><div><h2 class="panel-title">数据来源 · AGENTS</h2>' +
            '<p class="panel-sub">可多选；组内为「或」关系，默认全部选中且不加来源限制。</p></div>' +
            '<div class="panel-actions">' +
              '<button class="link-btn" type="button" data-agent-bulk="all">全选</button>' +
              '<button class="link-btn" type="button" data-agent-bulk="none">清空</button>' +
              '<button class="link-btn" type="button" data-agent-bulk="enabled">仅启用</button>' +
            '</div></div>' +
          '<div class="panel-body">' +
            '<div class="pick-grid agent-pick-grid" id="agent-pick-grid">' + agentCards + '</div>' +
            '<p class="export-note"><span data-agent-selected>' + st.agents.size + '</span> / ' + agents.length + ' 个 Agent 已选。未选择的 Agent 不会出现在导出结果中。</p>' +
          '</div>' +
        '</section>' +

        '<section class="panel export-panel">' +
          '<div class="panel-head"><div><h2 class="panel-title">日期范围</h2>' +
            '<p class="panel-sub">起止日期均为闭区间：起始日 00:00:00.000 与结束日 23:59:59.999 都会被包含。</p></div></div>' +
          '<div class="panel-body stack">' +
            '<div class="filter-chips export-range-chips" id="range-chips">' +
              RANGE_PRESETS.map(function (preset) {
                return '<button class="chip" type="button" data-range="' + preset.key + '">' + esc(preset.label) +
                  '<span class="count">' + esc(preset.hint) + '</span></button>';
              }).join('') +
              '<span class="chip" data-custom-chip hidden>自定义</span>' +
            '</div>' +
            '<div class="export-dates">' +
              '<label class="filter-field"><span class="filter-label">起始日期（含当天 00:00:00.000）</span>' +
                '<input class="input" type="date" id="export-from" data-date="from" value="' + attr(st.from) + '"></label>' +
              '<label class="filter-field"><span class="filter-label">结束日期（含当天 23:59:59.999）</span>' +
                '<input class="input" type="date" id="export-to" data-date="to" value="' + attr(st.to) + '"></label>' +
            '</div>' +
            '<div class="export-effective" data-effective></div>' +
            '<p class="export-error" data-range-error hidden></p>' +
          '</div>' +
        '</section>' +

        '<section class="panel export-panel">' +
          '<div class="panel-head"><div><h2 class="panel-title">消息范围</h2>' +
            '<p class="panel-sub">可多选；组内为「或」关系，默认四类全部导出。</p></div>' +
            '<div class="panel-actions"><span class="mode-state" data-mode-state></span><span class="badge neutral" data-type-selected>4 / 4 类</span></div></div>' +
          '<div class="panel-body stack">' +
            '<div class="export-modes" data-mode-group>' +
              MODE_PRESETS.map(function (preset) {
                return '<button class="mode-card" type="button" data-mode="' + preset.key + '">' +
                  '<span class="mode-card-head">' + icon(preset.key === 'full' ? 'layers' : 'message') + '<strong>' + esc(preset.label) + '</strong></span>' +
                  '<span class="mode-card-hint">' + esc(preset.hint) + '</span></button>';
              }).join('') +
            '</div>' +
            '<div class="pick-grid type-pick-grid">' + typeCards + '</div>' +
            '<label class="filter-field export-keyword"><span class="filter-label">内容关键词（可选，匹配消息正文或工具名）</span>' +
              '<input class="input" type="search" id="export-keyword" placeholder="留空表示不限制内容" value="' + attr(st.keyword) + '"></label>' +
          '</div>' +
        '</section>' +
      '</div>' +

      '<aside class="export-column export-side">' +
        '<section class="panel export-panel export-summary-panel">' +
          '<div class="panel-head"><div><h2 class="panel-title">命中概览</h2>' +
            '<p class="panel-sub">筛选条件变化后自动重新试算。</p></div></div>' +
          '<div class="panel-body">' +
            '<div class="export-summary" data-summary>' +
              '<span class="export-summary-dot"></span>' +
              '<span>正在试算…</span>' +
            '</div>' +
            '<div class="export-metrics" data-metrics></div>' +
            '<div class="export-type-bars" data-type-bars></div>' +
            '<p class="export-limit" data-limit hidden></p>' +
          '</div>' +
        '</section>' +

        '<section class="panel export-panel">' +
          '<div class="panel-head"><div><h2 class="panel-title">导出格式</h2>' +
            '<p class="panel-sub">分析包适合归档；CSV / JSONL 适合直接分析。</p></div></div>' +
          '<div class="panel-body">' +
            '<div class="export-formats">' + FORMATS.map(function (item) {
              return '<button class="format-card compact" type="button" data-format="' + item.key + '">' +
                '<span class="format-top"><strong>' + esc(item.name) + '</strong><span class="format-icon">' + icon(item.icon) + '</span></span>' +
                '<span class="format-desc">' + esc(item.desc) + '</span>' +
                '<span class="format-ext">' + esc(item.ext) + '</span>' +
              '</button>';
            }).join('') + '</div>' +
            '<div class="export-actions">' +
              '<div class="export-hint">导出遵循下方的<b>筛选规则</b>；文件生成后浏览器会自动下载，临时文件随即清理。</div>' +
              '<button class="btn btn-primary btn-sm" type="button" data-export-run>' + icon('download') + '<span>开始导出</span></button>' +
            '</div>' +
            '<p class="export-error" data-export-error hidden></p>' +
          '</div>' +
        '</section>' +
      '</aside>' +
    '</div>' +

    '<section class="panel export-rules">' +
      '<div class="panel-head"><div><h2 class="panel-title">规则说明</h2>' +
        '<p class="panel-sub">默认值、快捷选项联动、日期校验与内容筛选的完整定义。</p></div></div>' +
      '<div class="panel-body export-rules-body">' +
        '<article class="rule-block">' +
          '<h3>' + icon('sliders') + '<span>各选项默认值</span></h3>' +
          '<ul>' +
            '<li><b>AGENTS</b>：全部选中（包含已停用的 Agent），即不施加来源过滤。</li>' +
            '<li><b>日期范围</b>：起止日期均留空 → 全部日期，不设上下界。</li>' +
            '<li><b>消息范围</b>：用户消息、助手消息、工具调用、工具结果 4 类全部选中。</li>' +
            '<li><b>导出模式</b>：全量内容（等价于 4 类全选）。</li>' +
            '<li><b>文件格式</b>：分析包（ZIP，含 events / sessions / manifest / 数据字典）。</li>' +
            '<li><b>内容关键词</b>：留空，不额外匹配正文或工具名。</li>' +
          '</ul>' +
        '</article>' +
        '<article class="rule-block">' +
          '<h3>' + icon('layers') + '<span>多选与快捷选项联动</span></h3>' +
          '<ul>' +
            '<li><b>时间快捷选项</b>直接改写两个日期输入框：最近一天 = 今天 1 个自然日；最近一周 = 含今天在内 7 个自然日；最近一个月 = 含今天在内 30 个自然日；全部日期 = 清空两个日期。</li>' +
            '<li>手动修改任一日期后，快捷状态转为<b>自定义</b>；两个日期都被清空时自动回到<b>全部日期</b>。</li>' +
            '<li><b>导出模式快捷选项</b>直接改写消息类型勾选：全量内容 = 4 类全选；仅消息 = 仅用户消息 + 助手消息。</li>' +
            '<li>手动勾选或取消任一消息类型后，若结果恰好等于某个预设就高亮该预设，否则显示<b>自定义</b>。</li>' +
            '<li>快捷选项只改写自己那一组条件：切换时间快捷不会改变已选消息类型，切换模式也不会改变日期。</li>' +
            '<li>AGENTS 的「全选 / 清空 / 仅启用」只改写 Agent 选择；Agent、消息类型、日期三组条件互不覆盖。</li>' +
          '</ul>' +
        '</article>' +
        '<article class="rule-block">' +
          '<h3>' + icon('calendar') + '<span>日期范围校验</span></h3>' +
          '<ul>' +
            '<li>起止日期可单独留空：只填起始日表示「从该日起」，只填结束日表示「截止到该日」。</li>' +
            '<li>起始日按本地时区当天 <b>00:00:00.000</b> 参与比较（含该时刻）；结束日按本地时区当天 <b>23:59:59.999</b> 参与比较（含该时刻），因此结束日当天的记录不会被漏掉。</li>' +
            '<li>起始日期不得晚于结束日期；两者相同表示只导出这一天。</li>' +
            '<li>日期格式非法或起止颠倒时，导出按钮禁用并提示原因，且不会发出试算或导出请求。</li>' +
            '<li>允许选择未来日期，结果可能为 0 条；没有数据时仍可导出只有表头的文件。</li>' +
            '<li>时间条件作用于事件时间（events.timestamp），不是会话创建时间；只要会话中存在命中事件，该会话就会出现在结果里。</li>' +
          '</ul>' +
        '</article>' +
        '<article class="rule-block">' +
          '<h3>' + icon('filter') + '<span>导出内容筛选规则</span></h3>' +
          '<ul>' +
            '<li>一条事件必须同时满足：Agent 属于已选集合、事件时间落在闭区间内、消息类型属于已选集合；若填写关键词，还要求 content 或 tool_name 包含该关键词。</li>' +
            '<li>同一组多选内部为「或」（任一命中即可），不同组之间为「且」（必须同时满足）。</li>' +
            '<li>导出结果按事件时间、会话、序号排序；会话表只包含命中事件所在的会话。</li>' +
            '<li>分析包（ZIP）包含 events.csv、sessions.csv、summary.json、manifest.json、data_dictionary.md，manifest 会记录本次的 Agent、日期与类型条件。</li>' +
            '<li>默认导出全部命中记录，不设行数上限；如在设置页配置了上限，超限时导出按钮禁用，需缩小日期范围、Agent 或消息类型。</li>' +
          '</ul>' +
        '</article>' +
      '</div>' +
    '</section>' +
  '</div>';

  setView(html, function (root, innerSignal) {
    bind(root, st, innerSignal);
    refreshEstimates(st);
  });
}

/* ------------------------------------------------------------------- wiring */

function bind(root, st, signal) {
  function q(selector) {
    return root.querySelector(selector);
  }
  function qa(selector) {
    return Array.from(root.querySelectorAll(selector));
  }

  function sync() {
    // AGENTS
    qa('[data-agent-card]').forEach(function (card) {
      const key = card.getAttribute('data-agent-card');
      const active = st.agents.has(key);
      card.classList.toggle('active', active);
      const input = card.querySelector('input[data-agent-toggle]');
      if (input) input.checked = active;
    });
    const agentSelected = q('[data-agent-selected]');
    if (agentSelected) agentSelected.textContent = String(st.agents.size);
    qa('[data-agent-count]').forEach(function (el) {
      const entry = estimateAgent(st, el.getAttribute('data-agent-count'));
      el.textContent = entry ? num(entry.events) : '—';
    });
    qa('[data-agent-sessions]').forEach(function (el) {
      const entry = estimateAgent(st, el.getAttribute('data-agent-sessions'));
      el.textContent = entry ? num(entry.sessions) : '—';
    });

    // Range
    qa('[data-range]').forEach(function (chip) {
      chip.classList.toggle('active', chip.getAttribute('data-range') === st.range);
    });
    const customChip = q('[data-custom-chip]');
    if (customChip) customChip.hidden = st.range !== 'custom';
    if (customChip) customChip.classList.toggle('active', st.range === 'custom');
    const bounds = rangeBounds(st);
    const effective = q('[data-effective]');
    if (effective) {
      if (bounds.error) {
        effective.innerHTML = icon('alert-circle') + '<span>' + esc(bounds.error) + '</span>';
        effective.classList.add('invalid');
      } else if (!bounds.from && !bounds.to) {
        effective.classList.remove('invalid');
        effective.innerHTML = icon('calendar') + '<span>全部日期 · 不设上下界</span>';
      } else {
        effective.classList.remove('invalid');
        effective.innerHTML = icon('clock') +
          '<span><b>' + esc(bounds.from ? formatLocalStamp(bounds.from) : '不限') + '</b>' +
          ' → <b>' + esc(bounds.to ? formatLocalStamp(bounds.to) : '不限') + '</b>' +
          '（本地时区，两端均含）</span>';
      }
    }

    // Types & mode
    const mode = modeOf(st);
    qa('[data-type-card]').forEach(function (card) {
      const key = card.getAttribute('data-type-card');
      const active = st.types.has(key);
      card.classList.toggle('active', active);
      const input = card.querySelector('input[data-type-toggle]');
      if (input) input.checked = active;
    });
    qa('[data-mode]').forEach(function (chip) {
      chip.classList.toggle('active', chip.getAttribute('data-mode') === mode);
    });
    const modeState = q('[data-mode-state]');
    if (modeState) {
      const preset = MODE_PRESETS.find(function (item) { return item.key === mode; });
      modeState.textContent = preset ? '当前：' + preset.label : '当前：自定义组合';
      modeState.classList.toggle('custom', !preset);
    }
    const typeSelected = q('[data-type-selected]');
    if (typeSelected) typeSelected.textContent = st.types.size + ' / ' + EVENT_ORDER.length + ' 类';
    qa('[data-type-count]').forEach(function (el) {
      const key = el.getAttribute('data-type-count');
      const value = st.estimate && st.estimate.by_type ? Number(st.estimate.by_type[key] || 0) : null;
      el.textContent = value === null ? '—' : num(value);
    });

    // Format
    qa('[data-format]').forEach(function (card) {
      card.classList.toggle('selected', card.getAttribute('data-format') === st.format);
    });

    // Summary
    renderSummary(root, st);

    const message = validationError(st) || st.error || '';
    const rangeError = q('[data-range-error]');
    if (rangeError) {
      const rangeOnly = bounds.error;
      rangeError.hidden = !rangeOnly;
      rangeError.textContent = rangeOnly || '';
    }
    const exportError = q('[data-export-error]');
    if (exportError) {
      exportError.hidden = !message;
      exportError.textContent = message || '';
    }
    const disabled = Boolean(message) || st.loading || st.exporting;
    qa('[data-export-run]').forEach(function (button) {
      button.disabled = disabled;
      const label = button.querySelector('span:last-child');
      if (label) {
        label.textContent = st.exporting ? '生成中…' : (st.loading ? '试算中…' : '开始导出');
      }
    });
  }

  function startRefresh() {
    if (st.timer) clearTimeout(st.timer);
    st.timer = setTimeout(function () { refreshEstimates(st); }, 180);
  }

  function setRangeFromPreset(key) {
    const preset = RANGE_PRESETS.find(function (item) { return item.key === key; });
    if (!preset) return;
    const dates = presetDateRange(key);
    st.from = dates.from;
    st.to = dates.to;
    st.range = key;
    const fromInput = q('#export-from');
    const toInput = q('#export-to');
    if (fromInput) fromInput.value = st.from;
    if (toInput) toInput.value = st.to;
    refreshEstimates(st);
  }

  // AGENT cards
  qa('input[data-agent-toggle]').forEach(function (input) {
    input.addEventListener('change', function () {
      const key = input.getAttribute('data-agent-toggle');
      if (input.checked) st.agents.add(key); else st.agents.delete(key);
      startRefresh();
      sync();
    }, { signal: signal });
  });
  qa('[data-agent-bulk]').forEach(function (button) {
    button.addEventListener('click', function () {
      const action = button.getAttribute('data-agent-bulk');
      st.agents.clear();
      (state.agents || []).forEach(function (agent) {
        if (action === 'all' || (action === 'enabled' && agent.enabled)) st.agents.add(agent.key);
      });
      startRefresh();
      sync();
    }, { signal: signal });
  });

  // Date presets
  qa('[data-range]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      setRangeFromPreset(chip.getAttribute('data-range'));
    }, { signal: signal });
  });

  // Manual date edits
  qa('input[data-date]').forEach(function (input) {
    input.addEventListener('change', function () {
      const which = input.getAttribute('data-date');
      const value = input.value || '';
      if (which === 'from') st.from = value; else st.to = value;
      st.range = st.from || st.to ? 'custom' : 'all';
      refreshEstimates(st);
    }, { signal: signal });
  });

  // Type cards
  qa('input[data-type-toggle]').forEach(function (input) {
    input.addEventListener('change', function () {
      const key = input.getAttribute('data-type-toggle');
      if (input.checked) st.types.add(key); else st.types.delete(key);
      startRefresh();
      sync();
    }, { signal: signal });
  });

  // Mode presets
  qa('[data-mode]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      st.types.clear();
      if (chip.getAttribute('data-mode') === 'messages') {
        st.types.add('user_message');
        st.types.add('assistant_message');
      } else {
        EVENT_ORDER.forEach(function (key) { st.types.add(key); });
      }
      refreshEstimates(st);
    }, { signal: signal });
  });

  // Keyword
  const keyword = q('#export-keyword');
  if (keyword) {
    keyword.addEventListener('input', function () {
      st.keyword = keyword.value;
      startRefresh();
    }, { signal: signal });
  }

  // Format
  qa('[data-format]').forEach(function (card) {
    card.addEventListener('click', function () {
      st.format = card.getAttribute('data-format');
      sync();
    }, { signal: signal });
  });

  // Reset
  qa('[data-export-reset]').forEach(function (button) {
    button.addEventListener('click', function () {
      st.agents.clear();
      (state.agents || []).forEach(function (agent) { st.agents.add(agent.key); });
      st.types.clear();
      EVENT_ORDER.forEach(function (key) { st.types.add(key); });
      st.from = '';
      st.to = '';
      st.range = DEFAULTS.range;
      st.format = DEFAULTS.format;
      st.keyword = '';
      const fromInput = q('#export-from');
      const toInput = q('#export-to');
      if (fromInput) fromInput.value = '';
      if (toInput) toInput.value = '';
      const keywordInput = q('#export-keyword');
      if (keywordInput) keywordInput.value = '';
      refreshEstimates(st);
      showToast('已恢复默认筛选：全部 Agent、全部日期、全部消息类型。', 'info', '已恢复默认');
    }, { signal: signal });
  });

  // Export
  qa('[data-export-run]').forEach(function (button) {
    button.addEventListener('click', function () {
      runExport(st, button);
    }, { signal: signal });
  });

  sync();
  activePaint = function () {
    if (!root.isConnected || !view.contains(root)) return;
    sync();
  };
}

function estimateAgent(st, key) {
  if (!st.estimate || !Array.isArray(st.estimate.by_agent)) return null;
  return st.estimate.by_agent.find(function (item) { return item.agent_key === key; }) || null;
}

function renderSummary(root, st) {
  const summary = root.querySelector('[data-summary]');
  const metrics = root.querySelector('[data-metrics]');
  const bars = root.querySelector('[data-type-bars]');
  const limit = root.querySelector('[data-limit]');
  const estimate = st.estimate;

  if (summary) {
    if (!estimate) {
      summary.classList.toggle('warn', false);
      summary.innerHTML = '<span class="export-summary-dot"></span><span>' +
        (st.loading ? '正在试算命中范围…' : '等待有效筛选条件…') + '</span>';
    } else if (estimate.exceeded) {
      summary.classList.add('warn');
      summary.innerHTML = '<span class="export-summary-dot"></span><span>命中 <b>' + num(estimate.total) +
        '</b> 条，超过设置的上限 <b>' + num(estimate.limit) + '</b> 条，需缩小范围或调高上限。</span>';
    } else {
      summary.classList.remove('warn');
      summary.innerHTML = '<span class="export-summary-dot"></span><span>将导出 <b>' + num(estimate.total) +
        '</b> 条事件 · <b>' + num(estimate.sessions) + '</b> 个会话 · 约 <b>' + bytes(estimate.bytes) + '</b></span>';
    }
  }

  if (metrics) {
    const total = estimate ? Number(estimate.total || 0) : 0;
    metrics.innerHTML = [
      metric('事件', estimate ? num(total) : '—'),
      metric('会话', estimate ? num(estimate.sessions) : '—'),
      metric('体积', estimate ? bytes(estimate.bytes) : '—'),
      metric('上限', estimate ? (estimate.limit ? num(estimate.limit) : '无限制') : '—')
    ].join('');
  }

  if (bars) {
    if (!estimate || !estimate.by_type) {
      bars.innerHTML = '';
    } else {
      const total = Math.max(Number(estimate.total || 0), 1);
      bars.innerHTML = EVENT_ORDER.map(function (key) {
        const meta = eventMeta(key);
        const count = Number(estimate.by_type[key] || 0);
        const width = Math.max(count ? 2 : 0, Math.round((count / total) * 100));
        return '<div class="type-bar-row" style="--bar-color:' + attr(meta.color) + '">' +
          '<span class="type-bar-label">' + esc(meta.label) + '</span>' +
          '<span class="type-bar-track"><i style="width:' + width + '%"></i></span>' +
          '<span class="type-bar-value">' + num(count) + '</span>' +
        '</div>';
      }).join('');
    }
  }

  if (limit) {
    if (!estimate) {
      limit.hidden = true;
    } else if (estimate.limit) {
      limit.hidden = false;
      limit.textContent = '单次导出行数上限：' + num(estimate.limit) + ' 条';
    } else {
      limit.hidden = false;
      limit.textContent = '单次导出不限行数，将包含全部命中记录';
    }
  }
}

function metric(label, value) {
  return '<div class="export-metric"><span>' + esc(label) + '</span><b>' + esc(value) + '</b></div>';
}

async function refreshEstimates(st) {
  const immediate = validationError(st);
  const bounds = rangeBounds(st);
  if (bounds.error || !st.agents.size || !st.types.size || !(state.agents || []).length) {
    st.estimate = null;
    st.error = immediate;
    st.loading = false;
    paint(st);
    return;
  }
  const seq = ++st.seq;
  st.loading = true;
  st.error = '';
  paint(st);
  try {
    const query = queryParams(st).toString();
    const data = await api.get('/api/export/estimate' + (query ? '?' + query : ''));
    if (seq !== st.seq) return;
    st.estimate = data;
    st.error = '';
  } catch (error) {
    if (seq !== st.seq) return;
    st.estimate = null;
    st.error = error.message || '试算失败';
  } finally {
    if (seq === st.seq) {
      st.loading = false;
      paint(st);
    }
  }
}

// The page is rendered once; this re-runs the DOM sync closure registered by bind().
let activePaint = null;
function paint(st) {
  if (activePaint) activePaint(st);
}

async function runExport(st, button) {
  const message = validationError(st);
  if (message) {
    showToast(message, 'error', '无法导出');
    return;
  }
  if (st.exporting) return;
  st.exporting = true;
  paint(st);
  const params = queryParams(st);
  params.set('format', st.format);
  const fallback = 'agenttrail-export-' + new Date().toISOString().slice(0, 10);
  try {
    const filename = await download('/api/export?' + params.toString(), fallback);
    showToast('已生成：' + filename, 'success', '导出完成');
  } catch (error) {
    showToast(error.message, 'error', '导出失败');
  } finally {
    st.exporting = false;
    paint(st);
  }
}

