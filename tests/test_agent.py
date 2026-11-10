"""End-to-end: the LangGraph agent driving both real MCP servers (spawned over stdio)."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from matching_agent import ResumeMatchingAgent, mermaid_diagram
from mcp_client import MCPHub, MCPToolError, default_server_specs

PROJECT = Path(__file__).resolve().parent.parent


@pytest.fixture
def specs(sandbox, tmp_path):
    s = default_server_specs(traffic_dir=tmp_path / "logs", fs_root=str(sandbox), db_path=str(tmp_path / "db.sqlite"))
    for spec in s:
        if spec.name == "filesystem":
            spec.env["MCP_FS_WATCH_POLL_INTERVAL"] = "0.1"
    return s


class FakeToolLLM(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **kw: Any) -> "FakeToolLLM":
        return self


async def test_run_once(specs, sandbox):
    events: list[dict[str, Any]] = []
    traffic: list[tuple[str, str, dict]] = []
    async with MCPHub(specs, on_traffic=lambda s, d, m: traffic.append((s, d, m))) as hub:
        agent = ResumeMatchingAgent(hub, on_event=events.append)
        final = await agent.run(job_id="senior-python-backend")
        assert [c["name"] for c in final["ranking"]][:2] == ["John Doe", "Sara Lindqvist"]
        assert final["errors"] == []
        report = sandbox / final["report_path"]
        assert report.exists() and "John Doe" in report.read_text()             # written by the MCP server
        short = await hub.call_tool("candidate_db", "get_shortlist", {"job_id": "senior-python-backend", "limit": 1})
        assert short["shortlist"][0]["candidate_name"] == "John Doe"             # persisted via the DB server

    nodes = [e["node"] for e in events if e["type"] == "node_start"]
    assert nodes == ["discover_servers", "load_job", "list_resumes", "batch_read", "score_candidates",
                     "rank_and_report", "persist_results"]
    tools = [(e["server"], e["tool"]) for e in events if e["type"] == "tool_call"]
    assert ("filesystem", "batch_process") in tools and ("candidate_db", "save_match_results") in tools
    assert any(e["type"] == "progress" for e in events)
    # the tap sees real JSON-RPC on the wire, for both servers
    methods = {(s, m.get("method")) for s, d, m in traffic if d == "out"}
    assert ("filesystem", "initialize") in methods and ("candidate_db", "tools/call") in methods
    assert all(m.get("jsonrpc") == "2.0" for _, _, m in traffic)


async def test_watch_mode_scores_new_and_drops_deleted(specs, sandbox):
    events: list[dict[str, Any]] = []
    inbox = sandbox / "resumes"

    async def drop_files() -> None:
        while not any(e["type"] == "watch_started" for e in events):
            await asyncio.sleep(0.05)
        shutil.copy(PROJECT / "demo_assets" / "incoming" / "resume_lena_park.txt", inbox)
        while not any(e["type"] == "watch_event" and e["event"]["type"] == "created" for e in events):
            await asyncio.sleep(0.05)
        while sum(1 for e in events if e["type"] == "node_end" and e["node"] == "persist_results") < 2:
            await asyncio.sleep(0.05)
        (inbox / "resume_rahul_mehta.pdf").unlink()

    async with MCPHub(specs) as hub:
        agent = ResumeMatchingAgent(hub, on_event=events.append, watch_poll_seconds=0.5)
        dropper = asyncio.create_task(drop_files())
        final = await agent.run(job_id="senior-python-backend", watch_seconds=6)
        await dropper
        names = [c["name"] for c in final["ranking"]]
        assert names[0] == "Lena Park" and "Rahul Mehta" not in names
        short = await hub.call_tool("candidate_db", "get_shortlist", {"job_id": "senior-python-backend", "limit": 20})
        stored = {r["candidate_name"] for r in short["shortlist"]}
        assert "Lena Park" in stored and "Rahul Mehta" not in stored

    reads = [e for e in events if e["type"] == "tool_call" and e["tool"] == "batch_process"]
    assert reads[1]["arguments"]["filepaths"] == ["resumes/resume_lena_park.txt"]     # incremental
    assert any(e["type"] == "watch_stopped" for e in events)


async def test_llm_assessments(specs):
    llm = GenericFakeChatModel(messages=iter([AIMessage("Strong FastAPI evidence. Gap: none."),
                                              AIMessage("Good Python. Gap: PostgreSQL.")]))
    async with MCPHub(specs) as hub:
        final = await ResumeMatchingAgent(hub, llm=llm, assess_top=2).run(job_id="senior-python-backend")
    top = final["ranking"][:3]
    assert top[0]["assessment"].startswith("Strong FastAPI") and top[1]["assessment"].startswith("Good Python")
    assert top[2]["assessment"] is None


async def test_llm_failure_does_not_break_pipeline(specs):
    class Broken(GenericFakeChatModel):
        async def ainvoke(self, *a, **k):
            raise ConnectionError("model offline")

    async with MCPHub(specs) as hub:
        final = await ResumeMatchingAgent(hub, llm=Broken(messages=iter([]))).run(job_id="senior-python-backend")
    assert final["report_path"] and any("model offline" in e["message"] for e in final["errors"])


async def test_unknown_job_stops_cleanly(specs):
    events: list[dict] = []
    async with MCPHub(specs) as hub:
        final = await ResumeMatchingAgent(hub, on_event=events.append).run(job_id="astronaut")
    assert final["fatal"] and "Known jobs" in final["errors"][0]["message"]
    assert "list_resumes" not in [e["node"] for e in events if e["type"] == "node_start"]


async def test_adhoc_job_text(specs):
    async with MCPHub(specs) as hub:
        final = await ResumeMatchingAgent(hub).run(job_text="Frontend role: React, TypeScript, CSS and Jest testing.")
    assert final["job"]["required_skills"] == ["React", "TypeScript", "CSS", "Testing"]
    assert final["ranking"][0]["name"] == "Alex Chen"


async def test_ask_mode_uses_tools_from_both_servers(specs):
    llm = FakeToolLLM(messages=iter([
        AIMessage("", tool_calls=[{"name": "list_jobs", "args": {}, "id": "c1"},
                                  {"name": "list_files", "args": {"directory": "resumes"}, "id": "c2"}]),
        AIMessage("There are 4 jobs and 8 resumes."),
    ]))
    events: list[dict] = []
    async with MCPHub(specs) as hub:
        answer = await ResumeMatchingAgent(hub, llm=llm, on_event=events.append).ask("How many?")
    assert answer == "There are 4 jobs and 8 resumes."
    results = [e for e in events if e["type"] == "tool_result"]
    assert {e["tool"] for e in results} == {"list_jobs", "list_files"} and all(e["ok"] for e in results)


async def test_hub_errors(specs):
    async with MCPHub(specs) as hub:
        with pytest.raises(MCPToolError, match="outside"):
            await hub.call_tool("filesystem", "read_file", {"filepath": "../../x"})
        with pytest.raises(KeyError):
            await hub.call_tool("nope", "x")
        disc = await hub.discover()
        assert disc["filesystem"]["server_info"]["name"] == "filesystem-mcp"
        assert len(disc["candidate_db"]["tools"]) == 7


def test_mermaid_diagram():
    mm = mermaid_diagram()
    for node in ("discover_servers", "batch_read", "assess_with_llm", "watch_for_resumes"):
        assert node in mm
