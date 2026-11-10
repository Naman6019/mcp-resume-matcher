"""The hand-written server works with the *official* MCP Python SDK client, over both transports,
and the FastMCP candidate-db server works through the same client."""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

PROJECT = Path(__file__).resolve().parent.parent


def _params(script: str, env: dict[str, str]) -> StdioServerParameters:
    from mcp.client.stdio import get_default_environment
    return StdioServerParameters(command=sys.executable, args=[str(PROJECT / script)],
                                 env={**get_default_environment(), **env}, cwd=str(PROJECT))


async def test_official_client_over_stdio(sandbox):
    notes: list[types.ServerNotification] = []

    async def on_message(msg):
        if isinstance(msg, types.ServerNotification):
            notes.append(msg)

    progress: list[float] = []

    async def on_progress(p, total, message):
        progress.append(p)

    async with stdio_client(_params("filesystem_mcp_server.py", {"MCP_FS_ROOT": str(sandbox), "MCP_FS_WATCH_POLL_INTERVAL": "0.1"})) as (r, w):
        async with ClientSession(r, w, message_handler=on_message) as s:
            init = await s.initialize()
            assert init.protocolVersion == types.LATEST_PROTOCOL_VERSION
            assert init.capabilities.resources.subscribe is True
            tools = {t.name: t for t in (await s.list_tools()).tools}
            assert tools["batch_process"].annotations.readOnlyHint is True

            res = await s.call_tool("batch_process", {"operation": "read", "directory": "resumes"}, progress_callback=on_progress)
            assert not res.isError and res.structuredContent["summary"]["succeeded"] == 8
            assert progress and progress[-1] == 8

            res = await s.call_tool("read_file", {"filepath": "../x"})
            assert res.isError

            resources = (await s.list_resources()).resources
            assert any(str(r.uri) == "fs:///resumes/resume_john_doe.pdf" for r in resources)
            text = (await s.read_resource("fs:///resumes/resume_sara_lindqvist.txt")).contents[0].text
            assert "SARA LINDQVIST" in text

            with pytest.raises(Exception) as exc:                       # JSON-RPC error -> McpError
                await s.read_resource("fs:///resumes/ghost.pdf")
            assert "not found" in str(exc.value).lower()

            watch = (await s.call_tool("watch_directory", {"directory": "resumes"})).structuredContent
            await s.subscribe_resource(watch["resource_uri"])
            (sandbox / "resumes" / "fresh.txt").write_text("Fresh Person\nDeveloper")
            ev = (await s.call_tool("poll_watch_events", {"watch_id": watch["watch_id"], "timeout_seconds": 5})).structuredContent
            assert ev["events"][0]["path"] == "resumes/fresh.txt"
            for _ in range(100):
                if any(isinstance(n.root, types.ResourceUpdatedNotification) for n in notes):
                    break
                await asyncio.sleep(0.05)      # yield, so the client keeps reading pushes
            kinds = {type(n.root).__name__ for n in notes}
            assert {"ResourceListChangedNotification", "ResourceUpdatedNotification", "ProgressNotification"} <= kinds
            # A well-behaved client stops server-side work before disconnecting.
            assert not (await s.call_tool("stop_watch", {"watch_id": watch["watch_id"]})).isError


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_official_client_over_streamable_http(sandbox):
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(PROJECT / "filesystem_mcp_server.py"), "--transport", "http",
                             "--port", str(port), "--root", str(sandbox), "--log-level", "warning"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (r, w, get_sid):
            async with ClientSession(r, w) as s:
                await s.initialize()
                assert get_sid()
                res = await s.call_tool("search_in_file", {"filepath": "resumes/resume_john_doe.pdf", "keyword": "fastapi"})
                assert res.structuredContent["total_matches"] >= 2
                assert len((await s.list_resource_templates()).resourceTemplates) == 4
    finally:
        proc.terminate()
        proc.wait(timeout=10)


async def test_candidate_db_server(tmp_path):
    async with stdio_client(_params("candidate_db_mcp_server.py", {"CANDIDATE_DB": str(tmp_path / "t.db")})) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            jobs = (await s.call_tool("list_jobs", {})).structuredContent
            assert jobs["count"] == 4
            job = (await s.call_tool("get_job", {"job_id": "platform-sre"})).structuredContent
            assert "Kubernetes" in job["required_skills"]
            bad = await s.call_tool("get_job", {"job_id": "astronaut"})
            assert bad.isError and "Known jobs" in bad.content[0].text

            results = [{"filepath": "resumes/a.txt", "name": "A", "score": 80, "required_matched": ["Python"]},
                       {"filepath": "resumes/b.txt", "name": "B", "score": 60}]
            saved = (await s.call_tool("save_match_results", {"job_id": "platform-sre", "run_id": "r1", "results": results})).structuredContent
            assert saved == {"saved": 2, "job_id": "platform-sre", "run_id": "r1", "candidates_on_record": 2}
            short = (await s.call_tool("get_shortlist", {"job_id": "platform-sre", "limit": 1})).structuredContent
            assert short["shortlist"][0]["candidate_name"] == "A" and short["shortlist"][0]["required_matched"] == ["Python"]
            gone = (await s.call_tool("remove_matches", {"job_id": "platform-sre", "filepaths": ["resumes/a.txt"]})).structuredContent
            assert gone["removed"] == 1
            hist = (await s.call_tool("get_candidate_history", {"filepath": "resumes/b.txt"})).structuredContent
            assert hist["history"][0]["job_id"] == "platform-sre"

            created = await s.call_tool("create_job", {"job_id": "Bad Slug", "title": "x", "required_skills": ["Go"]})
            assert created.isError
            created = (await s.call_tool("create_job", {"job_id": "go-dev", "title": "Go Dev", "required_skills": ["Go"]})).structuredContent
            assert created["created"] is True

            tpl = (await s.list_resource_templates()).resourceTemplates
            assert {t.uriTemplate for t in tpl} == {"db://jobs/{job_id}", "db://jobs/{job_id}/shortlist"}
            jobs_res = json.loads((await s.read_resource("db://jobs")).contents[0].text)
            assert len(jobs_res) == 5
