import { Audio } from "@remotion/media";
import { fade } from "@remotion/transitions/fade";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import React from "react";
import { Sequence, staticFile } from "remotion";
import { ClipScene, ClipSceneProps, sceneDuration } from "./components/ClipScene";
import { Architecture } from "./scenes/Architecture";
import { consoleScene, llm, run, tour, watch } from "./scenes/clips";
import { Intro } from "./scenes/Intro";
import { Outro, OUTRO_SECONDS } from "./scenes/Outro";
import { Protocol, PROTOCOL_SECONDS } from "./scenes/Protocol";
import { Tests, TESTS_SECONDS } from "./scenes/Tests";
import { s } from "./theme";
import { TimeScaled } from "./timescale";
import VO from "./vo.json";

const T = 15;       // cross-fade, frames
const LEAD = 0.45;  // seconds of picture before the narration starts
const TAIL = 0.45;  // ... and after it ends
const PACE_MIN = 0.75, PACE_MAX = 1.3;   // how far a recorded scene may be slowed / sped up

type Voice = { file: string; seconds: number };
const voice = (name: string): Voice | undefined => (VO as Record<string, Voice>)[name];

type Item = { name: string; seconds: number; node: React.ReactNode; voice?: Voice };

/** An animated scene: its timeline is stretched (or compressed) to fit the narration. */
const animated = (name: string, base: number, el: React.ReactNode): Item => {
  const v = voice(name);
  const seconds = v ? v.seconds + LEAD + TAIL : base;
  return { name, seconds, voice: v, node: <TimeScaled scale={seconds / base}>{el}</TimeScaled> };
};

/** A recorded scene: played a little faster or slower so it matches the narration. */
const recorded = (name: string, p: ClipSceneProps): Item => {
  const base = sceneDuration(p.segments);
  const v = voice(name);
  const wanted = v ? v.seconds + LEAD + TAIL : base;
  const pace = Math.min(PACE_MAX, Math.max(PACE_MIN, base / wanted));
  return { name, seconds: Math.max(base / pace, wanted), voice: v, node: <ClipScene {...p} pace={pace} /> };
};

export const TIMELINE: Item[] = [
  animated("Intro", 9, <Intro />),
  animated("Architecture", 30, <Architecture />),
  animated("Protocol", PROTOCOL_SECONDS, <Protocol />),
  recorded("Dashboard tour", tour),
  recorded("Run", run),
  recorded("Watch directory", watch),
  recorded("Raw console", consoleScene),
  recorded("LLM + ask", llm),
  animated("Tests", TESTS_SECONDS, <Tests />),
  animated("Outro", OUTRO_SECONDS, <Outro />),
];

export const TOTAL_FRAMES = TIMELINE.reduce((a, it) => a + s(it.seconds), 0) - T * (TIMELINE.length - 1);

/** Scene start times in seconds (for the script / chapters). */
export const sceneStarts = () => {
  let acc = 0;
  return TIMELINE.map((it) => {
    const start = acc;
    acc += s(it.seconds) - T;
    return { name: it.name, start: start / 30, seconds: it.seconds };
  });
};

export const DemoVideo: React.FC = () => (
  <TransitionSeries>
    {TIMELINE.flatMap((it, i) => {
      const seq = (
        <TransitionSeries.Sequence key={it.name} name={it.name} durationInFrames={s(it.seconds)}>
          {it.node}
          {it.voice ? (
            <Sequence from={s(LEAD)} name={`voice: ${it.name}`} layout="none">
              <Audio src={staticFile(it.voice.file)} />
            </Sequence>
          ) : null}
        </TransitionSeries.Sequence>
      );
      return i === 0 ? [seq] : [
        <TransitionSeries.Transition key={`t-${it.name}`} presentation={fade()} timing={linearTiming({ durationInFrames: T })} />,
        seq,
      ];
    })}
  </TransitionSeries>
);
