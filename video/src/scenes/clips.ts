import { ClipSceneProps, Segment, sceneTime } from "../components/ClipScene";

/* Times come from video/public/clips/<scene>.marks.json, written by demo/record_demo.py. */

const def = (p: ClipSceneProps) => p;

// ------------------------------------------------------------------ tour
const tourSeg: Segment[] = [{ src: "tour", from: 0, to: 33.3 }];
export const tour = def({
  chapter: "03",
  title: "The dashboard: an MCP host",
  segments: tourSeg,
  captions: [
    { at: 0.2, text: "At start-up the host connects to both MCP servers and lists what each one offers" },
    { at: 4.2, text: "filesystem-mcp and candidate-db-mcp over stdio, plus the same filesystem server over Streamable HTTP" },
    { at: 6.6, text: "The four Milestone 1 tools, served over MCP with read-only / destructive annotations" },
    { at: 11.4, text: "New MCP capabilities: watch_directory, poll_watch_events, stop_watch and batch_process" },
    { at: 15.3, text: "Every resume is also an MCP resource: fs:///resumes/…" },
    { at: 17.9, text: "Resource templates: fs:///  dir:///  search:///  watch://" },
    { at: 20.9, text: "Second server: candidate-db-mcp, built with the official SDK (FastMCP) on SQLite" },
    { at: 24.8, text: "The LangGraph state machine. The dots show which server each node talks to" },
    { at: 30.3, text: "Every JSON-RPC message for both servers appears here live" },
  ],
  zooms: [
    { from: 6.6, to: 15.1, x: 175, y: 330, scale: 1.6 },
    { from: 15.3, to: 20.7, x: 175, y: 600, scale: 1.6 },
    { from: 20.9, to: 24.6, x: 175, y: 820, scale: 1.55 },
    { from: 24.8, to: 30.1, x: 880, y: 260, scale: 1.45 },
  ],
});

// ------------------------------------------------------------------ run
const runSeg: Segment[] = [
  { src: "run", from: 0, to: 4.8 },
  { src: "run_slowmo", from: 0, to: 8.0, badge: "replay · 6× slow motion · real run ≈ 0.5 s" },
  { src: "run", from: 4.8, to: 33.85 },
];
const r = (t: number) => sceneTime(runSeg, "run", t);
const slow = (t: number) => sceneTime(runSeg, "run_slowmo", t);
export const run = def({
  chapter: "04",
  title: "One run, end to end",
  segments: runSeg,
  captions: [
    { at: 0, text: "Run the agent for the Senior Python Backend Engineer role" },
    { at: r(3.6), text: "Finished in about half a second. Here is the replay in slow motion" },
    { at: slow(0.2), text: "discover_servers → load_job (candidate-db) → list_resumes → batch_read (filesystem)" },
    { at: slow(3.6), text: "batch_read: all 8 resumes in ONE batch_process call on 8 worker threads, with live progress" },
    { at: slow(5.8), text: "score → rank_and_report (write_file over MCP) → persist_results (candidate-db) → END" },
    { at: r(7.2), text: "Deterministic scores: required skills, nice-to-haves, experience and depth" },
    { at: r(11.6), text: "Weak mentions get partial credit: Alex Chen's \"basic Python\" is flagged, not counted" },
    { at: r(16.4), text: "The raw tools/call batch_process request, exactly as it went over stdio" },
    { at: r(21.5), text: "The response: structured content, per-file results and a summary" },
    { at: r(27.6), text: "notifications/progress, pushed by the server while the batch runs" },
  ],
  zooms: [
    { from: slow(0), to: slow(8.0), x: 880, y: 300, scale: 1.45 },
    { from: r(7.2), to: r(16.2), x: 880, y: 780, scale: 1.3 },
    { from: r(16.4), to: r(33.6), x: 1660, y: 520, scale: 1.6 },
  ],
});

// ------------------------------------------------------------------ watch
const watchSeg: Segment[] = [
  { src: "watch", from: 0, to: 34.6 },
  { src: "watch", from: 34.6, to: 58.6, rate: 8, badge: "8× · waiting for the watch window to close" },
  { src: "watch", from: 58.6, to: 61.9 },
];
const w = (t: number) => sceneTime(watchSeg, "watch", t);
export const watch = def({
  chapter: "05",
  title: "watch_directory: new resumes arrive",
  segments: watchSeg,
  captions: [
    { at: w(0), text: "Watch mode: after the first ranking, the agent calls watch_directory" },
    { at: w(3.7), text: "The server returns a watch_id and a subscribable watch:// resource. The agent long-polls for events" },
    { at: w(6.7), text: "A resume lands in the folder, dropped from outside (not through MCP)" },
    { at: w(8.7), text: "Only the new file is read and scored. Lena Park goes straight to #1" },
    { at: w(13.6), text: "A DOCX arrives too: same path, scored incrementally" },
    { at: w(20.9), text: "Delete a resume: 'deleted' event → re-rank, and remove_matches in the database" },
    { at: w(26.0), text: "notifications/resources/list_changed: a server push, as seen on the wire" },
    { at: w(30.5), text: "poll_watch_events is a long-poll with a seq cursor, so it works over any transport" },
    { at: w(34.6), text: "The watch window closes → stop_watch → END" },
  ],
  zooms: [
    { from: w(3.7), to: w(8.5), x: 700, y: 470, scale: 1.35 },
    { from: w(9.0), to: w(13.3), x: 700, y: 560, scale: 1.3 },
    { from: w(26.0), to: w(34.4), x: 1660, y: 500, scale: 1.6 },
  ],
});

// ------------------------------------------------------------------ console
const conSeg: Segment[] = [{ src: "console", from: 0, to: 67.9 }];
export const consoleScene = def({
  chapter: "06",
  title: "JSON-RPC 2.0 and error handling",
  segments: conSeg,
  captions: [
    { at: 1.8, text: "Raw console: the SAME filesystem server, over its Streamable HTTP transport" },
    { at: 4.3, text: "tools/list over HTTP. The session was opened with initialize (Mcp-Session-Id)" },
    { at: 10.2, text: "batch_process search: 'Kubernetes' across every resume in one call" },
    { at: 16.1, text: "A JSON-RPC batch: both requests are answered; the notification gets no reply" },
    { at: 21.9, text: "-32601  Method not found" },
    { at: 27.7, text: "-32602  Invalid params: unknown tool" },
    { at: 33.6, text: "Bad arguments → a result with isError: true, so the model can read it and retry" },
    { at: 39.5, text: "Sandbox: '../../' paths are refused as outside_sandbox" },
    { at: 45.3, text: "-32002  Resource not found" },
    { at: 51.2, text: "-32600  Invalid Request: \"jsonrpc\" must be \"2.0\"" },
    { at: 57.1, text: "-32700  Parse error, answered with id: null" },
    { at: 63.7, text: "HTTP traffic goes through the same inspector" },
  ],
  zooms: [{ from: 2.2, to: 63.5, x: 1660, y: 545, scale: 1.4 }],
});

// ------------------------------------------------------------------ llm
const llmSeg: Segment[] = [
  { src: "llm", from: 0, to: 6.1 },
  { src: "llm", from: 6.1, to: 26.6, rate: 2.5, badge: "2.5× · local LLM via Ollama" },
  { src: "llm", from: 26.6, to: 38.4 },
  { src: "llm", from: 38.4, to: 54.5, rate: 2.5, badge: "2.5× · local LLM via Ollama" },
  { src: "llm", from: 54.5, to: 61.7 },
];
const l = (t: number) => sceneTime(llmSeg, "llm", t);
export const llm = def({
  chapter: "07",
  title: "LLM notes and multi-MCP ask mode",
  segments: llmSeg,
  captions: [
    { at: l(0), text: "Optional LLM: two-sentence notes for the top 3. It never changes a score" },
    { at: l(6.1), text: "assess_with_llm runs a local model through Ollama, so no cloud API is needed" },
    { at: l(26.6), text: "The notes cite real evidence from each resume and name the gap" },
    { at: l(34.4), text: "Ask mode: the LLM gets the read-only tools of BOTH MCP servers" },
    { at: l(38.5), text: "It chooses list_jobs, then get_shortlist, both on the candidate-db server" },
    { at: l(54.5), text: "The answer comes from the stored scores, and every call went over MCP" },
  ],
  zooms: [
    { from: l(26.6), to: l(34.2), x: 700, y: 640, scale: 1.35 },
    { from: l(35.0), to: l(61.7), x: 1660, y: 520, scale: 1.55 },
  ],
});
