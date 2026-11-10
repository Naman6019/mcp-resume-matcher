"""Live dashboard: watch the agent and both MCP servers work, message by message.

    python -m dashboard.app            # then open http://127.0.0.1:8000

The dashboard is just another MCP *host*: it owns one ``MCPHub`` (stdio sessions to the
filesystem and candidate-db servers), runs ``ResumeMatchingAgent`` on demand and streams
everything -- JSON-RPC traffic, server notifications, LangGraph node transitions -- to the
browser over Server-Sent Events. A second copy of the filesystem server runs on the
Streamable HTTP transport and backs the raw JSON-RPC console.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from matching_agent import DB, ResumeMatchingAgent, make_llm, mermaid_diagram  # noqa: E402
from mcp_client import MCPHub, default_server_specs  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
INCOMING = PROJECT / "demo_assets" / "incoming"
RESUMES = PROJECT / "resumes"
HTTP_PORT = int(os.environ.get("DASHBOARD_MCP_HTTP_PORT", "8765"))
PREVIEW_CHARS = 2600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Bus:
    """Fan-out of dashboard events to every connected browser (plus a replay buffer)."""

    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()
        self.history: list[dict[str, Any]] = []

    def publish(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", _now())
        self.history = (self.history + [event])[-400:]
        for q in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)


class TrafficTracker:
    """Turns raw JSON-RPC messages into compact rows (kind, method, id, latency, preview)."""

    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self.pending: dict[tuple[str, Any], tuple[float, str]] = {}

    def __call__(self, server: str, direction: str, msg: dict[str, Any], transport: str = "stdio") -> None:
        kind, method, ms = "other", msg.get("method"), None
        key = (server + transport, msg.get("id"))
        if method == "tools/call":
            method = f"tools/call · {(msg.get('params') or {}).get('name')}"
        if "method" in msg and "id" in msg:
            kind = "request"
            self.pending[key] = (time.perf_counter(), method)
        elif "method" in msg:
            kind = "notification"
        elif "result" in msg or "error" in msg:
            kind = "error" if "error" in msg or (msg.get("result") or {}).get("isError") else "response"
            if key in self.pending:
                t0, method = self.pending.pop(key)
                ms = round((time.perf_counter() - t0) * 1000, 1)
        text = json.dumps(msg, indent=2, ensure_ascii=False)
        self.bus.publish({"type": "traffic", "server": server, "transport": transport, "dir": direction,
                          "kind": kind, "method": method, "id": msg.get("id"), "ms": ms, "bytes": len(text),
                          "preview": text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "\n  …"})


class State:
    hub: MCPHub | None = None
    bus = Bus()
    tracker: TrafficTracker | None = None
    discovery: dict[str, Any] = {}
    jobs: list[dict[str, Any]] = []
    run_task: asyncio.Task | None = None
    last_ranking: list[dict[str, Any]] = []
    http_proc: subprocess.Popen | None = None
    http_session: str | None = None


S = State()


async def _refresh_discovery() -> None:
    if S.hub is not None:
        with contextlib.suppress(Exception):
            S.discovery = await S.hub.discover()
            S.bus.publish({"type": "discovery", "discovery": S.discovery})


async def _on_notification(server: str, method: str, params: dict[str, Any]) -> None:
    S.bus.publish({"type": "notification", "server": server, "method": method, "params": params})
    if method == "notifications/resources/list_changed":
        # Correct client behaviour: the server said its resource list changed, so re-list it.
        # Run it as a task -- awaiting a request inside the session's message handler would deadlock.
        asyncio.get_running_loop().create_task(_refresh_discovery())


def _start_http_sidecar() -> None:
    try:
        socket.create_connection(("127.0.0.1", HTTP_PORT), timeout=0.2).close()
        return  # something is already serving there (e.g. a previous run); reuse it
    except OSError:
        pass
    logs = PROJECT / "logs"
    logs.mkdir(exist_ok=True)
    S.http_proc = subprocess.Popen(
        [sys.executable, str(PROJECT / "filesystem_mcp_server.py"), "--transport", "http", "--port", str(HTTP_PORT),
         "--root", str(PROJECT), "--log-level", "warning"],
        stdout=subprocess.DEVNULL, stderr=open(logs / "filesystem-mcp-http.stderr.log", "a"))  # noqa: SIM115


async def _http_rpc(raw: str | bytes, session: str | None) -> tuple[int, dict[str, str], Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-11-25"}
    if session:
        headers["Mcp-Session-Id"] = session
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"http://127.0.0.1:{HTTP_PORT}/mcp", content=raw, headers=headers)
    body = r.json() if r.content else None
    return r.status_code, dict(r.headers), body


async def _ensure_http_session() -> str:
    if S.http_session:
        return S.http_session
    for _ in range(50):
        try:
            init = {"jsonrpc": "2.0", "id": "console-init", "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                               "clientInfo": {"name": "dashboard-console", "version": "1"}}}
            status, headers, _ = await _http_rpc(json.dumps(init), None)
            S.http_session = headers.get("mcp-session-id")
            await _http_rpc(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), S.http_session)
            return S.http_session or ""
        except httpx.HTTPError:
            await asyncio.sleep(0.2)
    raise HTTPException(503, "HTTP MCP server did not start")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    S.tracker = TrafficTracker(S.bus)
    _start_http_sidecar()
    hub = MCPHub(default_server_specs(), on_traffic=S.tracker, on_notification=_on_notification,
                 client_name="mcp-dashboard")
    async with hub:
        S.hub = hub
        S.discovery = await hub.discover()
        S.jobs = (await hub.call_tool(DB, "list_jobs"))["jobs"]
        with contextlib.suppress(Exception):
            await hub.session("filesystem").set_logging_level("info")
        yield
        if S.run_task and not S.run_task.done():
            S.run_task.cancel()
    if S.http_proc:
        S.http_proc.terminate()


app = FastAPI(title="MCP Resume Matcher dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


@app.middleware("http")
async def local_only(request: Request, call_next):
    """The dashboard can drive the agent, drop files and send raw JSON-RPC, so only serve it
    to this machine: reject foreign Host headers (DNS rebinding) and cross-site Origins (CSRF)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
    origin = request.headers.get("origin")
    if host not in _LOCAL_HOSTS:
        return JSONResponse({"detail": "Host not allowed"}, status_code=403)
    if origin and origin.split("://", 1)[-1].rsplit(":", 1)[0].lower() not in _LOCAL_HOSTS:
        return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
    return await call_next(request)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
async def state() -> dict[str, Any]:
    return {
        "discovery": S.discovery, "jobs": S.jobs, "running": bool(S.run_task and not S.run_task.done()),
        "ranking": S.last_ranking, "incoming": sorted(p.name for p in INCOMING.iterdir() if p.is_file()),
        "dropped": sorted(p.name for p in INCOMING.iterdir() if (RESUMES / p.name).exists()),
        "llm": {"provider": os.environ.get("LLM_PROVIDER", "ollama"), "model": os.environ.get("OLLAMA_MODEL", "llama3.1:8b")},
        "http_port": HTTP_PORT, "mermaid": mermaid_diagram(),
    }


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    S.bus.subscribers.add(q)

    async def stream():
        try:
            yield "retry: 1000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            S.bus.subscribers.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class RunRequest(BaseModel):
    job_id: str
    use_llm: bool = False
    watch_seconds: float = 0
    assess_top: int = 3


@app.post("/api/run")
async def run(req: RunRequest) -> dict[str, Any]:
    if S.run_task and not S.run_task.done():
        raise HTTPException(409, "A run is already in progress")
    assert S.hub is not None
    llm = make_llm(os.environ.get("LLM_PROVIDER", "ollama")) if req.use_llm else None
    agent = ResumeMatchingAgent(S.hub, llm=llm, on_event=lambda e: S.bus.publish({**e, "type": "agent." + e["type"]}),
                                assess_top=req.assess_top, watch_poll_seconds=2.0)

    async def go() -> None:
        try:
            final = await agent.run(job_id=req.job_id, watch_seconds=req.watch_seconds or None)
            S.last_ranking = final.get("ranking") or []
        except asyncio.CancelledError:
            S.bus.publish({"type": "agent.error", "message": "run cancelled"})
            raise
        except Exception as exc:  # noqa: BLE001
            S.bus.publish({"type": "agent.error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            S.bus.publish({"type": "run_finished"})

    S.run_task = asyncio.create_task(go())
    return {"started": True}


@app.post("/api/cancel")
async def cancel() -> dict[str, Any]:
    if S.run_task and not S.run_task.done():
        S.run_task.cancel()
        return {"cancelled": True}
    return {"cancelled": False}


class FileRequest(BaseModel):
    name: str


def _incoming(name: str) -> Path:
    src = INCOMING / Path(name).name
    if not src.is_file():
        raise HTTPException(404, f"No demo resume {name!r}")
    return src


@app.post("/api/drop")
async def drop(req: FileRequest) -> dict[str, Any]:
    """Simulate a resume arriving from outside (email, ATS export...). Deliberately *not* via
    MCP: the point is that the filesystem server's watcher notices it on its own."""
    src = _incoming(req.name)
    shutil.copy(src, RESUMES / src.name)
    S.bus.publish({"type": "file_dropped", "name": src.name})
    return {"dropped": src.name}


@app.post("/api/remove")
async def remove(req: FileRequest) -> dict[str, Any]:
    target = RESUMES / _incoming(req.name).name
    if target.exists():
        target.unlink()
        S.bus.publish({"type": "file_removed", "name": target.name})
    return {"removed": target.name}


class AskRequest(BaseModel):
    question: str


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict[str, Any]:
    assert S.hub is not None
    llm = make_llm(os.environ.get("LLM_PROVIDER", "ollama"))
    agent = ResumeMatchingAgent(S.hub, llm=llm, on_event=lambda e: S.bus.publish({**e, "type": "agent." + e["type"]}))
    try:
        answer = await agent.ask(req.question)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"LLM error: {exc}") from exc
    return {"answer": answer}


class RpcRequest(BaseModel):
    raw: str


@app.post("/api/rpc")
async def rpc(req: RpcRequest) -> dict[str, Any]:
    """Raw JSON-RPC console against the filesystem server's Streamable HTTP transport."""
    sid = await _ensure_http_session()
    with contextlib.suppress(json.JSONDecodeError):
        S.tracker("filesystem", "out", json.loads(req.raw), transport="http")  # type: ignore[misc]
    t0 = time.perf_counter()
    status, headers, body = await _http_rpc(req.raw.encode("utf-8"), sid)
    if body is not None:
        for item in body if isinstance(body, list) else [body]:
            S.tracker("filesystem", "in", item, transport="http")  # type: ignore[misc]
    return {"status": status, "ms": round((time.perf_counter() - t0) * 1000, 1), "body": body,
            "session": sid[:8] + "…"}


@app.post("/api/reset")
async def reset() -> dict[str, Any]:
    removed = []
    for p in INCOMING.iterdir():
        if (RESUMES / p.name).exists():
            (RESUMES / p.name).unlink()
            removed.append(p.name)
    S.last_ranking = []
    return {"removed": removed}


def main() -> None:
    import uvicorn

    with contextlib.suppress(ImportError):
        from dotenv import load_dotenv
        load_dotenv(PROJECT / ".env")
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    uvicorn.run("dashboard.app:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
