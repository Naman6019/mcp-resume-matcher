"""Records the dashboard demo scenes as crisp 1920x1080 / 30 fps MP4 clips.

The dashboard must be running (python -m dashboard.app). Each scene drives the real UI
with Playwright; frames come from the Chrome DevTools screencast (much sharper than
Playwright's built-in recorder) and are assembled with ffmpeg at a constant 30 fps.

    python demo/record_demo.py                 # all scenes
    python demo/record_demo.py watch console   # some scenes

Output: video/public/clips/<scene>.mp4 plus <scene>.marks.json (timestamps of key moments,
used to place captions in the Remotion edit).
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.async_api import Page, async_playwright

PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "video" / "public" / "clips"
FRAMES = PROJECT / "demo" / "recordings"
URL = "http://127.0.0.1:8000"
W, H = 1920, 1080

CURSOR_JS = """
(() => {
  const install = () => {
    if (document.getElementById('fake-cursor')) return;
    const c = document.createElement('div');
    c.id = 'fake-cursor';
    c.innerHTML = `<svg width="30" height="30" viewBox="0 0 24 24"><path d="M4 2 L4 19 L8.6 14.8 L11.6 21.5 L14.4 20.3 L11.4 13.7 L17.6 13.4 Z"
      fill="#ffffff" stroke="#0f0e0c" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
    Object.assign(c.style, {position:'fixed', left:'0px', top:'0px', zIndex: 99999, pointerEvents:'none',
      transform:'translate(-4px,-2px)', filter:'drop-shadow(0 3px 6px rgba(0,0,0,.55))'});
    document.body.appendChild(c);
    addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', e => {
      const r = document.createElement('div');
      Object.assign(r.style, {position:'fixed', left:(e.clientX-22)+'px', top:(e.clientY-22)+'px', width:'44px', height:'44px',
        borderRadius:'50%', border:'3px solid #f2a93b', zIndex:99998, pointerEvents:'none', transition:'all .45s ease-out', opacity:'1'});
      document.body.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(1.8)'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 600);
    }, true);
  };
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', install); else install();
})();
"""


class Recorder:
    def __init__(self, page: Page, name: str) -> None:
        self.page, self.name = page, name
        self.dir = FRAMES / name
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)
        self.frames: list[tuple[float, Path]] = []
        self.marks: list[dict] = []
        self.t0: float | None = None
        self.cdp = None

    async def start(self) -> None:
        self.cdp = await self.page.context.new_cdp_session(self.page)

        async def on_frame(params: dict) -> None:
            ts = params["metadata"]["timestamp"]
            path = self.dir / f"{len(self.frames):06d}.jpg"
            path.write_bytes(base64.b64decode(params["data"]))
            self.frames.append((ts, path))
            if self.t0 is None:
                self.t0 = ts
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})

        self.cdp.on("Page.screencastFrame", lambda p: asyncio.ensure_future(on_frame(p)))
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": W, "maxHeight": H,
                                                     "everyNthFrame": 1})
        await asyncio.sleep(0.3)

    def mark(self, label: str) -> None:
        t = time.time() - (self.t0 or time.time())
        self.marks.append({"t": round(t, 2), "label": label})
        print(f"  [{self.name} {t:6.1f}s] {label}")

    async def stop(self) -> Path:
        await asyncio.sleep(0.5)
        await self.cdp.send("Page.stopScreencast")
        await asyncio.sleep(0.3)
        end = time.time()
        lines = ["ffconcat version 1.0"]
        for i, (ts, path) in enumerate(self.frames):
            nxt = self.frames[i + 1][0] if i + 1 < len(self.frames) else end
            lines += [f"file '{path.as_posix()}'", f"duration {max(0.001, nxt - ts):.4f}"]
        lines.append(f"file '{self.frames[-1][1].as_posix()}'")
        concat = self.dir / "list.ffconcat"
        concat.write_text("\n".join(lines), encoding="utf-8")
        OUT.mkdir(parents=True, exist_ok=True)
        out = OUT / f"{self.name}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                        "-vf", f"scale={W}:{H}:flags=lanczos,fps=30", "-c:v", "libx264", "-preset", "slow", "-crf", "17",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], check=True)
        (OUT / f"{self.name}.marks.json").write_text(json.dumps({"duration": round(end - self.t0, 2), "marks": self.marks}, indent=1))
        print(f"  -> {out.name}: {len(self.frames)} frames, {end - self.t0:.1f}s")
        return out


# ---------------------------------------------------------------- helpers

async def glide(page: Page, selector: str, pause: float = 0.35, click: bool = True, steps: int = 28) -> None:
    loc = page.locator(selector).first
    await loc.scroll_into_view_if_needed()
    box = await loc.bounding_box()
    if box is None:
        raise RuntimeError(f"not visible: {selector}")
    await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=steps)
    await asyncio.sleep(pause)
    if click:
        await page.mouse.down()
        await asyncio.sleep(0.06)
        await page.mouse.up()


async def hover_point(page: Page, x: float, y: float, pause: float = 0.6) -> None:
    await page.mouse.move(x, y, steps=30)
    await asyncio.sleep(pause)


async def wait_event(page: Page, js_condition: str, timeout: float = 240) -> None:
    await page.wait_for_function(js_condition, timeout=timeout * 1000, polling=200)


async def api(path: str, body: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(URL + path, json=body or {})
        return r.json()


RUN_DONE = "() => !document.querySelector('#run-btn').disabled"
RUN_BUSY = "() => document.querySelector('#run-btn').disabled"


async def fresh_page(browser) -> Page:
    ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
    await ctx.add_init_script(CURSOR_JS)
    page = await ctx.new_page()
    await api("/api/reset")
    await page.goto(URL)
    await page.wait_for_selector(".srv")
    await page.evaluate("document.fonts.ready")
    await asyncio.sleep(1.0)
    await page.mouse.move(960, 600)
    return page


async def configure(page: Page, llm: bool, watch: bool, seconds: int = 60) -> None:
    await page.evaluate(f"""() => {{
      document.querySelector('#llm-toggle').checked = {str(llm).lower()};
      document.querySelector('#watch-toggle').checked = {str(watch).lower()};
      document.querySelector('#watch-seconds').value = {seconds};
      document.querySelector('#job-select').value = 'senior-python-backend';
    }}""")


# ---------------------------------------------------------------- scenes

async def scene_tour(browser) -> None:
    page = await fresh_page(browser)
    rec = Recorder(page, "tour")
    await rec.start()
    rec.mark("dashboard loaded")
    await asyncio.sleep(2.5)
    await glide(page, "#pill-fs", click=False, pause=1.2)
    rec.mark("server pills")
    await glide(page, "#pill-db", click=False, pause=1.0)
    await glide(page, "#pill-http", click=False, pause=1.0)
    rec.mark("filesystem capabilities")
    await glide(page, ".srv.fs .srv-head", click=False, pause=1.5)
    for tool in ["list_files", "read_file", "write_file", "search_in_file"]:
        await glide(page, f'[data-tool="filesystem:{tool}"]', click=False, pause=0.7, steps=12)
    rec.mark("new MCP tools")
    for tool in ["watch_directory", "poll_watch_events", "stop_watch", "batch_process"]:
        await glide(page, f'[data-tool="filesystem:{tool}"]', click=False, pause=0.9, steps=12)
    rec.mark("resources")
    await glide(page, ".srv.fs .cap-group:nth-of-type(2) summary", click=False, pause=1.8)
    await glide(page, ".srv.fs details:nth-of-type(3) summary", pause=0.4)
    rec.mark("resource templates")
    await asyncio.sleep(2.5)
    await glide(page, ".srv.fs details:nth-of-type(3) summary", pause=0.3)
    rec.mark("candidate-db server")
    await glide(page, ".srv.db .srv-head", click=False, pause=2.0)
    await glide(page, '[data-tool="candidate_db:get_shortlist"]', click=False, pause=1.5)
    rec.mark("state machine")
    await hover_point(page, 900, 300, 1.5)
    await hover_point(page, 1300, 360, 1.5)
    await hover_point(page, 760, 420, 2.0)
    rec.mark("traffic inspector")
    await hover_point(page, 1640, 400, 2.0)
    await rec.stop()
    await page.context.close()


async def scene_run(browser) -> None:
    page = await fresh_page(browser)
    await configure(page, llm=False, watch=False)
    rec = Recorder(page, "run")
    await rec.start()
    await asyncio.sleep(1.0)
    await glide(page, "#job-select", click=False, pause=1.2)
    rec.mark("click run")
    await glide(page, "#run-btn", pause=0.5)
    await wait_event(page, RUN_DONE)
    rec.mark("run finished")
    await asyncio.sleep(2.0)
    await hover_point(page, 760, 600, 1.5)
    rec.mark("ranking explained")
    await glide(page, ".cand:nth-child(1) .chips", click=False, pause=2.0)
    await glide(page, ".cand:nth-child(7) .chips", click=False, pause=2.5)
    await page.locator("#rank-list").evaluate("el => el.scrollTo({top: 400, behavior: 'smooth'})")
    await asyncio.sleep(1.5)
    await glide(page, ".cand:nth-child(8) .chips", click=False, pause=2.0)
    await page.locator("#rank-list").evaluate("el => el.scrollTo({top: 0, behavior: 'smooth'})")
    rec.mark("open batch_process request")
    req = page.locator(".msg.request", has_text="batch_process").first
    await req.scroll_into_view_if_needed()
    await glide(page, '.msg.request:has-text("batch_process") .msg-row', pause=0.4)
    await asyncio.sleep(3.5)
    await glide(page, '.msg.request:has-text("batch_process") .msg-row', pause=0.3)
    rec.mark("open batch_process response")
    await glide(page, '.msg.response:has-text("batch_process") .msg-row', pause=0.4)
    await asyncio.sleep(1.0)
    await page.locator('.msg.response:has-text("batch_process") pre').evaluate("el => el.scrollTo({top: 0})")
    await asyncio.sleep(3.5)
    await glide(page, '.msg.response:has-text("batch_process") .msg-row', pause=0.3)
    rec.mark("progress notifications")
    await glide(page, '.msg.notification:has-text("notifications/progress") .msg-row', pause=0.4)
    await asyncio.sleep(3.0)
    await glide(page, '.msg.notification:has-text("notifications/progress") .msg-row', pause=0.3)
    await asyncio.sleep(1.0)
    await rec.stop()
    await page.context.close()


async def scene_watch(browser) -> None:
    page = await fresh_page(browser)
    await configure(page, llm=False, watch=True, seconds=55)
    rec = Recorder(page, "watch")
    await rec.start()
    await asyncio.sleep(0.8)
    await glide(page, "#watch-toggle + .sw", click=False, pause=1.2)
    rec.mark("run with watch")
    await glide(page, "#run-btn", pause=0.4)
    await wait_event(page, "() => !document.querySelector('#watch-strip').hidden && document.querySelector('.drop-btn')")
    rec.mark("watching")
    await asyncio.sleep(3.0)
    rec.mark("drop lena (txt)")
    await glide(page, '.drop-btn[data-name="resume_lena_park.txt"]', pause=0.4)
    await wait_event(page, "() => [...document.querySelectorAll('.cand .who b')].some(b => b.textContent === 'Lena Park')", 30)
    rec.mark("lena scored")
    await asyncio.sleep(1.5)
    await glide(page, ".cand:nth-child(1)", click=False, pause=3.0)
    rec.mark("drop tomas (docx)")
    await glide(page, '.drop-btn[data-name="resume_tomas_novak.docx"]', pause=0.4)
    await wait_event(page, "() => [...document.querySelectorAll('.cand .who b')].some(b => b.textContent === 'Tomas Novak')", 30)
    rec.mark("tomas scored")
    await asyncio.sleep(1.0)
    await page.locator("#rank-list").evaluate("el => el.scrollTo({top: el.scrollHeight, behavior: 'smooth'})")
    await asyncio.sleep(3.0)
    await page.locator("#rank-list").evaluate("el => el.scrollTo({top: 0, behavior: 'smooth'})")
    await asyncio.sleep(1.0)
    rec.mark("remove lena")
    await glide(page, '.drop-btn[data-name="resume_lena_park.txt"]', pause=0.4)
    await wait_event(page, "() => ![...document.querySelectorAll('.cand .who b')].some(b => b.textContent === 'Lena Park')", 30)
    rec.mark("lena removed")
    await asyncio.sleep(3.0)
    await glide(page, '.msg:has-text("list_changed") .msg-row', pause=0.4)
    rec.mark("list_changed notification")
    await asyncio.sleep(3.0)
    await glide(page, '.msg:has-text("list_changed") .msg-row', pause=0.3)
    await glide(page, '.msg.request:has-text("poll_watch_events") .msg-row', pause=0.4)
    rec.mark("poll_watch_events")
    await asyncio.sleep(3.5)
    await glide(page, '.msg.request:has-text("poll_watch_events") .msg-row', pause=0.3)
    rec.mark("waiting for timeout")
    await wait_event(page, RUN_DONE, 90)
    rec.mark("watch closed")
    await asyncio.sleep(2.5)
    await rec.stop()
    await page.context.close()
    await api("/api/reset")


async def scene_console(browser) -> None:
    page = await fresh_page(browser)
    rec = Recorder(page, "console")
    await rec.start()
    await asyncio.sleep(0.8)
    await glide(page, '.tab[data-tab="console"]', pause=0.4)
    rec.mark("console opened")
    await asyncio.sleep(2.5)
    for label in ["tools/list", "batch_process", "batch [ ]", "-32601 unknown method", "-32602 unknown tool",
                  "isError: bad args", "isError: sandbox", "-32002 missing resource", "-32600 invalid request",
                  "-32700 parse error"]:
        rec.mark(label)
        await glide(page, f'.preset:text-is("{label}")', pause=0.35, steps=20)
        await asyncio.sleep(1.4)
        await glide(page, "#rpc-send", pause=0.3, steps=20)
        await asyncio.sleep(3.4)
    await glide(page, '.tab[data-tab="traffic"]', pause=0.4)
    rec.mark("traffic shows HTTP")
    await asyncio.sleep(3.5)
    await rec.stop()
    await page.context.close()


async def scene_llm(browser) -> None:
    page = await fresh_page(browser)
    await configure(page, llm=True, watch=False)
    rec = Recorder(page, "llm")
    await rec.start()
    await asyncio.sleep(0.8)
    await glide(page, "#llm-toggle + .sw", click=False, pause=1.5)
    rec.mark("run with LLM notes")
    await glide(page, "#run-btn", pause=0.4)
    await wait_event(page, "() => document.querySelector('.assess')", 120)
    rec.mark("LLM writing notes")
    await wait_event(page, RUN_DONE, 400)
    rec.mark("notes done")
    await asyncio.sleep(1.0)
    await glide(page, ".cand:nth-child(1) .assess", click=False, pause=3.0)
    await glide(page, ".cand:nth-child(2) .assess", click=False, pause=3.0)
    rec.mark("ask tab")
    await glide(page, '.tab[data-tab="ask"]', pause=0.4)
    await asyncio.sleep(1.5)
    await glide(page, "#ask-input", click=False, pause=1.5)
    rec.mark("ask submitted")
    await glide(page, "#ask-btn", pause=0.4)
    await wait_event(page, "() => document.querySelector('#ask-answer').textContent.trim().length > 0", 400)
    rec.mark("answer")
    await asyncio.sleep(1.0)
    await glide(page, "#ask-answer", click=False, pause=5.0)
    await rec.stop()
    await page.context.close()


SCENES = {"tour": scene_tour, "run": scene_run, "watch": scene_watch, "console": scene_console, "llm": scene_llm}


async def main(names: list[str]) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True, args=["--force-color-profile=srgb", "--hide-scrollbars"])
        for name in names:
            print(f"recording {name}")
            await SCENES[name](browser)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or list(SCENES)))
