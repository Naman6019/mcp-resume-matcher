import React from "react";
import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from "remotion";
import { backdrop, C, display, mono, s } from "../theme";

/* Real messages from demo/raw_stdio_session.jsonl -> demo/raw_stdio_output.jsonl, shortened with "…".
   Responses are listed in the order the server actually wrote them. */
type Line = { at: number; text: string; color: string; note?: string };

const SENT: Line[] = [
  { at: 1.0, color: C.fs, text: `{"id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25",…}}` },
  { at: 2.6, color: C.fs, text: `{"method":"notifications/initialized"}`, note: "notification: no id, never answered" },
  { at: 4.2, color: C.fs, text: `{"id":2,"method":"tools/call","params":{"name":"batch_process","arguments":{"operation":"search","keyword":"Kubernetes"},"_meta":{"progressToken":"p1"}}}` },
  { at: 5.8, color: C.fs, text: `{"id":3,"method":"tools/delete_everything"}` },
  { at: 7.0, color: C.fs, text: `{"id":4,"method":"tools/call","params":{"name":"read_file","arguments":{"filepath":"../../secrets.txt"}}}` },
  { at: 8.2, color: C.fs, text: `{"id":5,"method":"resources/read","params":{"uri":"fs:///resumes/ghost.pdf"}}` },
  { at: 9.4, color: C.fs, text: `{"jsonrpc":"2.0", "id": 6, "method": "ping"          ← missing "}"` },
];

const RECEIVED: Line[] = [
  { at: 11.5, color: C.ok, text: `{"id":1,"result":{"protocolVersion":"2025-11-25","capabilities":{"tools":…,"resources":{"subscribe":true,"listChanged":true},"prompts":…,"logging":{}},"serverInfo":{"name":"filesystem-mcp"}}}` },
  { at: 13.5, color: C.err, text: `{"id":3,"error":{"code":-32601,"message":"Method not found: tools/delete_everything"}}` },
  { at: 15.3, color: C.err, text: `{"id":null,"error":{"code":-32700,"message":"Parse error: Expecting ',' delimiter …"}}` },
  { at: 17.1, color: C.db, text: `{"id":4,"result":{"isError":true,"content":[…"type":"outside_sandbox","message":"Access denied …"]}}` },
  { at: 18.9, color: C.err, text: `{"id":5,"error":{"code":-32002,"message":"File not found: 'resumes/ghost.pdf' …","data":{"uri":…}}}` },
  { at: 20.7, color: C.bone2, text: `{"method":"notifications/progress","params":{"progressToken":"p1","progress":PROGRESS,"total":8}}` },
  { at: 23.4, color: C.bone2, text: `{"method":"notifications/message","params":{"level":"info","data":{"event":"batch_complete",…}}}` },
  { at: 24.6, color: C.ok, text: `{"id":2,"result":{"structuredContent":{"summary":{"total":8,"succeeded":8,"workers":8,"files_with_matches":2}},…}}` },
];

const CODES: { code: string; label: string; at: number; color: string }[] = [
  { code: "-32700", label: "Parse error", at: 15.3, color: C.err },
  { code: "-32600", label: "Invalid Request", at: 99, color: C.err },
  { code: "-32601", label: "Method not found", at: 13.5, color: C.err },
  { code: "-32602", label: "Invalid params", at: 99, color: C.err },
  { code: "-32603", label: "Internal error", at: 99, color: C.err },
  { code: "-32000", label: "Not initialized", at: 99, color: C.err },
  { code: "-32002", label: "Resource not found", at: 18.9, color: C.err },
  { code: "isError", label: "tool failed, model can retry", at: 17.1, color: C.db },
];

const ease = Easing.bezier(0.16, 1, 0.3, 1);

const Term: React.FC<{ x: number; w: number; title: string; lines: Line[]; arrow: string }> = ({ x, w, title, lines, arrow }) => {
  const frame = useCurrentFrame();
  const t = frame / 30;
  return (
    <div style={{ position: "absolute", left: x, top: 145, width: w, height: 640, borderRadius: 16, background: "#0b0a08",
      border: `1px solid ${C.line2}`, overflow: "hidden" }}>
      <div style={{ padding: "12px 18px", borderBottom: `1px solid ${C.line}`, fontFamily: mono, fontSize: 21, color: C.bone2,
        display: "flex", gap: 10, alignItems: "center" }}>
        {[C.err, C.db, C.ok].map((c) => <span key={c} style={{ width: 12, height: 12, borderRadius: 6, background: c, opacity: 0.7 }} />)}
        <span style={{ marginLeft: 10 }}>{title}</span>
      </div>
      <div style={{ padding: "14px 18px", display: "flex", flexDirection: "column", gap: 10 }}>
        {lines.map((ln, i) => {
          const p = interpolate(t, [ln.at, ln.at + 0.45], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease });
          const progress = Math.max(0, Math.min(8, Math.floor((t - 20.7) / 0.3)));
          const text = ln.text.replace("PROGRESS", String(progress));
          return (
            <div key={i} style={{ opacity: p, translate: `${(1 - p) * 16}px 0px`, fontFamily: mono, fontSize: 17.5, lineHeight: 1.4,
              color: ln.color, wordBreak: "break-all" }}>
              <span style={{ color: C.mute }}>{arrow} </span>{text}
              {ln.note ? <div style={{ color: C.mute, fontStyle: "italic", fontSize: 18 }}>{ln.note}</div> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export const Protocol: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / 30;
  const head = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const outOfOrder = interpolate(t, [25.6, 26.2], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={backdrop}>
      <div style={{ position: "absolute", left: 90, top: 50, fontFamily: mono, fontSize: 24, color: C.db, opacity: head }}>02</div>
      <div style={{ position: "absolute", left: 140, top: 38, fontFamily: display, fontWeight: 800, fontSize: 44, opacity: head }}>
        On the wire: raw JSON-RPC 2.0 over stdio
      </div>
      <div style={{ position: "absolute", left: 140, top: 100, fontFamily: mono, fontSize: 22, color: C.mute, opacity: head }}>
        $ python filesystem_mcp_server.py &lt; demo/raw_stdio_session.jsonl     # no SDK, no client, just newline-delimited JSON
      </div>
      <Term x={80} w={860} title="stdin  · client → server" lines={SENT} arrow="→" />
      <Term x={980} w={860} title="stdout · server → client (arrival order)" lines={RECEIVED} arrow="←" />

      <div style={{ position: "absolute", left: 980, top: 795, width: 860, textAlign: "right", fontFamily: mono, fontSize: 21,
        color: C.db, opacity: outOfOrder }}>
        id 2 answered last: requests run concurrently, matched by id
      </div>

      <div style={{ position: "absolute", left: 80, right: 80, top: 828, fontFamily: mono, fontSize: 21, color: C.bone2 }}>
        error codes implemented
      </div>
      <div style={{ position: "absolute", left: 80, right: 80, top: 862, display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 14 }}>
        {CODES.map((c) => {
          const lit = interpolate(t, [c.at, c.at + 0.4], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          const all = interpolate(t, [26.5, 27.5], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          const on = Math.max(lit, all * 0.75);
          return (
            <div key={c.code} style={{ borderRadius: 12, padding: "8px 18px", border: `2px solid ${on > 0.5 ? c.color : C.line2}`,
              background: on > 0.5 ? "rgba(255,255,255,.03)" : "transparent", opacity: 0.35 + 0.65 * on }}>
              <div style={{ fontFamily: mono, fontWeight: 600, fontSize: 28, color: on > 0.5 ? c.color : C.bone2 }}>{c.code}</div>
              <div style={{ fontSize: 21, color: C.bone2 }}>{c.label}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
export const PROTOCOL_SECONDS = 32;
export const protocolFrames = s(PROTOCOL_SECONDS);
