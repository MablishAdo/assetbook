/* ============================================================
   AssetBook · 前端逻辑（无依赖，原生 JS）
   ============================================================ */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const COLORS = ['#1c6b52', '#b2612e', '#4a6fa5', '#c08a2d', '#7d5ba6',
                '#3e8f83', '#a85d75', '#6f6a5e', '#8fae4f', '#b23c2c'];

const state = {
  view: 'transactions',
  categories: [],
  rules: [],
  meta: { markets: [], holding_kinds: {} },
  pfRange: '30',
  tx: { page: 0, limit: 25, total: 0 },
  budgetMonth: new Date().toISOString().slice(0, 7),
  holdings: { items: [], total_market: 0, total_cost: 0, total_profit: 0 },
  portfolio: null,
  ai: null,
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
  const [categories, rules, meta] = await Promise.all([
    api('/api/categories'), api('/api/rules'), api('/api/meta'),
  ]);
  state.categories = categories.items;
  state.rules = rules.items;
  state.meta = meta;
  fillCategorySelect();
  refreshSyncNote();
}

/* ---------------- 路由 ---------------- */

const TITLES = { transactions: '记账', holdings: '投资', settings: '设置' };

// 同时接受 #overview、#/overview、#/，都归一成视图名
function currentView() {
  const raw = decodeURIComponent(location.hash).replace(/^#\/?/, '').trim();
  return TITLES[raw] ? raw : 'transactions';
}

function go(view) {
  if (!TITLES[view]) view = 'transactions';
  state.view = view;
  $$('.nav-item').forEach((b) => b.classList.toggle('is-on', b.dataset.view === view));
  $$('.view').forEach((s) => { s.hidden = s.dataset.view !== view; });
  $('#page-title').textContent = TITLES[view];
  if (location.hash !== '#/' + view) location.hash = '#/' + view;
  if (view === 'transactions') { loadMonthSummary(); loadTransactions(); loadAiPanel(); }
  if (view === 'holdings') loadInvestmentPage();
  if (view === 'settings') { renderSettings(); renderAutoPanel(); renderAiSettings(); }
}

/* ---------------- 记账页概览 ---------------- */

async function loadMonthSummary() {
  try {
    state.overview = await api('/api/overview');
    renderTxSummary();
  } catch (err) {
    state.overview = null;
  }
}

function renderTxSummary() {
  const o = state.overview;
  if (!o) return;
  $('#tx-net').textContent = fmt(o.month_net);
  $('#tx-net').className = 'val ' + cls(o.month_net);
  $('#tx-income').textContent = fmt0(o.month_in);
  $('#tx-expense').textContent = fmt0(o.month_out);
  $('#tx-rate').textContent = o.month_in ? (o.savings_rate || 0).toFixed(1) + '%' : '—';

  $('#tx-kpis').innerHTML = [
    { label: '记账月份', val: o.month, sub: '按月归档' },
    { label: '本月笔数', val: String(state.tx.total || 0), sub: '当前筛选结果' },
    { label: '近三月均支出', val: fmt0(o.avg_month_expense), sub: '完整月平均' },
    { label: '投资市值', val: fmt0((o.invest || {}).market_value), sub: '不在本月收支里' },
  ].map((k) => `<div class="stat"><div class="stat-label">${k.label}</div>
      <div class="stat-val">${esc(k.val)}</div><div class="stat-sub">${esc(k.sub)}</div></div>`).join('');
}

/* ---------------- 投资页 ---------------- */

function renderHoldingsTable(data) {
  state.holdings = data;
  const items = data.items || [];
  const total = data.total_market || 0;
  const profit = data.total_profit || 0;
  const pct = data.total_cost ? (profit / data.total_cost * 100) : 0;

  $('#hd-market').textContent = fmt0(total);
  $('#hd-cost').textContent = fmt0(data.total_cost);
  $('#hd-profit').textContent = (profit >= 0 ? '+' : '') + fmt0(profit);
  $('#hd-profit').className = cls(profit);
  $('#hd-pct').textContent = data.total_cost ? (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%' : '—';
  $('#hd-pct').className = cls(profit);

  const winners = items.filter((h) => h.profit > 0).length;
  const losers = items.filter((h) => h.profit < 0).length;
  const top = total ? Math.max(...items.map((h) => h.market_value / total * 100), 0) : 0;
  $('#hd-kpis').innerHTML = [
    { label: '持仓只数', val: String(items.length), sub: `盈 ${winners} / 亏 ${losers}` },
    { label: '最大单只占比', val: top.toFixed(1) + '%', sub: '集中度参考' },
    { label: '品种数', val: String(new Set(items.map((h) => h.kind)).size), sub: '按品种分' },
    { label: '总成本', val: fmt0(data.total_cost), sub: '含申购费' },
  ].map((k) => `<div class="stat"><div class="stat-label">${k.label}</div>
      <div class="stat-val">${esc(k.val)}</div><div class="stat-sub">${esc(k.sub)}</div></div>`).join('');

  $('#holding-body').innerHTML = items.length ? items.map((h) => `
    <tr>
      <td data-label="名称"><div class="h-name">${esc(h.name)}</div>
        ${h.code ? `<small class="h-code">${esc(h.code)}</small>` : ''}
        ${h.market && h.market !== 'auto' ? `<span class="tag tiny-tag">${esc(h.market_label || h.market)}</span>` : ''}</td>
      <td data-label="类型"><span class="tag">${esc(h.kind_label)}</span></td>
      <td data-label="份额" class="num">${h.units}</td>
      <td data-label="成本价" class="num">${h.cost_price}</td>
      <td data-label="现价" class="num"><input class="input price-input" type="number" step="0.0001" value="${h.price}" data-price="${h.id}"></td>
      <td data-label="市值" class="num">${fmt(h.market_value)}</td>
      <td data-label="占比" class="num">${total ? (h.market_value / total * 100).toFixed(1) : '0.0'}%</td>
      <td data-label="盈亏" class="num ${cls(h.profit)}">${h.profit >= 0 ? '+' : ''}${fmt(h.profit)}<br><span class="muted" style="font-size:11px">${h.profit_pct.toFixed(2)}%</span></td>
      <td data-label=""><div class="row-actions"><button class="btn tiny" data-edit-holding="${h.id}">编辑</button><button class="btn tiny danger" data-del-holding="${h.id}">删除</button></div></td>
    </tr>`).join('') : '<tr><td colspan="9"><div class="empty">还没有持仓。点右上角「新增持仓」，填个代码就能自动带出名称和现价。</div></td></tr>';

  const byKind = {};
  items.forEach((h) => { byKind[h.kind_label] = (byKind[h.kind_label] || 0) + h.market_value; });
  const t = total || 1;
  const donut = Object.entries(byKind).map(([name, amount]) => ({
    name, amount, ratio: amount / t * 100,
  })).sort((a, b) => b.amount - a.amount);
  drawDonut($('#holding-donut'), $('#holding-legend'), donut);

  $('#holding-body').querySelectorAll('[data-edit-holding]').forEach((btn) => {
    btn.addEventListener('click', () => openHoldingEdit(state.holdById[+btn.dataset.editHolding]));
  });
  $('#holding-body').querySelectorAll('[data-del-holding]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const h = state.holdById[+btn.dataset.delHolding];
      if (!h || !confirm(`确定删除持仓「${h.name}」？`)) return;
      try {
        await api('/api/holdings/' + h.id, { method: 'DELETE' });
        toast('已删除');
        await loadInvestmentPage();
      } catch (err) { toast(err.message, true); }
    });
  });
  $('#holding-body').querySelectorAll('[data-price]').forEach((inp) => {
    inp.addEventListener('change', () => savePrice(+inp.dataset.price, parseFloat(inp.value) || 0));
  });
}

function renderPortfolioChart() {
  const p = state.portfolio;
  if (!p) return;
  const is30 = state.pfRange === '30';
  const series = is30 ? p.daily : p.monthly;
  drawLine($('#pf-chart'), series, is30 ? 'date' : 'month');
  const first = series.length ? series[0].net : 0;
  const last = series.length ? series[series.length - 1].net : 0;
  $('#pf-foot').innerHTML = `<span>起点 ${fmt0(first)}</span>
    <span class="${cls(last - first)}">变化 ${last - first >= 0 ? '+' : ''}${fmt0(last - first)}</span>
    <span>现值 ${fmt0(last)}</span>`;
}

/* ---------------- AI 分析：记账 + 持仓 ---------------- */

// 极简 Markdown → HTML：模型固定输出「## 小标题 + 段落 + 列表」，够用就行
function aiMdToHtml(md) {
  const inline = (t) => esc(t)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+?)`/g, '<code>$1</code>');
  const out = [];
  let inList = false;
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };

  String(md || '').split('\n').forEach((raw) => {
    const line = raw.trim();
    if (!line) return closeList();
    const h = line.match(/^#{1,6}\s+(.*)$/);
    const lvl = line.match(/^(#{1,6})\s+/);
    const li = line.match(/^(?:[-*]|\d+[.、])\s+(.*)$/);
    if (h) {
      closeList();
      out.push(`<div class="ai-h ai-h${(lvl ? lvl[1].length : 2)}">${inline(h[1])}</div>`);
    } else if (li) {
      if (!inList) { out.push('<ul class="ai-ul">'); inList = true; }
      out.push(`<li>${inline(li[1])}</li>`);
    } else {
      closeList();
      out.push(`<p>${inline(line)}</p>`);
    }
  });
  closeList();
  return out.join('');
}

async function loadAiSettings() {
  try {
    state.ai = await api('/api/ai/settings');
  } catch (err) {
    state.ai = null;
  }
}

// 记账页顶部的 AI 分析面板
async function loadAiPanel() {
  await loadAiSettings();
  renderAiBody(state.ai);
}

// 投资页的 AI 持仓分析面板
async function loadPortfolioAiPanel() {
  await loadAiSettings();
  renderPortfolioAiBody(state.ai);
}

function renderAiBody(s) {
  const box = $('#ai-body');
  const when = $('#ai-when');
  const btn = $('#ai-run');
  if (!box) return;

  if (!s || !s.configured) {
    when.textContent = '';
    btn.hidden = true;
    box.innerHTML = '<div class="empty">还没配 AI 接口。去「设置 → AI 分析」填上接口地址、模型和 Key，回来就能生成。</div>';
    return;
  }

  btn.hidden = false;
  const ins = s.spending;
  btn.textContent = ins ? '重新生成' : '生成分析';

  if (!ins) {
    when.textContent = '';
    box.innerHTML = '<div class="empty">还没有分析。点右上角「生成分析」，我读一遍这个月的账本，给出一段人话点评。</div>';
    return;
  }

  const tokens = ins.usage && ins.usage.total_tokens ? ` · ${ins.usage.total_tokens} tokens` : '';
  when.textContent = `${(ins.at || '').replace('T', ' ').slice(0, 16)} · ${ins.model || ''}${tokens}`;
  const stale = ins.stale
    ? '<div class="ai-stale">生成之后账本又有了新流水，这份分析的数字有点旧，建议重新生成一次。</div>'
    : '';
  box.innerHTML = `${stale}<div class="ai-md">${aiMdToHtml(ins.content)}</div>`;
}

// 持仓分析：标题里带「加仓 / 持有 / 减仓」的给它上色，一眼能扫到结论
const ACTION_CLS = { '加仓': 'act-add', '持有': 'act-hold', '减仓': 'act-cut' };

function portfolioMdToHtml(md) {
  return aiMdToHtml(md).replace(
    /<div class="ai-h ai-h3">(.*?)<\/div>/g,
    (all, inner) => {
      const action = ['加仓', '减仓', '持有'].find((a) => inner.includes(a));
      return action
        ? `<div class="ai-h ai-h3 ${ACTION_CLS[action]}">${inner}</div>`
        : all;
    });
}

function renderPortfolioAiBody(s) {
  const box = $('#pai-body');
  const when = $('#pai-when');
  const btn = $('#pai-run');
  if (!box) return;

  const items = (state.holdings && state.holdings.items) || [];
  if (!items.length) {
    when.textContent = '';
    btn.hidden = true;
    box.innerHTML = '<div class="empty">先在上面加一只持仓，我才能谈加仓还是减仓。</div>';
    return;
  }

  if (!s || !s.configured) {
    when.textContent = '';
    btn.hidden = true;
    box.innerHTML = '<div class="empty">还没配 AI 接口。去「设置 → AI 分析」填上接口地址、模型和 Key，回来就能逐只看建议。</div>';
    return;
  }

  btn.hidden = false;
  const pai = s.portfolio;
  btn.textContent = pai ? '重新生成' : '生成分析';

  if (!pai) {
    when.textContent = '';
    box.innerHTML = '<div class="empty">还没有分析。点右上角「生成分析」，我会先抓每只标的的区间涨跌，再逐只给结论。</div>';
    return;
  }

  const tokens = pai.usage && pai.usage.total_tokens ? ` · ${pai.usage.total_tokens} tokens` : '';
  when.textContent = `${(pai.at || '').replace('T', ' ').slice(0, 16)} · ${pai.model || ''}${tokens}`;
  const stale = pai.stale
    ? '<div class="ai-stale">生成之后持仓价格或份额又变了，这份建议的数字有点旧，建议重新生成一次。</div>'
    : '';
  box.innerHTML = `${stale}<div class="ai-md">${portfolioMdToHtml(pai.content)}</div>`;
}

async function runPortfolioAi(force) {
  const btn = $('#pai-run');
  btn.disabled = true;
  btn.textContent = '分析中…';
  try {
    const r = await api('/api/ai/portfolio-analyze', { method: 'POST', body: { force: !!force } });
    state.ai = { ...(state.ai || {}), portfolio: r };
    renderPortfolioAiBody(state.ai);
    toast(r.cached ? '用的还是这份最新分析' : '持仓分析已生成');
  } catch (err) {
    toast(err.message, true);
    btn.disabled = false;
    btn.textContent = '生成分析';
  }
}

async function runAiAnalyze(force) {
  const btn = $('#ai-run');
  btn.disabled = true;
  btn.textContent = '分析中…';
  try {
    const r = await api('/api/ai/analyze', { method: 'POST', body: { force: !!force } });
    state.ai = { ...(state.ai || {}), spending: r };
    renderAiBody(state.ai);
    toast(r.cached ? '用的还是这份最新分析' : '分析已生成');
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = state.ai && state.ai.spending ? '重新生成' : '生成分析';
  }
}

/* ---------------- 记账 ---------------- */

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
  if ($('#f-month').value) p.set('month', $('#f-month').value);
  p.set('limit', state.tx.limit);
  p.set('offset', state.tx.page * state.tx.limit);
  return p;
}

async function loadTransactions() {
  const data = await api('/api/transactions?' + txFilterParams().toString());
  state.tx.total = data.total;
  state.tx.byId = {};
  data.items.forEach((t) => { state.tx.byId[t.id] = t; });
  renderTxSummary();
  $('#tx-body').innerHTML = data.items.length ? data.items.map((t) => `
    <tr>
      <td data-label="日期" class="muted">${t.date}</td>
      <td data-label="类型">${t.type === 'in' ? '收入' : '支出'}</td>
      <td data-label="分类">${t.category_name ? `<span class="tag">${esc(t.category_name)}</span>` : '<span class="muted">未分类</span>'}</td>
      <td data-label="金额" class="num ${t.type === 'in' ? 'pos' : 'neg'}">${t.type === 'in' ? '+' : '−'}${fmt(t.amount)}</td>
      <td data-label="备注" class="muted">${esc(t.note || '')}</td>
      <td data-label=""><div class="row-actions"><button class="btn tiny" data-edit="${t.id}">编辑</button><button class="btn tiny danger" data-del="${t.id}">删除</button></div></td>
    </tr>`).join('') : '<tr><td colspan="6"><div class="empty">没有符合条件的记录</div></td></tr>';

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
    note: fd.get('note') || '',
    source: 'manual',
  };
  if (fd.get('category_id')) body.category_id = parseInt(fd.get('category_id'), 10);
  try {
    const res = await api('/api/transactions', { method: 'POST', body });
    toast(res.category_id ? '已记账（自动分类好了）' : '已记账');
    f.reset();
    f.date.value = today();
    await loadMonthSummary();
    await loadTransactions();
  } catch (err) { toast(err.message, true); }
}

function catOptions(sel) {
  const opt = (c) => `<option value="${c.id}"${Number(sel) === c.id ? ' selected' : ''}>${esc(c.name)}</option>`;
  return '<option value="">自动分类</option>'
    + '<optgroup label="支出">' + state.categories.filter((c) => c.kind === 'expense').map(opt).join('') + '</optgroup>'
    + '<optgroup label="收入">' + state.categories.filter((c) => c.kind === 'income').map(opt).join('') + '</optgroup>';
}

// 编辑一条流水：预填原值，保存走 PATCH（后端按整条覆盖）
function openTxEdit(t) {
  if (!t) return;
  const typeOpt = (v, label) => `<option value="${v}"${t.type === v ? ' selected' : ''}>${label}</option>`;
  openModal('编辑流水', `
    <div class="modal-cols">
      <label class="field"><span>日期</span><input class="input" id="e-date" type="date" value="${t.date}"></label>
      <label class="field"><span>类型</span><select class="input" id="e-type">
        ${typeOpt('out', '支出')}${typeOpt('in', '收入')}</select></label>
      <label class="field"><span>金额</span><input class="input" type="number" step="0.01" min="0.01" id="e-amount" value="${t.amount}"></label>
      <label class="field"><span>分类</span><select class="input" id="e-cat">${catOptions(t.category_id)}</select></label>
    </div>
    <label class="field"><span>备注</span><input class="input" id="e-note" value="${esc(t.note || '')}"></label>
    <button class="btn primary block" id="e-save">保存修改</button>
  `, () => {
    $('#e-save').addEventListener('click', async () => {
      const body = {
        date: $('#e-date').value,
        type: $('#e-type').value,
        amount: parseFloat($('#e-amount').value),
        note: $('#e-note').value || '',
        autocategorize: !$('#e-cat').value,
      };
      if ($('#e-cat').value) body.category_id = parseInt($('#e-cat').value, 10);
      if (!body.date || !(body.amount > 0)) return toast('日期和金额都要填', true);
      try {
        await api('/api/transactions/' + t.id, { method: 'PATCH', body });
        toast('已修改');
        closeModal();
        await loadMonthSummary();
        await loadTransactions();
      } catch (err) { toast(err.message, true); }
    });
  });
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
    <label class="field"><span>压缩包密码（只有微信 zip 要填）</span>
      <input class="input" type="text" id="imp-zip" placeholder="微信「账单下载」通知里那串"></label>
    <div id="imp-preview"></div>
    <button class="btn primary block" id="imp-go" disabled>确认导入</button>
    <div id="imp-rules" class="imp-rules-wrap"></div>
  `, (root) => {
    $('#imp-file', root).addEventListener('change', (e) => {
      impState.file = e.target.files[0] || null;
      if (impState.file) previewImport();
    });
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

// 最近导入批次（可整批撤销）
async function renderImportRules() {
  const host = $('#imp-rules');
  if (!host) return;
  const s = impState.sources || await refreshImportSources();
  if (!s) { host.innerHTML = ''; return; }
  const batches = s.batches || [];

  const batchHtml = batches.length ? batches.slice(0, 5).map((b) => `
    <div class="imp-batch">
      <span class="imp-batch-name">${esc(b.batch)}</span>
      <span class="muted">${b.n} 笔 · ${esc(b.from_date || '')} ~ ${esc(b.to_date || '')}</span>
      <button class="btn tiny danger" data-del-batch="${esc(b.batch)}">撤销</button>
    </div>`).join('') : '';

  host.innerHTML = batchHtml
    ? '<div class="imp-section-title">最近导入批次</div><div class="imp-rule-grid">' + batchHtml + '</div>'
    : '';

  host.querySelectorAll('[data-del-batch]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!confirm('撤销这次导入？对应的流水会被删掉。')) return;
    try {
      const r = await api('/api/import/batch/' + encodeURIComponent(btn.dataset.delBatch), { method: 'DELETE' });
      toast(`已撤销 ${r.removed} 笔`);
      await refreshImportSources();
      await renderImportRules();
      await loadMonthSummary();
      if (currentView() === 'transactions') loadTransactions();
    } catch (err) { toast(err.message, true); }
  }));
}

async function previewImport() {
  const file = impState.file;
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
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
    </div>${warn}`;

  const rows = (res.rows || []).map((r, i) => impRowHtml(r, i)).join('');

  $('#imp-preview').innerHTML = head
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
  const pay = r.pay_method ? `<span class="imp-pay">${esc(r.pay_method)}</span>` : '';
  const note = r.note || r.counterparty || '';
  const sign = r.type === 'in' ? '收' : r.type === 'out' ? '支' : '·';
  return `<div class="imp-row${disabled ? ' is-dim' : ''}">
    <label class="imp-check"><input type="checkbox" data-check="${i}"${r.selected && !disabled ? ' checked' : ''}${disabled ? ' disabled' : ''}></label>
    <span class="imp-date">${esc((r.date || '').slice(5, 10))}</span>
    <span class="imp-type ${r.type === 'in' ? 'pos' : r.type === 'out' ? 'neg' : ''}">${sign}</span>
    <span class="imp-note" title="${esc(note)}">${esc(note)} ${tag}${pay}</span>
    <span class="imp-amt ${r.type === 'in' ? 'pos' : r.type === 'out' ? 'neg' : ''}">${fmt(r.amount)}</span>
  </div>`;
}

function bindImportPreviewEvents() {
  const box = $('#imp-preview');
  box.querySelectorAll('[data-check]').forEach((cb) => cb.addEventListener('change', updateImportGo));
}

function updateImportGo() {
  const rows = (impState.preview && impState.preview.rows) || [];
  let n = 0; let sumIn = 0; let sumOut = 0;
  rows.forEach((r, i) => {
    const cb = $('#imp-preview [data-check="' + i + '"]');
    const on = cb ? cb.checked : false;
    r.selected = on;
    if (!on) return;
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
  const payload = rows.filter((r) => r.selected && r.type !== 'skip').map((r) => ({
    date: r.date,
    type: r.type,
    amount: r.amount,
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
    await loadMonthSummary();
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
  state.holdById = {};
  data.items.forEach((h) => { state.holdById[h.id] = h; });
  renderHoldingsTable(data);
}

// 投资页一次把市值曲线一起拉了，两块数据共用一次渲染
async function loadInvestmentPage() {
  try {
    const [, pf] = await Promise.all([loadHoldings(), api('/api/portfolio')]);
    state.portfolio = pf;
  } catch (err) {
    toast(err.message, true);
    return;
  }
  renderPortfolioChart();
  await loadPortfolioAiPanel();
  refreshSyncNote();
}

// 场外基金：用户下单时填的是「金额」，不是份额。按申购金额 × 费率反推份额与含费成本价。
// 费率优先用接口查到的折后费率（rate），查不到就让用户手填（默认 0.12%）。
const FUND_FEE_DEFAULT = 0.0012;

function fundFeeInput(fund) {
  const rate = (fund && typeof fund.rate === 'number' && fund.rate > 0)
    ? fund.rate : FUND_FEE_DEFAULT;
  const tag = (fund && fund.rate)
    ? `（${fund.name ? fund.name + ' ' : ''}折后申购费）`
    : '（未查到费率，按 0.12% 估算，可改）';
  return `<label class="field"><span>申购费率</span>
    <input class="input" type="number" step="0.0001" id="h-fee" value="${rate}">
    <small class="hint" style="margin:2px 0 0">${tag}</small></label>`;
}

// 返回 {units, costPrice, note} 或 null（缺关键数据时不硬算，交给用户手填）
function calcUnitsFromAmount(amount, fee, nav) {
  if (!(amount > 0) || !(nav > 0)) return null;
  const f = Math.min(Math.max(fee || 0, 0), 0.5);
  const units = Math.round((amount * (1 - f)) / nav * 10000) / 10000;
  if (!(units > 0)) return null;
  return { units, costPrice: Math.round((amount / units) * 1e6) / 1e6 };
}

function openHoldingForm() {
  const marketOpts = (state.meta.markets || [{ value: 'auto', label: '自动识别' }])
    .map((m) => `<option value="${m.value}">${esc(m.label)}</option>`).join('');
  const kindOpts = Object.entries(state.meta.holding_kinds || {
    fund: '基金', stock: '股票', bond: '债券', gold: '黄金', cash: '现金类', other: '其他',
  }).map(([v, label]) => `<option value="${v}">${esc(label)}</option>`).join('');

  openModal('新增持仓', `
    <div class="modal-cols">
      <label class="field"><span>代码</span><input class="input" id="h-code" placeholder="510300 / 020602 / 00700 / BTC"></label>
      <label class="field"><span>市场</span><select class="input" id="h-market">${marketOpts}</select></label>
      <label class="field"><span>名称</span><input class="input" id="h-name" placeholder="填代码可自动带出"></label>
      <label class="field"><span>类型</span><select class="input" id="h-kind">${kindOpts}</select></label>
      <label class="field"><span>份额</span><input class="input" type="number" step="0.0001" id="h-units" value="0"></label>
      <label class="field"><span>成本价</span><input class="input" type="number" step="0.0001" id="h-cost" value="0"></label>
      <label class="field"><span>现价</span><input class="input" type="number" step="0.0001" id="h-price" value="0"></label>
    </div>
    <div id="h-amt-box" style="display:none">
      <div class="modal-cols" style="margin-top:12px">
        <label class="field"><span>买入金额</span><input class="input" type="number" step="0.01" id="h-amount" placeholder="下的是金额，比如 1000"></label>
        ${fundFeeInput(null)}
      </div>
      <p class="hint" id="h-calc">知道下单金额就够了：填金额和费率，自动算份额与成本价。</p>
    </div>
    <p class="hint" id="h-probe">填好代码后会试拉一次行情，成功就自动填名称和现价。</p>
    <button class="btn primary block" id="h-save">保存</button>
  `, () => {
    const probe = $('#h-probe');
    let probed = false;
    let probeData = null;

    // 场外基金两种情况：市场明确选了「场外基金」，或市场是 auto 但探测后确认是场外
    const isFund = () => $('#h-market').value === 'fund'
      || ($('#h-market').value === 'auto' && probeData && probeData.market === 'fund');

    const recalc = () => {
      const box = $('#h-calc');
      if (!isFund()) { $('#h-amt-box').style.display = 'none'; return; }
      $('#h-amt-box').style.display = 'block';
      const amount = parseFloat($('#h-amount').value) || 0;
      const fee = parseFloat($('#h-fee').value);
      const nav = parseFloat($('#h-price').value) || 0;
      const r = calcUnitsFromAmount(amount, fee, nav);
      if (!r) {
        box.className = 'hint';
        box.textContent = nav > 0
          ? '填上买入金额，自动算份额。'
          : '先在上面填代码，让它把现价（净值）带出来，再填买入金额。';
        return;
      }
      $('#h-units').value = r.units;
      $('#h-cost').value = r.costPrice;
      box.className = 'hint pos';
      box.textContent = `按净值 ${nav} 算：${amount} 元 ÷ (1-${((fee || 0) * 100).toFixed(3)}%) ÷ ${nav}`
        + ` ≈ ${r.units} 份，含费成本价 ${r.costPrice}。${(probeData && probeData.date) ? '注意：这是 ' + probeData.date + ' 的净值，今天下单要按晚上公布的净值确认份额。' : ''}`;
    };

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
          probeData = q;
          if (!$('#h-name').value.trim() && q.name) $('#h-name').value = q.name;
          $('#h-price').value = q.price;
          // 场外基金：把查到的折后费率填进输入框
          if (q.fund && isFund()) {
            const f = $('#h-fee');
            if (f) { f.value = q.fund.rate || FUND_FEE_DEFAULT; recalc(); }
          }
          probe.className = 'hint pos';
          probe.textContent = `已查到：${q.name || code} · ${q.price}（${q.date}）`;
        } else {
          probe.className = 'hint neg';
          probe.textContent = `没查到：${q.error}。可以换个市场选项，或直接手填现价。`;
        }
        recalc();
      } catch (err) {
        probe.className = 'hint neg';
        probe.textContent = '查询失败：' + err.message;
      }
    };

    $('#h-code').addEventListener('blur', tryProbe);
    $('#h-market').addEventListener('change', () => { probed = false; tryProbe(); recalc(); });
    $('#h-kind').addEventListener('change', () => { probed = false; tryProbe(); recalc(); });
    $('#h-amount').addEventListener('input', recalc);
    const feeEl = $('#h-fee');
    if (feeEl) feeEl.addEventListener('input', recalc);
    recalc();

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
        await loadMonthSummary();
        refreshSyncNote();
      } catch (err) { toast(err.message, true); }
    });
  });
}

// 编辑一个持仓：买卖后直接把份额改成新持有量；成本价可按需调整
function openHoldingEdit(h) {
  if (!h) return;
  const marketOpts = (state.meta.markets || [{ value: 'auto', label: '自动识别' }])
    .map((m) => `<option value="${m.value}"${h.market === m.value ? ' selected' : ''}>${esc(m.label)}</option>`).join('');
  const kindOpts = Object.entries(state.meta.holding_kinds || {
    fund: '基金', stock: '股票', bond: '债券', gold: '黄金', cash: '现金类', other: '其他',
  }).map(([v, label]) => `<option value="${v}"${h.kind === v ? ' selected' : ''}>${esc(label)}</option>`).join('');
  openModal(`编辑持仓 · ${esc(h.name)}`, `
    <div class="modal-cols">
      <label class="field"><span>代码</span><input class="input" id="eh-code" value="${esc(h.code || '')}"></label>
      <label class="field"><span>名称</span><input class="input" id="eh-name" value="${esc(h.name)}"></label>
      <label class="field"><span>类型</span><select class="input" id="eh-kind">${kindOpts}</select></label>
      <label class="field"><span>市场</span><select class="input" id="eh-market">${marketOpts}</select></label>
      <label class="field"><span>份额</span><input class="input" type="number" step="0.0001" id="eh-units" value="${h.units}"></label>
      <label class="field"><span>成本价</span><input class="input" type="number" step="0.0001" id="eh-cost" value="${h.cost_price}"></label>
      <label class="field"><span>现价</span><input class="input" type="number" step="0.0001" id="eh-price" value="${h.price}"></label>
    </div>
    <p class="hint" style="margin-top:0">交易之后把份额改成新的持有量即可；改成本价，盈亏按新成本算。</p>
    <button class="btn primary block" id="eh-save">保存修改</button>
  `, () => {
    $('#eh-save').addEventListener('click', async () => {
      const name = $('#eh-name').value.trim() || $('#eh-code').value.trim();
      if (!name) return toast('名称或代码至少填一个', true);
      try {
        await api('/api/holdings/' + h.id, {
          method: 'PATCH',
          body: {
            name,
            code: $('#eh-code').value.trim(),
            kind: $('#eh-kind').value,
            market: $('#eh-market').value,
            units: parseFloat($('#eh-units').value) || 0,
            cost_price: parseFloat($('#eh-cost').value) || 0,
            price: parseFloat($('#eh-price').value) || 0,
          },
        });
        toast('已修改');
        closeModal();
        await loadHoldings();
        await loadMonthSummary();
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
    await loadMonthSummary();
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
    await loadMonthSummary();
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
  await renderBudgets();
}

async function renderAiSettings() {
  let s;
  try {
    s = await api('/api/ai/settings');
  } catch (err) { return; }
  state.ai = { ...(state.ai || {}), ...s };

  $('#ai-base').value = s.base || '';
  $('#ai-model').value = s.model || '';
  const keyEl = $('#ai-key');
  keyEl.value = '';
  keyEl.placeholder = s.key_set ? `已保存 ${s.key_hint}，留空就不改` : '粘贴你的 API Key';
  $('#ai-key-clear').hidden = !s.key_set;

  const tag = $('#ai-cfg-state');
  tag.textContent = s.configured
    ? (s.source === 'env' ? '已配置（来自环境变量）' : '已配置')
    : '未配置';
  tag.className = 'tag' + (s.configured ? '' : ' warn-tag');

  renderJevSettings(s.jev || {});
}

function renderJevSettings(j) {
  $('#jev-model').value = j.model || 'jev-latest';
  const keyEl = $('#jev-key');
  keyEl.value = '';
  keyEl.placeholder = j.key_set ? `已保存 ${j.key_hint}，留空就不改` : 'apikey_...';
  $('#jev-key-clear').hidden = !j.key_set;

  const tag = $('#jev-cfg-state');
  tag.textContent = j.configured
    ? (j.source === 'env' ? '已配置（来自环境变量）' : '已配置')
    : '未配置';
  tag.className = 'tag' + (j.configured ? '' : ' warn-tag');
}

async function saveJevSettings() {
  try {
    await api('/api/ai/jev', {
      method: 'PUT',
      body: { model: $('#jev-model').value.trim(), key: $('#jev-key').value.trim() },
    });
    $('#jev-test-out').textContent = '';
    toast('Jev 配置已保存');
    await renderAiSettings();
  } catch (err) { toast(err.message, true); }
}

async function testJev() {
  const btn = $('#jev-test'), out = $('#jev-test-out');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '测试中…';
  out.textContent = '';
  try {
    const r = await api('/api/ai/jev/test', { method: 'POST' });
    out.textContent = `连接正常（${r.model}）：示例备注归到了「${r.choice}」，置信度 ${r.confidence}`;
    toast('Jev 连接正常');
  } catch (err) {
    out.textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function saveAiSettings() {
  try {
    await api('/api/ai/settings', {
      method: 'PUT',
      body: {
        base: $('#ai-base').value.trim(),
        model: $('#ai-model').value.trim(),
        key: $('#ai-key').value.trim(),
      },
    });
    toast('AI 配置已保存');
    await renderAiSettings();
  } catch (err) { toast(err.message, true); }
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
  });
  $('#logout-btn').addEventListener('click', async () => {
    await api('/api/auth/logout', { method: 'POST' });
    location.reload();
  });

  $('#pf-range').addEventListener('click', (e) => {
    const b = e.target.closest('.seg-btn');
    if (!b) return;
    state.pfRange = b.dataset.range;
    $$('#pf-range .seg-btn').forEach((x) => x.classList.toggle('is-on', x === b));
    renderPortfolioChart();
  });

  // 记账
  const txForm = $('#tx-form');
  txForm.date.value = today();
  txForm.addEventListener('submit', submitTx);
  $('#import-btn').addEventListener('click', openImport);
  ['#f-q', '#f-type', '#f-month'].forEach((sel) => {
    let timer;
    $(sel).addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { state.tx.page = 0; loadTransactions(); }, 220); });
    $(sel).addEventListener('change', () => { state.tx.page = 0; loadTransactions(); });
  });
  $('#f-reset').addEventListener('click', () => {
    $('#f-q').value = ''; $('#f-type').value = ''; $('#f-month').value = '';
    state.tx.page = 0; loadTransactions();
  });
  $('#page-prev').addEventListener('click', () => { if (state.tx.page > 0) { state.tx.page--; loadTransactions(); } });
  $('#page-next').addEventListener('click', () => { state.tx.page++; loadTransactions(); });
  $('#tx-body').addEventListener('click', async (e) => {
    const eid = e.target.dataset.edit;
    if (eid) { openTxEdit(state.tx.byId[eid]); return; }
    const id = e.target.dataset.del;
    if (!id) return;
    if (!confirm('删掉这条流水？')) return;
    await api('/api/transactions/' + id, { method: 'DELETE' });
    toast('已删除');
    await loadMonthSummary();
    await loadTransactions();
  });

  // 持仓
  $('#holding-add').addEventListener('click', openHoldingForm);

  // 预算
  $('#budget-add').addEventListener('click', openBudgetForm);
  $('#budget-month').addEventListener('change', renderBudgets);

  // 设置
  $('#rule-add').addEventListener('click', openRuleForm);
  $('#category-add').addEventListener('click', openCategoryForm);
  $('#ai-save').addEventListener('click', saveAiSettings);
  $('#jev-save').addEventListener('click', saveJevSettings);
  $('#jev-test').addEventListener('click', testJev);
  $('#jev-key-clear').addEventListener('click', async () => {
    if (!confirm('清除已保存的 Jev Key？清掉之后自动分类会退回本地规则。')) return;
    try {
      await api('/api/ai/jev', { method: 'PUT', body: { clear_key: true } });
      $('#jev-test-out').textContent = '';
      toast('Jev Key 已清除');
      await renderAiSettings();
    } catch (err) { toast(err.message, true); }
  });
  $('#ai-key-clear').addEventListener('click', async () => {
    if (!confirm('清除已保存的 API Key？清掉之后 AI 分类和分析都会停用。')) return;
    try {
      await api('/api/ai/settings', { method: 'PUT', body: { clear_key: true } });
      toast('Key 已清除');
      await renderAiSettings();
    } catch (err) { toast(err.message, true); }
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

  // AI 分析：已有结果时再点就是强制重新生成
  $('#ai-run').addEventListener('click', () => runAiAnalyze(!!(state.ai && state.ai.spending)));
  $('#pai-run').addEventListener('click', () => runPortfolioAi(!!(state.ai && state.ai.portfolio)));

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
      await loadMonthSummary();
    } catch (err) { toast(err.message, true); } finally {
      btn.disabled = false;
      btn.textContent = '扫收件目录';
    }
  });

  $('#auto-run-portfolio').addEventListener('click', async () => {
    const btn = $('#auto-run-portfolio');
    btn.disabled = true;
    try {
      await api('/api/portfolio/snapshot', { method: 'POST' });
      toast('已记录今天的市值快照');
      await renderAutoPanel();
      if (state.view === 'holdings') await loadInvestmentPage();
    } catch (err) { toast(err.message, true); } finally {
      btn.disabled = false;
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
