/* The views.
 *
 * Each one answers a question you would act on. Where a number depends on a
 * threshold, the threshold is adjustable and labelled as a choice, because BGE
 * cosine scores cluster high and "a miss" is a judgement, not a fact.
 */

import { api, card, esc, legend, num, pct, shortDate, status, table, tile, twin, wireSort } from './app.js';
import { barsH, forceGraph, histogram, lineChart, quadrant, stackedBars } from './charts.js';

const S1 = 'var(--series-1)';
const S2 = 'var(--series-2)';
const S3 = 'var(--series-3)';

function head(title, blurb) {
  return `<div class="view-head"><h1>${esc(title)}</h1><p>${blurb}</p></div>`;
}

/* =========================================================== 1. SIGNAL ==== */

async function signal(main) {
  const [sig, ts] = await Promise.all([api('/api/signal'), api('/api/retrieval/timeseries?bucket=month')]);
  const r = sig.retrieval;
  const c = sig.corpus;
  const q = sig.concentration;

  const bridgedPct = pct(r.bridged_share, 1);
  const deadPct = c.lessons ? pct(c.never_retrieved / c.lessons, 0) : '—';
  const topQ = q.most_repeated[0];

  main.innerHTML = `
    ${head('Signal', `Whether the memory is working: what it returns, how well it matches, and what
      it never surfaces. ${num(r.queries)} queries and ${num(r.slots)} returned results, all of it
      measured — nothing here is sampled. ${num(q.distinct_queries)} of those queries are
      different questions.`)}

    ${q.top_query_share > 0.15 && topQ ? `<div class="callout">
      <b>${pct(q.top_query_share, 1)} of every recorded query is the same string:
      <code>${esc(topQ.query)}</code>.</b>
      The hooks issue that one themselves. The git gate mandates
      <code>query_lessons('git commit')</code> before any commit, so it fires once per commit and
      lands here as ${num(topQ.count)} queries. It is a real question, really asked that often, so
      it is not noise and it is not removed. But it is one question, and counting it
      ${num(topQ.count)} times moves every average on this page. So match quality is given twice
      below: once per query, and once per different question counted a single time. Where the two
      disagree, the gap is the share of the evidence the system generated for itself.
    </div>` : ''}

    ${r.bridged_share > 0.15 ? `<div class="callout">
      <b>${bridgedPct} of everything returned arrived without a relevance score.</b>
      Those slots came from the community bridge, which appends neighbours of a matched community.
      Before v2.13 it ranked candidates by <code>usage_count</code> and then incremented that same
      counter, so the few lessons that won early kept winning. Bridged results are scored and
      floored now; this share is the historical record and should fall as new queries land.
    </div>` : ''}

    <div class="grid cols-4">
      ${tile({ label: 'Lessons', value: num(c.lessons),
               sub: `${num(c.never_retrieved)} never surfaced (${deadPct})` })}
      ${tile({ label: 'Median match', value: pct(q.top1_median_per_question, 0),
               sub: `per distinct question — ${pct(r.top1_median, 0)} counting repeats`,
               meter: q.top1_median_per_question })}
      ${tile({ label: 'Unscored share', value: bridgedPct,
               sub: `${num(r.slots_bridged)} of ${num(r.slots)} slots`, meter: r.bridged_share })}
      ${tile({ label: 'Misses', value: num(r.misses),
               sub: `best match below ${pct(r.miss_threshold)} — a chosen line` })}
    </div>

    <div class="grid cols-2" style="margin-top:1rem">
      <section class="card">
        <h2>Match quality over time</h2>
        <p class="note">Mean top-1 score per month, counting only results the search actually
          scored. Volume is a different measure on a different scale, so it gets its own chart
          rather than a second axis.</p>
        <div id="c-quality"></div>
        <div id="t-quality"></div>
      </section>
      <section class="card">
        <h2>Where results came from</h2>
        <p class="note">Matched by relevance versus appended by the community bridge.</p>
        ${legend([{ name: 'matched', color: S1 }, { name: 'bridged (unscored)', color: S2 }])}
        <div id="c-origin"></div>
        <div id="t-origin"></div>
      </section>
    </div>

    <div class="grid cols-2" style="margin-top:1rem">
      ${card('Never surfaced', `${num(c.never_retrieved)} lessons have never been returned by any
        query. Each one is knowledge the system holds and cannot reach.`,
        `<div id="t-dead"></div>`)}
      ${card('Corpus', 'What is stored.', `<div id="t-corpus"></div>`)}
    </div>`;

  lineChart(document.getElementById('c-quality'), ts, {
    x: 'period', y: 'top1_mean', yDomain: [0, 1], yFormat: (v) => `${Math.round(v * 100)}%`,
    label: 'mean top-1',
  });
  document.getElementById('t-quality').innerHTML = twin(table(ts, [
    { key: 'period', label: 'Period' },
    { key: 'scored_queries', label: 'Queries', num: true },
    { key: 'top1_mean', label: 'Mean top-1', num: true, render: (r2) => pct(r2.top1_mean, 1) },
    { key: 'misses', label: 'Misses', num: true },
  ]));

  stackedBars(document.getElementById('c-origin'), ts, {
    x: 'period', keys: ['matched', 'bridged'], colors: [S1, S2],
    names: ['matched', 'bridged'], yLabel: 'slots',
  });
  document.getElementById('t-origin').innerHTML = twin(table(ts, [
    { key: 'period', label: 'Period' },
    { key: 'matched', label: 'Matched', num: true },
    { key: 'bridged', label: 'Bridged', num: true },
    { key: 'queries', label: 'Queries', num: true },
  ]));

  document.getElementById('t-dead').innerHTML = table(
    c.never_retrieved_ids.map((id) => ({ id })),
    [{ key: 'id', label: 'Lesson', render: (x) => `<span class="id">${esc(x.id)}</span>` }],
  );

  document.getElementById('t-corpus').innerHTML = table([
    { k: 'Lessons', v: num(c.lessons) },
    { k: 'Workflows', v: num(c.workflows) },
    { k: 'Queries logged', v: num(r.queries) },
    { k: 'Results returned', v: num(r.slots) },
    { k: 'Queries returning nothing', v: num(r.zero_result_queries) },
  ], [{ key: 'k', label: 'Measure' }, { key: 'v', label: 'Value', num: true }]);
}

/* ======================================================== 2. RETRIEVAL ==== */

async function retrieval(main) {
  main.innerHTML = `
    ${head('Retrieval', `Every query whose best scored result fell below the line. This is the
      retrieval-miss log — the thing that tells you which knowledge exists but cannot be reached
      by the words people actually use.`)}
    <div class="filters">
      <label>Miss threshold
        <input id="thr" type="range" min="0.30" max="0.70" step="0.01" value="0.45">
        <output id="thrOut">45%</output>
      </label>
      <label>Search <input id="q" type="search" placeholder="query text or lesson id"></label>
      <span class="spacer"></span>
      <span class="note" id="count"></span>
    </div>
    <div class="grid cols-2">
      <section class="card">
        <h2>Score distribution</h2>
        <p class="note">Top-1 score for every scored query. The cluster is high because BGE cosine
          similarity is compressed — which is exactly why the miss line is a choice.</p>
        <div id="c-hist"></div>
      </section>
      ${card('Worst matches', 'Lowest-scoring queries first. These are the diagnostic ones.',
        '<div id="t-worst"></div>')}
    </div>
    <div class="grid" style="margin-top:1rem">
      ${card('Miss log', 'Each row is a real query and what came back for it.', '<div id="t-miss"></div>')}
    </div>`;

  const thr = document.getElementById('thr');
  const out = document.getElementById('thrOut');
  const search = document.getElementById('q');

  // one fetch at the widest threshold, then filter client-side so dragging the
  // control does not re-query the server on every tick
  const all = await api('/api/retrieval/misses?threshold=0.70&limit=999');
  const scored = await api('/api/retrieval/timeseries?bucket=month');
  void scored;

  const hist = await api('/api/signal');
  void hist;

  const draw = () => {
    const t = Number(thr.value);
    const needle = search.value.trim().toLowerCase();
    const rows = all.filter((m) => m.top < t)
      .filter((m) => !needle || m.query.toLowerCase().includes(needle)
        || (m.returned || []).some((id) => id.toLowerCase().includes(needle)));
    out.textContent = `${Math.round(t * 100)}%`;
    document.getElementById('count').textContent =
      `${rows.length} of ${all.length} queries below the line`;

    const cols = [
      { key: 'ts', label: 'When', render: (r) => shortDate(r.ts) },
      { key: 'query', label: 'Query', render: (r) => esc(r.query) },
      { key: 'top', label: 'Best', num: true, render: (r) => pct(r.top, 1) },
      { key: 'returned', label: 'Returned', render: (r) =>
        (r.returned || []).map((id) => `<span class="id">${esc(id)}</span>`).join(', ') },
    ];
    wireSort(null, rows, cols, document.getElementById('t-miss'), { sortBy: 'top', desc: false });
    document.getElementById('t-worst').innerHTML = table(rows, cols, { sortBy: 'top', desc: false, max: 10 });
  };

  histogram(document.getElementById('c-hist'), all.map((m) => m.top), { bins: 20 });
  thr.addEventListener('input', draw);
  search.addEventListener('input', draw);
  draw();
}

/* ===================================================== 3. EFFECTIVENESS === */

async function effectiveness(main) {
  const [rows, sig] = await Promise.all([api('/api/effectiveness'), api('/api/signal')]);
  const median = sig.retrieval.top1_median;

  const dead = rows.filter((r) => r.matched === 0 && r.bridged === 0);
  const bridgeHeavy = rows.filter((r) => r.bridged > r.matched && r.bridged > 20);

  main.innerHTML = `
    ${head('Effectiveness', `Every lesson placed by how often relevance matched it and how well it
      scored when it did. Appended (bridged) hits are sized in but never averaged into the score —
      folding them together is what made a good lesson read as 2% relevant.`)}

    <div class="grid cols-4">
      ${tile({ label: 'Lessons', value: num(rows.length) })}
      ${tile({ label: 'Never surfaced', value: num(dead.length),
               sub: 'no match, no append, ever' })}
      ${tile({ label: 'Mostly appended', value: num(bridgeHeavy.length),
               sub: 'bridged more than matched' })}
      ${tile({ label: 'Corpus median', value: pct(median, 0), sub: 'top-1 score' })}
    </div>

    <div class="grid" style="margin-top:1rem">
      <section class="card">
        <h2>Matched vs quality</h2>
        <p class="note">Dot size is total appearances. Click any dot for its record. High-and-right
          earns its place; low-and-right is surfaced often without scoring well. Lessons never
          matched by relevance have no score and so no position — they are counted above and listed
          in the table.</p>
        <div id="c-quad"></div>
        <div id="detail"></div>
      </section>
    </div>

    <div class="grid" style="margin-top:1rem">
      ${card('All lessons', 'Sort any column. This is the chart above, as numbers.', '<div id="t-eff"></div>')}
    </div>`;

  quadrant(document.getElementById('c-quad'), rows, {
    yRef: median,
    onPick: (d) => {
      document.getElementById('detail').innerHTML = `<div class="callout">
        <b>${esc(d.id)}</b><br>${esc(d.trigger)}<br><br>
        matched <b>${d.matched}</b> · appended <b>${d.bridged}</b> ·
        mean when matched <b>${pct(d.mean_matched_score, 1)}</b> ·
        best <b>${pct(d.best_score, 1)}</b><br>
        created ${shortDate(d.created_at)} · last refined ${shortDate(d.last_refined)}
        ${d.tags?.length ? `<br>${d.tags.map((t) => `<span class="pill">${esc(t)}</span>`).join(' ')}` : ''}
      </div>`;
    },
  });

  wireSort(null, rows, [
    { key: 'id', label: 'Lesson', render: (r) => `<span class="id">${esc(r.id)}</span>` },
    { key: 'matched', label: 'Matched', num: true },
    { key: 'bridged', label: 'Appended', num: true },
    { key: 'mean_matched_score', label: 'Mean when matched', num: true,
      render: (r) => pct(r.mean_matched_score, 1) },
    { key: 'best_score', label: 'Best', num: true, render: (r) => pct(r.best_score, 1) },
    { key: 'usage_count', label: 'usage_count', num: true },
    { key: 'last_refined', label: 'Refined', render: (r) => shortDate(r.last_refined) },
  ], document.getElementById('t-eff'), { sortBy: 'matched', desc: true });
}

/* ====================================================== 4. ENFORCEMENT ==== */

async function enforcement(main) {
  const [audit, rules] = await Promise.all([api('/api/gate-audit'), api('/api/enforcement/rules')]);
  const t = audit.totals;
  const captureRate = t.deny ? t.comply / t.deny : 0;
  const contestRate = t.deny ? t.adjudication / t.deny : 0;
  const neverFired = rules.filter((r) => !r.error && r.fires === 0);

  main.innerHTML = `
    ${head('Enforcement', `What the gates actually did. The audit log records friction, not
      traffic: an allowed call leaves no entry, so every number here is a refusal, a compliance,
      a contest or a human override.`)}

    <div class="callout">
      <b>${num(t.deny)} denials produced ${num(t.comply)} lessons.</b>
      The apology gate exists to force capture at a failure moment; measured against its own
      purpose that is a ${pct(captureRate, 1)} capture rate. Contests outnumber compliances
      ${t.comply ? `${(t.adjudication / t.comply).toFixed(0)}:1` : '—'}.
      ${neverFired.length ? `${neverFired.length} of ${rules.length} live rules have never fired once.` : ''}
    </div>

    <div class="grid cols-4">
      ${tile({ label: 'Denials', value: num(t.deny),
               sub: `${num(t.rule_denials)} rule · ${num(t.apology_denials)} apology` })}
      ${tile({ label: 'Lessons written', value: num(t.comply), sub: 'the gate’s purpose', meter: captureRate })}
      ${tile({ label: 'Contested', value: num(t.adjudication), sub: 'on the record', meter: contestRate })}
      ${tile({ label: 'Sessions affected', value: num(t.sessions_affected) })}
    </div>

    <div class="grid cols-2" style="margin-top:1rem">
      <section class="card">
        <h2>Activity by day</h2>
        <p class="note">Denials, compliances and contests.</p>
        ${legend([{ name: 'deny', color: S1 }, { name: 'comply', color: S3 }, { name: 'contest', color: S2 }])}
        <div id="c-days"></div>
        <div id="t-days"></div>
      </section>
      <section class="card">
        <h2>Which rules fire</h2>
        <p class="note">A rule that never fires is either dead or mis-triggered. The log cannot
          tell you which, so it is counted, not judged.</p>
        <div id="c-rules"></div>
      </section>
    </div>

    <div class="grid cols-2" style="margin-top:1rem">
      ${card('Live rules', 'Read from <code>enforcement_rules.json</code>.', '<div id="t-rules"></div>')}
      ${card('Tools denied', 'What the gates actually blocked.', '<div id="t-tools"></div>')}
    </div>

    <div class="grid" style="margin-top:1rem">
      ${card('Contested fires', `Every contest with the sentence that triggered it. An apology
        <em>denial</em> does not record its sentence — only a contest does — so this is the only
        window onto the gate’s false-positive rate.`, '<div id="t-adj"></div>')}
    </div>`;

  stackedBars(document.getElementById('c-days'), audit.per_day, {
    x: 'day', keys: ['deny', 'comply', 'adjudication'], colors: [S1, S3, S2],
    names: ['deny', 'comply', 'contest'], yLabel: 'events',
  });
  document.getElementById('t-days').innerHTML = twin(table(audit.per_day, [
    { key: 'day', label: 'Day' },
    { key: 'deny', label: 'Deny', num: true },
    { key: 'comply', label: 'Comply', num: true },
    { key: 'adjudication', label: 'Contest', num: true },
  ], { sortBy: 'day', desc: true }));

  barsH(document.getElementById('c-rules'), audit.rule_fires, { label: 'rule', value: 'fires' });

  document.getElementById('t-rules').innerHTML = table(rules, [
    { key: 'name', label: 'Rule', render: (r) => `<span class="id">${esc(r.name || r.error)}</span>` },
    { key: 'enabled', label: 'State', render: (r) => (r.enabled
      ? status('good', 'enabled') : status('warn', 'disabled')) },
    { key: 'fires', label: 'Fires', num: true,
      render: (r) => (r.fires === 0 ? status('warn', 'never') : num(r.fires)) },
    { key: 'bypass_scope', label: 'Bypass scope', render: (r) => `<code>${esc(r.bypass_scope)}</code>` },
  ], { sortBy: 'fires', desc: true });

  document.getElementById('t-tools').innerHTML = table(audit.tools_denied, [
    { key: 'tool', label: 'Tool' },
    { key: 'denials', label: 'Denials', num: true },
  ], { sortBy: 'denials', desc: true });

  document.getElementById('t-adj').innerHTML = audit.adjudications.length
    ? audit.adjudications.slice().reverse().map((a) => `<div class="entry">
        <div class="meta">
          <span>${shortDate(a.ts)}</span>
          <span>${a.verdict === 'not_apology' ? status('warn', 'contested') : status('good', 'confirmed')}</span>
        </div>
        <div class="body"><b>flagged:</b> ${esc(a.flagged)}</div>
        <details class="twin"><summary>reasoning</summary>
          <div class="body">${esc(a.reasoning)}</div></details>
      </div>`).join('')
    : '<div class="empty">No contests recorded.</div>';
}

/* ============================================================ 5. GRAPH ==== */

async function graphView(main) {
  const [graph, sig, communities] = await Promise.all([
    api('/api/graph'), api('/api/signal'), api('/api/communities').catch(() => []),
  ]);
  const deadSet = new Set(sig.corpus.never_retrieved_ids);

  // The endpoint returns a MIXED graph — lessons, the categories they hang
  // under, workflows and workflow steps — and it repeats some ids. Both matter:
  // calling all 378 nodes "lessons" is wrong, and duplicate ids make d3's link
  // resolution ambiguous.
  const seen = new Set();
  const allNodes = [];
  for (const n of (graph.nodes || [])) {
    if (seen.has(n.id)) continue;
    seen.add(n.id);
    allNodes.push({ ...n, usage: n.usage ?? 0, dead: n.type === 'lesson' && deadSet.has(n.id) });
  }
  const allLinks = (graph.links || graph.edges || [])
    .map((l) => ({ source: l.source ?? l.from, target: l.target ?? l.to }))
    .filter((l) => seen.has(l.source) && seen.has(l.target));

  const counts = allNodes.reduce((m, n) => { m[n.type] = (m[n.type] || 0) + 1; return m; }, {});
  const dupes = (graph.nodes || []).length - allNodes.length;

  const degree = new Map();
  allLinks.forEach((l) => {
    degree.set(l.source, (degree.get(l.source) || 0) + 1);
    degree.set(l.target, (degree.get(l.target) || 0) + 1);
  });

  main.innerHTML = `
    ${head('Graph', `Not one kind of thing: ${num(counts.lesson || 0)} lessons,
      ${num(counts.category || 0)} categories, ${num(counts.workflow || 0)} workflows and
      ${num(counts.workflow_step || 0)} workflow steps, joined by ${num(allLinks.length)} edges.
      Only lessons that carry an edge appear — the corpus holds ${num(sig.corpus.lessons)}.
      Node size is retrieval count; grey nodes are lessons that have never surfaced.
      ${dupes ? `${dupes} duplicate node ids were collapsed.` : ''}`)}
    <div class="filters">
      <label>Show
        <select id="filter">
          <option value="all">everything</option>
          <option value="lesson">lessons only</option>
          <option value="dead">never-surfaced lessons</option>
          <option value="workflow">workflows and steps</option>
        </select>
      </label>
      <span class="spacer"></span>
      <span class="note">drag to move · scroll to zoom · click a node for its record</span>
    </div>
    <div class="grid cols-2">
      <section class="card span-2">
        <h2>Knowledge graph</h2>
        <div id="c-graph"></div>
        <div id="g-detail"></div>
      </section>
    </div>
    <div class="grid cols-2" style="margin-top:1rem">
      ${card('Communities', 'Detected clusters, largest first.', '<div id="t-comm"></div>')}
      ${card('Most connected', 'Hubs carry the most edges. Categories dominate by design — they are the spine lessons hang from.', '<div id="t-hubs"></div>')}
    </div>`;

  let stop = null;
  const paint = () => {
    const mode = document.getElementById('filter').value;
    let nodes = allNodes;
    if (mode === 'lesson') nodes = allNodes.filter((n) => n.type === 'lesson');
    if (mode === 'dead') nodes = allNodes.filter((n) => n.dead);
    if (mode === 'workflow') nodes = allNodes.filter((n) => n.type === 'workflow' || n.type === 'workflow_step');
    const keep = new Set(nodes.map((n) => n.id));
    const links = allLinks.filter((l) => keep.has(l.source) && keep.has(l.target)).map((l) => ({ ...l }));
    if (stop) stop();
    stop = forceGraph(document.getElementById('c-graph'),
      { nodes: nodes.map((n) => ({ ...n, usage_count: n.usage })), links },
      { onPick: (d) => {
        document.getElementById('g-detail').innerHTML = `<div class="callout">
          <b>${esc(d.id)}</b> <span class="pill">${esc(d.type)}</span><br>
          ${esc(d.label || '')} — ${num(d.usage ?? 0)} retrievals,
          ${num(degree.get(d.id) || 0)} edges${d.dead ? ' · never surfaced' : ''}</div>`;
      } });
  };
  document.getElementById('filter').addEventListener('change', paint);
  paint();

  document.getElementById('t-comm').innerHTML = table(
    (communities || []).map((c) => ({
      id: c.community_id || c.id,
      size: c.size ?? (c.members || c.member_ids || []).length,
      title: c.title || c.summary_title || '—',
    })),
    [{ key: 'id', label: 'Community', render: (r) => `<span class="id">${esc(r.id)}</span>` },
     { key: 'title', label: 'Summary' },
     { key: 'size', label: 'Lessons', num: true }],
    { sortBy: 'size', desc: true });

  document.getElementById('t-hubs').innerHTML = table(
    allNodes.map((n) => ({ id: n.id, type: n.type, edges: degree.get(n.id) || 0, usage: n.usage ?? 0 })),
    [{ key: 'id', label: 'Node', render: (r) => `<span class="id">${esc(r.id)}</span>` },
     { key: 'type', label: 'Kind', render: (r) => `<span class="pill">${esc(r.type)}</span>` },
     { key: 'edges', label: 'Edges', num: true },
     { key: 'usage', label: 'Retrievals', num: true }],
    { sortBy: 'edges', desc: true, max: 25 });

  return () => { if (stop) stop(); };
}

/* ============================================================== 6. REM ==== */

async function rem(main) {
  const rows = await api('/api/rem/state');
  const overdue = rows.filter((r) => r.overdue);
  const projects = [...new Set(rows.map((r) => r.project))];

  main.innerHTML = `
    ${head('REM', `Maintenance schedule. The cadence is per project — it follows that project's own
      session count — while the corpus a cycle maintains is global. Both halves matter, which is
      why every row carries its project's session count.`)}

    <div class="grid cols-4">
      ${tile({ label: 'Projects', value: num(projects.length) })}
      ${tile({ label: 'Overdue', value: num(overdue.length),
               sub: overdue.length ? 'past next_due_session' : 'nothing past due' })}
      ${tile({ label: 'Never run', value: num(rows.filter((r) => r.never_run).length),
               sub: 'no row on this project’s clock' })}
      ${tile({ label: 'Operations', value: num(new Set(rows.map((r) => r.operation)).size) })}
    </div>

    <div class="filters" style="margin-top:1rem">
      <label>Project
        <select id="proj"><option value="">all</option>
          ${projects.map((p) => `<option>${esc(p)}</option>`).join('')}</select>
      </label>
      <label><input id="onlyOverdue" type="checkbox"> overdue only</label>
      <span class="spacer"></span>
      <span class="note">a “never run” row is the truth, not a gap: REM has never been scheduled on that clock</span>
    </div>

    <div class="grid">${card('Schedule', '', '<div id="t-rem"></div>')}</div>`;

  const cols = [
    { key: 'project', label: 'Project' },
    { key: 'session_count', label: 'Session', num: true },
    { key: 'operation', label: 'Operation', render: (r) => `<span class="id">${esc(r.operation)}</span>` },
    { key: 'strategy', label: 'Strategy', render: (r) => `<span class="pill">${esc(r.strategy)}</span>` },
    { key: 'last_run_session', label: 'Last run', num: true,
      render: (r) => (r.last_run_session ?? '—') },
    { key: 'next_due_session', label: 'Next due', num: true,
      render: (r) => (r.next_due_session ?? '—') },
    { key: 'overdue', label: 'State',
      render: (r) => (r.overdue ? status('bad', 'overdue')
        : r.never_run ? status('warn', 'never run') : status('good', 'current')) },
  ];

  const draw = () => {
    const p = document.getElementById('proj').value;
    const only = document.getElementById('onlyOverdue').checked;
    const filtered = rows.filter((r) => (!p || r.project === p) && (!only || r.overdue));
    wireSort(null, filtered, cols, document.getElementById('t-rem'),
      { sortBy: 'overdue', desc: true });
  };
  document.getElementById('proj').addEventListener('change', draw);
  document.getElementById('onlyOverdue').addEventListener('change', draw);
  draw();
}

/* ========================================================== 7. JOURNAL ==== */

async function journal(main) {
  const entries = await api('/api/soliloquies?limit=60');
  main.innerHTML = `
    ${head('Journal', `The soliloquy record — what the agent wrote to its future self at session
      close. Stored globally, one continuous voice across projects. ${num(entries.length)} most
      recent entries; this is the only surface that shows them.`)}
    <div class="filters">
      <label>Search <input id="jq" type="search" placeholder="text, project or mood"></label>
      <span class="spacer"></span><span class="note" id="jcount"></span>
    </div>
    <div class="grid">${card('Entries', '', '<div id="j-list"></div>')}</div>`;

  const search = document.getElementById('jq');
  const draw = () => {
    const n = search.value.trim().toLowerCase();
    const rows = entries.filter((e) => !n
      || (e.content || '').toLowerCase().includes(n)
      || (e.project || '').toLowerCase().includes(n)
      || (e.mood || '').toLowerCase().includes(n));
    document.getElementById('jcount').textContent = `${rows.length} of ${entries.length}`;
    document.getElementById('j-list').innerHTML = rows.length ? rows.map((e) => `<div class="entry">
      <div class="meta">
        <span>${shortDate(e.timestamp)}</span>
        <span>session ${e.session_number ?? '—'}</span>
        <span>${e.project ? esc(e.project) : '<em>untagged</em>'}</span>
        ${e.mood ? `<span class="pill">${esc(e.mood)}</span>` : ''}
      </div>
      ${e.content.length > 520
        ? `<details class="twin"><summary>${esc(e.content.slice(0, 220))}…</summary>
             <div class="body">${esc(e.content)}</div></details>`
        : `<div class="body">${esc(e.content)}</div>`}
    </div>`).join('') : '<div class="empty">Nothing matches.</div>';
  };
  search.addEventListener('input', draw);
  draw();
}

/* ========================================================== 8. CURATE ===== */
/* The authoring surface. The read-only views tell you which lessons are dead
 * weight or force-fed; this is where you act on that without leaving the app. */

async function writeJSON(path, method, body) {
  const res = await fetch(path, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(text.slice(0, 300) || `${method} ${path} → ${res.status}`);
  return text ? JSON.parse(text) : {};
}

async function curate(main) {
  const [effect, intents] = await Promise.all([
    api('/api/effectiveness', { fresh: true }),
    api('/api/intent-config', { fresh: true }).catch(() => ({ intents: [] })),
  ]);
  const intentList = intents.intents || intents || [];

  main.innerHTML = `
    ${head('Curate', `Edit what the other views expose. Changes here write straight through to
      <code>lessons.db</code> and <code>intent_config.json</code> — the same files the MCP tools
      and the hooks read, so a change lands on the next query, not the next restart.`)}
    <div class="filters">
      <label>Find lesson <input id="cq" type="search" placeholder="id, trigger or tag"></label>
      <span class="spacer"></span><span class="note" id="ccount"></span>
    </div>
    <div class="grid cols-2">
      ${card('Lessons', 'Click a row to load it into the editor.', '<div id="c-list"></div>')}
      ${card('Editor', '', '<div id="c-edit"><div class="empty">Select a lesson.</div></div>')}
    </div>
    <div class="grid" style="margin-top:1rem">
      ${card('Intents', `The routing config as data. Compiling writes a SKILL.md and changes
        nothing else — the intent stays the source of truth.`, '<div id="c-intents"></div>')}
    </div>`;

  const list = document.getElementById('c-list');
  const editor = document.getElementById('c-edit');
  const search = document.getElementById('cq');

  const paintList = () => {
    const n = search.value.trim().toLowerCase();
    const rows = effect.filter((r) => !n || r.id.toLowerCase().includes(n)
      || (r.trigger || '').toLowerCase().includes(n)
      || (r.tags || []).some((tg) => tg.toLowerCase().includes(n)));
    document.getElementById('ccount').textContent = `${rows.length} of ${effect.length}`;
    list.innerHTML = table(rows, [
      { key: 'id', label: 'Lesson', render: (r) => `<span class="id">${esc(r.id)}</span>` },
      { key: 'matched', label: 'Matched', num: true },
      { key: 'bridged', label: 'Appended', num: true },
    ], { sortBy: 'matched', desc: true, max: 200 });
    list.querySelectorAll('tbody tr').forEach((tr, i) => {
      tr.style.cursor = 'pointer';
      tr.addEventListener('click', () => load(rows[i].id));
    });
  };

  const load = async (id) => {
    const lesson = await api(`/api/lessons/${encodeURIComponent(id)}`, { fresh: true });
    if (!lesson) { editor.innerHTML = '<div class="empty">Lesson not found.</div>'; return; }
    editor.innerHTML = `
      <div class="entry">
        <div class="meta"><span class="id">${esc(lesson.id)}</span>
          <span>v${lesson.version ?? 1}</span>
          <span>used ${num(lesson.usage_count ?? 0)}</span></div>
      </div>
      <label class="note">Trigger — when it applies</label>
      <textarea id="f-trigger" rows="2">${esc(lesson.trigger || '')}</textarea>
      <label class="note">Action — what to do</label>
      <textarea id="f-action" rows="4">${esc(lesson.action || '')}</textarea>
      <label class="note">Rationale — why</label>
      <textarea id="f-rationale" rows="3">${esc(lesson.rationale || '')}</textarea>
      <label class="note">Tags — comma separated</label>
      <input id="f-tags" type="text" value="${esc((lesson.tags || []).join(', '))}">
      <div style="display:flex;gap:0.5rem;margin-top:0.75rem;align-items:center">
        <button class="ghost" id="f-save" type="button">Save</button>
        <button class="ghost" id="f-del" type="button">Delete</button>
        <span class="note" id="f-msg"></span>
      </div>`;
    const msg = document.getElementById('f-msg');
    document.getElementById('f-save').addEventListener('click', async () => {
      msg.textContent = 'saving…';
      try {
        await writeJSON(`/api/lessons/${encodeURIComponent(lesson.id)}`, 'PUT', {
          trigger: document.getElementById('f-trigger').value,
          action: document.getElementById('f-action').value,
          rationale: document.getElementById('f-rationale').value,
          tags: document.getElementById('f-tags').value.split(',').map((x) => x.trim()).filter(Boolean),
        });
        msg.innerHTML = status('good', 'saved');
      } catch (err) { msg.innerHTML = status('bad', err.message.slice(0, 120)); }
    });
    document.getElementById('f-del').addEventListener('click', async () => {
      // A lesson deleted here is gone from SQLite, Qdrant and the graph, so it
      // asks first. Nothing else in this app destroys anything.
      if (!window.confirm(`Delete ${lesson.id} from all three stores? This cannot be undone.`)) return;
      msg.textContent = 'deleting…';
      try {
        await writeJSON(`/api/lessons/${encodeURIComponent(lesson.id)}`, 'DELETE');
        msg.innerHTML = status('good', 'deleted');
        editor.innerHTML = '<div class="empty">Deleted. Select another lesson.</div>';
      } catch (err) { msg.innerHTML = status('bad', err.message.slice(0, 120)); }
    });
  };

  document.getElementById('c-intents').innerHTML = table(intentList, [
    { key: 'name', label: 'Intent', render: (r) => `<span class="id">${esc(r.name)}</span>` },
    { key: 'description', label: 'Description' },
    { key: 'action', label: 'Action' },
    { key: 'tags', label: 'Tags', render: (r) => (r.tags || []).length },
    { key: 'compile', label: '', render: (r) =>
      `<button class="ghost" data-compile="${esc(r.name)}" type="button">Compile skill</button>` },
  ]);
  document.getElementById('c-intents').querySelectorAll('[data-compile]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const name = btn.dataset.compile;
      btn.textContent = 'compiling…';
      try {
        const out = await writeJSON(`/api/intent-config/intents/${encodeURIComponent(name)}/compile`, 'POST', {});
        btn.textContent = out.path ? 'compiled' : 'done';
      } catch (err) { btn.textContent = err.message.slice(0, 40); }
    });
  });

  search.addEventListener('input', paintList);
  paintList();
}

/* ============================================================= registry === */

export const VIEWS = [
  { id: 'signal', title: 'Signal', render: signal },
  { id: 'retrieval', title: 'Retrieval', render: retrieval },
  { id: 'lessons', title: 'Effectiveness', render: effectiveness },
  { id: 'enforcement', title: 'Enforcement', render: enforcement },
  { id: 'graph', title: 'Graph', render: graphView },
  { id: 'rem', title: 'REM', render: rem },
  { id: 'journal', title: 'Journal', render: journal },
  { id: 'curate', title: 'Curate', render: curate },
];
