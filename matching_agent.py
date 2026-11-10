"""matching_agent -- LangGraph resume-matching agent that works only through MCP servers.

The Milestone 1 agent imported ``fs_tools`` and called the file system directly. This version
has **no direct file-system or database access** (``tests/test_architecture.py`` enforces it):

    filesystem MCP server   list_files, batch_process, write_file, watch_directory, poll_watch_events
    candidate-db MCP server get_job, list_jobs, save_match_results, get_shortlist

Workflow (a LangGraph state machine; ``--diagram`` prints it as Mermaid):

    discover_servers -> load_job -> list_resumes -> batch_read -> score_candidates
        -> [assess_with_llm] -> rank_and_report -> persist_results -> END
                                                       \\-> watch_for_resumes --(new files)--> batch_read
                                                                   ^   \\--(deleted)--> rank_and_report
                                                                   \\--(nothing yet)--/      \\--(timeout)--> END

Scores are deterministic (``resume_scoring``); an optional LLM only writes the short
assessments. A separate ``ask`` mode gives an LLM every MCP tool from both servers.

Examples::

    python matching_agent.py --list-jobs
    python matching_agent.py --job senior-python-backend
    python matching_agent.py --job senior-python-backend --watch --watch-seconds 120
    python matching_agent.py --job ml-engineer-nlp --llm ollama --model ornith-1.5:9b
    python matching_agent.py --ask "Who should we interview for platform-sre?" --llm ollama
    python matching_agent.py --diagram
"""

from __future__ import annotations

import argparse
import asyncio
import json
import operator
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from mcp_client import MCPHub, MCPToolError, default_server_specs
from resume_scoring import JobSpec, extract_skills, rank, render_report, score_resume

FS = "filesystem"
DB = "candidate_db"
READABLE = ("pdf", "docx", "txt")
REQUIRED_TOOLS = {FS: {"list_files", "batch_process", "write_file", "watch_directory", "poll_watch_events"},
                  DB: {"get_job", "save_match_results"}}

EventFn = Callable[[dict[str, Any]], Any]


class MatchState(TypedDict, total=False):
    job_id: str | None
    job_text: str | None
    job: dict[str, Any]
    resume_dir: str
    mode: str                       # "once" | "watch"
    run_id: str
    servers: dict[str, Any]         # discovery summary
    files: list[str]                # every resume currently known
    pending: list[str]              # resumes to (re)process this round
    documents: dict[str, str]       # path -> extracted text
    scores: dict[str, dict]         # path -> CandidateScore.to_dict()
    ranking: list[dict]
    report_path: str
    watch: dict[str, Any]           # watch_id, next_seq, deadline, action, arrivals
    removed: list[str]              # resumes deleted while watching (dropped from the DB too)
    arrivals: Annotated[list[dict], operator.add]
    errors: Annotated[list[dict], operator.add]
    fatal: bool
    rounds: int


# --------------------------------------------------------------------------- #
# LLM (optional)
# --------------------------------------------------------------------------- #

def make_llm(provider: str | None, model: str | None = None) -> Any:
    """Chat model for assessments / ask mode. ``None`` or 'none' disables the LLM."""
    if not provider or provider == "none":
        return None
    from langchain_openai import ChatOpenAI

    if provider == "ollama":
        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        return ChatOpenAI(base_url=base if base.endswith("/v1") else base + "/v1", api_key="ollama",
                          model=model or os.environ.get("OLLAMA_MODEL", "llama3.1:8b"), temperature=0.2,
                          timeout=180, max_retries=1, extra_body={"reasoning_effort": "none"})
    if provider == "openai":
        return ChatOpenAI(model=model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), temperature=0.2, timeout=60)
    if provider == "openrouter":
        return ChatOpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"],
                          model=model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"), temperature=0.2)
    raise ValueError(f"Unknown LLM provider {provider!r} (use ollama, openai, openrouter or none)")


def _message_text(msg: Any) -> str:
    content = getattr(msg, "content", msg)
    if isinstance(content, list):
        content = " ".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    return str(content).strip()


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #

class ResumeMatchingAgent:
    def __init__(self, hub: MCPHub, llm: Any = None, on_event: EventFn | None = None, assess_top: int = 3,
                 watch_poll_seconds: float = 5.0) -> None:
        self.hub = hub
        self.llm = llm
        self.on_event = on_event
        self.assess_top = assess_top
        self.watch_poll_seconds = watch_poll_seconds
        self.graph = self._build_graph()

    # ---------------- events ---------------- #

    async def emit(self, kind: str, **data: Any) -> None:
        if self.on_event is None:
            return
        res = self.on_event({"type": kind, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **data})
        if hasattr(res, "__await__"):
            await res

    def _node(self, name: str, fn: Callable[[MatchState], Awaitable[dict[str, Any]]]) -> Callable[[MatchState], Awaitable[dict[str, Any]]]:
        async def wrapped(state: MatchState) -> dict[str, Any]:
            await self.emit("node_start", node=name)
            t0 = time.perf_counter()
            try:
                update = await fn(state)
            except MCPToolError as exc:
                await self.emit("error", node=name, message=str(exc))
                update = {"errors": [{"node": name, "message": str(exc)}], "fatal": name in ("load_job", "list_resumes")}
            await self.emit("node_end", node=name, ms=round((time.perf_counter() - t0) * 1000),
                            summary=update.pop("_summary", None))
            return update
        wrapped.__name__ = name
        return wrapped

    async def _call(self, server: str, tool: str, args: dict[str, Any] | None = None, **kw: Any) -> Any:
        await self.emit("tool_call", server=server, tool=tool, arguments=args or {})
        t0 = time.perf_counter()
        try:
            result = await self.hub.call_tool(server, tool, args or {}, **kw)
        except MCPToolError as exc:
            await self.emit("tool_result", server=server, tool=tool, ok=False, ms=round((time.perf_counter() - t0) * 1000),
                            error=str(exc))
            raise
        await self.emit("tool_result", server=server, tool=tool, ok=True, ms=round((time.perf_counter() - t0) * 1000))
        return result

    # ---------------- nodes ---------------- #

    async def discover_servers(self, state: MatchState) -> dict[str, Any]:
        disc = await self.hub.discover()
        summary, errors = {}, []
        for server, need in REQUIRED_TOOLS.items():
            have = {t["name"] for t in disc.get(server, {}).get("tools", [])}
            if server not in disc or not need <= have:
                errors.append({"node": "discover_servers",
                               "message": f"{server} is missing tools: {sorted(need - have) or 'server not connected'}"})
        for name, d in disc.items():
            summary[name] = {"server": d["server_info"].get("name"), "protocol": d["protocol_version"],
                             "tools": len(d["tools"]), "resources": len(d["resources"]),
                             "templates": len(d["resource_templates"]), "prompts": len(d["prompts"])}
        return {"servers": summary, "errors": errors, "fatal": bool(errors),
                "_summary": ", ".join(f"{k}: {v['tools']} tools / {v['resources']} resources" for k, v in summary.items())}

    async def load_job(self, state: MatchState) -> dict[str, Any]:
        if state.get("job_id"):
            job = await self._call(DB, "get_job", {"job_id": state["job_id"]})
        elif state.get("job_text"):
            text = state["job_text"]
            skills = extract_skills(text)
            job = {"job_id": "adhoc-" + uuid.uuid4().hex[:6], "title": text.split(".")[0][:80],
                   "required_skills": skills[:6], "nice_to_have": skills[6:], "min_years": 0, "description": text,
                   "adhoc": True}
        else:
            return {"errors": [{"node": "load_job", "message": "No job_id or job_text given"}], "fatal": True}
        return {"job": job, "_summary": f"{job['title']} - requires {', '.join(job['required_skills'])}"}

    async def list_resumes(self, state: MatchState) -> dict[str, Any]:
        listing = await self._call(FS, "list_files", {"directory": state["resume_dir"]})
        files = [f["path"] for f in listing["files"] if f["extension"].lstrip(".") in READABLE]
        return {"files": files, "pending": files, "_summary": f"{len(files)} resumes in {state['resume_dir']}/"}

    async def batch_read(self, state: MatchState) -> dict[str, Any]:
        pending = state.get("pending") or []
        if not pending:
            return {"_summary": "nothing to read"}

        async def progress(done: float, total: float | None, message: str | None) -> None:
            await self.emit("progress", node="batch_read", done=done, total=total, message=message)

        res = await self._call(FS, "batch_process", {"operation": "read", "filepaths": pending}, progress=progress)
        docs = dict(state.get("documents") or {})
        errors = []
        for item in res["results"]:
            if item["success"]:
                docs[item["filepath"]] = item["data"]["content"]
            else:
                errors.append({"node": "batch_read", "message": f"{item['filepath']}: {item['error']['message']}"})
        s = res["summary"]
        return {"documents": docs, "errors": errors,
                "_summary": f"{s['succeeded']}/{s['total']} read with {s['workers']} workers in {s['duration_ms']} ms"}

    async def score_candidates(self, state: MatchState) -> dict[str, Any]:
        job = JobSpec.from_dict(state["job"])
        scores = dict(state.get("scores") or {})
        docs = state.get("documents") or {}
        fresh = []
        for path in state.get("pending") or []:
            if path in docs:
                prev = scores.get(path, {})
                cand = score_resume(docs[path], path, job).to_dict()
                if prev.get("assessment") and prev.get("score") == cand["score"]:
                    cand["assessment"] = prev["assessment"]
                scores[path] = cand
                fresh.append(cand)
        for c in sorted(fresh, key=lambda c: -c["score"]):
            await self.emit("candidate_scored", candidate=c)
        best = max(fresh, key=lambda c: c["score"]) if fresh else None
        return {"scores": scores,
                "_summary": f"scored {len(fresh)}" + (f"; best {best['name']} ({best['score']})" if best else "")}

    async def assess_with_llm(self, state: MatchState) -> dict[str, Any]:
        scores = dict(state.get("scores") or {})
        job = state["job"]
        top = [c for c in rank_dicts(scores.values())[: self.assess_top] if not c.get("assessment")]
        done = 0
        for cand in top:
            text = (state.get("documents") or {}).get(cand["filepath"], "")[:3500]
            prompt = (
                f"You are screening candidates for: {job['title']}.\n"
                f"Required skills: {', '.join(job['required_skills'])}. Nice to have: {', '.join(job.get('nice_to_have', []))}.\n"
                f"Minimum experience: {job.get('min_years', 0)} years.\n"
                f"Automated screening found -> matched: {cand['required_matched']}, weak: {cand['required_partial']}, "
                f"missing: {cand['required_missing']}, score {cand['score']}/100.\n\n"
                f"Resume:\n{text}\n\n"
                "Write exactly two sentences for a hiring manager: the candidate's strongest evidence for this role, "
                "then the main gap or risk. No preamble, no bullet points."
            )
            await self.emit("llm_call", candidate=cand["name"])
            t0 = time.perf_counter()
            try:
                reply = await self.llm.ainvoke(prompt)
            except Exception as exc:  # noqa: BLE001 - an LLM outage must not break the pipeline
                await self.emit("error", node="assess_with_llm", message=f"LLM failed for {cand['name']}: {exc}")
                return {"scores": scores, "errors": [{"node": "assess_with_llm", "message": str(exc)}],
                        "_summary": f"LLM unavailable after {done} assessments"}
            cand = {**cand, "assessment": _message_text(reply)}
            scores[cand["filepath"]] = cand
            done += 1
            await self.emit("llm_result", candidate=cand["name"], assessment=cand["assessment"],
                            ms=round((time.perf_counter() - t0) * 1000))
        return {"scores": scores, "_summary": f"{done} assessments written"}

    async def rank_and_report(self, state: MatchState) -> dict[str, Any]:
        job = JobSpec.from_dict(state["job"])
        ranked = rank_dicts((state.get("scores") or {}).values())
        from resume_scoring import CandidateScore
        report = render_report(job, [CandidateScore(**c) for c in ranked],
                               datetime.now().strftime("%Y-%m-%d %H:%M"), state["run_id"],
                               sources={"resumes": "filesystem-mcp batch_process", "job": "candidate-db-mcp get_job"})
        path = f"output/match_{job.job_id}.md"
        await self._call(FS, "write_file", {"filepath": path, "content": report, "overwrite": True})
        await self.emit("ranking", ranking=[{k: c[k] for k in ("filepath", "name", "score", "tier")} for c in ranked])
        lead = ranked[0] if ranked else None
        return {"ranking": ranked, "report_path": path,
                "_summary": f"report -> {path}" + (f"; #1 {lead['name']} ({lead['score']})" if lead else "")}

    async def persist_results(self, state: MatchState) -> dict[str, Any]:
        job = state["job"]
        if job.get("adhoc"):
            return {"pending": [], "_summary": "ad-hoc job: not stored"}
        scores = state.get("scores") or {}
        changed = [scores[p] for p in (state.get("pending") or []) if p in scores] or list(scores.values())
        payload = [{"filepath": c["filepath"], "name": c["name"], "score": c["score"], "tier": c["tier"],
                    "years_experience": c["years_experience"], "required_matched": c["required_matched"],
                    "required_missing": c["required_missing"], "nice_matched": c["nice_matched"],
                    "assessment": c.get("assessment")} for c in changed]
        summary = []
        if state.get("removed"):
            gone = await self._call(DB, "remove_matches", {"job_id": job["job_id"], "filepaths": state["removed"]})
            summary.append(f"removed {gone['removed']}")
        if payload and (state.get("pending") or not state.get("removed")):
            res = await self._call(DB, "save_match_results", {"job_id": job["job_id"], "run_id": state["run_id"], "results": payload})
            summary.append(f"saved {res['saved']} ({res['candidates_on_record']} on record)")
        return {"pending": [], "removed": [], "rounds": (state.get("rounds") or 0) + 1, "_summary": "; ".join(summary) or "nothing to store"}

    async def watch_for_resumes(self, state: MatchState) -> dict[str, Any]:
        watch = dict(state.get("watch") or {})
        if not watch.get("watch_id"):
            res = await self._call(FS, "watch_directory", {"directory": state["resume_dir"], "extensions": list(READABLE)})
            watch.update(watch_id=res["watch_id"], next_seq=res["next_seq"],
                         deadline=time.time() + float(watch.get("seconds", 60)))
            await self.emit("watch_started", watch_id=res["watch_id"], directory=res["directory"],
                            uri=res["resource_uri"], seconds=watch.get("seconds", 60))
        remaining = watch["deadline"] - time.time()
        if remaining <= 0:
            await self._call(FS, "stop_watch", {"watch_id": watch["watch_id"]})
            await self.emit("watch_stopped", watch_id=watch["watch_id"])
            return {"watch": {**watch, "action": "stop"}, "_summary": "watch window closed"}
        res = await self._call(FS, "poll_watch_events", {"watch_id": watch["watch_id"], "since_seq": watch["next_seq"] - 1,
                                                         "timeout_seconds": min(self.watch_poll_seconds, max(0.1, remaining))})
        watch["next_seq"] = res["next_seq"]
        events = res["events"]
        files = list(state.get("files") or [])
        scores = dict(state.get("scores") or {})
        new, deleted = [], []
        for ev in events:
            if not ev["path"].lower().endswith(tuple("." + e for e in READABLE)):
                continue
            await self.emit("watch_event", event=ev)
            if ev["type"] in ("created", "modified"):
                new.append(ev["path"])
                if ev["path"] not in files:
                    files.append(ev["path"])
            elif ev["type"] == "deleted":
                deleted.append(ev["path"])
                scores.pop(ev["path"], None)
                files = [f for f in files if f != ev["path"]]
        new = list(dict.fromkeys(new))
        if new:
            action, summary = "process", f"new/changed: {', '.join(p.split('/')[-1] for p in new)}"
        elif deleted:
            action, summary = "rerank", f"removed: {', '.join(p.split('/')[-1] for p in deleted)}"
        else:
            action, summary = "wait", f"no new resumes ({int(remaining)}s left)"
        return {"watch": {**watch, "action": action}, "files": files, "pending": new, "scores": scores, "removed": deleted,
                "arrivals": [{"path": p, "event": "created/modified"} for p in new], "_summary": summary}

    # ---------------- routing ---------------- #

    @staticmethod
    def _after_discover(state: MatchState) -> str:
        return END if state.get("fatal") else "load_job"

    @staticmethod
    def _after_load(state: MatchState) -> str:
        return END if state.get("fatal") else "list_resumes"

    @staticmethod
    def _after_list(state: MatchState) -> str:
        return END if state.get("fatal") else "batch_read"

    def _after_score(self, state: MatchState) -> str:
        return "assess_with_llm" if self.llm is not None else "rank_and_report"

    @staticmethod
    def _after_persist(state: MatchState) -> str:
        return "watch_for_resumes" if state.get("mode") == "watch" else END

    @staticmethod
    def _after_watch(state: MatchState) -> str:
        return {"process": "batch_read", "rerank": "rank_and_report", "wait": "watch_for_resumes"}.get(
            (state.get("watch") or {}).get("action", "stop"), END)

    def _build_graph(self) -> Any:
        g = StateGraph(MatchState)
        for name in ("discover_servers", "load_job", "list_resumes", "batch_read", "score_candidates",
                     "assess_with_llm", "rank_and_report", "persist_results", "watch_for_resumes"):
            g.add_node(name, self._node(name, getattr(self, name)))
        g.add_edge(START, "discover_servers")
        g.add_conditional_edges("discover_servers", self._after_discover, ["load_job", END])
        g.add_conditional_edges("load_job", self._after_load, ["list_resumes", END])
        g.add_conditional_edges("list_resumes", self._after_list, ["batch_read", END])
        g.add_edge("batch_read", "score_candidates")
        g.add_conditional_edges("score_candidates", self._after_score, ["assess_with_llm", "rank_and_report"])
        g.add_edge("assess_with_llm", "rank_and_report")
        g.add_edge("rank_and_report", "persist_results")
        g.add_conditional_edges("persist_results", self._after_persist, ["watch_for_resumes", END])
        g.add_conditional_edges("watch_for_resumes", self._after_watch,
                                ["batch_read", "rank_and_report", "watch_for_resumes", END])
        return g.compile()

    # ---------------- entry points ---------------- #

    async def run(self, job_id: str | None = None, job_text: str | None = None, resume_dir: str = "resumes",
                  watch_seconds: float | None = None) -> MatchState:
        state: MatchState = {
            "job_id": job_id, "job_text": job_text, "resume_dir": resume_dir,
            "mode": "watch" if watch_seconds else "once", "run_id": "run_" + uuid.uuid4().hex[:8],
            "watch": {"seconds": watch_seconds or 0}, "errors": [], "arrivals": [],
        }
        await self.emit("run_start", run_id=state["run_id"], job_id=job_id, mode=state["mode"])
        final = await self.graph.ainvoke(state, {"recursion_limit": 1000})
        await self.emit("run_end", run_id=state["run_id"], report=final.get("report_path"),
                        errors=len(final.get("errors") or []))
        return final

    async def ask(self, question: str, max_steps: int = 12) -> str:
        """Free-form question: the LLM gets every tool from every connected MCP server."""
        if self.llm is None:
            raise ValueError("ask mode needs an LLM (--llm ollama|openai|openrouter)")
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
        from langgraph.graph import MessagesState
        from langgraph.prebuilt import ToolNode, tools_condition

        tools = await self.hub.langchain_tools()
        bound = self.llm.bind_tools(tools)
        system = SystemMessage(
            "You are a recruiting assistant. You can only see data through tools. The 'filesystem' MCP server holds "
            "resumes in the 'resumes' folder (list_files, read_file, search_in_file, batch_process). The "
            "'candidate_db' MCP server holds job postings and stored match scores (list_jobs, get_job, get_shortlist, "
            "get_candidate_history). Prefer get_shortlist for questions about the best candidates. Use tools before "
            "answering, then answer concisely and cite candidate names.")

        async def call_model(state: MessagesState) -> dict[str, Any]:
            reply = await bound.ainvoke([system, *state["messages"]])
            for tc in getattr(reply, "tool_calls", []) or []:
                await self.emit("tool_call", server="llm", tool=tc["name"], arguments=tc["args"])
            return {"messages": [reply]}

        async def run_tools(state: MessagesState) -> dict[str, Any]:
            out = await tool_node.ainvoke(state)
            for m in out["messages"]:
                if isinstance(m, ToolMessage):
                    await self.emit("tool_result", server="llm", tool=m.name, ok=m.status != "error",
                                    preview=_message_text(m)[:200])
            return out

        tool_node = ToolNode(tools, handle_tool_errors=True)
        g = StateGraph(MessagesState)
        g.add_node("llm", call_model)
        g.add_node("tools", run_tools)
        g.add_edge(START, "llm")
        g.add_conditional_edges("llm", tools_condition, ["tools", END])
        g.add_edge("tools", "llm")
        graph = g.compile()
        await self.emit("ask_start", question=question)
        result = await graph.ainvoke({"messages": [HumanMessage(question)]}, {"recursion_limit": 2 * max_steps + 1})
        answer = next((_message_text(m) for m in reversed(result["messages"]) if isinstance(m, AIMessage) and _message_text(m)), "")
        await self.emit("ask_end", answer=answer)
        return answer


def rank_dicts(cands: Any) -> list[dict[str, Any]]:
    return sorted(cands, key=lambda c: (-c["score"], c["name"]))


def mermaid_diagram() -> str:
    """The compiled graph as Mermaid (works without MCP servers running)."""
    agent = ResumeMatchingAgent.__new__(ResumeMatchingAgent)
    agent.llm, agent.on_event, agent.hub = object(), None, None
    return agent._build_graph().get_graph().draw_mermaid()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _console_printer(verbose: bool) -> EventFn:
    icons = {"node_start": ">", "node_end": " ", "tool_call": "  ->", "tool_result": "  <-"}

    def show(ev: dict[str, Any]) -> None:
        t = ev["type"]
        if t == "node_start":
            print(f"> {ev['node']}")
        elif t == "node_end":
            print(f"  done in {ev['ms']} ms" + (f" -- {ev['summary']}" if ev.get("summary") else ""))
        elif t == "tool_call" and verbose:
            print(f"  -> {ev['server']}.{ev['tool']}({json.dumps(ev['arguments'])[:110]})")
        elif t == "tool_result" and verbose and not ev["ok"]:
            print(f"  <- error: {ev.get('error')}")
        elif t == "watch_started":
            print(f"  watching {ev['directory']}/ as {ev['uri']} for {ev['seconds']}s -- drop a resume in to see it scored")
        elif t == "watch_event":
            e = ev["event"]
            print(f"  ** {e['type']}: {e['path']}")
        elif t == "llm_result":
            print(f"  LLM on {ev['candidate']}: {ev['assessment'][:140]}")
        elif t == "error":
            print(f"  !! {ev['message']}")
    _ = icons
    return show


def _print_ranking(final: MatchState, top: int) -> None:
    ranking = final.get("ranking") or []
    job = final.get("job") or {}
    print(f"\n{job.get('title', '?')}  (required: {', '.join(job.get('required_skills', []))})")
    print(f"{'#':>2}  {'Candidate':<18} {'Score':>5}  {'Tier':<16} Missing")
    for i, c in enumerate(ranking[:top], 1):
        missing = ", ".join(c["required_missing"] + [p + "(weak)" for p in c["required_partial"]]) or "-"
        print(f"{i:>2}  {c['name']:<18} {c['score']:>5}  {c['tier']:<16} {missing}")
        if c.get("assessment"):
            print(f"      {c['assessment']}")
    if final.get("report_path"):
        print(f"\nReport written via MCP: {final['report_path']}")
    for e in final.get("errors") or []:
        print(f"warning [{e['node']}]: {e['message']}")


async def _amain(args: argparse.Namespace) -> int:
    llm = make_llm(args.llm, args.model)
    async with MCPHub(default_server_specs()) as hub:
        agent = ResumeMatchingAgent(hub, llm=llm, on_event=_console_printer(args.verbose), assess_top=args.assess_top)
        if args.list_jobs:
            jobs = await hub.call_tool(DB, "list_jobs")
            for j in jobs["jobs"]:
                print(f"{j['job_id']:<24} {j['title']:<34} {', '.join(j['required_skills'])}")
            return 0
        if args.ask:
            print(await agent.ask(args.ask))
            return 0
        final = await agent.run(job_id=args.job, job_text=args.job_text, resume_dir=args.resume_dir,
                                watch_seconds=args.watch_seconds if args.watch else None)
        _print_ranking(final, args.top)
        return 1 if final.get("fatal") else 0


def main(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    p = argparse.ArgumentParser(description="LangGraph resume-matching agent over MCP",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("Examples::")[1])
    p.add_argument("--job", help="job_id from the candidate-db server (see --list-jobs)")
    p.add_argument("--job-text", help="free-text job description instead of --job")
    p.add_argument("--resume-dir", default="resumes")
    p.add_argument("--watch", action="store_true", help="keep watching the folder and score new resumes")
    p.add_argument("--watch-seconds", type=float, default=60)
    p.add_argument("--llm", default=os.environ.get("LLM_PROVIDER", "none"), help="none | ollama | openai | openrouter")
    p.add_argument("--model")
    p.add_argument("--assess-top", type=int, default=3)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--ask", help="free-form question answered by the LLM using all MCP tools")
    p.add_argument("--list-jobs", action="store_true")
    p.add_argument("--diagram", action="store_true", help="print the workflow as Mermaid and exit")
    p.add_argument("-v", "--verbose", action="store_true", help="show every MCP tool call")
    args = p.parse_args(argv)
    if args.diagram:
        print(mermaid_diagram())
        return 0
    if not (args.job or args.job_text or args.ask or args.list_jobs):
        p.error("give --job, --job-text, --ask or --list-jobs")
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
