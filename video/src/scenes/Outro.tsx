import { useSceneFrame } from "../timescale";
import React from "react";
import { AbsoluteFill, Easing, interpolate } from "remotion";
import { backdrop, C, display, mono } from "../theme";

const ITEMS = [
  "filesystem_mcp_server.py: hand-written MCP server, JSON-RPC 2.0, stdio + Streamable HTTP",
  "watch_directory() and batch_process(), plus resources, templates, prompts and config management",
  "matching_agent.py: LangGraph agent that reaches files and data only through MCP",
  "Bonus: candidate-db-mcp (FastMCP + SQLite) and LLM ask mode across both servers",
  "State machine, sequence and architecture diagrams in docs/",
  "107 tests, including official-SDK interop and end-to-end agent runs",
];

export const Outro: React.FC = () => {
  const frame = useSceneFrame();
  const t = frame / 30;
  const on = (at: number) => interpolate(t, [at, at + 0.5], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1) });
  return (
    <AbsoluteFill style={{ ...backdrop, paddingLeft: 170, justifyContent: "center" }}>
      <div style={{ fontFamily: mono, fontSize: 26, color: C.db, letterSpacing: "0.12em", opacity: on(0) }}>DELIVERABLES</div>
      <div style={{ marginTop: 26, display: "flex", flexDirection: "column", gap: 18 }}>
        {ITEMS.map((it, i) => (
          <div key={it} style={{ fontSize: 34, display: "flex", gap: 20, alignItems: "baseline", opacity: on(0.5 + i * 0.5),
            translate: `${(1 - on(0.5 + i * 0.5)) * 24}px 0px` }}>
            <span style={{ color: C.ok, fontFamily: mono, fontWeight: 600 }}>✓</span>
            <span>{it}</span>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 70, fontFamily: display, fontWeight: 800, fontSize: 64, letterSpacing: "-0.02em", opacity: on(4) }}>
        github.com/<span style={{ color: C.fs }}>Naman6019</span>/mcp-resume-matcher
      </div>
    </AbsoluteFill>
  );
};
export const OUTRO_SECONDS = 12;
