import React from "react";
import { Composition, Folder } from "remotion";
import { ClipScene, sceneDuration } from "./components/ClipScene";
import { DemoVideo, TOTAL_FRAMES } from "./DemoVideo";
import { Architecture } from "./scenes/Architecture";
import { consoleScene, llm, run, tour, watch } from "./scenes/clips";
import { Intro } from "./scenes/Intro";
import { Outro, OUTRO_SECONDS } from "./scenes/Outro";
import { Protocol, PROTOCOL_SECONDS } from "./scenes/Protocol";
import { Tests, TESTS_SECONDS } from "./scenes/Tests";
import { FPS, s } from "./theme";

const size = { width: 1920, height: 1080, fps: FPS };

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="DemoVideo" component={DemoVideo} durationInFrames={TOTAL_FRAMES} {...size} />
    <Folder name="Scenes">
      <Composition id="Intro" component={Intro} durationInFrames={s(9)} {...size} />
      <Composition id="Architecture" component={Architecture} durationInFrames={s(30)} {...size} />
      <Composition id="Protocol" component={Protocol} durationInFrames={s(PROTOCOL_SECONDS)} {...size} />
      {([["Tour", tour], ["Run", run], ["Watch", watch], ["Console", consoleScene], ["Llm", llm]] as const).map(([id, p]) => (
        <Composition key={id} id={id} component={ClipScene} defaultProps={p} durationInFrames={s(sceneDuration(p.segments))} {...size} />
      ))}
      <Composition id="Tests" component={Tests} durationInFrames={s(TESTS_SECONDS)} {...size} />
      <Composition id="Outro" component={Outro} durationInFrames={s(OUTRO_SECONDS)} {...size} />
    </Folder>
  </>
);
