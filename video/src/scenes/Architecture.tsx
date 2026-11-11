import { useSceneFrame } from "../timescale";
import React from "react";
import { AbsoluteFill, Easing, interpolate } from "remotion";
import { backdrop, C, display, mono, s } from "../theme";

const ease = Easing.bezier(0.16, 1, 0.3, 1);
const appear = (frame: number, start: number, dur = 20) =>
  interpolate(frame, [start, start + dur], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease });

type BoxProps = { x: number; y: number; w: number; h: number; color: string; title: string; tag?: string; at: number;
  children?: React.ReactNode };
const Box: React.FC<BoxProps> = ({ x, y, w, h, color, title, tag, at, children }) => {
  const frame = useSceneFrame();
  const p = appear(frame, at);
  return (
    <div style={{ position: "absolute", left: x, top: y, width: w, height: h, borderRadius: 16, background: C.panel,
      border: `2px solid ${color}`, padding: "20px 24px", opacity: p, translate: `0px ${(1 - p) * 24}px`,
      boxShadow: `0 20px 60px rgba(0,0,0,.45)` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <span style={{ fontFamily: display, fontWeight: 800, fontSize: 34, color }}>{title}</span>
        {tag ? <span style={{ fontFamily: mono, fontSize: 19, padding: "3px 10px", borderRadius: 6, border: `1px solid ${color}`,
          color }}>{tag}</span> : null}
      </div>
      {children}
    </div>
  );
};

const Chips: React.FC<{ items: string[]; color: string; at: number }> = ({ items, color, at }) => {
  const frame = useSceneFrame();
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 9, marginTop: 16 }}>
      {items.map((t, i) => (
        <span key={t} style={{ fontFamily: mono, fontSize: 21, padding: "5px 11px", borderRadius: 7, background: "rgba(0,0,0,.3)",
          border: `1px solid ${C.line2}`, color: i >= 4 && color === C.fs ? C.fs : C.bone, opacity: appear(frame, at + i * 3, 10) }}>{t}</span>
      ))}
    </div>
  );
};

// wires from the hub (right edge) to each server (left edge)
const WIRES = [
  { from: [790, 545], to: [1060, 340], color: C.fs },
  { from: [790, 585], to: [1060, 790], color: C.db },
];

export const Architecture: React.FC = () => {
  const frame = useSceneFrame();
  const wireP = appear(frame, s(5), 30);
  const banner = appear(frame, s(17), 24);
  return (
    <AbsoluteFill style={backdrop}>
      <div style={{ position: "absolute", left: 90, top: 50, fontFamily: mono, fontSize: 24, color: C.db, opacity: appear(frame, 0) }}>01</div>
      <div style={{ position: "absolute", left: 140, top: 38, fontFamily: display, fontWeight: 800, fontSize: 44, opacity: appear(frame, 0) }}>
        Architecture: one client, two very different servers
      </div>

      {/* host */}
      <div style={{ position: "absolute", left: 80, top: 150, width: 720, height: 760, borderRadius: 22, border: `2px dashed ${C.line2}`,
        opacity: appear(frame, 4) }}>
        <div style={{ position: "absolute", top: -18, left: 24, background: C.ink, padding: "0 12px", fontFamily: mono, fontSize: 22, color: C.bone2 }}>
          MCP host (dashboard or CLI)
        </div>
      </div>
      <Box x={120} y={200} w={640} h={200} color={C.bone} title="LangGraph agent" tag="matching_agent.py" at={s(1)}>
        <div style={{ fontSize: 24, color: C.bone2, marginTop: 14, lineHeight: 1.4 }}>
          9-node state machine. Deterministic scoring, optional LLM notes. <b style={{ color: C.bone }}>No file-system or database imports.</b>
        </div>
      </Box>
      <Box x={120} y={450} w={640} h={230} color={C.bone} title="MCPHub" tag="mcp_client.py" at={s(2.6)}>
        <div style={{ fontSize: 24, color: C.bone2, marginTop: 14, lineHeight: 1.4 }}>
          Official SDK <span style={{ fontFamily: mono }}>ClientSession</span> per server, kept open, plus a tap on every JSON-RPC message
          and server push.
        </div>
      </Box>
      <Box x={120} y={730} w={640} h={150} color={C.llm} title="LLM (optional)" tag="Ollama / OpenAI" at={s(3.6)}>
        <div style={{ fontSize: 23, color: C.bone2, marginTop: 10 }}>Notes on top candidates · ask mode with read-only MCP tools</div>
      </Box>

      {/* wires */}
      <svg width={1920} height={1080} style={{ position: "absolute", inset: 0 }}>
        {WIRES.map((wr, i) => {
          const [x1, y1] = wr.from, [x2, y2] = wr.to;
          const d = `M${x1},${y1} C ${x1 + 150},${y1} ${x2 - 150},${y2} ${x2},${y2}`;
          return <path key={i} d={d} stroke={wr.color} strokeWidth={5} fill="none" pathLength={1}
            strokeDasharray={1} strokeDashoffset={1 - wireP} />;
        })}
        {WIRES.map((wr, i) => {
          const [x1, y1] = wr.from, [x2, y2] = wr.to;
          return [0, 1, 2].map((k) => {
            const base = ((frame - s(7) + k * 22 + i * 11) % 66) / 66;
            if (frame < s(7)) return null;
            const t = (k + i) % 2 === 0 ? base : 1 - base;   // requests go out, responses come back
            const mt = 1 - t;
            const cx = mt ** 3 * x1 + 3 * mt * mt * t * (x1 + 150) + 3 * mt * t * t * (x2 - 150) + t ** 3 * x2;
            const cy = mt ** 3 * y1 + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t ** 3 * y2;
            return <circle key={`${i}-${k}`} cx={cx} cy={cy} r={9} fill={C.bone} stroke={wr.color} strokeWidth={3} />;
          });
        })}
      </svg>
      <div style={{ position: "absolute", left: 868, top: 540, fontFamily: mono, fontSize: 20, lineHeight: 1.3, color: C.bone2, opacity: wireP,
        background: C.ink, padding: "4px 10px", borderRadius: 6 }}>stdio<br />JSON-RPC 2.0</div>

      {/* servers */}
      <Box x={1060} y={140} w={780} h={420} color={C.fs} title="filesystem-mcp" tag="hand-written · no SDK" at={s(8)}>
        <Chips color={C.fs} at={s(9)} items={["list_files", "read_file", "write_file", "search_in_file", "watch_directory",
          "poll_watch_events", "stop_watch", "batch_process"]} />
        <div style={{ fontFamily: mono, fontSize: 21, color: C.bone2, marginTop: 18, lineHeight: 1.5, opacity: appear(frame, s(11)) }}>
          resources  fs:///…  config://server  watch://…<br />templates  fs:///  dir:///  search:///<br />transports stdio + Streamable HTTP
        </div>
        <div style={{ position: "absolute", left: 24, right: 24, bottom: 20, borderTop: `1px dashed ${C.line2}`, paddingTop: 12,
          fontFamily: mono, fontSize: 21, color: C.mute, opacity: appear(frame, s(12)) }}>
          wraps fs_tools.py (Milestone 1, sandboxed) unchanged
        </div>
      </Box>
      <Box x={1060} y={600} w={780} h={300} color={C.db} title="candidate-db-mcp" tag="official SDK · FastMCP" at={s(12.5)}>
        <Chips color={C.db} at={s(13.5)} items={["list_jobs", "get_job", "create_job", "save_match_results", "remove_matches",
          "get_shortlist", "get_candidate_history"]} />
        <div style={{ fontFamily: mono, fontSize: 21, color: C.bone2, marginTop: 18, opacity: appear(frame, s(15)) }}>
          SQLite: jobs · matches · runs · resources db://jobs/{"{job_id}"}/shortlist
        </div>
      </Box>

      {/* before / after */}
      <div style={{ position: "absolute", left: 80, right: 80, top: 950, display: "flex", gap: 30, opacity: banner,
        translate: `0px ${(1 - banner) * 20}px`, fontFamily: mono, fontSize: 22, whiteSpace: "nowrap" }}>
        <div style={{ flex: 1, padding: "16px 22px", borderRadius: 12, border: `1px solid ${C.err}`, color: C.bone2 }}>
          <span style={{ color: C.err, fontWeight: 600 }}>Milestone 1 ✕ </span>agent <span style={{ color: C.mute }}>→</span> import fs_tools
          <span style={{ color: C.mute }}> → </span>disk
        </div>
        <div style={{ flex: 1.5, padding: "16px 22px", borderRadius: 12, border: `1px solid ${C.ok}`, color: C.bone }}>
          <span style={{ color: C.ok, fontWeight: 600 }}>Milestone 2 ✓ </span>agent → MCP client → JSON-RPC → MCP server → disk / SQLite
        </div>
      </div>
    </AbsoluteFill>
  );
};
