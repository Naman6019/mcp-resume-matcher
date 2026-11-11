import React, { createContext, useContext } from "react";
import { useCurrentFrame } from "remotion";

/** Lets an animated scene be stretched to fit its narration: every animation in the
 *  scene reads time through useSceneFrame(), which divides the real frame by `scale`. */
const TimeScale = createContext(1);

export const TimeScaled: React.FC<{ scale: number; children: React.ReactNode }> = ({ scale, children }) => (
  <TimeScale.Provider value={scale}>{children}</TimeScale.Provider>
);

export const useSceneFrame = (): number => {
  const frame = useCurrentFrame();
  const scale = useContext(TimeScale);
  return frame / scale;
};
