/* Dashboard client: renders discovery, the LangGraph state machine, the ranking and the
   JSON-RPC traffic, all driven by the Server-Sent Events stream from /api/events. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SRV = { filesystem: "fs", candidate_db: "db" };

let STATE = null;
let running = false;

/* ------------------------------------------------------------------ */
/* State machine                                                      */
/* ------------------------------------------------------------------ */
const NODES = [
  { id: "discover_servers", x: 115, y: 62, tags: ["fs", "db"] },
  { id: "load_job", x: 290, y: 62, tags: ["db"] },
  { id: "list_resumes", x: 465, y: 62, tags: ["fs"] },
  { id: "batch_read", x: 640, y: 62, tags: ["fs"] },
  { id: "score_candidates", x: 822, y: 62, tags: [] },
  { id: "assess_with_llm", x: 822, y: 212, tags: ["llm"], llm: true },
  { id: "rank_and_report", x: 640, y: 212, tags: ["fs"] },
  { id: "persist_results", x: 465, y: 212, tags: ["db"] },
  { id: "watch_for_resumes", x: 290, y: 212, tags: ["fs"] },
];
const W = 150, H = 44;
const EDGES = [
  ["__start__", "discover_servers", "M29,62 L38,62"],
  ["discover_servers", "load_job", "M190,62 L213,62"],
  ["load_job", "list_resumes", "M365,62 L388,62"],
  ["list_resumes", "batch_read", "M540,62 L563,62"],
  ["batch_read", "score_candidates", "M715,62 L745,62"],
  ["score_candidates", "assess_with_llm", "M822,84 L822,188", "LLM on", 830, 140, "start", true],
  ["score_candidates", "rank_and_report", "M790,84 C 780,140 690,140 668,188", "LLM off", 736, 126, "middle"],
  ["assess_with_llm", "rank_and_report", "M747,212 L717,212"],
  ["rank_and_report", "persist_results", "M565,212 L542,212"],
  ["persist_results", "watch_for_resumes", "M390,212 L367,212", "", 0, 0, "middle", true],
  ["persist_results", "__end__", "M465,234 C 465,296 115,296 115,226", "run once", 290, 290, "middle"],
  ["watch_for_resumes", "batch_read", "M300,190 C 300,128 640,138 640,86", "new / changed resume", 476, 128, "middle", true],
  ["watch_for_resumes", "rank_and_report", "M300,234 C 300,268 640,268 640,236", "resume deleted", 470, 263, "middle", true],
  ["watch_for_resumes", "watch_for_resumes", "M236,190 C 222,150 272,148 262,190", "waiting", 249, 146, "middle", true],
  ["watch_for_resumes", "__end__", "M215,212 L127,212", "timeout", 171, 203, "middle", true],
];

function buildFlow() {
  const svg = $("#flow-svg");
  let h = `<defs>
    <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="#7d7566"/></marker>
    <filter id="glow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="4" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>`;
  for (const [from, to, d, label, lx, ly, anchor, opt] of EDGES) {
    const key = `${from}->${to}`;
    h += `<path class="edge${opt ? " opt" : ""}" data-edge="${key}" d="${d}" marker-end="url(#arr)"/>`;
    if (label) h += `<text class="edge-label" data-edge-label="${key}" x="${lx}" y="${ly}" text-anchor="${anchor}">${label}</text>`;
  }
  h += `<g class="terminal start" id="n-__start__"><circle cx="22" cy="62" r="7"/></g>`;
  h += `<g class="terminal end" id="n-__end__"><circle cx="115" cy="212" r="11"/><circle class="inner" cx="115" cy="212" r="6"/></g>`;
  h += `<text class="edge-label" x="115" y="241" text-anchor="middle">END</text>`;
  const tagColor = { fs: "var(--fs)", db: "var(--db)", llm: "var(--llm)" };
  for (const n of NODES) {
    const x = n.x - W / 2, y = n.y - H / 2;
    h += `<g class="node${n.llm ? " llm" : ""}" id="n-${n.id}">
      <rect x="${x}" y="${y}" width="${W}" height="${H}"/>
      <text class="label" x="${n.x}" y="${n.y - 3}">${n.id}</text>
      <text class="ms" x="${n.x}" y="${n.y + 12}" text-anchor="middle"></text>
      ${n.tags.map((t, i) => `<circle cx="${x + W - 10 - i * 11}" cy="${y + 9}" r="3.6" fill="${tagColor[t]}"/>`).join("")}
    </g>`;
  }
  svg.innerHTML = h;
}

let lastNode = "__start__";
const visitCount = {};
function resetFlow() {
  lastNode = "__start__";
  for (const k in visitCount) delete visitCount[k];
  $$(".node").forEach((g) => { g.classList.remove("visited", "active", "skipped"); $(".ms", g).textContent = ""; });
  $$(".edge").forEach((e) => e.classList.remove("lit", "done"));
  $$(".edge-label").forEach((e) => e.classList.remove("lit"));
  $("#n-__end__").classList.remove("done");
}
function lightEdge(from, to) {
  $$(".edge.lit").forEach((e) => { e.classList.remove("lit"); e.classList.add("done"); });
  $$(".edge-label.lit").forEach((e) => e.classList.remove("lit"));
  const key = `${from}->${to}`;
  const e = $(`[data-edge="${key}"]`);
  if (e) { e.classList.add("lit"); e.classList.remove("done"); }
  const l = $(`[data-edge-label="${key}"]`);
  if (l) l.classList.add("lit");
}
function nodeStart(id) {
  lightEdge(lastNode, id);
  $$(".node.active").forEach((g) => g.classList.remove("active"));
  const g = $(`#n-${id}`);
  if (g) g.classList.add("active", "visited");
  lastNode = id;
  ticker(id, "running…");
}
function nodeEnd(id, ms, summary) {
  const g = $(`#n-${id}`);
  visitCount[id] = (visitCount[id] || 0) + 1;
  if (g) $(".ms", g).textContent = `${ms} ms${visitCount[id] > 1 ? `  ×${visitCount[id]}` : ""}`;
  if (summary) ticker(id, summary);
}
function ticker(node, text) {
  $("#ticker").innerHTML = `<span class="tick-node">${esc(node)}</span><span class="tick-text">${esc(text)}</span>`;
}

/* ------------------------------------------------------------------ */
/* Discovery                                                          */
/* ------------------------------------------------------------------ */
function renderPills() {
  const d = STATE.discovery;
  const pill = (cls, name, meta) => `<div class="pill ${cls}" id="pill-${cls}"><span class="led"></span><b>${name}</b><span class="meta">${meta}</span></div>`;
  let h = "";
  if (d.filesystem) h += pill("fs", d.filesystem.server_info.name, "stdio");
  if (d.candidate_db) h += pill("db", d.candidate_db.server_info.name, "stdio");
  h += pill("http", "filesystem-mcp", `HTTP :${STATE.http_port}`);
  $("#server-pills").innerHTML = h;
}

const seenResources = new Set();
function renderCaps(highlightNew = false) {
  const d = STATE.discovery;
  let h = "";
  for (const [name, s] of Object.entries(d)) {
    const cls = SRV[name];
    const impl = name === "filesystem" ? "hand-written JSON-RPC" : "official SDK · FastMCP";
    const tools = s.tools.map((t) => {
      const a = t.annotations || {};
      const isNew = ["watch_directory", "poll_watch_events", "stop_watch", "batch_process"].includes(t.name);
      const badges = [a.readOnlyHint ? `<span class="badge ro">read-only</span>` : "",
        a.destructiveHint ? `<span class="badge destr">destructive</span>` : "",
        isNew ? `<span class="badge mcp">new</span>` : ""].join("");
      return `<li data-tool="${name}:${t.name}" title="${esc(t.description)}"><span class="nm${isNew ? " new" : ""}">${t.name}</span>${badges}</li>`;
    }).join("");
    const res = s.resources.map((r) => {
      const fresh = highlightNew && !seenResources.has(r.uri);
      seenResources.add(r.uri);
      return `<li class="${fresh ? "hot" : ""}"><span class="nm">${esc(r.uri)}</span></li>`;
    }).join("");
    const tpl = s.resource_templates.map((t) => `<li title="${esc(t.description)}"><span class="nm">${esc(t.uri_template)}</span></li>`).join("");
    const pr = s.prompts.map((p) => `<li title="${esc(p.description)}"><span class="nm">${esc(p.name)}</span></li>`).join("");
    h += `<div class="srv ${cls}">
      <div class="srv-head"><div class="srv-name">${s.server_info.name}<span class="srv-impl">${impl}</span></div>
        <div class="srv-meta">protocol ${s.protocol_version} · ${s.transport}</div></div>
      <div class="counts"><div><b>${s.tools.length}</b><small>tools</small></div><div><b>${s.resources.length}</b><small>resources</small></div>
        <div><b>${s.resource_templates.length}</b><small>templates</small></div><div><b>${s.prompts.length}</b><small>prompts</small></div></div>
      <details class="cap-group" open><summary>Tools</summary><ul class="cap-list">${tools}</ul></details>
      <details class="cap-group" ${name === "filesystem" ? "open" : ""}><summary>Resources</summary><ul class="cap-list">${res || '<li class="dim">none</li>'}</ul></details>
      <details class="cap-group"><summary>Resource templates</summary><ul class="cap-list">${tpl || '<li class="dim">none</li>'}</ul></details>
      <details class="cap-group"><summary>Prompts</summary><ul class="cap-list">${pr || '<li class="dim">none</li>'}</ul></details>
    </div>`;
  }
  $("#caps-body").innerHTML = h;
}
function flashTool(server, tool) {
  const li = $(`[data-tool="${server}:${tool}"]`);
  if (!li) return;
  li.classList.add("hot");
  li.scrollIntoView({ block: "nearest" });
  clearTimeout(li._t);
  li._t = setTimeout(() => li.classList.remove("hot"), 1400);
  const pill = $(`#pill-${SRV[server]}`);
  if (pill) { pill.classList.remove("flash"); void pill.offsetWidth; pill.classList.add("flash"); }
}

/* ------------------------------------------------------------------ */
/* Ranking                                                            */
/* ------------------------------------------------------------------ */
const cands = new Map();
const freshPaths = new Set();
function upsertCandidate(c, fresh = false) {
  const prev = cands.get(c.filepath);
  cands.set(c.filepath, { ...prev, ...c, assessment: c.assessment ?? prev?.assessment ?? null });
  if (fresh) freshPaths.add(c.filepath);
}
function renderRanking() {
  const list = $("#rank-list");
  const sorted = [...cands.values()].sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));
  if (!sorted.length) { list.innerHTML = `<li class="empty">No ranking yet.</li>`; return; }
  const first = new Map($$(".cand", list).map((el) => [el.dataset.path, el.getBoundingClientRect().top]));
  $(".empty", list)?.remove();
  const existing = new Map($$(".cand", list).map((el) => [el.dataset.path, el]));
  for (const [path, el] of existing) if (!cands.has(path)) el.remove();
  sorted.forEach((c, i) => {
    let el = existing.get(c.filepath);
    if (!el) { el = document.createElement("li"); el.className = "cand"; el.dataset.path = c.filepath; }
    const tier = c.score >= 75 ? "strong" : c.score >= 55 ? "potential" : "low";
    el.classList.remove("strong", "potential", "low");
    el.classList.add(tier);
    const chips = [
      ...c.required_matched.map((s) => `<span class="chip ok">${esc(s)}</span>`),
      ...c.required_partial.map((s) => `<span class="chip weak" title="only a weak mention">${esc(s)}?</span>`),
      ...c.required_missing.map((s) => `<span class="chip miss">${esc(s)}</span>`),
      ...c.nice_matched.map((s) => `<span class="chip nice">+${esc(s)}</span>`),
    ].join("");
    let assess = "";
    if (c._pending) assess = `<div class="assess pending">thinking</div>`;
    else if (c.assessment) assess = `<div class="assess">${esc(c.assessment)}</div>`;
    el.innerHTML = `<div class="pos">${i + 1}</div>
      <div class="who"><b>${esc(c.name)}</b><span>${esc(c.headline)} · ${c.years_experience ?? "?"} yrs · ${esc(c.filepath.split("/").pop())}</span></div>
      <div class="scorebar"><div class="bar"><i style="width:${el._w ?? 0}%"></i></div><span class="score">${c.score.toFixed(1)}</span></div>
      <div class="tier">${esc(c.tier)}</div>
      <div class="chips">${chips}</div>${assess}
      ${freshPaths.has(c.filepath) ? `<span class="tag-new">NEW</span>` : ""}`;
    list.appendChild(el);
    requestAnimationFrame(() => { const bar = $(".bar i", el); if (bar) bar.style.width = `${c.score}%`; el._w = c.score; });
    if (freshPaths.has(c.filepath) && !el._flashed) { el.classList.add("fresh"); el._flashed = true; }
  });
  // FLIP: animate rows from their old position to the new one
  $$(".cand", list).forEach((el) => {
    const old = first.get(el.dataset.path);
    if (old === undefined) return;
    const dy = old - el.getBoundingClientRect().top;
    if (Math.abs(dy) < 2) return;
    el.animate([{ transform: `translateY(${dy}px)` }, { transform: "translateY(0)" }], { duration: 650, easing: "cubic-bezier(.2,.8,.2,1)" });
  });
}

/* ------------------------------------------------------------------ */
/* Traffic                                                            */
/* ------------------------------------------------------------------ */
let trafficCount = 0;
function highlightJson(text) {
  return esc(text).replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(-?\d+(?:\.\d+)?)\b|\b(true|false|null)\b/g,
    (m, str, colon, num, lit) => str ? (colon ? `<span class="j-key">${str}</span>${colon}` : `<span class="j-str">${str}</span>`)
      : num ? `<span class="j-num">${num}</span>` : `<span class="j-lit">${lit}</span>`);
}
function filters() { return new Set($$("#traffic-filters input:checked").map((i) => i.value)); }
function addTraffic(ev) {
  const f = filters();
  if (!f.has(ev.server)) return;
  if (ev.kind === "notification" && !f.has("notifications")) return;
  trafficCount++;
  $("#traffic-count").textContent = trafficCount;
  const arrow = ev.kind === "notification" ? (ev.dir === "in" ? "◆" : "◇") : ev.dir === "out" ? "→" : "←";
  const li = document.createElement("li");
  li.className = `msg ${SRV[ev.server] || ""} ${ev.kind}`;
  const id = ev.id !== undefined && ev.id !== null ? `<span class="id">#${esc(ev.id)}</span>` : "";
  const via = ev.transport === "http" ? `<span class="via">HTTP</span>` : "";
  li.innerHTML = `<div class="msg-row"><span class="arrow">${arrow}</span>
      <span class="method">${esc(ev.method || (ev.kind === "error" ? "error" : "response"))}${id}${via}</span>
      <span class="lat">${ev.ms != null ? ev.ms + " ms" : (ev.bytes > 1024 ? (ev.bytes / 1024).toFixed(1) + " kB" : "")}</span>
      <span class="kind">${ev.kind}</span></div><pre>${highlightJson(ev.preview)}</pre>`;
  li.querySelector(".msg-row").addEventListener("click", () => li.classList.toggle("open"));
  const list = $("#traffic");
  list.prepend(li);
  while (list.children.length > 300) list.lastChild.remove();
}

/* ------------------------------------------------------------------ */
/* Watch strip                                                        */
/* ------------------------------------------------------------------ */
let watchTimer = null;
function showWatch(ev) {
  const strip = $("#watch-strip");
  strip.hidden = false;
  $("#watch-dir").textContent = ev.directory + "/";
  $("#watch-uri").textContent = ev.uri;
  $("#watch-log").innerHTML = "";
  const end = Date.now() + ev.seconds * 1000;
  clearInterval(watchTimer);
  watchTimer = setInterval(() => {
    const left = Math.max(0, Math.round((end - Date.now()) / 1000));
    $("#watch-left").textContent = `${left}s left`;
    if (!left) clearInterval(watchTimer);
  }, 250);
  renderDropButtons();
}
async function renderDropButtons() {
  const st = await (await fetch("/api/state")).json();
  $("#drop-buttons").innerHTML = st.incoming.map((n) => {
    const inside = st.dropped.includes(n);
    return `<button class="drop-btn${inside ? " in" : ""}" data-name="${esc(n)}" data-action="${inside ? "remove" : "drop"}">${inside ? "✕ remove " : "+ "}${esc(n)}</button>`;
  }).join(" ");
}

/* ------------------------------------------------------------------ */
/* Toasts                                                             */
/* ------------------------------------------------------------------ */
function toast(html, kind = "MCP") {
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = `<span class="k">${kind}</span>${html}`;
  $("#toasts").appendChild(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 320); }, 3600);
}

/* ------------------------------------------------------------------ */
/* Event stream                                                       */
/* ------------------------------------------------------------------ */
function setRunning(on) {
  running = on;
  const b = $("#run-btn");
  b.disabled = on;
  b.classList.toggle("running", on);
  b.lastChild.textContent = on ? "Running…" : "Run agent";
}

function handle(ev) {
  switch (ev.type) {
    case "traffic": addTraffic(ev); break;
    case "notification":
      if (ev.method === "notifications/resources/list_changed") toast(`<code>notifications/resources/list_changed</code> from ${ev.server}`, "PUSH");
      break;
    case "discovery": STATE.discovery = ev.discovery; renderCaps(true); break;
    case "agent.run_start":
      resetFlow(); cands.clear(); freshPaths.clear(); renderRanking();
      $("#run-meta").innerHTML = `<b>${esc(ev.run_id)}</b> · ${esc(ev.mode)}`;
      setRunning(true); break;
    case "agent.node_start": nodeStart(ev.node); break;
    case "agent.node_end": nodeEnd(ev.node, ev.ms, ev.summary); break;
    case "agent.tool_call": if (ev.server !== "llm") flashTool(ev.server, ev.tool); else askTrace(ev); break;
    case "agent.tool_result": if (ev.server === "llm") askTraceResult(ev); break;
    case "agent.progress": ticker("batch_read", ev.message || `${ev.done}/${ev.total}`); break;
    case "agent.candidate_scored": {
      const isFresh = lastNode === "score_candidates" && $("#watch-strip").hidden === false;
      upsertCandidate(ev.candidate, isFresh); renderRanking(); break;
    }
    case "agent.ranking": break;
    case "agent.llm_call": {
      const c = [...cands.values()].find((x) => x.name === ev.candidate);
      if (c) { c._pending = true; renderRanking(); }
      ticker("assess_with_llm", `asking the LLM about ${ev.candidate}…`); break;
    }
    case "agent.llm_result": {
      const c = [...cands.values()].find((x) => x.name === ev.candidate);
      if (c) { c._pending = false; c.assessment = ev.assessment; renderRanking(); }
      break;
    }
    case "agent.watch_started": showWatch(ev); toast(`watching <code>${esc(ev.uri)}</code>`, "WATCH"); break;
    case "agent.watch_event": {
      const e = ev.event;
      const li = document.createElement("li");
      li.className = e.type;
      li.innerHTML = `<b>${e.type}</b> ${esc(e.path)} <span class="dim">seq ${e.seq}</span>`;
      $("#watch-log").appendChild(li);
      if (e.type === "deleted") { cands.delete(e.path); renderRanking(); }
      toast(`<b>${e.type}</b> <code>${esc(e.path)}</code>`, "EVENT");
      renderDropButtons(); break;
    }
    case "agent.watch_stopped": $("#watch-left").textContent = "stopped"; clearInterval(watchTimer); break;
    case "agent.error": toast(esc(ev.message), "ERROR"); break;
    case "agent.run_end":
      lightEdge(lastNode, "__end__");
      $$(".node.active").forEach((g) => g.classList.remove("active"));
      $("#n-__end__").classList.add("done");
      ticker("END", ev.report ? `report written via MCP → ${ev.report}` : "finished");
      break;
    case "run_finished":
      setRunning(false);
      setTimeout(() => { $("#watch-strip").hidden = true; }, 2500);
      break;
    case "file_dropped": case "file_removed": renderDropButtons(); break;
  }
}

function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => handle(JSON.parse(m.data));
  es.onerror = () => { es.close(); setTimeout(connect, 1500); };
}

/* ------------------------------------------------------------------ */
/* Raw console                                                        */
/* ------------------------------------------------------------------ */
const PRESETS = [
  ["tools/list", { jsonrpc: "2.0", id: 1, method: "tools/list" }],
  ["batch_process", { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "batch_process", arguments: { operation: "search", directory: "resumes", keyword: "Kubernetes" } } }],
  ["resources/read", { jsonrpc: "2.0", id: 3, method: "resources/read", params: { uri: "fs:///resumes/resume_maria_garcia.pdf" } }],
  ["templates", { jsonrpc: "2.0", id: 4, method: "resources/templates/list" }],
  ["config://server", { jsonrpc: "2.0", id: 5, method: "resources/read", params: { uri: "config://server" } }],
  ["batch [ ]", [{ jsonrpc: "2.0", id: 6, method: "ping" }, { jsonrpc: "2.0", method: "notifications/initialized" }, { jsonrpc: "2.0", id: 7, method: "prompts/list" }]],
  ["-32601 unknown method", { jsonrpc: "2.0", id: 8, method: "tools/delete_everything" }, true],
  ["-32602 unknown tool", { jsonrpc: "2.0", id: 9, method: "tools/call", params: { name: "format_disk", arguments: {} } }, true],
  ["isError: bad args", { jsonrpc: "2.0", id: 10, method: "tools/call", params: { name: "read_file", arguments: { filepath: 42 } } }, true],
  ["isError: sandbox", { jsonrpc: "2.0", id: 11, method: "tools/call", params: { name: "read_file", arguments: { filepath: "../../Windows/win.ini" } } }, true],
  ["-32002 missing resource", { jsonrpc: "2.0", id: 12, method: "resources/read", params: { uri: "fs:///resumes/ghost.pdf" } }, true],
  ["-32600 invalid request", { jsonrpc: "1.0", id: 13, method: "ping" }, true],
  ["-32700 parse error", '{"jsonrpc": "2.0", "id": 14, "method": "ping"', true],
];
function renderPresets() {
  $("#presets").innerHTML = PRESETS.map(([label, , bad], i) => `<button class="preset${bad ? " bad" : ""}" data-i="${i}" type="button">${esc(label)}</button>`).join("");
  setPreset(0);
}
function setPreset(i) {
  const [, body] = PRESETS[i];
  $("#rpc-input").value = typeof body === "string" ? body : JSON.stringify(body, null, 2);
  $$(".preset").forEach((b) => b.classList.toggle("active", +b.dataset.i === i));
}
async function sendRpc() {
  const out = $("#rpc-output");
  out.textContent = "…";
  const r = await fetch("/api/rpc", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ raw: $("#rpc-input").value }) });
  const j = await r.json();
  const body = j.body;
  const isErr = j.status >= 400 || (body && (Array.isArray(body) ? body.some((b) => b.error) : body.error || body.result?.isError));
  $("#rpc-status").innerHTML = `HTTP <span class="${j.status < 400 ? "status-ok" : "status-err"}">${j.status}</span> · ${j.ms} ms · session ${esc(j.session)}`;
  out.classList.toggle("err", !!isErr);
  out.innerHTML = body === null ? "<span class='dim'>202 Accepted — notifications get no response body</span>" : highlightJson(JSON.stringify(body, null, 2));
}

/* ------------------------------------------------------------------ */
/* Ask                                                                */
/* ------------------------------------------------------------------ */
function askTrace(ev) {
  $("#ask-trace .thinking")?.remove();
  const li = document.createElement("li");
  li.dataset.tool = ev.tool;
  li.innerHTML = `→ <b>${esc(ev.tool)}</b>(${esc(JSON.stringify(ev.arguments))})`;
  $("#ask-trace").appendChild(li);
  const srv = STATE.discovery.filesystem?.tools.some((t) => t.name === ev.tool) ? "filesystem" : "candidate_db";
  flashTool(srv, ev.tool);
}
function askTraceResult(ev) {
  const li = $$("#ask-trace li").reverse().find((l) => l.dataset.tool === ev.tool && !l.dataset.done);
  if (li) { li.dataset.done = 1; li.innerHTML += ` <span>${ev.ok ? "✓" : "✗"} ${esc((ev.preview || "").slice(0, 70))}…</span>`; }
  const t = document.createElement("li"); t.className = "thinking"; t.textContent = "model is reading the result…";
  $("#ask-trace").appendChild(t);
}
function miniMarkdown(md) {
  const lines = esc(md).split("\n");
  let html = "", i = 0;
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  while (i < lines.length) {
    const l = lines[i];
    if (l.trim().startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));
      const body = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
      html += "<table><tr>" + cells(body[0]).map((c) => `<th>${c}</th>`).join("") + "</tr>" +
        body.slice(1).map((r) => "<tr>" + cells(r).map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</table>";
      continue;
    }
    if (/^\s*[-*] /.test(l)) {
      html += "<ul>";
      while (i < lines.length && /^\s*[-*] /.test(lines[i])) html += `<li>${inline(lines[i++].replace(/^\s*[-*] /, ""))}</li>`;
      html += "</ul>"; continue;
    }
    if (/^#+ /.test(l)) { html += `<p><strong>${inline(l.replace(/^#+ /, ""))}</strong></p>`; i++; continue; }
    if (l.trim()) html += `<p>${inline(l)}</p>`;
    i++;
  }
  return html;
}
async function ask(e) {
  e.preventDefault();
  $("#ask-trace").innerHTML = `<li class="thinking">model is choosing tools…</li>`;
  $("#ask-answer").innerHTML = "";
  $("#ask-btn").disabled = true;
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: $("#ask-input").value }) });
    const j = await r.json();
    $("#ask-trace .thinking")?.remove();
    $("#ask-answer").innerHTML = r.ok ? miniMarkdown(j.answer) : `<p class="status-err">${esc(j.detail)}</p>`;
  } finally { $("#ask-btn").disabled = false; }
}

/* ------------------------------------------------------------------ */
/* Boot                                                               */
/* ------------------------------------------------------------------ */
async function boot() {
  buildFlow();
  STATE = await (await fetch("/api/state")).json();
  renderPills();
  renderCaps();
  $("#job-select").innerHTML = STATE.jobs.map((j) => `<option value="${j.job_id}">${esc(j.title)}</option>`).join("");
  $("#job-select").value = STATE.jobs.some((j) => j.job_id === "senior-python-backend") ? "senior-python-backend" : STATE.jobs[0]?.job_id;
  $("#http-port").textContent = STATE.http_port;
  renderPresets();
  for (const c of STATE.ranking) upsertCandidate(c);
  renderRanking();
  setRunning(STATE.running);
  connect();

  $("#job-select").addEventListener("change", () => { $("#rank-title").textContent = `Ranking · ${$("#job-select").selectedOptions[0].text}`; });
  $("#rank-title").textContent = `Ranking · ${$("#job-select").selectedOptions[0]?.text ?? ""}`;
  $("#run-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (running) return;
    $("#rank-title").textContent = `Ranking · ${$("#job-select").selectedOptions[0].text}`;
    const body = { job_id: $("#job-select").value, use_llm: $("#llm-toggle").checked,
      watch_seconds: $("#watch-toggle").checked ? +$("#watch-seconds").value : 0 };
    const r = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) toast(esc((await r.json()).detail), "BUSY");
  });
  $("#reset-btn").addEventListener("click", async () => {
    await fetch("/api/reset", { method: "POST" });
    resetFlow(); cands.clear(); freshPaths.clear(); renderRanking();
    $("#traffic").innerHTML = ""; trafficCount = 0; $("#traffic-count").textContent = 0;
    ticker("ready", "Choose a job and run the agent.");
  });
  $("#drop-buttons").addEventListener("click", async (e) => {
    const b = e.target.closest(".drop-btn");
    if (!b) return;
    await fetch(`/api/${b.dataset.action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: b.dataset.name }) });
    renderDropButtons();
  });
  $$(".tab").forEach((t) => t.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
    $$(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === `pane-${t.dataset.tab}`));
  }));
  $("#presets").addEventListener("click", (e) => { const b = e.target.closest(".preset"); if (b) setPreset(+b.dataset.i); });
  $("#rpc-send").addEventListener("click", sendRpc);
  $("#ask-form").addEventListener("submit", ask);
  $("#clear-traffic").addEventListener("click", () => { $("#traffic").innerHTML = ""; trafficCount = 0; $("#traffic-count").textContent = 0; });
}
boot();
