import { Video } from "@remotion/media";
import React from "react";
import { AbsoluteFill, Easing, interpolate, Sequence, staticFile, useCurrentFrame } from "remotion";
import { C, display, FPS, mono, s, sans } from "../theme";

/** A piece of a recorded clip. `from`/`to` are seconds in the source file. */
/** `badge` may contain "{x}", replaced by the effective speed-up (fast-forward) or, with
 *  `slowmo`, the effective slow-down factor of a replay clip. */
export type Segment = { src: string; from: number; to: number; rate?: number; badge?: string; slowmo?: number };
/** Times below are in *scene* seconds (use `sceneTime()` to convert from clip time). */
export type Caption = { at: number; text: string };
export type Zoom = { from: number; to: number; x: number; y: number; scale: number };

export type ClipSceneProps = {
  chapter: string;
  title: string;
  segments: Segment[];
  captions: Caption[];
  zooms?: Zoom[];
  /** Playback speed of the whole scene (fit to narration): 0.9 = 10% slower. */
  pace?: number;
};

const segDuration = (g: Segment) => (g.to - g.from) / (g.rate ?? 1);
export const sceneDuration = (segments: Segment[]) => segments.reduce((a, g) => a + segDuration(g), 0);

/** Scene seconds at which source time `t` of `src` is shown. */
export const sceneTime = (segments: Segment[], src: string, t: number): number => {
  let acc = 0;
  for (const g of segments) {
    if (g.src === src && t >= g.from && t <= g.to) return acc + (t - g.from) / (g.rate ?? 1);
    acc += segDuration(g);
  }
  throw new Error(`time ${t} of ${src} is not in any segment`);
};

const VW = 1690; // on-screen width of the recording
const SCALE = VW / 1920;
const VH = 1080 * SCALE;
const VX = (1920 - VW) / 2;
const VY = 84;

const ease = Easing.bezier(0.45, 0, 0.2, 1);

const fmtX = (x: number) => `${Number(x.toFixed(1))}×`;

export const ClipScene: React.FC<ClipSceneProps> = ({ chapter, title, segments, captions, zooms = [], pace = 1 }) => {
  const frame = useCurrentFrame();
  const t = (frame / FPS) * pace; // "design time": captions and zooms are keyed to pace 1

  // ---- zoom: pick the strongest active zoom, ease in/out over 0.7 s
  let zx = 0, zy = 0, zs = 1;
  let best = 0;
  for (const z of zooms) {
    const p = interpolate(t, [z.from, z.from + 0.7, z.to - 0.7, z.to], [0, 1, 1, 0], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease,
    });
    if (p > best) {
      best = p;
      zs = 1 + (z.scale - 1) * p;
      zx = Math.min(0, Math.max(1920 - 1920 * zs, 960 - z.x * zs));
      zy = Math.min(0, Math.max(1080 - 1080 * zs, 540 - z.y * zs));
    }
  }

  // ---- active segment (for the speed badge)
  let acc = 0;
  let active: Segment | undefined;
  const sequences = segments.map((g, i) => {
    const start = acc;
    acc += segDuration(g);
    if (t >= start && t < acc) active = g;
    return (
      <Sequence key={i} from={s(start / pace)} durationInFrames={Math.max(1, s(segDuration(g) / pace))} name={`${g.src} ${g.from}-${g.to}s`}>
        <Video src={staticFile(`clips/${g.src}.mp4`)} trimBefore={Math.round(g.from * FPS)} trimAfter={Math.round(g.to * FPS)}
          playbackRate={(g.rate ?? 1) * pace} muted style={{ width: 1920, height: 1080 }} />
      </Sequence>
    );
  });

  // ---- caption
  const current = [...captions].reverse().find((c) => c.at <= t);
  const capOpacity = current ? interpolate(t, [current.at, current.at + 0.35], [0, 1], { extrapolateRight: "clamp" }) : 0;

  const intro = interpolate(frame, [0, 12], [0, 1], { extrapolateRight: "clamp", easing: ease });

  return (
    <AbsoluteFill style={{ background: C.ink, fontFamily: sans }}>
      {/* header */}
      <div style={{ position: "absolute", left: VX, top: 22, right: VX, display: "flex", alignItems: "baseline", gap: 18, opacity: intro }}>
        <span style={{ fontFamily: mono, fontWeight: 600, fontSize: 24, color: C.db }}>{chapter}</span>
        <span style={{ fontFamily: display, fontWeight: 800, fontSize: 36, letterSpacing: "-0.02em", color: C.bone }}>{title}</span>
        <span style={{ marginLeft: "auto", fontFamily: mono, fontSize: 18, color: C.mute }}>live recording of the dashboard</span>
      </div>

      {/* the recording */}
      <div style={{ position: "absolute", left: VX, top: VY, width: VW, height: VH, overflow: "hidden", borderRadius: 14,
        border: `1px solid ${C.line2}`, boxShadow: "0 30px 80px rgba(0,0,0,.6)", scale: String(0.97 + 0.03 * intro) }}>
        <div style={{ width: 1920, height: 1080, transformOrigin: "0 0", scale: String(SCALE) }}>
          <div style={{ width: 1920, height: 1080, transformOrigin: "0 0", translate: `${zx}px ${zy}px`, scale: String(zs) }}>
            {sequences}
          </div>
        </div>
        {active?.badge ? (
          <div style={{ position: "absolute", right: 18, top: 16, background: "rgba(15,14,12,.88)", border: `1px solid ${C.db}`,
            color: C.db, fontFamily: mono, fontWeight: 600, fontSize: 22, padding: "8px 14px", borderRadius: 10 }}>
            {active.badge.replace("{x}", fmtX(active.slowmo ? active.slowmo / pace : (active.rate ?? 1) * pace))}
          </div>
        ) : null}
      </div>

      {/* caption */}
      <div style={{ position: "absolute", left: VX, right: VX, top: VY + VH + 14, textAlign: "center", fontSize: 27,
        color: C.bone, opacity: capOpacity, translate: `0px ${(1 - capOpacity) * 8}px` }}>
        {current?.text}
      </div>
    </AbsoluteFill>
  );
};
