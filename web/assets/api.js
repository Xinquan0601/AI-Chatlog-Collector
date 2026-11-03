export class ApiError extends Error {
  constructor(status, code, message) {
    super(message || '请求失败');
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

async function parseResponse(response) {
  const contentType = response.headers.get('Content-Type') || '';
  let payload = null;
  if (contentType.includes('application/json')) {
    try { payload = await response.json(); } catch (error) { payload = null; }
  }
  if (!response.ok) {
    const err = payload && payload.error ? payload.error : {};
    throw new ApiError(response.status, err.code || 'http_' + response.status, err.message || ('请求失败（' + response.status + '）'));
  }
  return payload ? payload.data : null;
}

export async function request(path, options) {
  const opts = options || {};
  const method = opts.method || 'GET';
  const headers = Object.assign({ 'Accept': 'application/json' }, opts.headers || {});
  if (method !== 'GET') {
    headers['X-AgentTrail-Request'] = '1';
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  }
  let response;
  try {
    response = await fetch(path, {
      method: method,
      headers: headers,
      credentials: 'same-origin',
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: 'no-store'
    });
  } catch (error) {
    throw new ApiError(0, 'network_error', '无法连接到本地服务，请确认服务仍在运行');
  }
  let payload = null;
  const contentType = response.headers.get('Content-Type') || '';
  if (contentType.includes('application/json')) {
    try { payload = await response.json(); } catch (error) { payload = null; }
  }
  if (response.status === 401 && !opts.silent) {
    window.dispatchEvent(new CustomEvent('agenttrail:auth-expired'));
  }
  if (!response.ok) {
    const err = payload && payload.error ? payload.error : {};
    throw new ApiError(response.status, err.code || 'http_' + response.status, err.message || ('请求失败（' + response.status + '）'));
  }
  return payload ? payload.data : null;
}

export const api = {
  get: function (path, options) { return request(path, Object.assign({ method: 'GET' }, options || {})); },
  post: function (path, body, options) { return request(path, Object.assign({ method: 'POST', body: body === undefined ? {} : body }, options || {})); },
  patch: function (path, body, options) { return request(path, Object.assign({ method: 'PATCH', body: body === undefined ? {} : body }, options || {})); },
  del: function (path, options) { return request(path, Object.assign({ method: 'DELETE' }, options || {})); }
};

export function queryString(params) {
  const search = new URLSearchParams();
  Object.keys(params || {}).forEach(function (key) {
    const value = params[key];
    if (value === undefined || value === null || value === '' || value === 'all') return;
    search.set(key, String(value));
  });
  const text = search.toString();
  return text ? '?' + text : '';
}

function filenameFromDisposition(value) {
  if (!value) return null;
  let match = /filename\*=UTF-8''([^;]+)/i.exec(value);
  if (match) {
    try { return decodeURIComponent(match[1]); } catch (error) { /* ignore */ }
  }
  match = /filename="?([^";]+)"?/i.exec(value);
  return match ? match[1] : null;
}

export async function download(path, fallbackName) {
  let response;
  try {
    response = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Accept': '*/*', 'X-AgentTrail-Request': '1' },
      cache: 'no-store'
    });
  } catch (error) {
    throw new ApiError(0, 'network_error', '下载失败：无法连接到本地服务');
  }
  if (!response.ok) {
    let payload = null;
    try { payload = await response.json(); } catch (error) { payload = null; }
    const err = payload && payload.error ? payload.error : {};
    if (response.status === 401) window.dispatchEvent(new CustomEvent('agenttrail:auth-expired'));
    throw new ApiError(response.status, err.code || 'download_failed', err.message || '下载失败');
  }
  const blob = await response.blob();
  const filename = filenameFromDisposition(response.headers.get('Content-Disposition')) || fallbackName || 'download';
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
  return filename;
}
