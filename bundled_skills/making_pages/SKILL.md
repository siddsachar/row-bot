---
name: making_pages
display_name: Making a page
icon: "🗂️"
description: "Templates and rules for interactive pages beside the chat: long plans with approvals, dashboards, forms and small tools."
enabled_by_default: true
version: "1.0"
tags:
  - pages
  - plans
  - dashboards
activation:
  phrases:
    - make a page
    - plan i can approve
    - launch tracker
    - build a dashboard
    - make a form
    - interactive page
  keywords:
    - tracker
    - dashboard
    - interactive
  negative_phrases:
    - landing page
    - website
    - web page
    - slides
    - presentation
  examples:
    - Make me a launch plan I can approve step by step
author: Row-Bot
---

# Making a page

A page is one HTML document the person uses beside the chat. Make one with
`page_create(title, html, data, libraries)`; keep it current with
`page_update(page, data=…)`; see what they ticked or decided with
`page_read(page)`.

A page is for something long or kept: a document, a tool, a tracker over weeks. A comparison, a budget, a checklist or
the person's own figures is an interface in the reply (INTERFACES), not a page;
never make both for the same thing.

## Rules

- **No network.** Nothing loads from the internet: no CDN scripts, fonts,
  images or fetch. Inline all CSS and JS. Images only as `data:` URLs.
- **Data in `data`, not in the HTML.** The page reads it with
  `rowbot.data()`. Progress and fresh numbers go in with
  `page_update(page, data=…)`, so the HTML stays the same.
- **Use `libraries: ["rowbot-style"]`** so the page looks like Row-Bot in
  light and dark. Add `"charts"` for Chart.js 4 (`window.Chart`).
- **Plain words.** Short sentences, sentence case, no ids or codes on screen.
- **Phone width.** It must work at 360 px: no fixed widths, tables in
  `.rb-table-wrap`, buttons that wrap.
- **One primary action per page,** at most. The person's Send to Row-Bot is
  outside the page, under it.

## The bridge: `window.rowbot`

| Call | What it does |
| --- | --- |
| `rowbot.data()` | The JSON you attached (a copy). |
| `rowbot.state.get()` | The page's saved object (`{}` at first). |
| `rowbot.state.set(obj)` | Saves the whole object (up to 256 KB) with the chat, on every device. |
| `rowbot.decide(id, choice, note?, label?)` | Records the person's choice on one item; `choice` `null` clears it. `label` is the item's name in words. |
| `rowbot.decided()` | What the person chose, by id: `{choice, note, label, sent}`. |
| `rowbot.onDecided(fn)` | Called when a choice changes, or once they are sent. |
| `rowbot.theme()` | `{mode: 'light' \| 'dark', tokens}`; tokens hold `chart-series-1…6`, `chart-grid`, `chart-axis`, `text-muted`… |
| `rowbot.onTheme(fn)` | Called when the person switches theme. |

Choices only gather: under the page the person sees how many and "Send to
Row-Bot". Their click sends you one message listing them, with a JSON list.
Choices you'll see in words: `approve` → Approved, `change` → Change,
`skip` → Skipped, `pick` → Picked, `refresh` → Refresh, `submit` → Filled in;
anything else as written. They never approve a tool call.

## rowbot-style

Colours: `var(--rb-canvas)`, `--rb-surface`, `--rb-text-primary`,
`--rb-text-secondary`, `--rb-text-muted`, `--rb-border-subtle`,
`--rb-accent-solid`, `--rb-status-{info,success,warning,danger}-{text,background}`,
`--rb-chart-series-1…6`. Classes: `rb-header`, `rb-stack`, `rb-row`,
`rb-grid`, `rb-card`, `rb-stat` (`rb-stat-value`, `rb-stat-label`),
`rb-badge` with `data-tone="success|info|warning|danger"`, `rb-progress`
(a `<span>` inside, width in %), `rb-list`, `rb-table-wrap`, `rb-number`,
`rb-muted`, `rb-primary` (a button), `rb-quiet`. Buttons with
`aria-pressed="true"` show as chosen.

## Templates

### A plan with approvals (the person approves, changes or skips each step)

data: `{"title", "summary", "steps": [{"id", "title", "detail", "status": "todo" | "doing" | "done"}]}`.
As you work, set each step's `status` with `page_update(page, data=…)`.

```html
<!doctype html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  .step { display: grid; gap: 8px; }
  .step-head { display: flex; gap: 8px; align-items: flex-start; justify-content: space-between; }
  .step h3 { margin: 0; }
  .step p { margin: 0; color: var(--rb-text-secondary); }
  .choices { display: flex; flex-wrap: wrap; gap: 8px; }
  .why { display: none; flex-direction: column; gap: 8px; }
  .step[data-editing] .why { display: flex; }
  .step[data-status="done"] h3 { color: var(--rb-text-muted); }
  #steps { list-style: none; padding: 0; margin: 16px 0 0; }
</style>
</head>
<body>
<header class="rb-header"><h1 id="title"></h1><span class="rb-badge" id="count"></span></header>
<p id="summary" class="rb-muted"></p>
<div class="rb-progress" aria-hidden="true"><span id="bar"></span></div>
<ol id="steps" class="rb-stack"></ol>
<script>
const plan = rowbot.data() || { steps: [] };
const STATUS = { done: ['Done', 'success'], doing: ['In progress', 'info'], todo: ['To do', ''] };
const SAID = { approve: 'Approved', change: 'Change', skip: 'Skipped' };
const editing = new Set();
const el = (tag, props, ...kids) => { const n = Object.assign(document.createElement(tag), props); n.append(...kids); return n; };
function render() {
  const chosen = rowbot.decided();
  const done = plan.steps.filter((s) => s.status === 'done').length;
  title.textContent = plan.title || '';
  summary.textContent = plan.summary || '';
  count.textContent = `${done} of ${plan.steps.length} done`;
  bar.style.width = `${plan.steps.length ? (100 * done) / plan.steps.length : 0}%`;
  steps.replaceChildren(...plan.steps.map((step) => {
    const mine = chosen[step.id];
    const [words, tone] = STATUS[step.status] || STATUS.todo;
    const card = el('li', { className: 'rb-card step' });
    card.dataset.status = step.status || 'todo';
    if (editing.has(step.id)) card.dataset.editing = '';
    const badge = el('span', { className: 'rb-badge', textContent: words });
    if (tone) badge.dataset.tone = tone;
    card.append(el('div', { className: 'step-head' }, el('h3', { textContent: step.title }), badge));
    if (step.detail) card.append(el('p', { textContent: step.detail }));
    if (step.status === 'done') return card;
    const choices = el('div', { className: 'choices', role: 'group' });
    choices.setAttribute('aria-label', `Your choice on ${step.title}`);
    for (const [choice, label] of [['approve', 'Approve'], ['change', 'Change'], ['skip', 'Skip']]) {
      const button = el('button', { type: 'button', textContent: label, onclick: () => choose(step, choice) });
      button.setAttribute('aria-pressed', String(mine?.choice === choice));
      choices.append(button);
    }
    const why = el('textarea', { placeholder: 'What should change?', value: mine?.choice === 'change' ? mine.note : '' });
    why.setAttribute('aria-label', `What should change in ${step.title}`);
    const save = el('button', { type: 'button', className: 'rb-primary', textContent: 'Save change' });
    save.onclick = () => { editing.delete(step.id); rowbot.decide(step.id, 'change', why.value.trim(), step.title); };
    card.append(choices, el('div', { className: 'why' }, why, el('div', { className: 'choices' }, save)));
    if (mine && !editing.has(step.id))
      card.append(el('small', { textContent: `${SAID[mine.choice] || mine.choice}${mine.note ? `: “${mine.note}”` : ''}${mine.sent ? ' · sent' : ''}` }));
    return card;
  }));
}
function choose(step, choice) {
  const current = rowbot.decided()[step.id];
  if (choice === 'change') {
    editing.add(step.id);
    render();
    steps.querySelector('[data-editing] textarea')?.focus();
    return;
  }
  editing.delete(step.id);
  if (current?.choice === choice && !current.sent) rowbot.decide(step.id, null);
  else rowbot.decide(step.id, choice, '', step.title);
}
rowbot.onDecided(render);
render();
</script>
</body>
</html>
```

### A checklist or launch tracker (remembers what's ticked, on every device)

data: `{"title", "sections": [{"title", "items": [{"id", "text"}]}]}`. Ticks live in state.

```html
<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body>
<header class="rb-header"><h1 id="title"></h1><span class="rb-badge" id="count"></span></header>
<div class="rb-progress" aria-hidden="true"><span id="bar"></span></div>
<div id="sections" class="rb-stack" style="margin-top:16px"></div>
<script>
const list = rowbot.data() || { sections: [] };
const ticked = rowbot.state.get().ticked || {};
const items = list.sections.flatMap((s) => s.items);
function render() {
  const done = items.filter((i) => ticked[i.id]).length;
  title.textContent = list.title || '';
  count.textContent = `${done} of ${items.length} done`;
  bar.style.width = `${items.length ? (100 * done) / items.length : 0}%`;
  sections.replaceChildren(...list.sections.map((section) => {
    const ul = document.createElement('ul'); ul.className = 'rb-list';
    for (const item of section.items) {
      const box = Object.assign(document.createElement('input'), { type: 'checkbox', checked: Boolean(ticked[item.id]) });
      box.onchange = () => { ticked[item.id] = box.checked; rowbot.state.set({ ticked }); render(); };
      const label = document.createElement('label'); label.append(box, item.text);
      const li = document.createElement('li'); li.append(label); ul.append(li);
    }
    const h2 = Object.assign(document.createElement('h2'), { textContent: section.title });
    const wrap = document.createElement('section'); wrap.append(h2, ul); return wrap;
  }));
}
render();
</script></body></html>
```

### A dashboard (charts, a table with sort and filter, Refresh)

data: `{"title", "updated", "metrics": [{"label", "value"}], "chart": {"labels": [], "series": [{"label", "values": []}], "stacked": true}, "columns": [{"key", "label", "number": true}], "rows": [{}]}`.
`stacked` shows parts of one total (online and shops) as one bar; leave it out to compare series side by side.
Check the numbers agree: the metrics, the chart and the table come from the same data.
Use `libraries: ["charts", "rowbot-style"]`. Refresh records a choice; when
it arrives, gather the numbers again and `page_update(page, data=…)`.

```html
<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>.chart { position: relative; height: 240px; } th button { cursor: pointer; }</style></head>
<body>
<header class="rb-header"><div><h1 id="title"></h1><small id="updated"></small></div>
  <button type="button" id="refresh">Refresh</button></header>
<div id="metrics" class="rb-grid"></div>
<div class="rb-card" style="margin:16px 0"><div class="chart"><canvas id="chart" role="img"></canvas></div></div>
<div class="rb-row" style="margin-bottom:8px"><input id="filter" type="search" placeholder="Filter" aria-label="Filter rows"></div>
<div class="rb-table-wrap"><table><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table></div>
<script>
const d = rowbot.data();
let sortKey = null, sortUp = true, chart;
title.textContent = d.title; updated.textContent = d.updated ? `Updated ${d.updated}` : '';
metrics.replaceChildren(...d.metrics.map((m) => { const c = document.createElement('div'); c.className = 'rb-card rb-stat';
  c.innerHTML = '<span class="rb-stat-value"></span><span class="rb-stat-label"></span>';
  c.children[0].textContent = m.value; c.children[1].textContent = m.label; return c; }));
function drawChart() {
  const t = rowbot.theme().tokens;
  chart?.destroy();
  chart = new Chart(document.getElementById('chart'), { type: 'bar',
    data: { labels: d.chart.labels, datasets: d.chart.series.map((s, i) => ({ label: s.label, data: s.values,
      backgroundColor: t[`chart-series-${(i % 6) + 1}`], borderRadius: 4 })) },
    options: { maintainAspectRatio: false, animation: false, plugins: { legend: { labels: { color: t['text-muted'] } } },
      scales: { x: { stacked: Boolean(d.chart.stacked), ticks: { color: t['chart-axis'], autoSkip: true, maxRotation: 0 }, grid: { display: false } },
                y: { stacked: Boolean(d.chart.stacked), ticks: { color: t['chart-axis'] }, grid: { color: t['border-subtle'] } } } } });
  document.getElementById('chart').setAttribute('aria-label', d.chart.series.map((s) => s.label).join(', '));
}
function drawTable() {
  head.replaceChildren(...d.columns.map((c) => { const th = document.createElement('th'); if (c.number) th.className = 'rb-number';
    const b = Object.assign(document.createElement('button'), { type: 'button', textContent: c.label + (sortKey === c.key ? (sortUp ? ' ↑' : ' ↓') : '') });
    b.onclick = () => { sortUp = sortKey === c.key ? !sortUp : true; sortKey = c.key; drawTable(); }; th.append(b); return th; }));
  const q = filter.value.trim().toLowerCase();
  const rows = d.rows.filter((r) => !q || Object.values(r).join(' ').toLowerCase().includes(q));
  if (sortKey) rows.sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : -1) * (sortUp ? 1 : -1));
  body.replaceChildren(...rows.map((r) => { const tr = document.createElement('tr');
    for (const c of d.columns) { const td = document.createElement('td'); const v = r[c.key];
      td.textContent = typeof v === 'number' ? v.toLocaleString() : v; if (c.number) td.className = 'rb-number'; tr.append(td); }
    return tr; }));
}
filter.oninput = drawTable;
refresh.onclick = () => { rowbot.decide('refresh', 'refresh', '', `Refresh ${d.title}`); refresh.textContent = 'Refresh asked'; };
rowbot.onTheme(drawChart);
drawChart(); drawTable();
</script></body></html>
```

### A comparison (options side by side, pick one)

data: `{"question", "options": [{"id", "name", "summary", "pros": [], "cons": []}]}`.
Picking records `rowbot.decide('pick', 'pick', why, option.name)`.

```html
<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body>
<h1 id="question"></h1>
<div id="options" class="rb-grid"></div>
<label style="margin-top:16px;width:100%">Anything to add?<textarea id="why"></textarea></label>
<script>
const d = rowbot.data();
question.textContent = d.question;
why.value = rowbot.decided().pick?.note || '';
function render() {
  const picked = rowbot.decided().pick;
  options.replaceChildren(...d.options.map((o) => {
    const card = document.createElement('div'); card.className = 'rb-card rb-stack';
    const h = Object.assign(document.createElement('h2'), { textContent: o.name });
    const p = Object.assign(document.createElement('p'), { textContent: o.summary });
    const list = (words, items) => { const ul = document.createElement('ul');
      for (const i of items) ul.append(Object.assign(document.createElement('li'), { textContent: `${words} ${i}` })); return ul; };
    const choose = Object.assign(document.createElement('button'), { type: 'button', textContent: 'Choose this' });
    choose.setAttribute('aria-pressed', String(picked?.label === o.name));
    choose.onclick = () => rowbot.decide('pick', 'pick', why.value.trim(), o.name);
    card.append(h, p, list('+', o.pros), list('−', o.cons), choose); return card;
  }));
}
why.onchange = () => { const p = rowbot.decided().pick; if (p) rowbot.decide('pick', 'pick', why.value.trim(), p.label); };
rowbot.onDecided(render); render();
</script></body></html>
```

### A form (collect details, send them in one go)

data: `{"title", "fields": [{"id", "label", "type": "text" | "date" | "number" | "textarea" | "select", "options": []}]}`.
The draft lives in state; Done records one choice with every answer as
`Label: value` lines.

```html
<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body>
<h1 id="title"></h1>
<div id="fields" class="rb-stack"></div>
<div class="rb-row" style="margin-top:16px"><button type="button" class="rb-primary" id="done">Done</button><small id="said"></small></div>
<script>
const d = rowbot.data();
const draft = rowbot.state.get().draft || {};
title.textContent = d.title;
fields.replaceChildren(...d.fields.map((f) => {
  const input = f.type === 'textarea' ? document.createElement('textarea')
    : f.type === 'select' ? document.createElement('select') : Object.assign(document.createElement('input'), { type: f.type || 'text' });
  for (const o of f.options || []) input.append(Object.assign(document.createElement('option'), { textContent: o }));
  input.value = draft[f.id] || '';
  input.oninput = () => { draft[f.id] = input.value; rowbot.state.set({ draft }); };
  const label = document.createElement('label'); label.append(f.label, input); return label;
}));
done.onclick = () => {
  const lines = d.fields.map((f) => `${f.label}: ${draft[f.id] || '—'}`).join('\n');
  rowbot.decide('form', 'submit', lines, d.title);
  said.textContent = 'Ready to send to Row-Bot.';
};
</script></body></html>
```

### A calculator or small tool (runs entirely in the page)

Nothing to send; keep inputs in state so they're there next time.

```html
<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body>
<h1>Price per seat</h1>
<div class="rb-grid">
  <label>Seats<input id="seats" type="number" min="1"></label>
  <label>Price per seat (£)<input id="price" type="number" min="0" step="0.01"></label>
  <label>Discount (%)<input id="discount" type="number" min="0" max="100"></label>
</div>
<div class="rb-card rb-stat" style="margin-top:16px"><span class="rb-stat-value" id="total"></span><span class="rb-stat-label">Total a month</span></div>
<script>
const saved = Object.assign({ seats: 10, price: 12, discount: 0 }, rowbot.state.get());
for (const id of ['seats', 'price', 'discount']) {
  const input = document.getElementById(id); input.value = saved[id];
  input.oninput = () => { saved[id] = Number(input.value) || 0; rowbot.state.set(saved); render(); };
}
function render() {
  const total = saved.seats * saved.price * (1 - saved.discount / 100);
  document.getElementById('total').textContent = total.toLocaleString(undefined, { style: 'currency', currency: 'GBP' });
}
render();
</script></body></html>
```
