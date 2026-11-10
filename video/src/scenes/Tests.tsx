import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { backdrop, C, display, mono } from "../theme";

/* Real output: `pytest` (107 passed) and `python matching_agent.py --job senior-python-backend --llm none -v`. */
const FILES: [string, number, string][] = [
  ["test_jsonrpc_protocol.py", 27, "framing, batches, ids, every error code, version negotiation, pagination"],
  ["test_config_and_transports.py", 22, "config precedence, stdio framing, HTTP sessions, auth, UNC paths"],
  ["test_mcp_tools.py", 18, "Milestone 1 tools over MCP, validation, batch_process, progress"],
  ["test_watch_directory.py", 9, "created/modified/deleted, long-poll, half-written files, pushes"],
  ["test_agent.py", 9, "agent vs real server subprocesses: once, watch, LLM, ask, failures"],
  ["test_resources_and_prompts.py", 8, "resources/list, templates, read, subscribe, prompts"],
  ["test_scoring.py", 7, "weak mentions, false positives, ranking per role"],
  ["test_architecture.py", 4, "agent imports no file-system code; server uses no SDK"],
  ["test_sdk_interop.py", 3, "OFFICIAL MCP SDK client over stdio + Streamable HTTP"],
];

const CLI = [
  "> discover_servers   filesystem: 8 tools / 9 resources, candidate_db: 7 tools / 1 resources",
  "> load_job           -> candidate_db.get_job(senior-python-backend)",
  "> list_resumes       -> filesystem.list_files(resumes)",
  "> batch_read         -> filesystem.batch_process(read, 8 files)   8/8 in 325 ms",
  "> score_candidates   best John Doe (95.7)",
  "> rank_and_report    -> filesystem.write_file(output/match_senior-python-backend.md)",
  "> persist_results    -> candidate_db.save_match_results(8)",
  "",
  " 1  John Doe          95.7  Strong match     -",
  " 2  Sara Lindqvist    67.0  Potential match  PostgreSQL",
  " 3  Priya Sharma      53.0  Not a fit        PostgreSQL, AWS",
  " 7  Alex Chen         24.5  Not a fit        FastAPI, PostgreSQL, Docker, AWS, Python(weak)",
];

export const Tests: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / 30;
  const on = (at: number) => interpolate(t, [at, at + 0.3], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={backdrop}>
      <div style={{ position: "absolute", left: 90, top: 50, fontFamily: mono, fontSize: 24, color: C.db }}>08</div>
      <div style={{ position: "absolute", left: 140, top: 38, fontFamily: display, fontWeight: 800, fontSize: 44 }}>
        Test scenarios and the CLI
      </div>
      <div style={{ position: "absolute", left: 80, top: 130, width: 900, height: 860, borderRadius: 16, background: "#0b0a08",
        border: `1px solid ${C.line2}`, padding: "22px 26px", fontFamily: mono }}>
        <div style={{ fontSize: 24, color: C.bone2 }}>$ pytest</div>
        {FILES.map(([f, n, what], i) => (
          <div key={f} style={{ marginTop: 16, opacity: on(0.8 + i * 0.7) }}>
            <div style={{ fontSize: 23, color: C.bone }}>
              tests/{f} <span style={{ color: C.ok }}>{"·".repeat(Math.min(n, 27))}</span> <span style={{ color: C.mute }}>{n}</span>
            </div>
            <div style={{ fontSize: 18, color: C.mute, fontFamily: "inherit" }}>{what}</div>
          </div>
        ))}
        <div style={{ marginTop: 26, fontSize: 30, fontWeight: 600, color: C.ok, opacity: on(8) }}>
          ===== 107 passed in 32.5s =====
        </div>
      </div>
      <div style={{ position: "absolute", left: 1005, top: 130, width: 840, height: 860, borderRadius: 16, background: "#0b0a08",
        border: `1px solid ${C.line2}`, padding: "22px 24px", fontFamily: mono, fontSize: 15, lineHeight: 1.55, opacity: on(9.5) }}>
        <div style={{ fontSize: 21, color: C.bone2, marginBottom: 14 }}>$ python matching_agent.py --job senior-python-backend -v <span style={{ color: C.mute }}>(condensed)</span></div>
        {CLI.map((line, i) => (
          <div key={i} style={{ whiteSpace: "pre", color: line.startsWith(">") ? C.fs : line.includes("Strong") ? C.db : C.bone,
            opacity: on(10 + i * 0.45), minHeight: 24 }}>{line}</div>
        ))}
        <div style={{ marginTop: 18, color: C.mute, opacity: on(16) }}>Report written via MCP: output/match_senior-python-backend.md</div>
      </div>
    </AbsoluteFill>
  );
};
export const TESTS_SECONDS = 22;
