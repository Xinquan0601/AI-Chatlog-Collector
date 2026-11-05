import {
  api, state, view, isAdmin, isCurrent, $, $$, esc, attr, num, compact, bytes, pct,
  truncate, fmtDate, fmtFull, relTime, initial, agentMeta, agentName, agentGlyph,
  statusBadge, jobStatus, showToast, showModal, closeModal, confirmDialog, navigate,
  hrefFor, toQuery, currentParams, setView, skeletonPage, pageHead, emptyState,
  hydrateIcons, icon, formatRichText, prettyJson, copyText
} from './core.js';

import { render as renderOverview } from './pages/overview.js';
import { render as renderAgents } from './pages/agents.js';
import { render as renderSessions } from './pages/sessions.js';
import { render as renderSession } from './pages/session.js';
import { render as renderAnalytics } from './pages/analytics.js';
import { render as renderExport } from './pages/export.js';
import { render as renderTasks } from './pages/tasks.js';
import { render as renderBackups } from './pages/backups.js';
import { render as renderImport } from './pages/import.js';
import { render as renderLogs } from './pages/logs.js';
import { render as renderSettings } from './pages/settings.js';
import { render as renderAbout } from './pages/about.js';

const PAGES = {
  overview: { title: '总览', nav: 'overview', admin: false, render: renderOverview },
  agents: { title: 'Agent 接入', nav: 'agents', admin: false, render: renderAgents },
  sessions: { title: '会话记录', nav: 'sessions', admin: false, render: renderSessions },
  session: { title: '会话详情', nav: 'sessions', admin: false, render: renderSession },
  analytics: { title: '统计分析', nav: 'analytics', admin: false, render: renderAnalytics },
  export: { title: '数据导出', nav: 'export', admin: false, render: renderExport },
  tasks: { title: '采集任务', nav: 'tasks', admin: false, render: renderTasks },
  backups: { title: '数据备份', nav: 'backups', admin: true, render: renderBackups },
  import: { title: '数据导入', nav: 'import', admin: true, render: renderImport },
  logs: { title: '日志与审计', nav: 'logs', admin: true, render: renderLogs },
  settings: { title: '系统设置', nav: 'settings', admin: true, render: renderSettings },
  about: { title: '架构与文档', nav: 'about', admin: false, render: renderAbout }
};

let routeAbort = null;
let lastActiveJobs = 0;
let bootstrapped = false;

// Must match chatvault.constants.API_REVISION.  The server also reports the
// revision of the process that is actually running, so a page served from a
// freshly updated checkout on top of an old process is caught at startup
// instead of failing later with 404 / 501 responses.
const API_REVISION = 1;

function parseRoute() {
  const raw = (location.hash || '#/overview').replace(/^#\/?/, '');
  const pieces = raw.split('?');
  const pathPart = pieces[0] || 'overview';
  const query = new URLSearchParams(pieces[1] || '');
  const parts = pathPart.split('/').filter(Boolean);
  if (parts[0] === 'sessions' && parts[1] && /^\d+$/.test(parts[1])) {
    return { name: 'session', id: Number(parts[1]), params: query, path: pathPart };
  }
  const name = parts[0] && PAGES[parts[0]] ? parts[0] : 'overview';
  return { name: name, id: null, params: query, path: pathPart };
}

async function route() {
  if (!bootstrapped) return;
  const parsed = parseRoute();
  if (PAGES[parsed.name].admin && !isAdmin()) {
    location.hash = '#/overview';
    return;
  }
  if (routeAbort) routeAbort.abort();
  routeAbort = new AbortController();
  state.currentSignal = routeAbort.signal;
  state.route = parsed;
  state.renderToken += 1;
  const token = state.renderToken;
  clearPageTimers();
  updateNavigation(parsed);
  document.getElementById('view').scrollTop = 0;
  window.scrollTo(0, 0);
  closeSearch();
  closeSidebar();
  const page = PAGES[parsed.name];
  setView(skeletonPage(page.title));
  try {
    await page.render(parsed.params, token, routeAbort.signal, parsed.id);
  } catch (error) {
    if (!isCurrent(token)) return;
    console.error(error);
    setView('<div class="page"><div class="panel"><div class="panel-body">' + emptyState('页面加载失败', error.message || '请稍后重试', 'alert-circle') + '<div style="text-align:center"><button class="btn btn-secondary btn-sm" data-action="reload-page">刷新页面</button></div></div></div></div>');
  }
}

function clearPageTimers() {
  if (state.taskTimer) { clearInterval(state.taskTimer); state.taskTimer = null; }
}

function updateNavigation(routeInfo) {
  const page = PAGES[routeInfo.name];
  document.getElementById('topbar-title').textContent = page.title;
  document.title = page.title + ' · 迹录 AgentTrail';
  $$('.nav-link').forEach(function (link) {
    link.classList.toggle('active', link.dataset.route === page.nav);
  });
}

function closeSidebar() {
  const shell = document.getElementById('app-shell');
  if (shell) shell.classList.remove('sidebar-open');
}

function openSidebar() {
  const shell = document.getElementById('app-shell');
  if (shell) shell.classList.add('sidebar-open');
}

function showLogin(message) {
  bootstrapped = false;
  const shell = document.getElementById('app-shell');
  const login = document.getElementById('login-view');
  if (shell) shell.hidden = true;
  if (login) login.hidden = false;
  clearPageTimers();
  if (state.jobTimer) { clearInterval(state.jobTimer); state.jobTimer = null; }
  const error = document.getElementById('login-error');
  if (error) {
    error.hidden = !message;
    error.textContent = message || '';
  }
  const password = document.getElementById('login-password');
  if (password) password.value = '';
  setTimeout(function () {
    const username = document.getElementById('login-username');
    if (username && !username.value) username.focus();
  }, 50);
}

async function enterApp(boot) {
  state.boot = boot;
  state.user = boot.user;
  state.permissions = boot.permissions || { admin: false };
  state.settings = boot.settings || {};
  state.agents = boot.agents || [];
  state.eventTypes = boot.event_types || {};
  bootstrapped = true;
  document.getElementById('login-view').hidden = true;
  document.getElementById('app-shell').hidden = false;
  updateChrome();
  hydrateIcons(document);
  if (!location.hash) history.replaceState(null, '', '#/overview');
  await route();
  startJobPolling();
  pollJobs();
  if (boot.user && boot.user.must_change_password) {
    setTimeout(function () { showToast('当前账号需要修改密码后才能继续安全使用。', 'info', '安全提醒'); }, 700);
  }
}

function updateChrome() {
  const user = state.user || {};
  const name = user.username || 'admin';
  const role = user.role === 'admin' ? '管理员' : '只读成员';
  const avatars = ['user-avatar', 'topbar-avatar'];
  avatars.forEach(function (id) {
    const element = document.getElementById(id);
    if (element) element.textContent = initial(name);
  });
  const userName = document.getElementById('user-name');
  if (userName) userName.textContent = name;
  const userRole = document.getElementById('user-role');
  if (userRole) userRole.textContent = role;
  const sidebarStatus = document.getElementById('sidebar-status');
  if (sidebarStatus) sidebarStatus.textContent = 'SQLite · WAL · 已连接';
  document.querySelectorAll('[data-admin-only]').forEach(function (element) {
    element.hidden = !isAdmin();
  });
  if (state.settings && state.settings.site_name) {
    const brand = document.querySelector('.brand-text strong');
    if (brand) brand.textContent = '迹录';
  }
  const lastAgent = (state.agents || []).filter(function (agent) { return agent.last_success_at; }).sort(function (a, b) {
    return String(b.last_success_at).localeCompare(String(a.last_success_at));
  })[0];
  const label = document.getElementById('last-sync-label');
  if (label) label.textContent = lastAgent ? '同步于 ' + relTime(lastAgent.last_success_at) : '尚未同步';
  updateJobUI();
}

function updateJobUI() {
  const active = state.activeJobs || 0;
  const indicator = document.getElementById('job-indicator');
  const badge = document.getElementById('nav-task-badge');
  const engine = document.getElementById('engine-status');
  if (indicator) {
    indicator.hidden = active === 0;
    const text = document.getElementById('job-indicator-text');
    if (text) text.textContent = active + ' 个任务运行中';
  }
  if (badge) {
    badge.hidden = active === 0;
    badge.textContent = String(active);
  }
  if (engine) engine.textContent = active ? '采集中' : '就绪';
}

async function pollJobs() {
  try {
    const data = await api.get('/api/tasks?page_size=1', { silent: true });
    const active = Number(data.active || 0);
    const previous = lastActiveJobs;
    lastActiveJobs = active;
    state.activeJobs = active;
    updateJobUI();
    if (previous > 0 && active === 0) {
      await refreshAgentsSilent();
      if (state.route && state.route.name === 'overview') route();
    }
  } catch (error) {
    if (error && error.status === 401) return;
  }
}

async function refreshAgentsSilent() {
  try {
    const data = await api.get('/api/agents', { silent: true });
    state.agents = data.items || [];
    const lastAgent = state.agents.filter(function (agent) { return agent.last_success_at; }).sort(function (a, b) {
      return String(b.last_success_at).localeCompare(String(a.last_success_at));
    })[0];
    const label = document.getElementById('last-sync-label');
    if (label) label.textContent = lastAgent ? '同步于 ' + relTime(lastAgent.last_success_at) : '尚未同步';
  } catch (error) {
    // Keep the last known agent snapshot.
  }
}

function startJobPolling() {
  if (state.jobTimer) clearInterval(state.jobTimer);
  state.jobTimer = setInterval(pollJobs, 10000);
}

async function handleLogin(event) {
  event.preventDefault();
  const username = document.getElementById('login-username').value.trim();
  const password = document.getElementById('login-password').value;
  const errorBox = document.getElementById('login-error');
  const button = document.getElementById('login-submit');
  if (!username || !password) {
    if (errorBox) { errorBox.hidden = false; errorBox.textContent = '请输入用户名和密码'; }
    return;
  }
  const original = button.innerHTML;
  button.disabled = true;
  button.innerHTML = '<span class="spinner dark"></span><span>正在验证…</span>';
  if (errorBox) errorBox.hidden = true;
  try {
    await api.post('/api/auth/login', { username: username, password: password }, { silent: true });
    const boot = await api.get('/api/bootstrap', { silent: true });
    await enterApp(boot);
    showToast('已进入工作台', 'success', '登录成功');
  } catch (error) {
    if (errorBox) {
      errorBox.hidden = false;
      errorBox.textContent = error.message || '登录失败，请检查账号和密码';
    }
  } finally {
    button.disabled = false;
    button.innerHTML = original;
    hydrateIcons(button);
  }
}

async function logout() {
  try { await api.post('/api/auth/logout', {}); } catch (error) { /* clear locally anyway */ }
  state.user = null;
  state.permissions = { admin: false };
  state.agents = [];
  showLogin('');
  showToast('已安全退出登录', 'info', '已退出');
}

function showAccountModal() {
  const user = state.user || {};
  const body = '<div class="meta-list">' +
    '<div class="meta-item"><span class="meta-key">当前账号</span><span class="meta-value">' + esc(user.username || '—') + '</span></div>' +
    '<div class="meta-item"><span class="meta-key">角色权限</span><span class="meta-value">' + (user.role === 'admin' ? '管理员 · 可配置采集与系统' : '只读成员 · 可浏览与导出') + '</span></div>' +
    '<div class="meta-item"><span class="meta-key">上次登录</span><span class="meta-value mono">' + esc(user.last_login_at ? fmtFull(user.last_login_at) : '—') + '</span></div>' +
    '</div>';
  const instance = showModal({
    title: '账号与安全',
    subtitle: '会话凭据保存在 HttpOnly Cookie 中，不会暴露给页面脚本。',
    body: body,
    footer: '<button class="btn btn-ghost" type="button" data-account-logout>退出登录</button><button class="btn btn-primary" type="button" data-account-password>修改密码</button>',
    onMount: function (element) {
      element.querySelector('[data-account-logout]').addEventListener('click', function () { instance.close(); logout(); });
      element.querySelector('[data-account-password]').addEventListener('click', function () { instance.close(); showPasswordModal(); });
    }
  });
}

function showPasswordModal() {
  const body = '<form id="password-form"><label class="field-label" for="current-password">当前密码</label><input class="input" id="current-password" type="password" autocomplete="current-password" required><label class="field-label" for="new-password">新密码</label><input class="input" id="new-password" type="password" autocomplete="new-password" minlength="8" required><p class="field-help">至少 8 个字符。修改后其他设备上的会话将失效。</p><label class="field-label" for="confirm-password">确认新密码</label><input class="input" id="confirm-password" type="password" autocomplete="new-password" minlength="8" required></form>';
  const instance = showModal({
    title: '修改密码',
    subtitle: '建议使用与其它服务不同的高强度密码。',
    body: body,
    footer: '<button class="btn btn-ghost" type="button" data-modal-close>取消</button><button class="btn btn-primary" type="submit" form="password-form">保存新密码</button>',
    onMount: function (element) {
      const form = element.querySelector('#password-form');
      form.addEventListener('submit', async function (event) {
        event.preventDefault();
        const current = element.querySelector('#current-password').value;
        const next = element.querySelector('#new-password').value;
        const confirm = element.querySelector('#confirm-password').value;
        if (next !== confirm) { showToast('两次输入的新密码不一致', 'error'); return; }
        try {
          await api.post('/api/auth/change-password', { current_password: current, new_password: next });
          instance.close();
          showToast('密码已更新，其他登录会话已失效', 'success');
        } catch (error) {
          showToast(error.message, 'error', '修改失败');
        }
      });
    }
  });
}

async function syncAll() {
  const button = document.querySelector('[data-action="sync-all"]');
  if (button) button.disabled = true;
  try {
    const result = await api.post('/api/agents/sync-all', {});
    const items = result.items || [];
    const running = items.filter(function (item) { return item.job_id; }).length;
    showToast(running ? '已排队 ' + running + ' 个采集任务' : '没有可同步的 Agent', running ? 'success' : 'info');
    pollJobs();
  } catch (error) {
    showToast(error.message, 'error', '同步失败');
  } finally {
    if (button) button.disabled = false;
  }
}

function closeSearch() {
  const result = document.getElementById('search-results');
  if (result) result.hidden = true;
}

function clearSearch() {
  const input = document.getElementById('global-search');
  if (input) input.value = '';
  closeSearch();
}

async function performSearch(query) {
  const box = document.getElementById('search-results');
  if (!box) return;
  const text = String(query || '').trim();
  if (text.length < 1) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = '<div class="search-empty">正在搜索…</div>';
  try {
    const data = await api.get('/api/search?q=' + encodeURIComponent(text) + '&limit=6');
    const sessions = data.sessions || [];
    const events = data.events || [];
    if (!sessions.length && !events.length) {
      box.innerHTML = '<div class="search-empty">没有找到与「' + esc(text) + '」相关的内容</div>';
      return;
    }
    let html = '<div class="search-pop-head"><span>SEARCH RESULTS</span><span>' + (sessions.length + events.length) + ' HITS</span></div>';
    if (sessions.length) {
      html += '<div class="search-section-label">会话</div>';
      sessions.forEach(function (item) {
        html += '<div class="search-result" data-action="search-session" data-id="' + item.id + '">' + agentGlyph(item.agent_key) +
          '<div class="search-result-main"><div class="search-result-title">' + esc(item.title) + '</div><div class="search-result-sub">' + esc(item.agent_name) + ' · ' + esc(item.project || '未标注项目') + ' · ' + fmtDate(item.started_at) + '</div></div></div>';
      });
    }
    if (events.length) {
      html += '<div class="search-section-label">消息内容</div>';
      events.forEach(function (item) {
        html += '<div class="search-result" data-action="search-event" data-id="' + item.id + '" data-session="' + item.session_id + '">' + agentGlyph(item.agent_key) +
          '<div class="search-result-main"><div class="search-result-title">' + esc(item.session_title || '会话消息') + '</div><div class="search-result-sub">' + esc(truncate(item.preview || '', 110)) + '</div></div></div>';
      });
    }
    box.innerHTML = html;
    hydrateIcons(box);
  } catch (error) {
    box.innerHTML = '<div class="search-empty">搜索失败：' + esc(error.message) + '</div>';
  }
}

function handleGlobalAction(action, element) {
  if (action === 'logout') { logout(); return true; }
  if (action === 'open-sidebar') { openSidebar(); return true; }
  if (action === 'close-sidebar') { closeSidebar(); return true; }
  if (action === 'focus-search') {
    const input = document.getElementById('global-search');
    const box = document.getElementById('topbar-search');
    if (input) {
      const searchBar = input.closest('.global-search');
      if (searchBar && searchBar.offsetParent === null) {
        navigate('sessions', {}, true);
        setTimeout(function () { const target = document.getElementById('session-search'); if (target) target.focus(); }, 260);
      } else {
        input.focus();
      }
    }
    return true;
  }
  if (action === 'sync-all') { syncAll(); return true; }
  if (action === 'open-account') { showAccountModal(); return true; }
  if (action === 'reload-page') { route(); return true; }
  if (action === 'search-session') {
    closeSearch();
    navigate('sessions/' + element.dataset.id, {}, true);
    return true;
  }
  if (action === 'search-event') {
    closeSearch();
    navigate('sessions/' + element.dataset.session, { event_id: element.dataset.id }, true);
    return true;
  }
  return false;
}

function bindGlobalEvents() {
  document.addEventListener('click', function (event) {
    const actionElement = event.target.closest('[data-action]');
    if (actionElement) {
      const action = actionElement.dataset.action;
      if (handleGlobalAction(action, actionElement)) {
        event.preventDefault();
        return;
      }
    }
    const searchWrap = document.getElementById('global-search-wrap');
    if (searchWrap && !searchWrap.contains(event.target)) closeSearch();
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeSearch();
      closeModal();
      closeSidebar();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      const input = document.getElementById('global-search');
      if (input) input.focus();
      return;
    }
    if (event.key === '/' && !/input|textarea|select/i.test(document.activeElement.tagName)) {
      event.preventDefault();
      const input = document.getElementById('global-search');
      if (input) input.focus();
    }
  });

  window.addEventListener('hashchange', function () { if (bootstrapped) route(); });
  window.addEventListener('agenttrail:auth-expired', function () {
    if (bootstrapped) {
      showLogin('登录已过期，请重新登录');
      showToast('登录状态已失效，请重新登录', 'error');
    }
  });
  window.addEventListener('agenttrail:open-password', function () { showPasswordModal(); });
  window.addEventListener('agenttrail:settings-updated', function (event) {
    if (event.detail) state.settings = event.detail;
    updateChrome();
  });

  const loginForm = document.getElementById('login-form');
  if (loginForm) loginForm.addEventListener('submit', handleLogin);
  document.querySelector('[data-action="toggle-password"]').addEventListener('click', function () {
    const input = document.getElementById('login-password');
    if (!input) return;
    const visible = input.type === 'text';
    input.type = visible ? 'password' : 'text';
    this.innerHTML = icon(visible ? 'eye' : 'eye-off');
  });

  const search = document.getElementById('global-search');
  if (search) {
    search.addEventListener('input', function () {
      clearTimeout(state.searchTimer);
      const value = this.value;
      state.searchTimer = setTimeout(function () { performSearch(value); }, 220);
    });
    search.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        const value = this.value.trim();
        closeSearch();
        if (value) navigate('sessions', { q: value }, true);
      }
    });
  }

  document.getElementById('sidebar-overlay').addEventListener('click', closeSidebar);
}

function versionProblems(health) {
  if (!health) return ['无法读取后端版本信息。'];
  const problems = [];
  const backend = Number(health.api_revision);
  if (!Number.isFinite(backend)) {
    problems.push('正在运行的服务没有返回接口版本，说明后端仍是旧版本。');
  } else if (backend !== API_REVISION) {
    problems.push('页面接口版本 v' + API_REVISION + '，运行中的服务 v' + backend + '。');
  }
  if (health.stale) {
    problems.push('磁盘上的代码比服务启动时间新，正在运行的仍是更新前的代码。');
  }
  return problems;
}

function showVersionBanner(problems) {
  if (!problems || !problems.length) return;
  let banner = document.getElementById('version-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'version-banner';
    banner.className = 'version-banner';
    banner.setAttribute('role', 'alert');
    banner.addEventListener('click', function (event) {
      const action = event.target.closest('[data-version-action]');
      if (!action) return;
      if (action.getAttribute('data-version-action') === 'reload') location.reload();
      else banner.remove();
    });
    document.body.appendChild(banner);
  }
  banner.innerHTML =
    '<div class="version-banner-copy">' +
    '<strong>前后端版本不一致，部分操作会失败</strong>' +
    '<span>' + problems.map(esc).join(' ') + ' 请重启 AgentTrail 服务，再按 Ctrl+F5 强制刷新页面。</span>' +
    '</div>' +
    '<div class="version-banner-actions">' +
    '<button type="button" class="btn btn-secondary btn-sm" data-version-action="reload">重新加载</button>' +
    '<button type="button" class="btn btn-ghost btn-sm" data-version-action="dismiss">关闭</button>' +
    '</div>';
}

async function checkBackendVersion() {
  let health = null;
  try {
    health = await api.get('/api/health', { silent: true });
  } catch (error) {
    health = null;
  }
  const problems = versionProblems(health);
  if (problems.length) console.warn('[AgentTrail] 版本校验失败：', problems.join(' '), health);
  showVersionBanner(problems);
}

async function init() {
  hydrateIcons(document);
  bindGlobalEvents();
  if (!location.hash) history.replaceState(null, '', '#/overview');
  checkBackendVersion();
  try {
    const authStatus = await api.get('/api/auth/status', { silent: true });
    if (!authStatus || !authStatus.authenticated) {
      showLogin('');
      return;
    }
    const boot = await api.get('/api/bootstrap', { silent: true });
    await enterApp(boot);
  } catch (error) {
    showLogin(error && error.message ? error.message : '无法连接到本地服务');
  }
}

init();
