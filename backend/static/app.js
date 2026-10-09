/* ============================================================
   AssetBook · 前端逻辑（无依赖，原生 JS）
   ============================================================ */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const COLORS = ['#1c6b52', '#b2612e', '#4a6fa5', '#c08a2d', '#7d5ba6',
                '#3e8f83', '#a85d75', '#6f6a5e', '#8fae4f', '#b23c2c'];

const state = {
  view: 'overview',
  accounts: [],
  categories: [],
  rules: [],
  overview: null,
  meta: { markets: [], holding_kinds: {}, account_kinds: {} },
  nwRange: '30',
  tx: { page: 0, limit: 25, total: 0 },
  budgetMonth: new Date().toISOString().slice(0, 7),
};

/* ---------------- 基础工具 ---------------- */

const fmt = (n) => '¥' + (Number(n) || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt0 = (n) => '¥' + Math.round(Number(n) || 0).toLocaleString('zh-CN');
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const today = () => new Date().toISOString().slice(0, 10);
const cls = (n) => (n > 0 ? 'pos' : n < 0 ? 'neg' : '');

function toast(msg, isErr) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.toggle('err', !!isErr);
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 2200);
}

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (opts.form) {
    init.body = opts.form;
  } else if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  if (res.status === 401) {
    showGate(true);
    throw new Error('需要登录');
  }
  if (opts.raw) return res;
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || ('请求失败 ' + res.status));
  return data;
}

function openModal(title, html, onMount) {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = html;
  $('#modal').hidden = false;
  if (onMount) onMount($('#modal-body'));
}
function closeModal() {
  $('#modal').hidden = true;
  $('#modal-body').innerHTML = '';
  $('#modal-card').classList.remove('is-wide');
}

/* ---------------- 登录 / 初始化 ---------------- */

function showGate(needLogin) {
  $('#gate').hidden = false;
  $('#app').hidden = true;
  $('#gate-confirm-wrap').hidden = $('#gate').dataset.setup !== '1';
  $('#gate-title').textContent = needLogin ? '输入密码' : '设置一个密码';
  $('#gate-hint').textContent = needLogin
    ? '数据只存在你自己的服务器上。'
    : '第一次使用，先设个密码，避免公网部署时被人随便打开。';
  $('#gate-submit').textContent = needLogin ? '进入账本' : '创建并进入';
  setTimeout(() => $('#gate-password').focus(), 60);
}

async function onSubmitGate(e) {
  e.preventDefault();
  const isSetup = $('#gate').dataset.setup === '1';
  const pw = $('#gate-password').value;
  try {
    if (isSetup) {
      if (pw !== $('#gate-confirm').value) return toast('两次输入不一致', true);
      await api('/api/auth/setup', { method: 'POST', body: { password: pw } });
    } else {
      await api('/api/auth/login', { method: 'POST', body: { password: pw } });
    }
    $('#gate-password').value = '';
    $('#gate-confirm').value = '';
    await enterApp();
  } catch (err) {
    toast(err.message, true);
  }
}

async function enterApp() {
  $('#gate').hidden = true;
  $('#app').hidden = false;
  await reloadAll();
  go(currentView());
}

async function reloadAll() {
  const [accounts, categories, rules, meta] = await Promise.all([
    api('/api/accounts'), api('/api/categories'), api('/api/rules'), api('/api/meta'),
  ]);
  state.accounts = accounts.items;
  state.categories = categories.items;
  state.rules = rules.items;
  state.meta = meta;
  fillAccountSelects();
  fillCategorySelect();
  await loadOverview();
  refreshSyncNote();
}

/* ---------------- 路由 ---------------- */

const TITLES = { overview: '概览', transactions: '记账', holdings: '持仓', budgets: '预算', settings: '设置' };

// 同时接受 #overview、#/overview、#/，都归一成视图名
function currentView() {
  const raw = decodeURIComponent(location.hash).replace(/^#\/?/, '').trim();
  return TITLES[raw] ? raw : 'overview';
}

function go(view) {
  if (!TITLES[view]) view = 'overview';
  state.view = view;
  $$('.nav-item').forEach((b) => b.classList.toggle('is-on', b.dataset.view === view));
  $$('.view').forEach((s) => { s.hidden = s.dataset.view !== view; });
  $('#page-title').textContent = TITLES[view];
  if (location.hash !== '#/' + view) location.hash = '#/' + view;
  if (view === 'overview') loadOverview().then(renderOverview);
  if (view === 'transactions') loadTransactions();
  if (view === 'holdings') { loadHoldings(); refreshSyncNote(); }
  if (view === 'budgets') renderBudgets();
  if (view === 'settings') { renderSettings(); renderAutoPanel(); }
}

/* ---------------- 概览 ---------------- */

async function loadOverview() {
  state.overview = await api('/api/overview');
}

function renderOverview() {
  const o = state.overview;
  if (!o) return;
  $('#ov-net').textContent = fmt(o.net);
  $('#ov-assets').textContent = fmt0(o.assets);
  $('#ov-liab').textContent = fmt0(o.liabilities);
  $('#ov-invest').textContent = fmt0(o.invest);

  $('#ov-kpis').innerHTML = [
    { label: '本月收入', val: fmt0(o.month_in), sub: o.month },
    { label: '本月支出', val: fmt0(o.month_out), sub: '净 ' + fmt0(o.month_net) },
    { label: '储蓄率', val: (o.savings_rate || 0).toFixed(1) + '%', sub: '本月' },
    { label: '应急金', val: (o.emergency_months || 0).toFixed(1) + ' 个月', sub: '流动资产 ' + fmt0(o.liquid) },
  ].map((k) => `<div class="stat"><div class="stat-label">${k.label}</div>
      <div class="stat-val">${k.val}</div><div class="stat-sub">${esc(k.sub)}</div></div>`).join('');

  const series = state.nwRange === '30' ? o.networth_30d : o.networth_12m;
  drawLine($('#nw-chart'), series, state.nwRange === '30' ? 'date' : 'month');
  const first = series.length ? series[0].net : 0;
  const last = series.length ? series[series.length - 1].net : 0;
  $('#nw-foot').innerHTML = `<span>起点 ${fmt0(first)}</span>
    <span class="${cls(last - first)}">变化 ${last - first >= 0 ? '+' : ''}${fmt0(last - first)}</span>
    <span>现值 ${fmt0(last)}</span>`;

  drawDonut($('#alloc-donut'), $('#alloc-legend'), o.allocation);

  const cats = o.month_categories || [];
  const max = Math.max(...cats.map((c) => c.amount), 1);
  $('#ov-cats').innerHTML = cats.length ? cats.map((c, i) => `
    <div class="cat-row">
      <span class="name">${esc(c.name)}</span>
      <span class="bar"><i style="width:${(c.amount / max * 100).toFixed(1)}%;background:${c.color || COLORS[i % COLORS.length]}"></i></span>
      <span class="amt">${fmt(c.amount)}</span>
      <span class="pct">${o.month_out ? (c.amount / o.month_out * 100).toFixed(1) : '0.0'}%</span>
    </div>`).join('') : '<div class="empty">本月还没有支出记录</div>';

  $('#ov-accounts').innerHTML = o.accounts.length ? o.accounts.map((a, i) => `
    <div class="acct-item" style="--acct-color:${COLORS[i % COLORS.length]}">
      <div><div class="name">${esc(a.name)} · ${esc(a.kind_label)}</div></div>
      <div class="amt ${a.is_liability ? 'neg' : ''}">${fmt0(a.balance)}</div>
    </div>`).join('') : '<div class="empty">还没有账户</div>';

  loadRecent();
}

async function loadRecent() {
  try {
    const data = await api('/api/transactions?limit=8');
    $('#ov-recent').innerHTML = data.items.length ? data.items.map((t) => `
      <div class="mini-row">
        <span class="d">${t.date.slice(5)}</span>
        <span class="n">${esc(t.note || t.category_name || t.account_name || '')}</span>
        <span class="a ${t.type === 'in' ? 'pos' : t.type === 'out' ? 'neg' : ''}">
          ${t.type === 'out' ? '−' : t.type === 'in' ? '+' : '⇄'}${fmt0(t.amount)}</span>
      </div>`).join('') : '<div class="empty">还没有流水</div>';
  } catch (err) { /* 忽略 */ }
}

/* ---------------- 记账 ---------------- */

function accountOptions() {
  // 负债账户（信用卡/房贷）排到最后并标注，免得记账时误选
  const sorted = [...state.accounts].sort((a, b) => (a.is_liability ? 1 : 0) - (b.is_liability ? 1 : 0));
  return sorted.map((a) =>
    `<option value="${a.id}">${esc(a.name)}${a.is_liability ? '（欠款）' : ''}</option>`).join('');
}

function fillAccountSelects() {
  const opts = accountOptions();
  const main = $('#tx-account');
  const to = $('#tx-to');
  const filter = $('#f-account');
  if (main) main.innerHTML = opts;
  if (to) to.innerHTML = opts;
  if (filter) filter.innerHTML = '<option value="">全部账户</option>' + opts;
}

function fillCategorySelect() {
  const sel = $('#tx-category');
  if (!sel) return;
  const opt = (c) => `<option value="${c.id}">${esc(c.name)}</option>`;
  sel.innerHTML = '<option value="">自动分类</option>'
    + '<optgroup label="支出">' + state.categories.filter((c) => c.kind === 'expense').map(opt).join('') + '</optgroup>'
    + '<optgroup label="收入">' + state.categories.filter((c) => c.kind === 'income').map(opt).join('') + '</optgroup>';
}

function txFilterParams() {
  const p = new URLSearchParams();
  const q = $('#f-q').value.trim();
  if (q) p.set('q', q);
  if ($('#f-type').value) p.set('type', $('#f-type').value);
  if ($('#f-account').value) p.set('account_id', $('#f-account').value);
  if ($('#f-month').value) p.set('month', $('#f-month').value);
  p.set('limit', state.tx.limit);
  p.set('offset', state.tx.page * state.tx.limit);
  return p;
}

async function loadTransactions() {
  const data = await api('/api/transactions?' + txFilterParams().toString());
  state.tx.total = data.total;
  $('#tx-body').innerHTML = data.items.length ? data.items.map((t) => `
    <tr>
      <td data-label="日期" class="muted">${t.date}</td>
      <td data-label="类型">${t.type === 'in' ? '收入' : t.type === 'out' ? '支出' : '转账'}</td>
      <td data-label="账户">${esc(t.account_name || '')}${t.to_account_name ? ' → ' + esc(t.to_account_name) : ''}</td>
      <td data-label="分类">${t.category_name ? `<span class="tag">${esc(t.category_name)}</span>` : '<span class="muted">未分类</span>'}</td>
      <td data-label="金额" class="num ${t.type === 'in' ? 'pos' : t.type === 'out' ? 'neg' : ''}">${t.type === 'in' ? '+' : t.type === 'out' ? '−' : ''}${fmt(t.amount)}</td>
      <td data-label="备注" class="muted">${esc(t.note || '')}</td>
      <td data-label=""><div class="row-actions"><button class="btn tiny danger" data-del="${t.id}">删除</button></div></td>
    </tr>`).join('') : '<tr><td colspan="7"><div class="empty">没有符合条件的记录</div></td></tr>';

  $('#f-count').textContent = `${data.total} 笔 · 收入 ${fmt0(data.sum_in)} · 支出 ${fmt0(data.sum_out)}`;
  const pages = Math.max(1, Math.ceil(data.total / state.tx.limit));
  $('#page-info').textContent = `第 ${state.tx.page + 1} / ${pages} 页`;
  $('#page-prev').disabled = state.tx.page === 0;
  $('#page-next').disabled = state.tx.page >= pages - 1;
}

async function submitTx(e) {
  e.preventDefault();
  const f = e.target;
  const fd = new FormData(f);
  const body = {
    date: fd.get('date'),
    type: fd.get('type'),
    amount: parseFloat(fd.get('amount')),
    account_id: parseInt(fd.get('account_id'), 10),
    note: fd.get('note') || '',
    source: 'manual',
  };
  if (body.type === 'transfer') body.to_account_id = parseInt(fd.get('to_account_id'), 10);
  if (fd.get('category_id')) body.category_id = parseInt(fd.get('category_id'), 10);
  try {
    const res = await api('/api/transactions', { method: 'POST', body });
    toast(res.category_id ? '已记账（自动分类好了）' : '已记账');
    f.reset();
    f.date.value = today();
    f.account_id.value = body.account_id;
    await loadOverview();
    await loadTransactions();
    await reloadRulesIfNeeded();
  } catch (err) { toast(err.message, true); }
}

async function reloadRulesIfNeeded() { /* 规则不常变，占位便于扩展 */ }

/* ---------------- 导入 ---------------- */

// 导入弹窗的临时状态：当前文件、预览结果、来源信息
const impState = { file: null, preview: null, sources: null };

async function openImport() {
  impState.file = null;
  impState.preview = null;
  openModal('导入账单', `
    <p class="hint" style="margin-top:0">
      支持支付宝 / 微信支付 / 银行流水 / 通用 CSV，微信导出的加密 zip 也能直接传。
      同一个单号重复导入会自动跳过，不会把账目翻倍。
    </p>
    <div id="imp-channels" class="imp-channels"></div>
    <label class="field"><span>账单文件（.csv / .txt / 微信加密 .zip）</span>
      <input class="input" type="file" id="imp-file" accept=".csv,.txt,.zip"></label>
    <div class="modal-cols">
      <label class="field"><span>压缩包密码（只有微信 zip 要填）</span>
        <input class="input" type="text" id="imp-zip" placeholder="微信「账单下载」通知里那串"></label>
      <label class="field"><span>没匹配到规则的账户</span>
        <select class="input" id="imp-account">
          ${state.accounts.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}
        </select></label>
    </div>
    <div id="imp-preview"></div>
    <button class="btn primary block" id="imp-go" disabled>确认导入</button>
    <div id="imp-rules" class="imp-rules-wrap"></div>
  `, (root) => {
    $('#imp-file', root).addEventListener('change', (e) => {
      impState.file = e.target.files[0] || null;
      if (impState.file) previewImport();
    });
    $('#imp-account', root).addEventListener('change', () => { if (impState.file) previewImport(); });
    $('#imp-zip', root).addEventListener('change', () => { if (impState.file) previewImport(); });
    $('#imp-go', root).addEventListener('click', doImport);
    $('#modal-card').classList.add('is-wide');
    renderImportChannels();
    renderImportRules();
  });
}

async function refreshImportSources() {
  try {
    impState.sources = await api('/api/import/sources');
  } catch (err) { impState.sources = null; }
  return impState.sources;
}

// 收件目录 / 邮箱 两条自动管道的手动触发条
async function renderImportChannels() {
  const host = $('#imp-channels');
  if (!host) return;
  const s = await refreshImportSources();
  if (!s) { host.innerHTML = ''; return; }
  const inbox = s.inbox || {};
  host.innerHTML = `
    <div class="imp-channel">
      <div class="imp-channel-main">
        <strong>收件目录</strong>
        <code class="imp-path">${esc(inbox.dir || '')}</code>
        <span class="muted">待处理 ${inbox.pending || 0} · 已导入 ${inbox.imported || 0}${inbox.failed ? ' · 失败 ' + inbox.failed : ''}</span>
      </div>
      <button class="btn tiny" id="imp-scan" type="button">立刻扫描</button>
    </div>
    <div class="imp-channel">
      <div class="imp-channel-main">
        <strong>邮箱收账单</strong>
        <span class="muted">${s.mail_configured ? '已配置 IMAP，可自动收未读邮件里的账单' : '未配置（设 ASSETBOOK_IMAP_HOST / USER / PASS 后可用）'}</span>
      </div>
      <button class="btn tiny" id="imp-mail" type="button"${s.mail_configured ? '' : ' disabled'}>收一次邮件</button>
    </div>`;

  $('#imp-scan').addEventListener('click', async () => {
    const btn = $('#imp-scan');
    btn.disabled = true;
    try {
      const r = await api('/api/import/inbox/scan', { method: 'POST' });
      toast(r.seen ? `处理 ${r.seen} 个文件，新增 ${r.imported} 笔` : '收件目录里没有新文件');
      await renderImportChannels();
      await renderImportRules();
      if (impState.file) previewImport();
    } catch (err) { toast(err.message, true); } finally { const b = $('#imp-scan'); if (b) b.disabled = false; }
  });
  const mb = $('#imp-mail');
  if (mb) mb.addEventListener('click', async () => {
    mb.disabled = true;
    try {
      const r = await api('/api/import/mail/fetch', { method: 'POST' });
      if (!r.ok) toast(r.error || '收邮件失败', true);
      else toast(`读了 ${r.mails} 封邮件，导入 ${r.imported} 笔`);
      await renderImportChannels();
      if (impState.file) previewImport();
    } catch (err) { toast(err.message, true); } finally { const b = $('#imp-mail'); if (b) b.disabled = false; }
  });
}

// 支付方式映射规则 + 最近批次（可撤销）
async function renderImportRules() {
  const host = $('#imp-rules');
  if (!host) return;
  const s = impState.sources || await refreshImportSources();
  if (!s) { host.innerHTML = ''; return; }
  const rules = s.payee_rules || [];
  const batches = s.batches || [];

  const ruleHtml = rules.length ? rules.map((r) => `
    <div class="imp-rule">
      <span class="imp-rule-pat">${esc(r.pattern)}</span>
      <span class="arrow">→</span>
      <span class="imp-rule-acc">${esc(r.account_name || '')}</span>
      ${r.source ? `<span class="tag">${esc(r.source)}</span>` : ''}
      <button class="btn tiny danger" data-del-payee="${r.id}">删除</button>
    </div>`).join('') : '<div class="empty">还没有映射规则</div>';

  const batchHtml = batches.length ? batches.slice(0, 5).map((b) => `
    <div class="imp-batch">
      <span class="imp-batch-name">${esc(b.batch)}</span>
      <span class="muted">${b.n} 笔 · ${esc(b.from_date || '')} ~ ${esc(b.to_date || '')}</span>
      <button class="btn tiny danger" data-del-batch="${esc(b.batch)}">撤销</button>
    </div>`).join('') : '';

  host.innerHTML = `
    <div class="imp-section-title">支付方式 → 账户 映射</div>
    <div class="imp-rule-grid">${ruleHtml}</div>
    ${batchHtml ? '<div class="imp-section-title">最近导入批次</div><div class="imp-rule-grid">' + batchHtml + '</div>' : ''}`;

  host.querySelectorAll('[data-del-payee]').forEach((btn) => btn.addEventListener('click', async () => {
    await api('/api/payee-rules/' + btn.dataset.delPayee, { method: 'DELETE' });
    toast('规则已删除');
    await refreshImportSources();
    await renderImportRules();
    if (impState.file) previewImport();
  }));
  host.querySelectorAll('[data-del-batch]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!confirm('撤销这次导入？对应的流水会被删掉。')) return;
    try {
      const r = await api('/api/import/batch/' + encodeURIComponent(btn.dataset.delBatch), { method: 'DELETE' });
      toast(`已撤销 ${r.removed} 笔`);
      await refreshImportSources();
      await renderImportRules();
      await loadOverview();
      if (currentView() === 'transactions') loadTransactions();
    } catch (err) { toast(err.message, true); }
  }));
}

async function previewImport() {
  const file = impState.file;
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  form.append('account_id', $('#imp-account').value);
  form.append('zip_password', ($('#imp-zip') && $('#imp-zip').value) || '');
  const box = $('#imp-preview');
  box.innerHTML = '<p class="hint">解析中…</p>';
  $('#imp-go').disabled = true;
  try {
    const res = await api('/api/import/preview', { method: 'POST', form });
    impState.preview = res;
    renderImportPreview(res);
  } catch (err) {
    impState.preview = null;
    box.innerHTML = `<p class="hint neg">${esc(err.message)}</p>`;
  }
}

function impAccountOptions(selectedId) {
  return state.accounts.map((a) =>
    `<option value="${a.id}"${a.id === selectedId ? ' selected' : ''}>${esc(a.name)}</option>`).join('');
}

function renderImportPreview(res) {
  const s = res.stats || {};
  const warn = (res.warnings || []).length
    ? `<div class="imp-warn">${res.warnings.map(esc).join('<br>')}</div>` : '';
  const head = `
    <div class="imp-summary">
      <span>识别为 <b>${esc(res.source_label)}</b>${res.encoding ? ' · ' + esc(res.encoding) : ''}</span>
      <span class="muted">${esc(res.filename || '')}</span>
    </div>
    <div class="imp-stats">
      <span>共 <b>${s.total || 0}</b></span>
      <span class="ok">新增 <b>${s.new || 0}</b></span>
      ${s.duplicate ? `<span class="warn">重复 <b>${s.duplicate}</b></span>` : ''}
      ${s.skipped ? `<span class="muted">跳过 <b>${s.skipped}</b></span>` : ''}
      ${s.no_account ? `<span class="neg">未定账户 <b>${s.no_account}</b></span>` : ''}
    </div>${warn}`;

  const rows = (res.rows || []).map((r, i) => impRowHtml(r, i)).join('');
  const unmatched = s.unmatched_pay_methods || [];
  const unmatchedHtml = unmatched.length ? `
    <div class="imp-unmatched">
      <div class="imp-unmatched-title">这些支付方式还没对应账户，选一个即可顺手记住：</div>
      ${unmatched.map((pm) => `
        <div class="imp-unmatched-row">
          <span class="pm">${esc(pm)}</span>
          <select class="input tiny-input" data-pm-acc>${impAccountOptions(null)}</select>
          <button class="btn tiny" data-pm-remember>记住</button>
        </div>`).join('')}
    </div>` : '';

  $('#imp-preview').innerHTML = head + unmatchedHtml
    + `<div class="imp-rows">${rows || '<div class="empty">没有解析到可导入的流水</div>'}</div>`;
  bindImportPreviewEvents();
  updateImportGo();
}

function impRowHtml(r, i) {
  const isSkip = r.type === 'skip';
  const dup = !!r.duplicate;
  const disabled = isSkip || dup;
  const tag = dup ? '<span class="tag warn-tag">重复</span>'
    : isSkip ? '<span class="tag">不计收支</span>' : '';
  const byRule = r.account_by ? `<span class="imp-by" title="由映射规则自动归属">规则「${esc(r.account_by)}」</span>` : '';
  const pay = r.pay_method ? `<span class="imp-pay">${esc(r.pay_method)}</span>` : '';
  const note = r.note || r.counterparty || '';
  const sign = r.type === 'in' ? '收' : r.type === 'out' ? '支' : '·';
  return `<div class="imp-row${disabled ? ' is-dim' : ''}">
    <label class="imp-check"><input type="checkbox" data-check="${i}"${r.selected && !disabled ? ' checked' : ''}${disabled ? ' disabled' : ''}></label>
    <span class="imp-date">${esc((r.date || '').slice(5, 10))}</span>
    <span class="imp-type ${r.type === 'in' ? 'pos' : r.type === 'out' ? 'neg' : ''}">${sign}</span>
    <span class="imp-note" title="${esc(note)}">${esc(note)} ${tag}${pay}</span>
    <span class="imp-amt ${r.type === 'in' ? 'pos' : r.type === 'out' ? 'neg' : ''}">${fmt(r.amount)}</span>
    <span class="imp-acct">${disabled ? '<span class="muted">跳过</span>'
      : `<select class="input tiny-input" data-acct="${i}">${impAccountOptions(r.account_id)}</select>${byRule}`}</span>
  </div>`;
}

function bindImportPreviewEvents() {
  const box = $('#imp-preview');
  box.querySelectorAll('[data-check]').forEach((cb) => cb.addEventListener('change', updateImportGo));
  box.querySelectorAll('[data-acct]').forEach((sel) => sel.addEventListener('change', () => {
    const i = parseInt(sel.dataset.acct, 10);
    if (impState.preview && impState.preview.rows[i]) {
      impState.preview.rows[i].account_id = parseInt(sel.value, 10);
    }
    updateImportGo();
  }));
  box.querySelectorAll('[data-pm-remember]').forEach((btn) => btn.addEventListener('click', async () => {
    const row = btn.closest('.imp-unmatched-row');
    const pm = row.querySelector('.pm').textContent;
    const accountId = parseInt(row.querySelector('select').value, 10);
    if (!accountId) return;
    try {
      await api('/api/payee-rules', { method: 'POST', body: { pattern: pm, account_id: accountId, priority: 50 } });
      const acct = state.accounts.find((a) => a.id === accountId);
      toast(`已记住：${pm} → ${acct ? acct.name : ''}`);
      await refreshImportSources();
      await renderImportRules();
      await previewImport();
    } catch (err) { toast(err.message, true); }
  }));
}

function updateImportGo() {
  const rows = (impState.preview && impState.preview.rows) || [];
  let n = 0; let sumIn = 0; let sumOut = 0;
  rows.forEach((r, i) => {
    const cb = $('#imp-preview [data-check="' + i + '"]');
    const on = cb ? cb.checked : false;
    r.selected = on;
    if (!on || !r.account_id) return;
    n += 1;
    if (r.type === 'in') sumIn += r.amount; else if (r.type === 'out') sumOut += r.amount;
  });
  const btn = $('#imp-go');
  if (!btn) return;
  btn.disabled = n === 0;
  btn.textContent = n ? `导入 ${n} 笔（收入 ${fmt0(sumIn)} · 支出 ${fmt0(sumOut)}）` : '确认导入';
}

async function doImport() {
  const rows = (impState.preview && impState.preview.rows) || [];
  const payload = rows.filter((r) => r.selected && r.account_id && r.type !== 'skip').map((r) => ({
    date: r.date,
    type: r.type,
    amount: r.amount,
    account_id: r.account_id,
    category_id: r.category_id || null,
    note: r.note || '',
    external_id: r.external_id || null,
    source: r.source || null,
    selected: true,
  }));
  if (!payload.length) return toast('没有可导入的行', true);
  const btn = $('#imp-go');
  btn.disabled = true;
  try {
    const res = await api('/api/import/commit', {
      method: 'POST',
      body: { rows: payload, label: (impState.preview && impState.preview.source_label) || 'import' },
    });
    let msg = `导入 ${res.imported} 笔`;
    if (res.skipped) msg += `，重复跳过 ${res.skipped}`;
    if (res.failed) msg += `，失败 ${res.failed}`;
    toast(msg);
    await loadOverview();
    await reloadAll();
    if (currentView() === 'transactions') loadTransactions();
    // 再预览一次：刚导入的行会标成「重复」，方便接着传下一个文件
    if (impState.file) { await previewImport(); await renderImportRules(); }
    else closeModal();
  } catch (err) {
    toast(err.message, true);
    btn.disabled = false;
  }
}

/* ---------------- 持仓 ---------------- */

async function loadHoldings() {
  const data = await api('/api/holdings');
  const pnl = data.total_profit;
  $('#holding-kpis').innerHTML = [
    { label: '持仓市值', val: fmt0(data.total_market), sub: '' },
    { label: '投入成本', val: fmt0(data.total_cost), sub: '' },
    { label: '浮动盈亏', val: fmt0(pnl), sub: data.total_cost ? ((pnl / data.total_cost) * 100).toFixed(1) + '%' : '—', cls: cls(pnl) },
    { label: '持仓数', val: data.items.length + ' 项', sub: '' },
  ].map((k) => `<div class="stat"><div class="stat-label">${k.label}</div>
      <div class="stat-val ${k.cls || ''}">${k.val}</div><div class="stat-sub">${esc(k.sub)}</div></div>`).join('');

  $('#holding-body').innerHTML = data.items.length ? data.items.map((h) => `
    <tr>
      <td data-label="名称">${esc(h.name)}${h.code ? ` <span class="muted">${esc(h.code)}</span>` : ''}
        ${h.market && h.market !== 'auto' ? `<span class="tag tiny-tag">${esc(h.market_label || h.market)}</span>` : ''}</td>
      <td data-label="类型"><span class="tag">${esc(h.kind_label)}</span></td>
      <td data-label="份额" class="num">${h.units}</td>
      <td data-label="成本价" class="num">${h.cost_price}</td>
      <td data-label="现价" class="num"><input class="input price-input" type="number" step="0.0001" value="${h.price}" data-price="${h.id}"></td>
      <td data-label="市值" class="num">${fmt(h.market_value)}</td>
      <td data-label="盈亏" class="num ${cls(h.profit)}">${fmt(h.profit)}<br><span class="muted" style="font-size:11px">${h.profit_pct.toFixed(2)}%</span></td>
      <td data-label=""><div class="row-actions"><button class="btn tiny danger" data-del-holding="${h.id}">删除</button></div></td>
    </tr>`).join('') : '<tr><td colspan="8"><div class="empty">还没有持仓，点右上角新增</div></td></tr>';

  const byKind = {};
  data.items.forEach((h) => { byKind[h.kind_label] = (byKind[h.kind_label] || 0) + h.market_value; });
  const total = data.total_market || 1;
  const items = Object.entries(byKind).map(([name, amount]) => ({
    name, amount, ratio: amount / total * 100,
  })).sort((a, b) => b.amount - a.amount);
  drawDonut($('#holding-donut'), $('#holding-legend'), items);
}

function openHoldingForm() {
  const marketOpts = (state.meta.markets || [{ value: 'auto', label: '自动识别' }])
    .map((m) => `<option value="${m.value}">${esc(m.label)}</option>`).join('');
  const kindOpts = Object.entries(state.meta.holding_kinds || {
    fund: '基金', stock: '股票', bond: '债券', gold: '黄金', cash: '现金类', other: '其他',
  }).map(([v, label]) => `<option value="${v}">${esc(label)}</option>`).join('');

  openModal('新增持仓', `
    <div class="modal-cols">
      <label class="field"><span>代码</span><input class="input" id="h-code" placeholder="510300 / 00700 / BTC"></label>
      <label class="field"><span>市场</span><select class="input" id="h-market">${marketOpts}</select></label>
      <label class="field"><span>名称</span><input class="input" id="h-name" placeholder="填代码可自动带出"></label>
      <label class="field"><span>类型</span><select class="input" id="h-kind">${kindOpts}</select></label>
      <label class="field"><span>份额</span><input class="input" type="number" step="0.0001" id="h-units" value="0"></label>
      <label class="field"><span>成本价</span><input class="input" type="number" step="0.0001" id="h-cost" value="0"></label>
      <label class="field"><span>现价</span><input class="input" type="number" step="0.0001" id="h-price" value="0"></label>
    </div>
    <p class="hint" id="h-probe">填好代码后会试拉一次行情，成功就自动填名称和现价。</p>
    <button class="btn primary block" id="h-save">保存</button>
  `, () => {
    const probe = $('#h-probe');
    let probed = false;

    const tryProbe = async () => {
      const code = $('#h-code').value.trim();
      if (!code || probed) return;
      probed = true;
      probe.className = 'hint';
      probe.textContent = '正在查行情…';
      try {
        const q = await api(`/api/quotes/preview?code=${encodeURIComponent(code)}`
          + `&market=${encodeURIComponent($('#h-market').value)}`
          + `&kind=${encodeURIComponent($('#h-kind').value)}`);
        if (q.ok) {
          if (!$('#h-name').value.trim() && q.name) $('#h-name').value = q.name;
          $('#h-price').value = q.price;
          probe.className = 'hint pos';
          probe.textContent = `已查到：${q.name || code} · ${q.price}（${q.date}）`;
        } else {
          probe.className = 'hint neg';
          probe.textContent = `没查到：${q.error}。可以换个市场选项，或直接手填现价。`;
        }
      } catch (err) {
        probe.className = 'hint neg';
        probe.textContent = '查询失败：' + err.message;
      }
    };

    $('#h-code').addEventListener('blur', tryProbe);
    $('#h-market').addEventListener('change', () => { probed = false; tryProbe(); });
    $('#h-kind').addEventListener('change', () => { probed = false; tryProbe(); });

    $('#h-save').addEventListener('click', async () => {
      const name = $('#h-name').value.trim() || $('#h-code').value.trim();
      if (!name) return toast('名称或代码至少填一个', true);
      try {
        await api('/api/holdings', {
          method: 'POST',
          body: {
            name,
            code: $('#h-code').value.trim() || null,
            kind: $('#h-kind').value,
            market: $('#h-market').value,
            units: parseFloat($('#h-units').value) || 0,
            cost_price: parseFloat($('#h-cost').value) || 0,
            price: parseFloat($('#h-price').value) || 0,
          },
        });
        toast('已添加');
        closeModal();
        await loadHoldings();
        await loadOverview();
        refreshSyncNote();
      } catch (err) { toast(err.message, true); }
    });
  });
}

async function savePrice(id, price) {
  try {
    await api(`/api/holdings/${id}/price`, { method: 'POST', body: { price } });
    toast('价格已更新');
    await loadHoldings();
    await loadOverview();
  } catch (err) { toast(err.message, true); }
}

/* ---------------- 自动同步 ---------------- */

async function refreshSyncNote() {
  try {
    const s = await api('/api/auto/status');
    const el = $('#sync-note');
    if (!el) return;
    el.textContent = s.last_quote_at
      ? `${s.enabled ? '自动更新' : '已暂停'} · 上次 ${s.last_quote_at.slice(5, 16)}`
      : (s.enabled ? '自动更新已开启' : '自动更新已关闭');
    el.classList.toggle('is-off', !s.enabled);
    state.auto = s;
  } catch (err) { /* 忽略 */ }
}

async function refreshQuotes() {
  const btn = $('#refresh-quotes');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '刷新中…';
  try {
    const r = await api('/api/quotes/refresh', { method: 'POST' });
    if (r.total === 0) {
      toast('没有填代码的持仓，没什么可刷的');
    } else if (r.failed && r.failed.length) {
      toast(`成功 ${r.ok}/${r.total}，${r.failed.length} 条没查到`, true);
    } else {
      toast(`已更新 ${r.ok} 条持仓价格`);
    }
    await loadHoldings();
    await loadOverview();
    await refreshSyncNote();
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function renderAutoPanel() {
  let s;
  try {
    s = await api('/api/auto/status');
  } catch (err) { return; }
  state.auto = s;

  $('#auto-enabled').checked = !!s.enabled;
  $('#auto-interval').value = s.quote_interval_min;
  $('#auto-hour').value = s.snapshot_hour;
  $('#auto-rates').value = s.rates_interval_hour;
  $('#auto-inbox').value = s.inbox_interval_min;
  $('#auto-mail').value = s.mail_interval_min;

  const lr = s.last_quote_result;
  const inbox = s.inbox || {};
  const rows = [
    ['运行状态', s.running ? '后台任务在跑' : '未启动'],
    ['上次刷新行情', s.last_quote_at || '还没跑过'],
    ['下次刷新行情', s.enabled ? (s.next_quote_at || '待定') : '已暂停'],
    ['上次刷新汇率', s.last_rates_at || '还没跑过'],
    ['最近净值快照', s.last_snapshot_date || '还没记'],
    ['上次结果', lr ? `成功 ${lr.ok}/${lr.total}${lr.failed && lr.failed.length ? ` · 失败 ${lr.failed.length}` : ''}` : '—'],
    ['收件目录', `${inbox.pending || 0} 个待处理 · 已导入 ${inbox.imported || 0}${inbox.failed ? ' · 失败 ' + inbox.failed : ''}`],
    ['上次扫收件目录', s.last_inbox_at || '还没跑过'],
    ['邮箱收账单', s.mail_configured ? `已配置 · 上次 ${s.last_mail_at || '还没跑过'}` : '未配置（设 ASSETBOOK_IMAP_* 后启用）'],
  ];
  $('#auto-status').innerHTML = rows.map(([k, v]) =>
    `<div class="auto-row"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('');

  $('#auto-log').innerHTML = (s.log || []).length
    ? `<div class="auto-log-title">最近动态</div>` + s.log.slice(0, 8).map((l) =>
        `<div class="auto-log-item ${l.level}"><span class="t">${esc(l.at.slice(5, 16))}</span>${esc(l.message)}</div>`).join('')
    : '';
}

async function saveAutoSettings() {
  try {
    await api('/api/auto/settings', {
      method: 'PUT',
      body: {
        enabled: $('#auto-enabled').checked,
        quote_interval_min: parseInt($('#auto-interval').value, 10) || 30,
        snapshot_hour: parseInt($('#auto-hour').value, 10) || 0,
        rates_interval_hour: parseInt($('#auto-rates').value, 10) || 12,
        inbox_interval_min: parseInt($('#auto-inbox').value, 10) || 10,
        mail_interval_min: parseInt($('#auto-mail').value, 10) || 60,
      },
    });
    toast('设置已保存');
    await renderAutoPanel();
    refreshSyncNote();
  } catch (err) { toast(err.message, true); }
}

/* ---------------- 预算 ---------------- */

async function renderBudgets() {
  const monthEl = $('#budget-month');
  if (!monthEl.value) monthEl.value = state.budgetMonth;
  const month = monthEl.value;
  const data = await api('/api/budgets?month=' + month);
  $('#budget-list').innerHTML = data.items.length ? data.items.map((b) => {
    const over = b.pct > 100;
    const warn = b.pct > 80 && b.pct <= 100;
    return `<div class="budget-row">
      <span class="name"><i class="dot" style="background:${b.color || '#6f6a5e'}"></i>${esc(b.name)}</span>
      <span class="bar"><i style="width:${Math.min(b.pct, 100).toFixed(1)}%;background:${over ? 'var(--neg)' : warn ? 'var(--amber)' : 'var(--accent)'}"></i></span>
      <span class="b-amt">${fmt0(b.budget)}</span>
      <span class="b-spent">用 ${fmt0(b.spent)}</span>
      <span class="badge ${over ? 'over' : warn ? 'warn' : 'ok'}">${b.pct.toFixed(0)}%</span>
    </div>`;
  }).join('') : '<div class="empty">这个月还没设预算</div>';

  const rep = await api('/api/reports/monthly?months=12');
  const max = Math.max(...rep.items.map((r) => Math.max(r.in, r.out)), 1);
  $('#monthly-bars').innerHTML = rep.items.length ? rep.items.map((r) => `
    <div class="bar-group" title="${r.month} 收 ${fmt0(r.in)} 支 ${fmt0(r.out)}">
      <div class="bar-pair">
        <i class="in" style="height:${(r.in / max * 100).toFixed(1)}%" title="收入 ${fmt0(r.in)}"></i>
        <i class="out" style="height:${(r.out / max * 100).toFixed(1)}%" title="支出 ${fmt0(r.out)}"></i>
      </div>
      <span class="bar-label">${r.month.slice(2)}</span>
    </div>`).join('') : '<div class="empty">还没有月度数据</div>';
}

function openBudgetForm() {
  openModal('设置预算', `
    <label class="field"><span>月份（留 * 表示每月通用）</span>
      <input class="input" id="b-month" value="${$('#budget-month').value || state.budgetMonth}"></label>
    <label class="field"><span>分类</span><select class="input" id="b-cat">
      ${state.categories.filter((c) => c.kind === 'expense').map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join('')}
    </select></label>
    <label class="field"><span>预算额度</span><input class="input" type="number" min="0" step="1" id="b-amount" placeholder="1500"></label>
    <button class="btn primary block" id="b-save">保存</button>
  `, () => {
    $('#b-save').addEventListener('click', async () => {
      try {
        await api('/api/budgets', {
          method: 'POST',
          body: {
            month: $('#b-month').value.trim() || '*',
            category_id: parseInt($('#b-cat').value, 10),
            amount: parseFloat($('#b-amount').value) || 0,
          },
        });
        toast('已保存');
        closeModal();
        await renderBudgets();
      } catch (err) { toast(err.message, true); }
    });
  });
}

/* ---------------- 设置 ---------------- */

async function renderSettings() {
  $('#account-body').innerHTML = state.accounts.length ? state.accounts.map((a) => `
    <tr>
      <td data-label="名称">${esc(a.name)}</td>
      <td data-label="类型"><span class="tag">${esc(a.kind_label)}</span></td>
      <td data-label="当前余额" class="num ${a.is_liability ? 'neg' : ''}">${fmt(a.balance)}</td>
      <td data-label="基准" class="num muted">${fmt0(a.base_balance)}<br><span style="font-size:11px">${a.base_date || ''}</span></td>
      <td data-label=""><div class="row-actions">
        <button class="btn tiny" data-校准="${a.id}" data-name="${esc(a.name)}" data-balance="${a.balance}" data-liability="${a.is_liability ? 1 : 0}">校准</button>
        <button class="btn tiny danger" data-del-account="${a.id}">删除</button>
      </div></td>
    </tr>`).join('') : '<tr><td colspan="5"><div class="empty">还没有账户，先建一个</div></td></tr>';

  $('#rule-list').innerHTML = state.rules.length ? state.rules.map((r) => `
    <div class="rule-item">
      <span><span class="kw">${esc(r.keyword)}</span> <span class="arrow">→</span> ${esc(r.category_name || '')}</span>
      <span class="muted" style="font-size:11px">优先级 ${r.priority}</span>
      <button class="btn tiny danger" data-del-rule="${r.id}">删除</button>
    </div>`).join('') : '<div class="empty">还没有规则</div>';

  $('#category-list').innerHTML = state.categories.map((c) => `
    <span class="chip"><i class="dot" style="background:${c.color || '#6f6a5e'}"></i>${esc(c.name)}
      <button data-del-cat="${c.id}" title="删除">×</button></span>`).join('');

  $('#db-path').textContent = 'data/assetbook.db';
}

function openAccountForm() {
  openModal('新增账户', `
    <div class="modal-cols">
      <label class="field"><span>名称</span><input class="input" id="a-name" placeholder="招商银行"></label>
      <label class="field"><span>类型</span><select class="input" id="a-kind">
        <option value="bank">银行卡</option><option value="payment">支付账户（支付宝/微信）</option>
        <option value="cash">现金</option><option value="broker">券商</option>
        <option value="credit">信用卡（算负债）</option><option value="loan">贷款（算负债）</option>
        <option value="other">其他</option>
      </select></label>
      <label class="field"><span>当前余额</span><input class="input" type="number" step="0.01" id="a-balance" value="0"></label>
      <label class="field"><span>日期</span><input class="input" type="date" id="a-date" value="${today()}"></label>
    </div>
    <button class="btn primary block" id="a-save">保存</button>
  `, () => {
    $('#a-save').addEventListener('click', async () => {
      try {
        await api('/api/accounts', {
          method: 'POST',
          body: {
            name: $('#a-name').value.trim(),
            kind: $('#a-kind').value,
            base_balance: parseFloat($('#a-balance').value) || 0,
            base_date: $('#a-date').value || today(),
          },
        });
        toast('账户已创建');
        closeModal();
        await reloadAll();
        renderSettings();
      } catch (err) { toast(err.message, true); }
    });
  });
}

function openRuleForm() {
  openModal('新增分类规则', `
    <label class="field"><span>关键词</span><input class="input" id="r-kw" placeholder="麦当劳"></label>
    <label class="field"><span>归到分类</span><select class="input" id="r-cat">
      ${state.categories.map((c) => `<option value="${c.id}">${esc(c.name)}（${c.kind === 'income' ? '收入' : '支出'}）</option>`).join('')}
    </select></label>
    <label class="field"><span>优先级（小的先匹配）</span><input class="input" type="number" id="r-prio" value="100"></label>
    <button class="btn primary block" id="r-save">保存</button>
  `, () => {
    $('#r-save').addEventListener('click', async () => {
      try {
        await api('/api/rules', {
          method: 'POST',
          body: {
            keyword: $('#r-kw').value.trim(),
            category_id: parseInt($('#r-cat').value, 10),
            priority: parseInt($('#r-prio').value, 10) || 100,
          },
        });
        toast('规则已添加');
        closeModal();
        await reloadAll();
        renderSettings();
      } catch (err) { toast(err.message, true); }
    });
  });
}

function openCategoryForm() {
  openModal('新增分类', `
    <label class="field"><span>名称</span><input class="input" id="c-name" placeholder="宠物"></label>
    <label class="field"><span>类型</span><select class="input" id="c-kind">
      <option value="expense">支出</option><option value="income">收入</option>
    </select></label>
    <label class="field"><span>颜色</span><input class="input" type="color" id="c-color" value="#1c6b52"></label>
    <button class="btn primary block" id="c-save">保存</button>
  `, () => {
    $('#c-save').addEventListener('click', async () => {
      try {
        await api('/api/categories', {
          method: 'POST',
          body: { name: $('#c-name').value.trim(), kind: $('#c-kind').value, color: $('#c-color').value },
        });
        toast('分类已添加');
        closeModal();
        await reloadAll();
        renderSettings();
      } catch (err) { toast(err.message, true); }
    });
  });
}

/* ---------------- 图表 ---------------- */

function drawLine(svg, series, key) {
  if (!svg) return;
  if (!series || series.length < 2) {
    svg.innerHTML = '<text x="400" y="120" text-anchor="middle" fill="#8a8371" font-size="13">数据还不够，记几笔就有了</text>';
    return;
  }
  const W = 800, H = 240, pad = 26;
  const vals = series.map((d) => d.net);
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = (max - min) || 1;
  const step = (W - pad * 2) / (series.length - 1);
  const pts = series.map((d, i) => [pad + i * step, H - pad - ((d.net - min) / range) * (H - pad * 2)]);
  let path = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i];
    const cx = (x0 + x1) / 2;
    path += ` C${cx},${y0} ${cx},${y1} ${x1},${y1}`;
  }
  const area = path + ` L${pts[pts.length - 1][0]},${H - pad} L${pts[0][0]},${H - pad} Z`;
  const id = 'g' + Math.random().toString(36).slice(2, 8);
  svg.innerHTML = `
    <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--accent)" stop-opacity=".32"/>
      <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
    </linearGradient></defs>
    <line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="var(--line)" stroke-width="1"/>
    <path d="${area}" fill="url(#${id})"/>
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round"/>
    <circle cx="${pts[pts.length - 1][0]}" cy="${pts[pts.length - 1][1]}" r="4.5"
            fill="var(--card)" stroke="var(--accent)" stroke-width="2.5"/>
    ${pts.map((p, i) => `<circle cx="${p[0]}" cy="${p[1]}" r="7" fill="transparent">
        <title>${series[i][key] || ''} · ${fmt0(series[i].net)}</title></circle>`).join('')}`;
}

function drawDonut(svg, legendEl, items) {
  if (!svg) return;
  const data = (items || []).filter((i) => i.amount > 0);
  const total = data.reduce((s, i) => s + i.amount, 0);
  if (!data.length || !total) {
    svg.innerHTML = '<circle cx="100" cy="100" r="70" fill="none" stroke="var(--line)" stroke-width="22"/>';
    if (legendEl) legendEl.innerHTML = '<div class="empty">暂无数据</div>';
    return;
  }
  const R = 70, C = 2 * Math.PI * R;
  let offset = 0;
  const arcs = data.map((item, i) => {
    const frac = item.amount / total;
    const color = COLORS[i % COLORS.length];
    const seg = `<circle cx="100" cy="100" r="${R}" fill="none" stroke="${color}" stroke-width="24"
      stroke-dasharray="${(frac * C).toFixed(2)} ${(C - frac * C).toFixed(2)}"
      stroke-dashoffset="${(-offset * C).toFixed(2)}" transform="rotate(-90 100 100)">
      <title>${esc(item.name)} ${fmt0(item.amount)} (${(frac * 100).toFixed(1)}%)</title></circle>`;
    offset += frac;
    return seg;
  }).join('');
  svg.innerHTML = `<circle cx="100" cy="100" r="${R}" fill="none" stroke="var(--line)" stroke-width="24" opacity=".35"/>${arcs}`;
  if (legendEl) {
    legendEl.innerHTML = data.map((item, i) => `
      <div class="item"><i class="sw" style="background:${COLORS[i % COLORS.length]}"></i>
        <span class="name">${esc(item.name)}</span>
        <span class="val">${fmt0(item.amount)} · ${(item.amount / total * 100).toFixed(1)}%</span>
      </div>`).join('');
  }
}

/* ---------------- 事件绑定 ---------------- */

function bind() {
  $('#nav').addEventListener('click', (e) => {
    const btn = e.target.closest('.nav-item');
    if (btn) go(btn.dataset.view);
  });
  window.addEventListener('hashchange', () => go(currentView()));
  $('#gate-form').addEventListener('submit', onSubmitGate);

  $('#theme-btn').addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme');
    const sys = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    const next = cur === 'dark' ? 'light' : cur === 'light' ? 'dark' : (sys === 'dark' ? 'light' : 'dark');
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    if (state.view === 'overview') renderOverview();
  });
  $('#logout-btn').addEventListener('click', async () => {
    await api('/api/auth/logout', { method: 'POST' });
    location.reload();
  });

  $('#nw-range').addEventListener('click', (e) => {
    const b = e.target.closest('.seg-btn');
    if (!b) return;
    state.nwRange = b.dataset.range;
    $$('#nw-range .seg-btn').forEach((x) => x.classList.toggle('is-on', x === b));
    renderOverview();
  });

  // 记账
  const txForm = $('#tx-form');
  txForm.date.value = today();
  txForm.addEventListener('submit', submitTx);
  $('#tx-type').addEventListener('change', (e) => {
    $('#tx-to-wrap').hidden = e.target.value !== 'transfer';
  });
  $('#import-btn').addEventListener('click', openImport);
  ['#f-q', '#f-type', '#f-account', '#f-month'].forEach((sel) => {
    let timer;
    $(sel).addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { state.tx.page = 0; loadTransactions(); }, 220); });
    $(sel).addEventListener('change', () => { state.tx.page = 0; loadTransactions(); });
  });
  $('#f-reset').addEventListener('click', () => {
    $('#f-q').value = ''; $('#f-type').value = ''; $('#f-account').value = ''; $('#f-month').value = '';
    state.tx.page = 0; loadTransactions();
  });
  $('#page-prev').addEventListener('click', () => { if (state.tx.page > 0) { state.tx.page--; loadTransactions(); } });
  $('#page-next').addEventListener('click', () => { state.tx.page++; loadTransactions(); });
  $('#tx-body').addEventListener('click', async (e) => {
    const id = e.target.dataset.del;
    if (!id) return;
    if (!confirm('删掉这条流水？')) return;
    await api('/api/transactions/' + id, { method: 'DELETE' });
    toast('已删除');
    await loadOverview();
    await loadTransactions();
  });

  // 持仓
  $('#holding-add').addEventListener('click', openHoldingForm);
  $('#holding-body').addEventListener('change', (e) => {
    const id = e.target.dataset.price;
    if (id) savePrice(id, parseFloat(e.target.value) || 0);
  });
  $('#holding-body').addEventListener('click', async (e) => {
    const id = e.target.dataset.delHolding;
    if (!id) return;
    if (!confirm('删除这个持仓？')) return;
    await api('/api/holdings/' + id, { method: 'DELETE' });
    toast('已删除');
    await loadHoldings();
    await loadOverview();
  });

  // 预算
  $('#budget-add').addEventListener('click', openBudgetForm);
  $('#budget-month').addEventListener('change', renderBudgets);

  // 设置
  $('#account-add').addEventListener('click', openAccountForm);
  $('#rule-add').addEventListener('click', openRuleForm);
  $('#category-add').addEventListener('click', openCategoryForm);
  $('#account-body').addEventListener('click', async (e) => {
    const cal = e.target.dataset['校准'];
    if (cal) {
      const name = e.target.dataset.name;
      const isLiability = e.target.dataset.liability === '1';
      const cur = parseFloat(e.target.dataset.balance) || 0;
      const ask = isLiability
        ? `「${name}」现在还欠多少？（当前欠 ${fmt0(Math.abs(cur))}）`
        : `把「${name}」的余额校准成多少？（当前 ${fmt0(cur)}）`;
      const input = prompt(ask, String(isLiability ? Math.abs(cur) : cur));
      if (input === null) return;
      await api(`/api/accounts/${cal}/snapshot`, { method: 'POST', body: { balance: parseFloat(input) || 0 } });
      toast('已校准');
      await reloadAll();
      renderSettings();
      return;
    }
    const del = e.target.dataset.delAccount;
    if (del) {
      if (!confirm('删除账户会连带删掉它的流水，确定？')) return;
      await api('/api/accounts/' + del, { method: 'DELETE' });
      toast('已删除');
      await reloadAll();
      renderSettings();
    }
  });
  $('#rule-list').addEventListener('click', async (e) => {
    const id = e.target.dataset.delRule;
    if (!id) return;
    await api('/api/rules/' + id, { method: 'DELETE' });
    await reloadAll();
    renderSettings();
  });
  $('#category-list').addEventListener('click', async (e) => {
    const id = e.target.dataset.delCat;
    if (!id) return;
    if (!confirm('删除这个分类？相关的流水会变成未分类')) return;
    await api('/api/categories/' + id, { method: 'DELETE' });
    await reloadAll();
    renderSettings();
  });
  $('#password-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const pw = $('#new-password').value;
    if (pw.length < 6) return toast('至少 6 位', true);
    try {
      await api('/api/auth/setup', { method: 'POST', body: { password: pw } });
      toast('密码已更新，请重新登录');
      setTimeout(() => location.reload(), 900);
    } catch (err) { toast(err.message, true); }
  });

  // 行情刷新
  $('#refresh-quotes').addEventListener('click', refreshQuotes);

  // 自动同步
  $('#auto-save').addEventListener('click', saveAutoSettings);
  $('#auto-run-quotes').addEventListener('click', async () => {
    const btn = $('#auto-run-quotes');
    btn.disabled = true;
    btn.textContent = '刷新中…';
    try {
      const r = await api('/api/auto/run/quotes', { method: 'POST' });
      toast(r.failed && r.failed.length ? `成功 ${r.ok}/${r.total}，${r.failed.length} 条失败` : `已更新 ${r.ok} 条`);
      await renderAutoPanel();
    } catch (err) { toast(err.message, true); } finally {
      btn.disabled = false;
      btn.textContent = '立即刷新行情';
    }
  });
  $('#auto-run-inbox').addEventListener('click', async () => {
    const btn = $('#auto-run-inbox');
    btn.disabled = true;
    btn.textContent = '扫描中…';
    try {
      const r = await api('/api/auto/run/inbox', { method: 'POST' });
      toast(r.seen ? `处理 ${r.seen} 个文件，新增 ${r.imported} 笔` : '收件目录里没有新文件');
      await renderAutoPanel();
      await loadOverview();
    } catch (err) { toast(err.message, true); } finally {
      btn.disabled = false;
      btn.textContent = '扫收件目录';
    }
  });

  // 模态
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
}

/* ---------------- 启动 ---------------- */

(async function boot() {
  const stored = localStorage.getItem('theme');
  if (stored) document.documentElement.setAttribute('data-theme', stored);
  bind();
  let st;
  try {
    st = await api('/api/auth/state');
  } catch (err) {
    toast('连不上后端：' + err.message, true);
    return;
  }
  $('#version').textContent = 'v' + (st.version || '1.0.0');
  if (!st.has_password) {
    $('#gate').dataset.setup = '1';
    showGate(false);
  } else if (!st.authed) {
    $('#gate').dataset.setup = '0';
    showGate(true);
  } else {
    await enterApp();
  }
})();
