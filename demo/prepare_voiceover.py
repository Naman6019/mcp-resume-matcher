"""Clean the recorded voice-over clips for the Remotion edit.

    python demo/prepare_voiceover.py

Reads video/public/vo/*.mp4 (one recording per script section; only the audio is used),
and writes video/public/vo/clean/<section>.m4a plus video/src/vo.json with each clip's
duration. Processing: trim leading/trailing silence, shorten pauses longer than 0.6 s to
0.45 s, high-pass at 80 Hz, speed up by --tempo (default 1.06; pitch is preserved) so the
video stays within 6 minutes, and loudness-normalise to -16 LUFS (speech level for video).
Use --tempo 1.0 to keep the original pace.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SRC = PROJECT / "video" / "public" / "vo"
OUT = SRC / "clean"

# recording file stem -> scene name used in video/src/DemoVideo.tsx
SECTIONS = {
    "intro": "Intro",
    "architecture": "Architecture",
    "on-the-wire": "Protocol",
    "dashboard-tour": "Dashboard tour",
    "one-run": "Run",
    "watch-directory": "Watch directory",
    "json-rpc and error handling": "Raw console",
    "llm notes": "LLM + ask",
    "tests": "Tests",
    "deliverables": "Outro",
}

FILTER = (
    "highpass=f=80,"
    # trim the start, shorten long pauses, then trim the end (reverse trick)
    "silenceremove=start_periods=1:start_threshold=-40dB:start_silence=0.08:"
    "stop_periods=-1:stop_duration=0.6:stop_threshold=-40dB:stop_silence=0.45,"
    "areverse,silenceremove=start_periods=1:start_threshold=-40dB:start_silence=0.15,areverse,"
    "atempo={tempo},loudnorm=I=-16:TP=-1.5:LRA=11"
)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tempo", type=float, default=1.06)
    tempo = ap.parse_args().tempo
    OUT.mkdir(exist_ok=True)
    result = {}
    for stem, scene in SECTIONS.items():
        src = SRC / f"{stem}.mp4"
        dst = OUT / f"{stem.replace(' ', '-')}.m4a"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-af", FILTER.format(tempo=tempo), "-ar", "48000",
                        "-c:a", "aac", "-b:a", "192k", str(dst)], check=True)
        before, after = duration(src), duration(dst)
        result[scene] = {"file": f"vo/clean/{dst.name}", "seconds": round(after, 3)}
        print(f"{scene:<16} {before:6.2f}s -> {after:6.2f}s  {dst.name}")
    (PROJECT / "video" / "src" / "vo.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"total {sum(v['seconds'] for v in result.values()):.1f}s")


if __name__ == "__main__":
    main()
