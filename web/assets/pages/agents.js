import {
  api, state, isAdmin, isCurrent, setView, skeletonPage, pageHead, emptyState, icon,
  esc, attr, num, compact, agentGlyph, agentStatus, statusBadge, relTime, fmtDate,
  showToast, showModal, confirmDialog, navigate
} from '../core.js';

const FORMATS = {
  codex: 'JSONL · rollout',
  claudecode: 'JSONL · projects',
  opencode: 'SQLite · read-only',
  workbuddy: 'JSONL · projects',
  deepseekharness: 'JSONL · zstd',
  zcode: 'SQLite · read-only',
  vscodecopilot: 'JSONL · workspaceStorage'
};

export async function render(params, token, signal) {
  setView(skeletonPage('Agent 接入'));
  let data;
  try {
    data = await api.get('/api/agents');
  } catch (error) {
    if (!isCurrent(token)) return;
    setView('<div class="page"><div class="panel"><div class="panel-body">' + emptyState('Agent 配置加载失败', error.message, 'alert-circle') + '<div style="text-align:center"><button class="btn btn-secondary btn-sm" data-action="reload-page">重新加载</button></div></div></div></div>');
    return;
  }
  if (!isCurrent(token)) return;
  const agents = data.items || [];
  state.agents = agents;
  draw(agents, signal);
}

function draw(agents, signal) {
  const enabled = agents.filter(function (agent) { return agent.enabled; }).length;
  const totalEvents = agents.reduce(function (sum, agent) { return sum + Number(agent.event_count || 0); }, 0);
  const totalSessions = agents.reduce(function (sum, agent) { return sum + Number(agent.session_count || 0); }, 0);
  const errors = agents.reduce(function (sum, agent) { return sum + Number(agent.source_errors || 0); }, 0);
  const admin = isAdmin();

  const summary = '<div class="agent-summary"><span class="summary-item"><span class="status-dot ok"></span><b>' + enabled + '</b> 个已启用</span><span class="summary-sep"></span><span class="summary-item">' + icon('nodes') + '<b>' + num(agents.length) + '</b> 个适配器</span><span class="summary-sep"></span><span class="summary-item">' + icon('message') + '<b>' + compact(totalSessions) + '</b> 会话</span><span class="summary-sep"></span><span class="summary-item">' + icon('layers') + '<b>' + compact(totalEvents) + '</b> 事件</span>' + (errors ? '<span class="summary-sep"></span><span class="summary-item"><span class="status-dot error"></span><b>' + errors + '</b> 个异常源</span>' : '') + '<span class="summary-note">采集采用文件游标 + 事件指纹双重去重 · 支持增量同步</span></div>';

  const cards = agents.map(function (agent) {
    const status = agentStatus(agent.status);
    const roots = agent.roots || [];
    const activeJob = agent.active_job;
    const syncing = agent.status === 'syncing' || activeJob;
    return '<article class="agent-card ' + (agent.enabled ? '' : 'disabled ') + (syncing ? 'syncing' : '') + '" style="--agent-color:' + attr(agent.color) + '">' +
      '<div class="agent-card-head">' + agentGlyph(agent) +
      '<div class="agent-card-title"><h3>' + esc(agent.name) + '</h3><p>' + esc(agent.vendor) + ' · ' + esc(FORMATS[agent.key] || 'ADAPTER') + '</p><div class="agent-status-line"><span class="status-dot ' + status.dot + '"></span>' + esc(status.label) + ' · ' + (agent.last_success_at ? relTime(agent.last_success_at) : '尚未同步') + '</div></div>' +
      (admin ? '<label class="switch" title="' + (agent.enabled ? '停用' : '启用') + '"><input type="checkbox" data-agent-toggle="' + attr(agent.key) + '"' + (agent.enabled ? ' checked' : '') + '><span></span></label>' : '') +
      '</div>' +
      '<p class="agent-card-desc">' + esc(agent.description) + '</p>' +
      '<div class="agent-meta-grid"><div class="agent-meta-cell"><b>' + compact(agent.session_count || 0) + '</b><span>会话</span></div><div class="agent-meta-cell"><b>' + compact(agent.event_count || 0) + '</b><span>事件</span></div><div class="agent-meta-cell"><b>' + compact(agent.tool_count || 0) + '</b><span>工具调用</span></div></div>' +
      '<div class="agent-roots">' + (roots.length ? roots.slice(0, 3).map(function (root) {
        return '<span class="root-chip" title="' + attr(root) + '">' + icon('folder') + esc(shortPath(root)) + '</span>';
      }).join('') : '<span class="root-chip">' + icon('alert-circle') + '未配置数据源路径</span>') + (roots.length > 3 ? '<span class="root-chip">+' + (roots.length - 3) + '</span>' : '') + '</div>' +
      '<div class="agent-card-foot">' +
      (admin ? '<button class="btn btn-secondary btn-xs" data-agent-sync="' + attr(agent.key) + '"' + (syncing ? ' disabled' : '') + '>' + icon(syncing ? 'refresh' : 'zap') + '<span>' + (syncing ? '采集中' : '立即同步') + '</span></button><button class="btn btn-ghost btn-xs" data-agent-config="' + attr(agent.key) + '">配置</button>' : '<a class="btn btn-ghost btn-xs" href="#/sessions?agent=' + attr(agent.key) + '">查看会话' + icon('arrow-right') + '</a>') +
      '<span class="agent-last">' + (agent.last_scan_at ? fmtDate(agent.last_scan_at) : '等待首次同步') + '</span>' +
      '</div></article>';
  }).join('');

  const grid = agents.length ? '<div class="agent-grid">' + cards + '</div>' : '<div class="panel"><div class="panel-body">' + emptyState('还没有 Agent 适配器', '初始化数据目录后重试。', 'nodes') + '</div></div>';

  const header = pageHead({
    eyebrow: 'COLLECTORS / ' + agents.length + ' ADAPTERS',
    title: 'Agent 接入',
    subtitle: '自由选择需要采集的 Agent。每个适配器只读取本地记录文件，统一映射为四类规范事件；关闭某个 Agent 不会删除已归档数据。',
    actions: '<button class="btn btn-ghost btn-sm" data-action="reload-page">' + icon('refresh') + '<span>刷新状态</span></button>' + (admin ? '<button class="btn btn-primary btn-sm" data-action="sync-all">' + icon('zap') + '<span>同步全部</span></button>' : '')
  });

  setView('<div class="page">' + header + summary + grid + '</div>', function (root, signal) {
    if (!admin) return;
    root.addEventListener('click', function (event) {
      const sync = event.target.closest('[data-agent-sync]');
      if (sync) {
        event.preventDefault();
        syncAgent(sync.dataset.agentSync, sync);
        return;
      }
      const config = event.target.closest('[data-agent-config]');
      if (config) {
        event.preventDefault();
        const agent = agents.find(function (item) { return item.key === config.dataset.agentConfig; });
        if (agent) openConfig(agent);
        return;
      }
    }, { signal: signal });

    root.addEventListener('change', async function (event) {
      const toggle = event.target.closest('[data-agent-toggle]');
      if (!toggle) return;
      const key = toggle.dataset.agentToggle;
      const enabled = toggle.checked;
      toggle.disabled = true;
      try {
        await api.patch('/api/agents/' + encodeURIComponent(key), { enabled: enabled });
        showToast((enabled ? '已启用 ' : '已停用 ') + agentLabel(key), 'success');
        navigate('agents', {}, true);
      } catch (error) {
        toggle.checked = !enabled;
        toggle.disabled = false;
        showToast(error.message, 'error');
      }
    }, { signal: signal });
  });
}

function agentLabel(key) {
  const agent = (state.agents || []).find(function (item) { return item.key === key; });
  return agent ? agent.name : key;
}

function shortPath(path) {
  let value = String(path || '').replace(/\\/g, '/');
  value = value.replace(/^[A-Za-z]:\/Users\/[^/]+\//, '~/').replace(/^\/(home|Users)\/[^/]+\//, '~/');
  if (value.length <= 38) return value;
  const parts = value.split('/').filter(Boolean);
  const tail = parts.slice(-2).join('/');
  return tail.length + 2 <= 38 ? '…/' + tail : '…/' + parts[parts.length - 1];
}

async function syncAgent(key, button) {
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<span class="spinner tiny"></span><span>排队中</span>';
  try {
    const result = await api.post('/api/agents/' + encodeURIComponent(key) + '/sync', {});
    showToast(result.notice || '采集任务已排队', result.notice ? 'info' : 'success');
    setTimeout(function () { navigate('agents', {}, true); }, 650);
  } catch (error) {
    button.disabled = false;
    button.innerHTML = original;
    showToast(error.message, 'error', '同步失败');
  }
}

function openConfig(agent) {
  const config = agent.config || {};
  const body = '<form id="agent-config-form"><div class="inline-switch" style="margin-top:0"><div class="inline-switch-copy"><strong>启用采集</strong><span>停用后自动调度会跳过该 Agent，已归档数据仍然保留。</span></div><label class="switch"><input id="agent-enabled" type="checkbox"' + (agent.enabled ? ' checked' : '') + '><span></span></label></div>' +
    '<div class="inline-switch"><div class="inline-switch-copy"><strong>自动同步</strong><span>按配置间隔由调度器增量拉取。</span></div><label class="switch"><input id="agent-auto-sync" type="checkbox"' + (config.auto_sync !== false ? ' checked' : '') + '><span></span></label></div>' +
    '<div class="settings-form" style="margin-top:14px"><div class="field"><label class="field-label" for="agent-interval">同步间隔（分钟）</label><input class="input" id="agent-interval" type="number" min="1" max="1440" value="' + Number(config.interval_minutes || 30) + '"></div><div class="field"><label class="field-label" for="agent-attempts">失败重试次数</label><input class="input" id="agent-attempts" type="number" min="1" max="10" value="' + Number(config.max_attempts || 3) + '"></div></div>' +
    '<label class="field-label" for="agent-roots">数据源路径 <span style="color:var(--muted-2);font-weight:400">（每行一个，可为目录或文件）</span></label><textarea class="textarea" id="agent-roots" rows="4" spellcheck="false">' + esc((config.roots || []).join('\n')) + '</textarea>' +
    '<label class="field-label" for="agent-patterns">文件匹配模式 <span style="color:var(--muted-2);font-weight:400">（逗号或换行分隔）</span></label><input class="input" id="agent-patterns" value="' + attr((config.patterns || []).join(', ')) + '">' +
    '<p class="field-help">采集器会对 JSONL 使用字节游标增量读取；压缩文件或 SQLite 使用时间游标，并以事件指纹做幂等去重。</p>' +
    '<p class="field-help">路径使用当前系统用户通配：' + '<code>~</code>' + ' 表示运行时的用户主目录（例如 Windows 的 ' + '<code>C:/Users/&lt;当前用户&gt;</code>' + '、Linux 的 ' + '<code>/home/&lt;当前用户&gt;</code>' + '），也支持 ' + '<code>%APPDATA%</code>' + ' / ' + '<code>$HOME</code>' + ' 等环境变量。保存时会把 ' + '<code>C:/Users/Administrator/...</code>' + ' 这类绝对路径自动改写为 ' + '<code>~/...</code>' + '，换账号或换机器后无需再改配置。</p>' +
    '</form>';
  const instance = showModal({
    title: '配置 ' + agent.name,
    subtitle: '只读接入 · 不修改任何原始 Agent 数据',
    wide: true,
    body: body,
    footer: '<button class="btn btn-ghost" type="button" data-modal-close>取消</button><button class="btn btn-ghost" type="button" id="agent-reset">重置游标</button><button class="btn btn-primary" type="submit" form="agent-config-form">保存配置</button>',
    onMount: function (element) {
      element.querySelector('#agent-config-form').addEventListener('submit', async function (event) {
        event.preventDefault();
        const roots = element.querySelector('#agent-roots').value.split('\n').map(function (line) { return line.trim(); }).filter(Boolean);
        const patterns = element.querySelector('#agent-patterns').value.split(/[\n,]/).map(function (value) { return value.trim(); }).filter(Boolean);
        const payload = {
          enabled: element.querySelector('#agent-enabled').checked,
          auto_sync: element.querySelector('#agent-auto-sync').checked,
          interval_minutes: Number(element.querySelector('#agent-interval').value || 30),
          max_attempts: Number(element.querySelector('#agent-attempts').value || 3),
          roots: roots,
          patterns: patterns
        };
        try {
          await api.patch('/api/agents/' + encodeURIComponent(agent.key), payload);
          instance.close();
          showToast(agent.name + ' 配置已保存', 'success');
          navigate('agents', {}, true);
        } catch (error) {
          showToast(error.message, 'error', '保存失败');
        }
      });
      element.querySelector('#agent-reset').addEventListener('click', async function () {
        const confirmed = await confirmDialog({
          title: '重置采集游标',
          body: '将清除 <strong>' + esc(agent.name) + '</strong> 的增量游标，下次同步会重新扫描全部源文件。事件指纹会阻止重复写入，但大文件首次重扫可能耗时较长。',
          confirmText: '重置游标',
          danger: true
        });
        if (!confirmed) return;
        try {
          await api.post('/api/agents/' + encodeURIComponent(agent.key) + '/reset', {});
          instance.close();
          showToast('游标已重置，下次同步将重新扫描', 'success');
          navigate('agents', {}, true);
        } catch (error) {
          showToast(error.message, 'error');
        }
      });
    }
  });
}
