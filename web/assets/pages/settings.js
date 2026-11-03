import {
  api, state, isCurrent, setView, skeletonPage, pageHead, emptyState, icon, esc, attr,
  num, bytes, fmtFull, relTime, initial, showToast, showModal, confirmDialog, copyText
} from '../core.js';

const TIMEZONES = ['Asia/Shanghai', 'Asia/Tokyo', 'Asia/Singapore', 'UTC', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles'];

export async function render(params, token, signal) {
  setView(skeletonPage('系统设置'));
  let settingsData;
  let usersData;
  try {
    const results = await Promise.all([api.get('/api/settings'), api.get('/api/users')]);
    settingsData = results[0];
    usersData = results[1];
  } catch (error) {
    if (!isCurrent(token)) return;
    setView('<div class="page"><div class="panel"><div class="panel-body">' + emptyState('设置加载失败', error.message, 'alert-circle') + '<div style="text-align:center"><button class="btn btn-secondary btn-sm" data-action="reload-page">重新加载</button></div></div></div></div>');
    return;
  }
  if (!isCurrent(token)) return;
  draw(settingsData, usersData, signal);
}

function draw(settingsData, usersData, signal) {
  const settings = settingsData.settings || {};
  const users = usersData.items || [];
  const counts = settingsData.counts || {};
  const paths = settingsData.paths || {};

  const nav = '<div class="settings-nav">' +
    navButton('general', 'sliders', '通用') +
    navButton('collection', 'zap', '采集') +
    navButton('backup', 'database', '备份与导出') +
    navButton('users', 'user', '成员与权限') +
    navButton('security', 'shield', '安全') +
    navButton('system', 'server', '系统信息') +
    '</div>';

  const general = '<section class="panel settings-section" id="settings-general"><div class="panel-head"><div><h2 class="panel-title">通用设置</h2><p class="panel-sub">界面与默认行为</p></div><span class="badge neutral">GENERAL</span></div><div class="panel-body"><form id="settings-general-form"><div class="settings-form">' +
    '<div class="field full"><label class="field-label" for="setting-site-name">站点名称</label><input class="input" id="setting-site-name" value="' + attr(settings.site_name || '迹录 AgentTrail') + '" maxlength="60"></div>' +
    '<div class="field"><label class="field-label" for="setting-page-size">默认分页大小</label><input class="input" id="setting-page-size" type="number" min="5" max="100" value="' + Number(settings.default_page_size || 20) + '"></div>' +
    '<div class="field"><label class="field-label" for="setting-timezone">展示时区</label><select class="select" id="setting-timezone">' + TIMEZONES.map(function (zone) { return '<option value="' + zone + '"' + (settings.timezone === zone ? ' selected' : '') + '>' + zone + '</option>'; }).join('') + '</select></div>' +
    '<div class="field full"><div class="inline-switch"><div class="inline-switch-copy"><strong>启用自动同步</strong><span>调度器按每个 Agent 配置的间隔执行增量采集。</span></div><label class="switch"><input id="setting-auto-sync" type="checkbox"' + (settings.auto_sync_enabled ? ' checked' : '') + '><span></span></label></div></div>' +
    '<div class="field"><label class="field-label" for="setting-sync-interval">默认同步间隔（分钟）</label><input class="input" id="setting-sync-interval" type="number" min="1" max="1440" value="' + Number(settings.sync_interval_minutes || 30) + '"></div>' +
    '<div class="field"><label class="field-label" for="setting-retry">默认失败重试次数</label><input class="input" id="setting-retry" type="number" min="1" max="10" value="' + Number(settings.auto_retry_attempts || 3) + '"></div>' +
    '</div><div class="form-actions"><button class="btn btn-primary btn-sm" type="submit">' + icon('save') + '<span>保存通用设置</span></button></div></form></div></section>';

  const collection = '<section class="panel settings-section" id="settings-collection"><div class="panel-head"><div><h2 class="panel-title">采集策略</h2><p class="panel-sub">增量同步、去重与失败重试</p></div><span class="badge accent">DEDUP ON</span></div><div class="panel-body"><div class="storage-note" style="margin-top:0;line-height:1.8">采集采用两层幂等保护：文件级游标避免重复读取，事件级 <b class="mono">fingerprint</b> 在 SQLite 唯一索引上阻止重复写入。压缩流与 SQLite 来源无法按字节定位时会重扫，但重复事件不会重复入库。<br><br>Agent 的数据源路径、匹配模式、间隔与重试次数在「Agent 接入」中分别配置。</div><div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap"><a class="btn btn-secondary btn-sm" href="#/agents">' + icon('nodes') + '<span>前往 Agent 接入</span></a><a class="btn btn-ghost btn-sm" href="#/tasks">' + icon('activity') + '<span>查看采集任务</span></a></div></div></section>';

  const backup = '<section class="panel settings-section" id="settings-backup"><div class="panel-head"><div><h2 class="panel-title">备份与导出</h2><p class="panel-sub">本地快照与导出行数上限</p></div><span class="badge neutral">LOCAL ONLY</span></div><div class="panel-body"><form id="settings-backup-form"><div class="settings-form">' +
    '<div class="field full"><div class="inline-switch"><div class="inline-switch-copy"><strong>启用自动备份</strong><span>调度器按间隔创建 SQLite 一致性快照。</span></div><label class="switch"><input id="setting-auto-backup" type="checkbox"' + (settings.auto_backup_enabled ? ' checked' : '') + '><span></span></label></div></div>' +
    '<div class="field"><label class="field-label" for="setting-backup-interval">备份间隔（小时）</label><input class="input" id="setting-backup-interval" type="number" min="1" max="168" value="' + Number(settings.backup_interval_hours || 24) + '"></div>' +
    '<div class="field"><label class="field-label" for="setting-backup-retention">保留备份数量</label><input class="input" id="setting-backup-retention" type="number" min="1" max="30" value="' + Number(settings.backup_retention || 7) + '"></div>' +
    '<div class="field"><label class="field-label" for="setting-export-max">单次导出行数上限</label><input class="input" id="setting-export-max" type="number" min="1000" max="1000000" step="1000" value="' + Number(settings.export_max_rows || 200000) + '"></div>' +
    '<div class="field"><label class="field-label">数据保留策略</label><div class="input" style="display:flex;align-items:center;color:var(--muted);font-size:11px">永久保留 · 不做自动清理</div></div>' +
    '</div><div class="form-actions"><button class="btn btn-primary btn-sm" type="submit">' + icon('save') + '<span>保存备份设置</span></button></div></form></div></section>';

  const userRows = users.length ? users.map(function (user) {
    return '<tr><td><div class="user-avatar-cell"><span class="avatar">' + esc(initial(user.username)) + '</span><div><div class="cell-main">' + esc(user.username) + (user.id === state.user.id ? ' <span class="tag accent">YOU</span>' : '') + '</div><div class="cell-sub">加入于 ' + fmtFull(user.created_at) + '</div></div></div></td>' +
      '<td>' + (user.role === 'admin' ? '<span class="badge accent">管理员</span>' : '<span class="badge neutral">只读成员</span>') + '</td>' +
      '<td>' + (user.active ? '<span class="badge accent"><span class="dot"></span>启用</span>' : '<span class="badge rose">停用</span>') + '</td>' +
      '<td><div class="cell-main">' + (user.last_login_at ? relTime(user.last_login_at) : '从未登录') + '</div><div class="cell-sub mono">' + esc(user.last_login_at || '—') + '</div></td>' +
      '<td class="cell-actions"><button class="btn btn-ghost btn-xs" data-user-edit="' + user.id + '">管理</button><label class="switch" title="启用 / 停用" style="vertical-align:middle;margin-left:6px"><input type="checkbox" data-user-active="' + user.id + '"' + (user.active ? ' checked' : '') + (user.id === state.user.id ? ' disabled' : '') + '><span></span></label></td></tr>';
  }).join('') : '<tr><td colspan="5" class="table-empty">暂无用户</td></tr>';

  const usersSection = '<section class="panel settings-section" id="settings-users"><div class="panel-head"><div><h2 class="panel-title">成员与权限</h2><p class="panel-sub">管理员可配置采集和系统；只读成员可浏览、搜索与导出</p></div><button class="btn btn-primary btn-sm" data-user-create>' + icon('plus') + '<span>新增成员</span></button></div><div class="table-wrap" style="margin-top:10px"><table class="data-table"><thead><tr><th>成员</th><th>角色</th><th>状态</th><th>最近登录</th><th></th></tr></thead><tbody>' + userRows + '</tbody></table></div></section>';

  const security = '<section class="panel settings-section" id="settings-security"><div class="panel-head"><div><h2 class="panel-title">安全与会话</h2><p class="panel-sub">访问控制与会话保护</p></div><span class="badge accent">' + icon('shield') + ' PROTECTED</span></div><div class="panel-body">' +
    '<div class="inline-switch" style="padding-top:0"><div class="inline-switch-copy"><strong>当前登录</strong><span>' + esc(state.user.username) + ' · ' + (state.user.role === 'admin' ? '管理员' : '只读成员') + ' · ' + esc(state.settings.timezone || 'Asia/Shanghai') + '</span></div><button class="btn btn-secondary btn-sm" data-settings-password>' + icon('key') + '<span>修改密码</span></button></div>' +
    '<div class="inline-switch"><div class="inline-switch-copy"><strong>会话保护</strong><span>HttpOnly Cookie · SameSite=Strict · 同源请求头校验 · 登录限流</span></div><span class="badge accent"><span class="dot"></span>已启用</span></div>' +
    '<div class="inline-switch"><div class="inline-switch-copy"><strong>审计轨迹</strong><span>登录、配置、导出与备份操作写入审计表，不提供修改接口。</span></div><a class="btn btn-ghost btn-xs" href="#/logs?tab=audit">查看审计' + icon('arrow-right') + '</a></div>' +
    '<div class="storage-note" style="margin-top:14px">初始管理员为 <b class="mono">admin</b>。若仍在使用默认密码 <b class="mono">admin123</b>，请立即修改。部署在非本机环境时，应同时启用 HTTPS 反向代理并设置 <b class="mono">AGENTTRAIL_SECURE_COOKIE=1</b>。</div>' +
    '</div></section>';

  const pathsList = '<div class="path-list">' + [
    ['数据库', paths.database], ['数据目录', paths.data], ['备份目录', paths.backups]
  ].map(function (pair) {
    return '<div class="path-row"><span title="' + attr(pair[1]) + '">' + esc(pair[1]) + '</span><button class="copy-btn" data-copy-path="' + attr(pair[1]) + '" aria-label="复制路径">' + icon('copy') + '</button></div>';
  }).join('') + '</div>';

  const system = '<section class="panel settings-section" id="settings-system"><div class="panel-head"><div><h2 class="panel-title">系统信息</h2><p class="panel-sub">运行环境与数据规模</p></div><span class="badge neutral">v' + esc(state.boot.app.version) + '</span></div><div class="panel-body"><div class="settings-form" style="margin-bottom:14px"><div class="analytics-kpi"><span>会话</span><b>' + num(counts.sessions) + '</b><small>SESSIONS</small></div><div class="analytics-kpi"><span>事件</span><b>' + num(counts.events) + '</b><small>EVENTS</small></div><div class="analytics-kpi"><span>数据源</span><b>' + num(counts.sources) + '</b><small>SOURCES</small></div><div class="analytics-kpi"><span>用户</span><b>' + num(counts.users) + '</b><small>USERS</small></div></div>' + pathsList + '</div></section>';

  const header = pageHead({
    eyebrow: 'CONFIGURATION / ADMIN ONLY',
    title: '系统设置',
    subtitle: '配置调度、备份、用户与访问安全。所有变更都会写入审计日志。',
    actions: '<button class="btn btn-ghost btn-sm" data-action="reload-page">' + icon('refresh') + '<span>刷新</span></button>'
  });

  setView('<div class="page">' + header + '<div class="settings-layout">' + nav + '<div>' + general + collection + backup + usersSection + security + system + '</div></div></div>', function (root, sig) {
    root.addEventListener('click', function (event) {
      const target = event.target.closest('[data-settings-target]');
      if (target) {
        event.preventDefault();
        const section = document.getElementById('settings-' + target.dataset.settingsTarget);
        if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
        root.querySelectorAll('[data-settings-target]').forEach(function (button) { button.classList.toggle('active', button === target); });
        return;
      }
      const copy = event.target.closest('[data-copy-path]');
      if (copy) {
        event.preventDefault();
        copyText(copy.dataset.copyPath).then(function (ok) { showToast(ok ? '路径已复制' : '复制失败', ok ? 'success' : 'error'); });
        return;
      }
      if (event.target.closest('[data-settings-password]')) {
        event.preventDefault();
        window.dispatchEvent(new CustomEvent('agenttrail:open-password'));
        return;
      }
      if (event.target.closest('[data-user-create]')) {
        event.preventDefault();
        openCreateUser();
        return;
      }
      const edit = event.target.closest('[data-user-edit]');
      if (edit) {
        event.preventDefault();
        const user = users.find(function (item) { return String(item.id) === edit.dataset.userEdit; });
        if (user) openEditUser(user);
      }
    }, { signal: sig });

    root.addEventListener('change', async function (event) {
      const toggle = event.target.closest('[data-user-active]');
      if (!toggle) return;
      const id = Number(toggle.dataset.userActive);
      const user = users.find(function (item) { return item.id === id; });
      if (!user) return;
      toggle.disabled = true;
      try {
        await api.patch('/api/users/' + id, { active: toggle.checked });
        showToast('成员状态已更新', 'success');
        window.dispatchEvent(new HashChangeEvent('hashchange'));
      } catch (error) {
        toggle.checked = !toggle.checked;
        toggle.disabled = false;
        showToast(error.message, 'error', '更新失败');
      }
    }, { signal: sig });

    root.addEventListener('submit', async function (event) {
      const generalForm = event.target.closest('#settings-general-form');
      if (generalForm) {
        event.preventDefault();
        await saveSettings({
          site_name: root.querySelector('#setting-site-name').value.trim(),
          default_page_size: Number(root.querySelector('#setting-page-size').value),
          timezone: root.querySelector('#setting-timezone').value,
          auto_sync_enabled: root.querySelector('#setting-auto-sync').checked,
          sync_interval_minutes: Number(root.querySelector('#setting-sync-interval').value),
          auto_retry_attempts: Number(root.querySelector('#setting-retry').value)
        });
        return;
      }
      const backupForm = event.target.closest('#settings-backup-form');
      if (backupForm) {
        event.preventDefault();
        await saveSettings({
          auto_backup_enabled: root.querySelector('#setting-auto-backup').checked,
          backup_interval_hours: Number(root.querySelector('#setting-backup-interval').value),
          backup_retention: Number(root.querySelector('#setting-backup-retention').value),
          export_max_rows: Number(root.querySelector('#setting-export-max').value)
        });
      }
    }, { signal: sig });
  });
}

function navButton(key, iconName, label) {
  return '<button type="button" data-settings-target="' + key + '">' + icon(iconName) + '<span>' + label + '</span></button>';
}

async function saveSettings(payload) {
  try {
    const result = await api.patch('/api/settings', payload);
    state.settings = result.settings || state.settings;
    window.dispatchEvent(new CustomEvent('agenttrail:settings-updated', { detail: state.settings }));
    showToast('设置已保存', 'success');
  } catch (error) {
    showToast(error.message, 'error', '保存失败');
  }
}

function openCreateUser() {
  const instance = showModal({
    title: '新增成员',
    subtitle: '只读成员可访问会话、统计和导出；管理员拥有全部权限。',
    body: '<form id="user-create-form"><label class="field-label" for="new-username">用户名</label><input class="input" id="new-username" autocomplete="off" minlength="3" maxlength="40" required><label class="field-label" for="new-password">初始密码</label><input class="input" id="new-password" type="password" autocomplete="new-password" minlength="8" required><p class="field-help">至少 8 个字符；成员首次登录后建议自行修改。</p><label class="field-label" for="new-role">角色</label><select class="select" id="new-role"><option value="viewer">只读成员</option><option value="admin">管理员</option></select><div class="checkbox-row"><input id="new-must-change" type="checkbox" checked><label for="new-must-change">首次登录后要求修改密码</label></div></form>',
    footer: '<button class="btn btn-ghost" type="button" data-modal-close>取消</button><button class="btn btn-primary" type="submit" form="user-create-form">创建成员</button>',
    onMount: function (element) {
      const form = element.querySelector('#user-create-form');
      form.addEventListener('submit', async function (event) {
        event.preventDefault();
        try {
          await api.post('/api/users', {
            username: element.querySelector('#new-username').value.trim(),
            password: element.querySelector('#new-password').value,
            role: element.querySelector('#new-role').value,
            must_change_password: element.querySelector('#new-must-change').checked
          });
          instance.close();
          showToast('成员已创建', 'success');
          window.dispatchEvent(new HashChangeEvent('hashchange'));
        } catch (error) {
          showToast(error.message, 'error', '创建失败');
        }
      });
    }
  });
}

function openEditUser(user) {
  const instance = showModal({
    title: '管理成员 · ' + user.username,
    subtitle: '角色变更与密码重置会立即生效，并写入审计日志。',
    body: '<div class="meta-list" style="margin-bottom:12px"><div class="meta-item"><span class="meta-key">用户名</span><span class="meta-value">' + esc(user.username) + '</span></div><div class="meta-item"><span class="meta-key">创建时间</span><span class="meta-value mono">' + fmtFull(user.created_at) + '</span></div></div><form id="user-edit-form"><label class="field-label" for="edit-role">角色</label><select class="select" id="edit-role"><option value="viewer"' + (user.role === 'viewer' ? ' selected' : '') + '>只读成员</option><option value="admin"' + (user.role === 'admin' ? ' selected' : '') + '>管理员</option></select><div class="inline-switch"><div class="inline-switch-copy"><strong>账号启用</strong><span>停用后该用户所有登录会话立即失效。</span></div><label class="switch"><input id="edit-active" type="checkbox"' + (user.active ? ' checked' : '') + '><span></span></label></div><label class="field-label" for="edit-password">重置密码 <span style="color:var(--muted-2);font-weight:400">（留空不修改）</span></label><input class="input" id="edit-password" type="password" autocomplete="new-password" minlength="8" placeholder="至少 8 个字符"></form>',
    footer: '<button class="btn btn-ghost" type="button" data-modal-close>取消</button><button class="btn btn-primary" type="submit" form="user-edit-form">保存变更</button>',
    onMount: function (element) {
      element.querySelector('#user-edit-form').addEventListener('submit', async function (event) {
        event.preventDefault();
        const payload = {
          role: element.querySelector('#edit-role').value,
          active: element.querySelector('#edit-active').checked
        };
        const password = element.querySelector('#edit-password').value;
        if (password) payload.password = password;
        try {
          await api.patch('/api/users/' + user.id, payload);
          instance.close();
          showToast('成员信息已更新', 'success');
          window.dispatchEvent(new HashChangeEvent('hashchange'));
        } catch (error) {
          showToast(error.message, 'error', '更新失败');
        }
      });
    }
  });
}
