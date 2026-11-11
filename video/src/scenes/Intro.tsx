import { useSceneFrame } from "../timescale";
import React from "react";
import { AbsoluteFill, Easing, interpolate } from "remotion";
import { backdrop, C, display, mono, s } from "../theme";

const ease = Easing.bezier(0.16, 1, 0.3, 1);
const fade = (frame: number, start: number, dur = 18) =>
  interpolate(frame, [start, start + dur], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease });

const CHIPS: [string, string][] = [
  [C.fs, "Hand-written MCP server"],
  [C.db, "LangGraph agent, MCP-only"],
  [C.llm, "Multi-MCP: files + candidate DB"],
];

export const Intro: React.FC = () => {
  const frame = useSceneFrame();
  return (
    <AbsoluteFill style={{ ...backdrop, justifyContent: "center", paddingLeft: 170 }}>
      <div style={{ display: "flex", gap: 12, marginBottom: 46, rotate: "-12deg", width: 60 }}>
        {[C.fs, C.db, C.llm].map((c, i) => (
          <div key={c} style={{ width: 16, borderRadius: 6, background: c, height: [64, 50, 36][i], marginTop: [0, 7, 14][i],
            scale: `1 ${fade(frame, i * 5, 22)}`, transformOrigin: "bottom" }} />
        ))}
      </div>
      <div style={{ fontFamily: mono, fontSize: 28, color: C.db, letterSpacing: "0.12em", opacity: fade(frame, 8) }}>
        MILESTONE 2 · MODEL CONTEXT PROTOCOL
      </div>
      <div style={{ fontFamily: display, fontWeight: 800, fontSize: 132, lineHeight: 1, letterSpacing: "-0.035em", marginTop: 18,
        opacity: fade(frame, 14), translate: `0px ${(1 - fade(frame, 14)) * 30}px` }}>
        Resume Matcher <span style={{ fontWeight: 400, color: C.bone2 }}>over MCP</span>
      </div>
      <div style={{ fontSize: 38, color: C.bone2, marginTop: 26, maxWidth: 1350, lineHeight: 1.35, opacity: fade(frame, 26) }}>
        The Milestone 1 file tools become a standard MCP server, and the matching agent is rebuilt to reach files and data
        only through MCP clients.
      </div>
      <div style={{ display: "flex", gap: 18, marginTop: 58 }}>
        {CHIPS.map(([c, text], i) => (
          <div key={text} style={{ border: `2px solid ${c}`, color: C.bone, borderRadius: 999, padding: "14px 24px", fontSize: 24, whiteSpace: "nowrap",
            fontFamily: mono, display: "flex", alignItems: "center", gap: 14, opacity: fade(frame, s(1.6) + i * 8),
            translate: `0px ${(1 - fade(frame, s(1.6) + i * 8)) * 20}px` }}>
            <span style={{ width: 14, height: 14, borderRadius: 7, background: c }} />
            {text}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};
