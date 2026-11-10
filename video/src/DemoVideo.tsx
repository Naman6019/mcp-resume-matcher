import { fade } from "@remotion/transitions/fade";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import React from "react";
import { ClipScene, ClipSceneProps, sceneDuration } from "./components/ClipScene";
import { Architecture } from "./scenes/Architecture";
import { consoleScene, llm, run, tour, watch } from "./scenes/clips";
import { Intro } from "./scenes/Intro";
import { Outro, OUTRO_SECONDS } from "./scenes/Outro";
import { Protocol, PROTOCOL_SECONDS } from "./scenes/Protocol";
import { Tests, TESTS_SECONDS } from "./scenes/Tests";
import { s } from "./theme";

const T = 15; // cross-fade frames

type Item = { name: string; seconds: number; node: React.ReactNode };
const clip = (name: string, p: ClipSceneProps): Item => ({ name, seconds: sceneDuration(p.segments), node: <ClipScene {...p} /> });

export const TIMELINE: Item[] = [
  { name: "Intro", seconds: 9, node: <Intro /> },
  { name: "Architecture", seconds: 30, node: <Architecture /> },
  { name: "Protocol", seconds: PROTOCOL_SECONDS, node: <Protocol /> },
  clip("Dashboard tour", tour),
  clip("Run", run),
  clip("Watch directory", watch),
  clip("Raw console", consoleScene),
  clip("LLM + ask", llm),
  { name: "Tests", seconds: TESTS_SECONDS, node: <Tests /> },
  { name: "Outro", seconds: OUTRO_SECONDS, node: <Outro /> },
];

export const TOTAL_FRAMES = TIMELINE.reduce((a, it) => a + s(it.seconds), 0) - T * (TIMELINE.length - 1);

/** Scene start times in seconds, for the voice-over script. */
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
        </TransitionSeries.Sequence>
      );
      return i === 0 ? [seq] : [
        <TransitionSeries.Transition key={`t-${it.name}`} presentation={fade()} timing={linearTiming({ durationInFrames: T })} />,
        seq,
      ];
    })}
  </TransitionSeries>
);
