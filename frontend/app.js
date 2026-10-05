/* ===========================================================
   CloudRive · 前端逻辑
   =========================================================== */
const state = {
  folderId: null,
  nodes: [],
  selected: new Set(),
  view: localStorage.getItem('cr_view') || 'list',
  uploadTotal: 0,
  uploadDone: 0,
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

/** 构造带登录令牌的请求头（分片上传等非 api() 路径使用） */
function authHeaders(extra = {}) {
  const h = { ...extra };
  const tk = localStorage.getItem('cr_token');
  if (tk) h['Authorization'] = `Bearer ${tk}`;
  return h;
}

async function api(url, opt = {}) {
  // 大文件合并等耗时操作需要更长超时
  const ms = opt.timeout || 60000;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);

  // 登录令牌：优先用 Authorization 头（Cookie 已由服务端设置，双保险）
  const headers = { ...(opt.headers || {}) };
  if (opt.body) headers['Content-Type'] = 'application/json';
  const token = localStorage.getItem('cr_token');
  if (token) headers['Authorization'] = `Bearer ${token}`;

  try {
    const r = await fetch(url, {
      method: opt.method || 'GET',
      headers,
      body: opt.body ? JSON.stringify(opt.body) : undefined,
      signal: ctrl.signal,
      // 带上 Cookie，Cookie 不可被 JS 读取但浏览器会自动附加
      credentials: 'same-origin',
    });

    // 401 表示未登录或令牌过期：跳登录页
    if (r.status === 401) {
      localStorage.removeItem('cr_token');
      if (!location.pathname.startsWith('/s/') && !location.pathname.startsWith('/login')) {
        location.replace('/login');
      }
      throw new Error('请先登录');
    }

    if (!r.ok) {
      let msg = r.statusText;
      try { const d = await r.json(); msg = d.detail?.message || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  } catch (e) {
    if (e.name === 'AbortError') {
      throw new Error('请求超时，请检查网络或降低上传并发后重试');
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/* ---------------- 工具 ---------------- */
const fmtSize = (n) => {
  if (!n || n < 0) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return i === 0 ? `${v} B` : `${v < 10 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
};

const fmtTime = (s) => {
  if (!s) return '—';
  const d = new Date(s), now = new Date();
  const p = (n) => String(n).padStart(2, '0');
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? `今天 ${p(d.getHours())}:${p(d.getMinutes())}`
    : `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;' }[c]));

// 按扩展名归类，用于图标与配色
function kind(name, isFolder) {
  if (isFolder) return 'folder';
  const e = (name.split('.').pop() || '').toLowerCase();
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'avif'].includes(e)) return 'img';
  if (['mp4', 'mkv', 'mov', 'avi', 'webm', 'flv', 'm4v', 'rmvb'].includes(e)) return 'vid';
  if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz', 'iso'].includes(e)) return 'zip';
  if (['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'pdf', 'txt', 'md', 'csv'].includes(e)) return 'doc';
  return 'gen';
}

const ICONS = {
  folder: '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.3l2 2.4h8.7A1.5 1.5 0 0 1 21 9.9V18a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18z"/>',
  img: '<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 16 4.5-4.5 3 3L15 11l5 5"/>',
  vid: '<rect x="3" y="6" width="12" height="12" rx="2"/><path d="m15 10.5 5-3v9l-5-3z"/>',
  zip: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M11 10.5h2M11 13.5h2M11 16.5h2"/>',
  doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 16.5h4"/>',
  gen: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
};
const svgIcon = (k, size = 17) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[k] || ICONS.gen}</svg>`;

const ACT_ICONS = {
  share: '<circle cx="17.5" cy="5.5" r="2.6"/><circle cx="6.5" cy="12" r="2.6"/><circle cx="17.5" cy="18.5" r="2.6"/><path d="m8.8 10.8 6.4-3.9M8.8 13.2l6.4 3.9"/>',
  down: '<path d="M12 4v12"/><path d="M7.5 11.5 12 16l4.5-4.5"/><path d="M4 19h16"/>',
  del: '<path d="M4 7h16"/><path d="M9.5 7V5.5A1.5 1.5 0 0 1 11 4h2a1.5 1.5 0 0 1 1.5 1.5V7"/><path d="M6.5 7l.8 12A1.5 1.5 0 0 0 8.8 20.5h6.4a1.5 1.5 0 0 0 1.5-1.4L17.5 7"/>',
  edit: '<path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17z"/><path d="M14.5 6.5 17.5 9.5"/>',
};
const rowBtn = (act, id, title, cls = '') =>
  `<button class="r-btn ${cls}" data-${act}="${id}" title="${title}">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${ACT_ICONS[act]}</svg></button>`;

function toast(msg) {
  let t = $('.toast');
  if (!t) { t = document.createElement('div'); t.className = 'toast'; document.body.appendChild(t); }
  t.textContent = msg;
  requestAnimationFrame(() => t.classList.add('on'));
  clearTimeout(t._tm);
  t._tm = setTimeout(() => t.classList.remove('on'), 2300);
}

/* ---------------- 加载 ---------------- */
async function load(folderId) {
  state.folderId = folderId || null;
  state.selected.clear();
  await Promise.all([renderList(), renderTree(), renderStats(), renderCrumbs()]);
}

async function renderList() {
  state.nodes = await api(`/api/nodes?parent_id=${state.folderId || ''}`);
  render();
}

async function renderTree() {
  const roots = await api('/api/nodes?parent_id=');
  const folders = roots.filter(n => n.type === 'folder');
  const box = $('#navTree');

  if (!folders.length) {
    box.innerHTML = '<p class="nav-empty">暂无文件夹</p>';
  } else {
    box.innerHTML = folders.map(f => `
      <div class="tree-item ${f.id === state.folderId ? 'active' : ''}" data-id="${f.id}" title="${esc(f.name)}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${ICONS.folder}</svg>
        <span>${esc(f.name)}</span>
      </div>`).join('');
  }
  $('#navAllCount').textContent = roots.length;
}

async function renderStats() {
  // 统计信息随容量接口一起返回，避免多次请求
  await loadCapacity();
}

/* ---------------- 存储容量 ---------------- */
// 精确格式化：按字节数输出 GB/MB，保留 2 位小数
function fmtCap(bytes) {
  if (bytes === null || bytes === undefined) return '--';
  if (bytes <= 0) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0, v = bytes;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  if (i === 0) return `${Math.round(v)} B`;
  // 不足 10 的保留 2 位，如 9.45 GB；更大的取整，如 512 GB
  return v < 10 ? `${v.toFixed(2)} ${u[i]}` : `${Math.round(v)} ${u[i]}`;
}

/**
 * 加载并渲染容量。
 * refresh=true 时跳过缓存强制读盘（上传/删除后调用）。
 */
async function loadCapacity(refresh = false) {
  const box = $('#quotaBox');
  try {
    const c = await api(`/api/capacity?refresh=${refresh ? 'true' : 'false'}`);

    if (c.backend !== 'local') {
      // 对象存储模式：磁盘字段无意义
      $('#capUsed').textContent = fmtCap(c.drive_used);
      $('#capBar').style.width = '0%';
      $('#capUsedText').textContent = fmtCap(c.drive_used);
      $('#capTotalText').textContent = '对象存储';
      $('#diskText').textContent = '-';
      $('#capPath').textContent = c.storage_path || '-';
      $('#statFiles').textContent = c.file_count;
      $('#statFolders').textContent = c.folder_count;
      return;
    }

    // 主指标：已用 / 可用总量
    const used = c.drive_used;
    const total = c.quota_total || c.disk_total;
    const free = c.quota_free;

    $('#capUsed').textContent = fmtCap(used);
    $('#capUsedText').textContent = fmtCap(used);
    $('#capTotalText').textContent = fmtCap(total);

    // 进度条：已用占总量的百分比
    const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
    const bar = $('#capBar');
    bar.style.width = pct.toFixed(2) + '%';
    // 超过 85% 转黄，超过 95% 转红
    box.querySelector('.quota-bar').classList.toggle('warn', pct >= 85 && pct < 95);
    box.querySelector('.quota-bar').classList.toggle('danger', pct >= 95);

    // 磁盘真实数据
    $('#diskText').textContent = `${fmtCap(c.disk_free)} 可用 / ${fmtCap(c.disk_total)}`;
    $('#diskText').title = `已用 ${fmtCap(c.disk_used)}（${c.disk_percent}%）`;

    const pathEl = $('#capPath');
    pathEl.textContent = c.storage_path || '-';
    pathEl.title = c.storage_path || '';

    $('#statFiles').textContent = c.file_count;
    $('#statFolders').textContent = c.folder_count;

    box.title = `存储 ${fmtCap(used)} / 可用 ${fmtCap(free)}\n磁盘 ${fmtCap(c.disk_free)} 可用`;
  } catch (e) {
    console.warn('读取容量失败', e);
    $('#capUsed').textContent = '--';
  }
}

/** 容量轮询定时器 */
let _capTimer = null;
function startCapacityPolling(intervalMs = 15000) {
  stopCapacityPolling();
  _capTimer = setInterval(() => {
    // 页面隐藏时不轮询，省资源
    if (document.visibilityState === 'visible') loadCapacity(false);
  }, intervalMs);
}
function stopCapacityPolling() {
  if (_capTimer) { clearInterval(_capTimer); _capTimer = null; }
}

/* ---------------- 设置 ---------------- */
const fmtBytes = fmtSize;

async function loadSettings() {
  try {
    const cfg = await api('/api/settings');
    return cfg;
  } catch (e) {
    console.warn('读取设置失败', e);
    return null;
  }
}

/** 退出登录：清本地令牌 + 清 Cookie，然后回登录页 */
async function doLogout() {
  if (!confirm('确定退出登录？')) return;
  try {
    await api('/api/auth/logout', { method: 'POST' });
  } catch (e) { /* 忽略网络错误，本地照样清 */ }
  localStorage.removeItem('cr_token');
  location.replace('/login');
}

async function openSettings() {
  const cfg = await loadSettings();
  if (!cfg) return toast('无法读取设置');

  const mb = (b) => (b / 1024 ** 2).toFixed(0) + ' MB';
  const gb = (b) => (b / 1024 ** 3).toFixed(2) + ' GB';

  modal('存储设置', `
    <div class="set-info">
      <span>磁盘剩余 <b>${cfg.free_space ? gb(cfg.free_space) : '未知'}</b></span>
      <span>已用 <b>${cfg.used_space ? gb(cfg.used_space) : '未知'}</b></span>
      <span>目录 <b>${cfg.exists ? '正常' : '不存在'}</b></span>
    </div>

    <div class="set-row">
      <label>存储后端</label>
      <select id="setBackend">
        <option value="local" ${cfg.storage_backend === 'local' ? 'selected' : ''}>本地磁盘</option>
        <option value="s3" ${cfg.storage_backend === 's3' ? 'selected' : ''}>S3 / MinIO 对象存储</option>
      </select>
      <div class="hint">对象存储模式下浏览器直连上传下载，不占用服务器带宽</div>
    </div>

    <div class="set-row">
      <label>存储路径</label>
      <div class="set-inline">
        <input type="text" id="setPath" class="mono" value="${cfg.current_root}" spellcheck="false">
        <button class="set-browse" id="btnBrowse">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.3l2 2.4h8.7A1.5 1.5 0 0 1 21 9.9V18a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18z"/></svg>
          浏览</button>
      </div>
      <div class="hint">目录不存在会自动创建。支持绝对路径（Linux 如 /data/files，Windows 如 D:\\files）与相对路径</div>
      <div id="dirList"></div>
    </div>

    <div class="set-row">
      <label>分片大小（字节）</label>
      <input type="text" id="setChunk" value="${cfg.multipart_chunk_size}">
      <div class="hint">当前 ${mb(cfg.multipart_chunk_size)}。不能小于 5 MiB（S3 协议要求）。越大越省请求，但单片失败需重传更多</div>
    </div>

    <div class="acts">
      <button class="act act-ghost" id="setReset">恢复默认</button>
      <button class="act act-primary" id="setSave">保存并应用</button>
    </div>`);

  $('#btnBrowse').onclick = () => browseDir($('#setPath').value.trim());
  $('#setSave').onclick = async () => {
    const btn = $('#setSave');
    btn.disabled = true; btn.textContent = '应用中…';
    try {
      await api('/api/settings', {
        method: 'PUT',
        body: {
          local_storage_dir: $('#setPath').value.trim(),
          storage_backend: $('#setBackend').value,
          multipart_chunk_size: parseInt($('#setChunk').value, 10),
        },
      });
      closeModal();
      toast('设置已生效');
      await load(state.folderId);
      // 存储路径可能变了，强制刷新容量
      await loadCapacity(true);
    } catch (e) {
      toast(e.message);
      btn.disabled = false; btn.textContent = '保存并应用';
    }
  };

  $('#setReset').onclick = async () => {
    try {
      await api('/api/settings/reset', { method: 'POST' });
      closeModal(); toast('已恢复默认');
      await loadSettings();
      await load(state.folderId);
    } catch (e) { toast(e.message); }
  };
}

async function browseDir(path) {
  try {
    const r = await api('/api/settings/browse?path=' + encodeURIComponent(path || ''));
    const box = $('#dirList');
    const up = path && path !== '/' ? `<div class="dir-item" data-p="${esc(parentOf(path))}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="m11 18-6-6 6-6"/></svg>
        <span>上级目录</span></div>` : '';

    const dirs = r.entries.filter(e => e.is_dir);
    if (!dirs.length && !up) {
      box.innerHTML = `<div class="dir-item" style="color:var(--t3);cursor:default">该目录下没有子文件夹</div>`;
      return;
    }
    box.innerHTML = `<div class="dir-list">${up}${dirs.map(d => `
      <div class="dir-item" data-p="${esc(d.path)}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${ICONS.folder}</svg>
        <span>${esc(d.name)}</span>
      </div>`).join('')}</div>`;

    box.querySelectorAll('.dir-item[data-p]').forEach(el => {
      el.onclick = () => {
        $('#setPath').value = el.dataset.p;
        browseDir(el.dataset.p);
      };
    });
  } catch (e) { toast('无法读取目录'); }
}

const parentOf = (p) => {
  const parts = p.replace(/[\\/]+$/, '').split(/[\\/]/);
  parts.pop();
  return parts.join('/') || '/';
};

async function renderCrumbs() {
  const box = $('#crumbs');
  if (!state.folderId) {
    $('#pageTitle').textContent = '全部文件';
    box.innerHTML = '';
    return;
  }
  const d = await api(`/api/nodes/detail?id=${state.folderId}`);
  $('#pageTitle').textContent = d.node.name;
  const chain = [{ name: '全部文件', id: '' }, ...d.breadcrumb];
  box.innerHTML = chain.map((p, i) => `
    ${i ? '<span class="crumb-sep">/</span>' : ''}
    <span class="crumb ${i === chain.length - 1 ? 'last' : ''}" data-id="${p.id}">${esc(p.name)}</span>
  `).join('');
}

/* ---------------- 渲染 ---------------- */
function currentList() {
  const q = ($('#searchInput').value || '').trim().toLowerCase();
  const list = q ? state.nodes.filter(n => n.name.toLowerCase().includes(q)) : [...state.nodes];
  list.sort((a, b) =>
    a.type === b.type ? a.name.localeCompare(b.name, 'zh') : a.type === 'folder' ? -1 : 1);
  return list;
}

function render() {
  const list = currentList();
  const isEmpty = list.length === 0;
  const searching = !!($('#searchInput').value || '').trim();

  $('#empty').hidden = !isEmpty;
  $('#listView').hidden = isEmpty || state.view !== 'list';
  $('#gridView').hidden = isEmpty || state.view !== 'grid';

  if (isEmpty) {
    $('#emptyTitle').textContent = searching ? '没有匹配的文件' : '这个文件夹还是空的';
    $('#emptySub').textContent = searching ? '换个关键词试试' : '把文件拖进来，或点击左上角上传';
    $('#emptyUpload').hidden = searching;
  }
  if (!isEmpty) state.view === 'list' ? renderRows(list) : renderCards(list);
  renderToolbar();
}

function renderRows(list) {
  $('#fileList').innerHTML = list.map(n => {
    const k = kind(n.name, n.type === 'folder');
    const sel = state.selected.has(n.id);
    const tag = n.type === 'folder'
      ? '<span class="tag">文件夹</span>'
      : (n.share_enabled ? '<span class="tag share">已分享</span>' : '');
    return `<div class="row ${sel ? 'sel' : ''}" data-id="${n.id}">
      <label class="checkbox"><input type="checkbox" class="row-check" data-id="${n.id}" ${sel ? 'checked' : ''}><span></span></label>
      <div class="r-name">
        <div class="r-ico ${k}">${svgIcon(k)}</div>
        <div class="r-text">
          <div class="r-label" data-open="${n.id}" title="${esc(n.name)}">${esc(n.name)}</div>
          ${tag ? `<div class="r-tags">${tag}</div>` : ''}
        </div>
      </div>
      <span class="r-size">${n.type === 'folder' ? '—' : fmtSize(n.size)}</span>
      <span class="r-time">${fmtTime(n.updated_at)}</span>
      <div class="r-act">
        ${rowBtn('share', n.id, '分享')}
        ${rowBtn('down', n.id, '下载')}
        ${rowBtn('edit', n.id, '重命名')}
        ${rowBtn('del', n.id, '删除', 'dgr')}
      </div>
    </div>`;
  }).join('');
}

function renderCards(list) {
  $('#gridView').innerHTML = list.map(n => {
    const k = kind(n.name, n.type === 'folder');
    const sel = state.selected.has(n.id);
    const meta = n.type === 'folder'
      ? '文件夹'
      : `${fmtSize(n.size)}${n.share_enabled ? ' · 已分享' : ''}`;
    return `<div class="card ${sel ? 'sel' : ''}" data-id="${n.id}">
      <div class="card-check">
        <label class="checkbox"><input type="checkbox" class="row-check" data-id="${n.id}" ${sel ? 'checked' : ''}><span></span></label>
      </div>
      <div class="c-ico r-ico ${k}">${svgIcon(k, 22)}</div>
      <div class="c-name" title="${esc(n.name)}">${esc(n.name)}</div>
      <div class="c-meta">${meta}</div>
    </div>`;
  }).join('');
}

function renderToolbar() {
  const ids = $$('.row-check');
  const n = state.selected.size;
  const total = ids.length;
  const allChecked = total > 0 && n === total;
  const indeterminate = n > 0 && !allChecked;

  $('#toolbar').hidden = n === 0;
  $('#selCount').textContent = `已选 ${n} 项`;

  [$('#checkAll'), $('#checkAllBar')].forEach(cb => {
    if (!cb) return;
    cb.checked = allChecked;
    cb.indeterminate = indeterminate;
  });
}

/* ---------------- 上传 ---------------- */
// 单文件内部并发。多文件同时上传时共享这个池，
// 避免 3 文件 × 4 并发 = 12 请求压垮本地服务导致中断。
const CONCURRENCY = 3;
const RETRIES = 5;          // 加大重试次数，应对大文件长传输
const GLOBAL_MAX = 6;       // 全局并发上限

let _active = 0;
const _waiting = [];

/** 获取并发槽位，无空位时排队等待 */
function acquireSlot() {
  if (_active < GLOBAL_MAX) { _active++; return Promise.resolve(); }
  return new Promise(resolve => _waiting.push(() => { _active++; resolve(); }));
}
function releaseSlot() {
  _active--;
  const next = _waiting.shift();
  if (next) next();
}

function upPanelShow() {
  const p = $('#uploadPanel');
  p.hidden = false;
  $('#upTitle').textContent = state.uploadTotal > 1
    ? `正在上传 ${state.uploadTotal} 个文件（${state.uploadDone}/${state.uploadTotal} 完成）`
    : '正在上传 1 个文件';
}

async function uploadFiles(files) {
  if (!files.length) return;
  state.uploadTotal += files.length;
  state.uploadDone = 0;
  upPanelShow();
  // 逐个串行上传，避免多文件同时抢带宽导致整体变慢
  for (const f of files) await uploadOne(f);
  await renderList();
  // 上传后强制刷新容量（跳过缓存，立即反映新占用）
  await loadCapacity(true);
}

async function uploadOne(file) {
  const el = document.createElement('div');
  el.className = 'up-item';
  el.innerHTML = `
    <div class="up-row">
      <div class="up-ico"><svg width="15" height="15" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 16V4"/><path d="M7.5 8.5 12 4l4.5 4.5"/><path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15"/></svg></div>
      <div class="up-info">
        <div class="up-name">${esc(file.name)}</div>
        <div class="up-meta">${fmtSize(file.size)}</div>
      </div>
      <div class="up-right">
        <span class="up-pct">0%</span>
        <button class="up-x" title="取消">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>
        </button>
      </div>
    </div>
    <div class="up-bar"><div class="up-fill"></div></div>
    <div class="up-err" hidden></div>`;

  $('#uploadQueue').prepend(el);
  const pct = el.querySelector('.up-pct');
  const fill = el.querySelector('.up-fill');
  const errBox = el.querySelector('.up-err');
  let aborted = false;
  let nodeId = null;          // 记录已创建的节点，失败时回滚
  el.querySelector('.up-x').onclick = () => { aborted = true; };

  const setPct = (p) => {
    const v = Math.max(0, Math.min(100, Math.round(p)));
    pct.textContent = v + '%';
    fill.style.width = v + '%';
  };

  try {
    const info = await api('/api/files/prepare', {
      method: 'POST',
      body: { filename: file.name, size: file.size, mime_type: file.type, parent_id: state.folderId },
    });
    nodeId = info.node_id;

    const done = new Set();
    const prog = () => setPct((done.size / (info.part_count || 1)) * 100);

    if (info.mode === 'proxy') {
      await xhrUpload(file, info.upload_url, (f) => setPct(f * 100), () => aborted);
    } else if (info.mode === 'multipart') {
      await s3Multipart(file, info, done, prog, () => aborted);
    } else {
      await localMultipart(file, info, done, prog, () => aborted);
    }

    if (aborted) throw new Error('已取消');
    fill.classList.add('ok');
    setPct(100);
    state.uploadDone++;
    upPanelShow();
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 260); }, 900);
  } catch (e) {
    // 失败时清理占位节点，避免列表出现"幽灵文件"（有记录无内容）
    if (nodeId) {
      try { await api(`/api/files/raw/${nodeId}`, { method: 'DELETE' }); } catch (_) {}
    }

    fill.classList.add('bad');
    pct.textContent = e.message === '已取消' ? '已取消' : '失败';
    // 展示具体错误，便于定位问题
    errBox.textContent = e.message === '已取消' ? '' : e.message;
    errBox.hidden = e.message === '已取消';
    el.querySelector('.up-x').remove();
    state.uploadDone++;
    await renderList();
    await loadCapacity(true);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 260); }, e.message === '已取消' ? 1200 : 6000);
  }

  if (state.uploadDone >= state.uploadTotal) {
    setTimeout(() => { $('#uploadQueue').innerHTML = ''; state.uploadTotal = 0; state.uploadDone = 0; }, 1200);
  }
}

function xhrUpload(file, url, onProgress, isAborted) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url, true);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    // 带上登录令牌，否则大文件上传会被 401拒绝
    const tk = localStorage.getItem('cr_token');
    if (tk) xhr.setRequestHeader('Authorization', `Bearer ${tk}`);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
      if (isAborted()) { xhr.abort(); reject(new Error('已取消')); }
    };
    xhr.onload = () => xhr.status < 400 ? resolve() : reject(new Error('上传失败 ' + xhr.status));
    xhr.onerror = () => reject(new Error('网络错误'));
    xhr.send(file);
  });
}

async function s3Multipart(file, info, done, prog, isAborted) {
  const CHUNK = info.chunk_size, total = info.part_count;
  const queue = Array.from({ length: total }, (_, i) => i + 1);
  const etags = new Map();

  const worker = async () => {
    while (queue.length) {
      if (isAborted()) throw new Error('已取消');
      const n = queue.shift();
      const blob = file.slice((n - 1) * CHUNK, Math.min(n * CHUNK, file.size));
      const { urls } = await api(`/api/files/multipart/${info.node_id}/sign`, { method: 'POST', body: [n] });
      const url = urls.find(u => u.partNumber === n)?.url;
      if (!url) throw new Error('分片签名失败');
      const resp = await retry(
        () => fetch(url, {
          method: 'PUT',
          body: blob,
          headers: authHeaders(),
          credentials: 'same-origin',
        }),
        isAborted, RETRIES, n
      );
      const etag = resp.headers.get('ETag') || '"x"';
      etags.set(n, { PartNumber: n, ETag: etag });
      done.add(n); prog();
    }
  };
  await Promise.all(Array.from({ length: CONCURRENCY }, worker));

  if (etags.size !== total) {
    throw new Error(`分片不完整（${etags.size}/${total}），请重新上传`);
  }

  await api(`/api/files/multipart/${info.node_id}/complete`, {
    method: 'POST', body: { parts: [...etags.values()] },
  });
}

async function localMultipart(file, info, done, prog, isAborted) {
  const CHUNK = info.chunk_size, total = info.part_count;
  const queue = Array.from({ length: total }, (_, i) => i + 1);
  const parts = [];

  const worker = async () => {
    while (queue.length) {
      if (isAborted()) throw new Error('已取消');
      const n = queue.shift();
      const blob = file.slice((n - 1) * CHUNK, Math.min(n * CHUNK, file.size));

      await acquireSlot();
      let r;
      try {
        r = await retry(
          () => fetch(`/api/files/part/${info.node_id}/${n}`, {
            method: 'PUT',
            body: blob,
            headers: authHeaders(),
            credentials: 'same-origin',
          }),
          isAborted,
          RETRIES,
          n
        );
      } finally {
        releaseSlot();
      }

      const d = await r.json();
      parts.push({ partNumber: n, etag: d.etag });
      done.add(n); prog();
    }
  };
  await Promise.all(Array.from({ length: CONCURRENCY }, worker));

  // 合并前确认分片齐全，避免合并出残缺文件
  if (parts.length !== total) {
    throw new Error(`分片不完整（${parts.length}/${total}），请重新上传`);
  }

  // 合并大文件耗时较长，给足超时时间
  await api(`/api/files/multipart/${info.node_id}/complete`, {
    method: 'POST',
    body: { parts },
    timeout: 30 * 60 * 1000,
  });
}

async function retry(fn, isAborted, times = RETRIES, partNo = null) {
  let last;
  for (let i = 0; i < times; i++) {
    if (isAborted()) throw new Error('已取消');
    try {
      const r = await fn();
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r;
    } catch (e) {
      last = e;
      // 指数退避 + 随机抖动，避免多请求同时重试形成尖峰
      const wait = Math.min(800 *Math.pow(2, i) + Math.random() * 400, 8000);
      if (partNo !== null && i === times - 1) {
        last = new Error(`分片 ${partNo} 上传失败：${e.message}`);
      }
      await new Promise(r => setTimeout(r, wait));
    }
  }
  throw last;
}

/* ---------------- 操作 ---------------- */
async function createFolder() {
  modal('新建文件夹', `
    <input type="text" id="nfInput" placeholder="文件夹名称" maxlength="255">
    <div class="acts">
      <button class="act act-ghost" id="nfCancel">取消</button>
      <button class="act act-primary" id="nfOk">创建</button>
    </div>`);
  const input = $('#nfInput');
  input.focus();
  const ok = async () => {
    const v = input.value.trim();
    if (!v) { input.focus(); return; }
    try {
      await api('/api/nodes/folder', { method: 'POST', body: { name: v, parent_id: state.folderId } });
      closeModal(); toast('文件夹已创建');
      await renderList(); await renderTree();
    } catch (e) { toast(e.message); }
  };
  $('#nfOk').onclick = ok;
  $('#nfCancel').onclick = closeModal;
  input.onkeydown = e => { if (e.key === 'Enter') ok(); };
}

async function doDelete(ids) {
  if (!ids.length) return;
  const hasFolder = state.nodes.some(n => ids.includes(n.id) && n.type === 'folder');
  modal('确认删除', `
    <p>${hasFolder
      ? `即将删除选中的 ${ids.length} 项，其中包含文件夹。文件夹内的所有文件将被<b>永久删除</b>，无法恢复。`
      : `即将删除选中的 ${ids.length} 个文件，此操作<b>无法恢复</b>。`}</p>
    <div class="acts">
      <button class="act act-ghost" id="delCancel">取消</button>
      <button class="act act-danger" id="delOk">永久删除</button>
    </div>`);
  $('#delCancel').onclick = closeModal;
  $('#delOk').onclick = async () => {
    try {
      await api('/api/nodes/batch-delete', { method: 'POST', body: { ids } });
      closeModal(); toast('已删除');
      await load(state.folderId);
      await renderTree();
    } catch (e) { toast(e.message); }
  };
}

async function doShare(id) {
  let s;
  try { s = await api(`/api/nodes/${id}/share`); }
  catch (e) { return toast(e.message); }

  const full = location.origin + s.url;
  modal('分享链接', `
    <p>${s.enabled ? '链接已生成，任何人打开即可下载，无需登录。' : '分享已关闭，链接暂时失效。'}</p>
    <div class="share-field">
      <input type="text" id="shareInput" value="${esc(full)}" readonly>
      <button class="btn-copy" id="btnCopy">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>复制</button>
    </div>
    <div class="note">链接永久有效，直到文件被删除。${s.enabled ? '' : '重新开启后仍是同一链接。'}</div>
    <div class="acts" style="margin-top:16px">
      <button class="act act-ghost" id="btnToggleShare">${s.enabled ? '关闭分享' : '开启分享'}</button>
      <button class="act act-primary" id="btnDone">完成</button>
    </div>`);

  $('#btnCopy').onclick = () => {
    $('#shareInput').select();
    navigator.clipboard?.writeText(full).then(() => toast('已复制到剪贴板'),
      () => { document.execCommand('copy'); toast('已复制'); });
  };
  $('#btnDone').onclick = closeModal;
  $('#btnToggleShare').onclick = async () => {
    try {
      await api(`/api/nodes/${id}/share`, { method: 'POST', body: { enabled: !s.enabled } });
      closeModal(); doShare(id);
    } catch (e) { toast(e.message); }
  };
}

async function doMove() {
  const ids = [...state.selected];
  const all = await api('/api/nodes?parent_id=');
  const folders = all.filter(t => t.type === 'folder' && !ids.includes(t.id));
  pickTarget(folders, '移动到', async (tid) => {
    try {
      const r = await api('/api/nodes/batch-move', { method: 'POST', body: { ids, parent_id: tid } });
      if (r.failed.length) toast(`${r.succeeded.length} 项成功，${r.failed.length} 项失败`);
      else toast('已移动');
      await load(state.folderId); await renderTree();
    } catch (e) { toast(e.message); }
  });
}

async function doRename() {
  const id = [...state.selected][0];
  const n = state.nodes.find(x => x.id === id);
  if (!n) return toast('请先选择一项');

  modal('重命名', `
    <input type="text" id="rnInput" value="${esc(n.name)}" maxlength="255">
    <div class="acts">
      <button class="act act-ghost" id="rnCancel">取消</button>
      <button class="act act-primary" id="rnOk">保存</button>
    </div>`);

  const input = $('#rnInput');
  input.focus();
  const dot = n.name.lastIndexOf('.');
  input.setSelectionRange(0, dot > 0 ? dot : n.name.length);

  const ok = async () => {
    const v = input.value.trim();
    if (!v) { input.focus(); return; }
    try {
      if (v !== n.name) await api(`/api/nodes/${id}/rename`, { method: 'PATCH', body: { name: v } });
      closeModal(); await renderList();
    } catch (e) { toast(e.message); }
  };
  $('#rnOk').onclick = ok;
  $('#rnCancel').onclick = closeModal;
  input.onkeydown = e => { if (e.key === 'Enter') ok(); };
}

function doDownload(ids) {
  ids.forEach((id, i) => {
    const n = state.nodes.find(x => x.id === id);
    const url = n?.type === 'folder' ? `/api/files/${id}/download-zip` : `/api/files/${id}/download`;
    setTimeout(() => {
      const a = document.createElement('a');
      a.href = url; a.download = ''; document.body.appendChild(a); a.click(); a.remove();
    }, i * 350);
  });
}

/* ---------------- 弹窗 ---------------- */
function modal(title, html) {
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = html;
  $('#modalMask').hidden = false;
}
function closeModal() { $('#modalMask').hidden = true; }

function pickTarget(folders, title, onPick) {
  modal(title, `
    <div class="pick">
      <div class="pick-item" data-id="">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${ICONS.folder}</svg>
        <span>根目录</span>
      </div>
      ${folders.map(f => `<div class="pick-item" data-id="${f.id}">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${ICONS.folder}</svg>
        <span>${esc(f.name)}</span></div>`).join('')}
    </div>
    <div class="acts"><button class="act act-ghost" id="pkCancel">取消</button></div>`);

  $$('.pick-item').forEach(el => el.onclick = () => { closeModal(); onPick(el.dataset.id || null); });
  $('#pkCancel').onclick = closeModal;
}

/* ---------------- 事件 ---------------- */
document.addEventListener('click', async (e) => {
  const t = e.target;

  // 复选框优先处理，避免触发行的点击逻辑
  if (t.closest('.checkbox')) {
    const cb = t.closest('.row-check');
    if (cb) {
      cb.checked ? state.selected.add(cb.dataset.id) : state.selected.delete(cb.dataset.id);
      cb.closest('.row, .card')?.classList.toggle('sel', cb.checked);
      renderToolbar();
    }
    return;
  }

  const open = t.closest('[data-open]');
  if (open) {
    const id = open.dataset.open;
    const n = state.nodes.find(x => x.id === id);
    if (n?.type === 'folder') await load(id);
    else if (n) { const a = document.createElement('a'); a.href = `/api/files/${id}/download`; a.click(); }
    return;
  }

  // 网格卡片：点卡片切换选中，点名字进入
  const card = t.closest('.card');
  if (card && !t.closest('.card-check')) {
    const id = card.dataset.id;
    const n = state.nodes.find(x => x.id === id);
    if (n?.type === 'folder') await load(id);
    else { const a = document.createElement('a'); a.href = `/api/files/${id}/download`; a.click(); }
    return;
  }

  if (t.closest('#btnUpload') || t.closest('#emptyUpload')) { $('#fileInput').click(); return; }
  if (t.closest('#btnNewFolder')) { createFolder(); return; }
  if (t.closest('#btnSettings')) { openSettings(); return; }
  if (t.closest('#btnLogout')) { doLogout(); return; }
  if (t.closest('#btnRefresh')) {
    await load(state.folderId);
    await loadCapacity(true);
    toast('已刷新');
    return;
  }
  if (t.closest('#btnClearSel')) { state.selected.clear(); render(); return; }

  const sh = t.closest('[data-share]'); if (sh) return doShare(sh.dataset.share);
  const dn = t.closest('[data-down]');  if (dn) return doDownload([dn.dataset.down]);
  const dl = t.closest('[data-del]');   if (dl) return doDelete([dl.dataset.del]);
  const ed = t.closest('[data-edit]');  if (ed) { state.selected.clear(); state.selected.add(ed.dataset.edit); return doRename(); }

  const act = t.closest('[data-act]');
  if (act) {
    const a = act.dataset.act, ids = [...state.selected];
    if (a === 'download') doDownload(ids);
    else if (a === 'move') doMove();
    else if (a === 'share') { ids.length === 1 ? doShare(ids[0]) : toast('请选择一项'); }
    else if (a === 'rename') doRename();
    else if (a === 'delete') doDelete(ids);
    return;
  }

  const seg = t.closest('.seg');
  if (seg) {
    state.view = seg.dataset.view;
    localStorage.setItem('cr_view', state.view);
    $$('.seg').forEach(s => s.classList.toggle('active', s === seg));
    render();
    return;
  }

  const nav = t.closest('.nav-item'); if (nav) return load(nav.dataset.folder || null);
  const tree = t.closest('.tree-item'); if (tree) return load(tree.dataset.id);
  const crumb = t.closest('.crumb:not(.last)'); if (crumb) return load(crumb.dataset.id || null);

  if (t.id === 'modalX' || t.id === 'modalMask') closeModal();
});

document.addEventListener('change', (e) => {
  if (e.target.id === 'checkAll' || e.target.id === 'checkAllBar') {
    const on = e.target.checked;
    state.selected.clear();
    if (on) $$('.row-check').forEach(c => { state.selected.add(c.dataset.id); c.checked = true; });
    render();
  }
});

$('#searchInput').addEventListener('input', render);
$('#fileInput').addEventListener('change', e => { uploadFiles([...e.target.files]); e.target.value = ''; });
$('#upClear').addEventListener('click', () => {
  $$('.up-item').forEach(el => {
    const done = el.querySelector('.up-fill')?.classList.contains('ok');
    if (done) el.remove();
  });
});

// 拖拽上传：整页覆盖层
const content = $('#content');
let dragDepth = 0;
['dragenter', 'dragover'].forEach(ev => document.addEventListener(ev, e => {
  e.preventDefault();
  if (ev === 'dragenter') dragDepth++;
  $('#dropOverlay').classList.add('on');
}));
document.addEventListener('dragleave', e => {
  e.preventDefault();
  if (--dragDepth <= 0) { dragDepth = 0; $('#dropOverlay').classList.remove('on'); }
});
document.addEventListener('drop', e => {
  e.preventDefault();
  dragDepth = 0;
  $('#dropOverlay').classList.remove('on');
  const files = [...(e.dataTransfer?.files || [])];
  if (files.length) uploadFiles(files);
});

// 粘贴上传
document.addEventListener('paste', e => {
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) uploadFiles(files);
});

// 键盘快捷键
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeModal(); return; }
  // Ctrl+A 全选当前列表（弹窗打开时或输入框聚焦时不拦截）
  const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || '');
  if ((e.ctrlKey || e.metaKey) && e.key === 'a' && !typing && $('#modalMask').hidden) {
    e.preventDefault();
    $$('.row-check').forEach(c => { state.selected.add(c.dataset.id); c.checked = true; });
    renderToolbar();
  }
});

/* ---------------- 启动 ---------------- */
(async function init() {
  $$('.seg').forEach(s => s.classList.toggle('active', s.dataset.view === state.view));

  try {
    // 先确认登录态。未登录时后端返回 401，api() 会自动跳转登录页。
    const auth = await fetch('/api/auth/status', { credentials: 'same-origin' })
      .then(r => r.json())
      .catch(() => null);

    if (auth && auth.must_login) {
      location.replace('/login');
      return;
    }

    // 启用了认证才显示「退出登录」
    const lo = $('#btnLogout');
    if (lo) lo.hidden = !(auth && auth.enabled);

    await loadSettings();
    await load(null);

    // 容量定时刷新（15秒），文件增删后自动同步
    startCapacityPolling(15000);
  } catch (err) {
    $('#listView').hidden = true;
    $('#gridView').hidden = true;
    const e = $('#empty');
    e.hidden = false;
    // 区分「未登录」与「连不上服务器」
    const notAuth = err && /登录/.test(err.message || '');
    $('#emptyTitle').textContent = notAuth ? '请先登录' : '无法连接到服务器';
    $('#emptySub').textContent = notAuth
      ? '正在跳转到登录页…'
      : '请确认后端服务已启动（双击 start.bat）';
    $('#emptyUpload').hidden = true;
    if (!notAuth) console.error('[CloudRive] 初始化失败:', err);
  }
})();