import {
  api, state, isAdmin, isCurrent, setView, skeletonPage, pageHead, emptyState, icon,
  esc, attr, num, bytes, fmtFull, relTime, showToast, showModal, confirmDialog,
  navigate
} from '../core.js';

export async function render(params, token, signal) {
  setView(skeletonPage('数据备份'));
  let data;
  try {
    data = await api.get('/api/backups');
  } catch (error) {
    if (!isCurrent(token)) return;
    setView('<div class="page"><div class="panel"><div class="panel-body">' + emptyState('备份信息加载失败', error.message, 'alert-circle') + '<div style="text-align:center"><button class="btn btn-secondary btn-sm" data-action="reload-page">重新加载</button></div></div></div></div>');
    return;
  }
  if (!isCurrent(token)) return;
  draw(data, signal);
}

function draw(data, signal) {
  const items = data.items || [];
  const stats = data.stats || {};
  const config = data.config || {};
  const last = items[0];

  const hero = '<div class="backup-hero"><span class="backup-hero-icon">' + icon('database') + '</span><div class="backup-hero-copy"><h2>本地数据库快照</h2><p>使用 SQLite Online Backup API 创建一致性副本，再以 gzip 压缩。备份包含会话、事件、用户、审计与配置；请妥善保管。</p></div><div class="backup-actions"><button class="btn btn-primary" data-backup-create>' + icon('plus') + '<span>立即备份</span></button><button class="btn btn-ghost" data-action="reload-page">' + icon('refresh') + '<span>刷新</span></button></div></div>';

  const summary = '<div class="task-summary"><div class="task-stat"><span>备份数量</span><b>' + num(stats.count) + '</b><small class="dim">SNAPSHOTS</small></div><div class="task-stat"><span>占用空间</span><b>' + bytes(stats.size_bytes) + '</b><small class="dim">COMPRESSED</small></div><div class="task-stat"><span>最近备份</span><b style="font-size:13px">' + (stats.last_backup_at ? relTime(stats.last_backup_at) : '还没有备份') + '</b><small class="dim">LAST RUN</small></div><div class="task-stat"><span>自动备份</span><b style="font-size:14px">' + (config.auto_backup_enabled ? '每 ' + Number(config.backup_interval_hours || 24) + ' 小时' : '已关闭') + '</b><small class="dim">RETAIN ' + Number(config.backup_retention || 7) + '</small></div></div>';

  const rows = items.length ? items.map(function (item) {
    return '<tr><td><div class="backup-file"><span class="backup-file-icon">' + icon('archive') + '</span><div><div class="backup-file-name">' + esc(item.filename) + '</div><div class="cell-sub">' + esc(item.note || '手动备份') + ' · ' + esc(item.created_by || 'system') + '</div></div></div></td>' +
      '<td class="num">' + bytes(item.size_bytes) + '</td>' +
      '<td><div class="cell-main">' + fmtFull(item.created_at) + '</div><div class="cell-sub">' + relTime(item.created_at) + '</div></td>' +
      '<td>' + (item.exists === false ? '<span class="badge rose">文件丢失</span>' : '<span class="badge accent"><span class="dot"></span>可用</span>') + '</td>' +
      '<td class="cell-actions"><a class="btn btn-ghost btn-xs" href="/api/backups/' + item.id + '/download"' + (item.exists === false ? ' aria-disabled="true" style="pointer-events:none;opacity:.4"' : '') + '>' + icon('download') + '<span>下载</span></a><button class="btn btn-ghost btn-xs danger" data-backup-delete="' + item.id + '" data-name="' + attr(item.filename) + '">' + icon('trash') + '</button></td></tr>';
  }).join('') : '<tr><td colspan="5" class="table-empty">还没有备份。建议在首次同步完成后创建第一份快照。</td></tr>';

  const table = '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">备份历史</h2><p class="panel-sub">保留最近 ' + Number(config.backup_retention || 7) + ' 份自动备份，旧文件会自动清理</p></div><span class="badge neutral">' + num(stats.count) + ' FILES</span></div><div class="panel-body flush"><div class="table-wrap"><table class="data-table"><thead><tr><th>文件</th><th class="num">大小</th><th>创建时间</th><th>状态</th><th class="cell-actions"></th></tr></thead><tbody>' + rows + '</tbody></table></div></div></section>';

  const restore = '<section class="panel"><div class="panel-head"><div><h2 class="panel-title">恢复与迁移</h2><p class="panel-sub">离线恢复流程 · 请先停止服务</p></div><span class="badge amber">MANUAL</span></div><div class="panel-body"><div class="flow-steps">' +
    '<div class="flow-step"><span>STEP 01</span><strong>停止服务</strong><p>在运行服务的终端按 Ctrl+C，确保没有并发写入。</p></div>' +
    '<div class="flow-step"><span>STEP 02</span><strong>解压快照</strong><p>使用 gzip 解压 .db.gz 得到 .db 文件。</p></div>' +
    '<div class="flow-step"><span>STEP 03</span><strong>替换数据库</strong><p>移除 data/agenttrail.db 及 -wal / -shm 文件后替换。</p></div>' +
    '<div class="flow-step"><span>STEP 04</span><strong>重启服务</strong><p>启动后自动执行 schema 兼容检查并恢复登录会话。</p></div>' +
    '<div class="flow-step"><span>STEP 05</span><strong>核对数据</strong><p>在「总览」与「日志」中确认会话数量与最近同步状态。</p></div>' +
    '</div><div class="storage-note">备份包含密码哈希，请存放在受控目录；系统不会将备份上传到任何云端。</div></div></section>';

  const header = pageHead({
    eyebrow: 'MAINTENANCE / SQLITE SNAPSHOTS',
    title: '数据备份',
    subtitle: '保护本地档案库免受误操作、迁移或存储故障影响。备份是完整数据库副本，恢复操作需要手动执行。',
    actions: '<button class="btn btn-primary btn-sm" data-backup-create>' + icon('plus') + '<span>创建备份</span></button>'
  });

  setView('<div class="page">' + header + hero + summary + table + restore + '</div>', function (root, sig) {
    root.addEventListener('click', function (event) {
      const create = event.target.closest('[data-backup-create]');
      if (create) { event.preventDefault(); openCreate(); return; }
      const remove = event.target.closest('[data-backup-delete]');
      if (remove) { event.preventDefault(); removeBackup(remove.dataset.backupDelete, remove.dataset.name); }
    }, { signal: sig });
  });
}

function openCreate() {
  const instance = showModal({
    title: '创建数据库备份',
    subtitle: '在线备份不会中断正在运行的采集任务。',
    body: '<form id="backup-create-form"><label class="field-label" for="backup-note">备注</label><input class="input" id="backup-note" placeholder="例如：升级前快照 / 月度归档" maxlength="120"><p class="field-help">生成的文件保存在 data/backups/，可在备份历史中下载。</p></form>',
    footer: '<button class="btn btn-ghost" type="button" data-modal-close>取消</button><button class="btn btn-primary" type="submit" form="backup-create-form">开始备份</button>',
    onMount: function (element) {
      const form = element.querySelector('#backup-create-form');
      const submit = element.querySelector('[form="backup-create-form"]');
      form.addEventListener('submit', async function (event) {
        event.preventDefault();
        const note = element.querySelector('#backup-note').value.trim() || '手动备份';
        submit.disabled = true;
        submit.innerHTML = '<span class="spinner tiny"></span><span>备份中…</span>';
        try {
          await api.post('/api/backups', { note: note });
          instance.close();
          showToast('数据库快照已创建', 'success');
          navigate('backups', {}, true);
        } catch (error) {
          submit.disabled = false;
          submit.textContent = '开始备份';
          showToast(error.message, 'error', '备份失败');
        }
      });
    }
  });
}

async function removeBackup(id, name) {
  const confirmed = await confirmDialog({
    title: '删除备份文件',
    body: '将永久删除 <strong class="mono">' + esc(name) + '</strong>。此操作无法撤销，但不会影响当前数据库。',
    confirmText: '删除备份',
    danger: true
  });
  if (!confirmed) return;
  try {
    await api.del('/api/backups/' + id);
    showToast('备份文件已删除', 'success');
    navigate('backups', {}, true);
  } catch (error) {
    showToast(error.message, 'error', '删除失败');
  }
}
