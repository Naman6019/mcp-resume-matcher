"""Cut a slow-motion replay out of a recorded scene, using the raw screencast frames.

    python demo/make_slowmo.py run 2.6 4.6 6     # scene, start s, end s, slow-down factor

The screencast captured frames at their real timestamps (often 60+ fps while the UI is
animating), so the replay shows genuinely captured frames, just held longer. Output:
video/public/clips/<scene>_slowmo.mp4 (30 fps).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def main(scene: str, start: float, end: float, factor: float) -> None:
    lines = (PROJECT / "demo" / "recordings" / scene / "list.ffconcat").read_text(encoding="utf-8").splitlines()
    frames: list[tuple[str, float]] = []
    for i, line in enumerate(lines):
        if line.startswith("file ") and i + 1 < len(lines) and lines[i + 1].startswith("duration "):
            frames.append((line, float(lines[i + 1].split()[1])))
    out_lines, t = ["ffconcat version 1.0"], 0.0
    for file_line, dur in frames:
        if start <= t + dur and t <= end:
            out_lines += [file_line, f"duration {dur * factor:.4f}"]
        t += dur
    out_lines.append(out_lines[-2])
    concat = PROJECT / "demo" / "recordings" / scene / "slowmo.ffconcat"
    concat.write_text("\n".join(out_lines), encoding="utf-8")
    out = PROJECT / "video" / "public" / "clips" / f"{scene}_slowmo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-vf", "fps=30", "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p", str(out)], check=True)
    print(f"{out.name}: {len(out_lines) // 2} source frames, {(end - start) * factor:.1f}s")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]))
