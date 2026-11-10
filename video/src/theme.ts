import { loadFont as loadDisplay } from "@remotion/google-fonts/BricolageGrotesque";
import { loadFont as loadMono } from "@remotion/google-fonts/IBMPlexMono";
import { loadFont as loadSans } from "@remotion/google-fonts/IBMPlexSans";

export const display = loadDisplay("normal", { weights: ["400", "600", "800"], subsets: ["latin"] }).fontFamily;
export const mono = loadMono("normal", { weights: ["400", "500", "600"], subsets: ["latin"] }).fontFamily;
export const sans = loadSans("normal", { weights: ["400", "500", "600"], subsets: ["latin"] }).fontFamily;

/* Same palette as the dashboard: one signal colour per MCP server. */
export const C = {
  ink: "#0f0e0c",
  ink2: "#16140f",
  panel: "#1a1813",
  panel2: "#211e18",
  line: "#2f2b23",
  line2: "#3d382e",
  bone: "#ece4d4",
  bone2: "#b9b09e",
  mute: "#7d7566",
  fs: "#4fd1c5",
  db: "#f2a93b",
  llm: "#ff7a6b",
  ok: "#9fd36a",
  err: "#ff5d5d",
};

export const FPS = 30;
export const s = (seconds: number) => Math.round(seconds * FPS);

export const backdrop: React.CSSProperties = {
  background: `radial-gradient(1200px 600px at 78% -10%, rgba(242,169,59,.08), transparent 60%),
    radial-gradient(900px 500px at 8% 110%, rgba(79,209,197,.08), transparent 60%),
    radial-gradient(circle at 1px 1px, rgba(236,228,212,.05) 1px, transparent 0) 0 0 / 24px 24px, ${C.ink}`,
  color: C.bone,
  fontFamily: sans,
};
