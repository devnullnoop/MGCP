/* Chart builders.
 *
 * Rules these all obey, from the data-viz method:
 *  - one y-axis, ever. Two measures of different scale get two charts.
 *  - thin marks (2px lines, >=8px dots), hairline solid grid, no dashes.
 *  - a 2px surface gap between adjacent fills; a 2px surface ring on dots.
 *  - a hover layer by default, with hit targets larger than the mark.
 *  - selective direct labels (endpoint / extreme), never a number per point.
 *  - colour follows the entity, never its rank, so a filter cannot repaint.
 */

const TT = () => document.getElementById('tooltip');

export function showTip(html, event) {
  const tip = TT();
  tip.innerHTML = html;
  tip.setAttribute('aria-hidden', 'false');
  const pad = 14;
  const r = tip.getBoundingClientRect();
  let x = event.clientX + pad;
  let y = event.clientY + pad;
  if (x + r.width > window.innerWidth - 8) x = event.clientX - r.width - pad;
  if (y + r.height > window.innerHeight - 8) y = event.clientY - r.height - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}

export function hideTip() {
  const tip = TT();
  tip.setAttribute('aria-hidden', 'true');
  tip.style.left = '-9999px';   // park it, or it keeps extending scrollWidth
  tip.style.top = '-9999px';
}

function svgIn(el, width, height) {
  el.innerHTML = '';
  const svg = d3.select(el).append('svg')
    .attr('class', 'chart')
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('role', 'img');
  return svg;
}

function pct(v) { return `${Math.round(v * 100)}%`; }

/* ---------------------------------------------------------------- line ---- */
/* One series. No legend box: the card title names it. Endpoint is labelled. */

export function lineChart(el, rows, { x, y, height = 210, yDomain, yFormat = (v) => v.toFixed(2), label }) {
  const pts = rows.filter((r) => r[y] !== null && r[y] !== undefined);
  if (!pts.length) { el.innerHTML = '<div class="empty">No data in this range.</div>'; return; }

  const W = 760;
  const m = { top: 14, right: 58, bottom: 30, left: 44 };   // right pad holds the endpoint label
  const svg = svgIn(el, W, height);
  const iw = W - m.left - m.right;
  const ih = height - m.top - m.bottom;
  const g = svg.append('g').attr('transform', `translate(${m.left},${m.top})`);

  const sx = d3.scalePoint().domain(pts.map((d) => d[x])).range([0, iw]).padding(0.5);
  const sy = d3.scaleLinear()
    .domain(yDomain || [0, d3.max(pts, (d) => d[y]) * 1.15])
    .nice().range([ih, 0]);

  g.selectAll('.grid-line').data(sy.ticks(4)).join('line')
    .attr('class', 'grid-line').attr('x1', 0).attr('x2', iw)
    .attr('y1', (d) => sy(d)).attr('y2', (d) => sy(d));

  g.append('g').attr('class', 'tick').attr('transform', `translate(0,${ih})`)
    .call(d3.axisBottom(sx).tickSize(0).tickPadding(8))
    .call((s) => s.select('.domain').attr('class', 'axis-line'));
  g.append('g').attr('class', 'tick')
    .call(d3.axisLeft(sy).ticks(4).tickFormat(yFormat).tickSize(0).tickPadding(6))
    .call((s) => s.select('.domain').remove());

  const line = d3.line().x((d) => sx(d[x])).y((d) => sy(d[y])).curve(d3.curveMonotoneX);
  g.append('path').datum(pts).attr('class', 'line')
    .attr('stroke', 'var(--series-1)').attr('d', line);

  g.selectAll('.dot').data(pts).join('circle')
    .attr('class', 'dot').attr('fill', 'var(--series-1)')
    .attr('cx', (d) => sx(d[x])).attr('cy', (d) => sy(d[y])).attr('r', 4);

  // direct-label the endpoint only
  const last = pts[pts.length - 1];
  g.append('text').attr('class', 'series-label')
    .attr('x', sx(last[x]) + 10).attr('y', sy(last[y]) + 4)
    .text(yFormat(last[y]));

  // crosshair + tooltip; hit bands are far wider than the dots
  const crosshair = g.append('line').attr('class', 'crosshair')
    .attr('y1', 0).attr('y2', ih).attr('opacity', 0);
  g.selectAll('.hit').data(pts).join('rect')
    .attr('class', 'hit')
    .attr('x', (d) => sx(d[x]) - iw / pts.length / 2)
    .attr('y', 0).attr('width', iw / pts.length).attr('height', ih)
    .on('mousemove', (event, d) => {
      crosshair.attr('opacity', 1).attr('x1', sx(d[x])).attr('x2', sx(d[x]));
      showTip(`<div class="tt-title">${d[x]}</div>
        <div class="tt-row"><span>${label || y}</span><b>${yFormat(d[y])}</b></div>
        ${d.scored_queries !== undefined ? `<div class="tt-row"><span>queries</span><b>${d.scored_queries}</b></div>` : ''}`, event);
    })
    .on('mouseleave', () => { crosshair.attr('opacity', 0); hideTip(); });
}

/* ------------------------------------------------------- stacked columns -- */
/* Part-to-whole over time. 2px surface gap between segments, never a border. */

export function stackedBars(el, rows, { x, keys, colors, names, height = 220, yLabel }) {
  if (!rows.length) { el.innerHTML = '<div class="empty">No data.</div>'; return; }
  const W = 760;
  const m = { top: 14, right: 12, bottom: 34, left: 48 };
  const svg = svgIn(el, W, height);
  const iw = W - m.left - m.right;
  const ih = height - m.top - m.bottom;
  const g = svg.append('g').attr('transform', `translate(${m.left},${m.top})`);

  const sx = d3.scaleBand().domain(rows.map((d) => d[x])).range([0, iw]).padding(0.28);
  const total = (d) => keys.reduce((s, k) => s + (d[k] || 0), 0);
  const sy = d3.scaleLinear().domain([0, d3.max(rows, total) * 1.08]).nice().range([ih, 0]);

  g.selectAll('.grid-line').data(sy.ticks(4)).join('line')
    .attr('class', 'grid-line').attr('x1', 0).attr('x2', iw)
    .attr('y1', (d) => sy(d)).attr('y2', (d) => sy(d));

  // With many periods every label collides, so thin them to ~12 and keep the
  // first and last: a crammed axis is unreadable, which is worse than sparse.
  const every = Math.max(1, Math.ceil(rows.length / 12));
  const shown = rows.map((d) => d[x]).filter((_, i) => i % every === 0 || i === rows.length - 1);
  g.append('g').attr('class', 'tick').attr('transform', `translate(0,${ih})`)
    .call(d3.axisBottom(sx).tickValues(shown).tickSize(0).tickPadding(8))
    .call((s) => s.select('.domain').attr('class', 'axis-line'));
  g.append('g').attr('class', 'tick')
    .call(d3.axisLeft(sy).ticks(4).tickSize(0).tickPadding(6))
    .call((s) => s.select('.domain').remove());

  const stack = d3.stack().keys(keys)(rows);
  stack.forEach((layer, li) => {
    g.selectAll(`.seg-${li}`).data(layer).join('rect')
      .attr('class', `seg-${li}`)
      .attr('x', (d) => sx(d.data[x]))
      .attr('width', sx.bandwidth())
      .attr('y', (d) => sy(d[1]))
      // subtract the 2px gap so adjacent fills are separated by surface
      .attr('height', (d) => Math.max(0, sy(d[0]) - sy(d[1]) - 2))
      .attr('rx', 2)
      .attr('fill', colors[li])
      .on('mousemove', (event, d) => showTip(
        `<div class="tt-title">${d.data[x]}</div>` +
        keys.map((k, i) => `<div class="tt-row"><span>${names[i]}</span><b>${d.data[k]}</b></div>`).join('') +
        `<div class="tt-row"><span>total ${yLabel || ''}</span><b>${total(d.data)}</b></div>`, event))
      .on('mouseleave', hideTip);
  });
}

/* ------------------------------------------------------------- histogram -- */
/* Magnitude across ordered bins: one hue, sequential. */

export function histogram(el, values, { bins = 18, height = 190, xFormat = (v) => v.toFixed(2) }) {
  if (!values.length) { el.innerHTML = '<div class="empty">No scored results.</div>'; return; }
  const W = 760;
  const m = { top: 12, right: 12, bottom: 32, left: 44 };
  const svg = svgIn(el, W, height);
  const iw = W - m.left - m.right;
  const ih = height - m.top - m.bottom;
  const g = svg.append('g').attr('transform', `translate(${m.left},${m.top})`);

  const sx = d3.scaleLinear().domain(d3.extent(values)).nice().range([0, iw]);
  const binned = d3.bin().domain(sx.domain()).thresholds(bins)(values);
  const sy = d3.scaleLinear().domain([0, d3.max(binned, (b) => b.length)]).nice().range([ih, 0]);

  g.selectAll('.grid-line').data(sy.ticks(4)).join('line')
    .attr('class', 'grid-line').attr('x1', 0).attr('x2', iw)
    .attr('y1', (d) => sy(d)).attr('y2', (d) => sy(d));
  g.append('g').attr('class', 'tick').attr('transform', `translate(0,${ih})`)
    .call(d3.axisBottom(sx).ticks(6).tickFormat(xFormat).tickSize(0).tickPadding(8))
    .call((s) => s.select('.domain').attr('class', 'axis-line'));
  g.append('g').attr('class', 'tick')
    .call(d3.axisLeft(sy).ticks(4).tickSize(0).tickPadding(6))
    .call((s) => s.select('.domain').remove());

  g.selectAll('.hbar').data(binned).join('rect')
    .attr('class', 'hbar')
    .attr('x', (b) => sx(b.x0) + 1)
    .attr('width', (b) => Math.max(1, sx(b.x1) - sx(b.x0) - 2))
    .attr('y', (b) => sy(b.length))
    .attr('height', (b) => ih - sy(b.length))
    .attr('rx', 2)
    .attr('fill', 'var(--seq-400)')
    .on('mousemove', (event, b) => showTip(
      `<div class="tt-title">${xFormat(b.x0)} – ${xFormat(b.x1)}</div>
       <div class="tt-row"><span>results</span><b>${b.length}</b></div>`, event))
    .on('mouseleave', hideTip);
}

/* -------------------------------------------------------------- quadrant -- */
/* Scatter: an all-pairs form, so at most three hues. This uses one hue plus a
 * muted class for "never matched", and position does the rest. */

export function quadrant(el, allRows, { onPick, height = 420, xMax, yRef }) {
  // A lesson never matched by relevance has no score, so it has no position:
  // plotting those at (0,0) stacked 34 deep looked like a single stray dot and
  // hid the very group the caption pointed at. They are counted in the tiles
  // and listed in the table instead.
  const rows = allRows.filter((d) => d.matched > 0 && d.mean_matched_score !== null);
  if (!rows.length) { el.innerHTML = '<div class="empty">No scored lessons to place.</div>'; return; }
  const W = 760;
  const m = { top: 16, right: 16, bottom: 42, left: 52 };
  const svg = svgIn(el, W, height);
  const iw = W - m.left - m.right;
  const ih = height - m.top - m.bottom;
  const g = svg.append('g').attr('transform', `translate(${m.left},${m.top})`);

  const sx = d3.scaleSqrt().domain([0, xMax || d3.max(rows, (d) => d.matched) || 1]).nice().range([0, iw]);
  // Fit to the scores that exist. Nothing scores below ~0.30 (BGE cosine is
  // compressed), so a 0-100% axis spends half its height where no lesson can be.
  const lo = Math.max(0, d3.min(rows, (d) => d.mean_matched_score) - 0.06);
  const hi = Math.min(1, d3.max(rows, (d) => d.mean_matched_score) + 0.06);
  const sy = d3.scaleLinear().domain([lo, hi]).nice().range([ih, 0]);
  const sr = d3.scaleSqrt().domain([0, d3.max(rows, (d) => d.matched + d.bridged) || 1]).range([3.5, 15]);

  g.selectAll('.grid-line').data(sy.ticks(5)).join('line')
    .attr('class', 'grid-line').attr('x1', 0).attr('x2', iw)
    .attr('y1', (d) => sy(d)).attr('y2', (d) => sy(d));
  g.append('g').attr('class', 'tick').attr('transform', `translate(0,${ih})`)
    .call(d3.axisBottom(sx).ticks(6).tickSize(0).tickPadding(8))
    .call((s) => s.select('.domain').attr('class', 'axis-line'));
  g.append('g').attr('class', 'tick')
    .call(d3.axisLeft(sy).ticks(5).tickFormat(pct).tickSize(0).tickPadding(6))
    .call((s) => s.select('.domain').remove());

  g.append('text').attr('x', iw / 2).attr('y', ih + 34).attr('text-anchor', 'middle')
    .text('times matched by relevance →');
  g.append('text').attr('transform', 'rotate(-90)').attr('x', -ih / 2).attr('y', -38)
    .attr('text-anchor', 'middle').text('mean score when matched');

  if (yRef) {
    g.append('line').attr('class', 'quad-line')
      .attr('x1', 0).attr('x2', iw).attr('y1', sy(yRef)).attr('y2', sy(yRef));
    g.append('text').attr('class', 'quad-label').attr('x', 4).attr('y', sy(yRef) - 5)
      .text(`corpus median ${pct(yRef)}`);
  }

  g.selectAll('.pt').data(rows).join('circle')
    .attr('class', 'dot')
    .attr('fill', 'var(--series-1)')
    .attr('fill-opacity', 0.78)
    .attr('cx', (d) => sx(d.matched))
    .attr('cy', (d) => sy(d.mean_matched_score))
    .attr('r', (d) => sr(d.matched + d.bridged))
    .style('cursor', 'pointer')
    .on('mousemove', (event, d) => showTip(
      `<div class="tt-title">${d.id}</div>
       <div class="tt-row"><span>matched</span><b>${d.matched}</b></div>
       <div class="tt-row"><span>bridged (unscored before v2.13)</span><b>${d.bridged}</b></div>
       <div class="tt-row"><span>mean when matched</span><b>${d.mean_matched_score === null ? '—' : pct(d.mean_matched_score)}</b></div>`, event))
    .on('mouseleave', hideTip)
    .on('click', (event, d) => onPick && onPick(d));
}

/* --------------------------------------------------------------- h-bars --- */
/* Long category names read better horizontally. One hue: identity is the row. */

export function barsH(el, rows, { label, value, height, max, colorFor }) {
  if (!rows.length) { el.innerHTML = '<div class="empty">Nothing recorded.</div>'; return; }
  const top = max || d3.max(rows, (d) => d[value]) || 1;
  el.innerHTML = `<table><tbody>${rows.map((r) => {
    const w = Math.max(2, (r[value] / top) * 100);
    const colour = colorFor ? colorFor(r) : 'var(--series-1)';
    return `<tr>
      <td style="width:1%;white-space:nowrap"><span class="id">${r[label]}</span></td>
      <td><span class="bar-inline" style="width:${w}%;background:${colour}"></span></td>
      <td class="num" style="width:1%">${r[value]}</td>
    </tr>`;
  }).join('')}</tbody></table>`;
}

/* ------------------------------------------------------------ force graph -- */
/* Communities are >8, so hue cannot carry them: nodes are one hue, size is
 * usage, and the community is a label plus the layout's own clustering. */

export function forceGraph(el, { nodes, links }, { height = 560, onPick }) {
  if (!nodes.length) { el.innerHTML = '<div class="empty">Graph is empty.</div>'; return; }
  const W = 900;
  const svg = svgIn(el, W, height);
  const g = svg.append('g');

  const sr = d3.scaleSqrt().domain([0, d3.max(nodes, (n) => n.usage_count || 0) || 1]).range([3, 13]);
  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((d) => d.id).distance(52).strength(0.35))
    .force('charge', d3.forceManyBody().strength(-90))
    .force('center', d3.forceCenter(W / 2, height / 2))
    .force('collide', d3.forceCollide().radius((d) => sr(d.usage_count || 0) + 3));

  const link = g.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', 'var(--axis)').attr('stroke-width', 1).attr('stroke-opacity', 0.5);

  const node = g.append('g').selectAll('circle').data(nodes).join('circle')
    .attr('class', 'dot')
    .attr('r', (d) => sr(d.usage_count || 0))
    .attr('fill', (d) => (d.dead ? 'var(--ink-muted)' : 'var(--series-1)'))
    .attr('fill-opacity', (d) => (d.dead ? 0.5 : 1))
    .style('cursor', 'pointer')
    .on('mousemove', (event, d) => showTip(
      `<div class="tt-title">${d.id}</div>
       <div class="tt-row"><span>retrievals</span><b>${d.usage_count ?? 0}</b></div>
       ${d.dead ? '<div class="tt-row"><span>never surfaced</span><b>yes</b></div>' : ''}`, event))
    .on('mouseleave', hideTip)
    .on('click', (event, d) => onPick && onPick(d));

  node.call(d3.drag()
    .on('start', (event, d) => { if (!event.active) sim.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; })
    .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
    .on('end', (event, d) => { if (!event.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

  svg.call(d3.zoom().scaleExtent([0.4, 4]).on('zoom', (e) => g.attr('transform', e.transform)));

  sim.on('tick', () => {
    link.attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
    node.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
  });
  return () => sim.stop();
}
