"""tools/list + tools/call: the Milestone 1 tools over MCP, and batch_process."""
from __future__ import annotations

import json

import filesystem_mcp_server as fsm


def test_tools_list_advertises_all_tools_with_schemas(client):
    tools = client.request("tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"list_files", "read_file", "write_file", "search_in_file"} <= names           # Milestone 1
    assert {"watch_directory", "poll_watch_events", "stop_watch", "batch_process"} <= names  # new
    for t in tools:
        assert t["inputSchema"]["type"] == "object"
        assert t["description"] and t["annotations"]
    by = {t["name"]: t for t in tools}
    assert by["read_file"]["annotations"]["readOnlyHint"] is True
    assert by["write_file"]["annotations"]["destructiveHint"] is True


def test_list_files(client):
    res = client.call("list_files", directory="resumes", extension="pdf")
    assert res["isError"] is False
    sc = res["structuredContent"]
    assert sc["count"] == 3 and all(f["extension"] == ".pdf" for f in sc["files"])
    assert json.loads(res["content"][0]["text"]) == sc                 # text mirrors structured output


def test_read_file_pdf_docx_txt(client):
    for path, word in [("resumes/resume_john_doe.pdf", "FastAPI"), ("resumes/resume_priya_sharma.docx", "PyTorch"),
                       ("resumes/resume_alex_chen.txt", "React")]:
        sc = client.call("read_file", filepath=path)["structuredContent"]
        assert sc["success"] and word in sc["content"], path


def test_read_file_respects_server_cap(client_factory):
    c = client_factory(max_read_chars=50)
    sc = c.call("read_file", filepath="resumes/resume_john_doe.pdf", max_chars=10_000)["structuredContent"]
    assert len(sc["content"]) <= 50 and sc["metadata"]["truncated"] is True


def test_sandbox_escape_is_a_tool_error(client):
    res = client.call("read_file", filepath="../../../etc/passwd")
    assert res["isError"] is True
    assert res["structuredContent"]["error"]["type"] == "outside_sandbox"


def test_missing_file_is_a_tool_error(client):
    res = client.call("read_file", filepath="resumes/nobody.pdf")
    assert res["isError"] and res["structuredContent"]["error"]["type"] == "not_found"


def test_argument_validation_is_a_tool_error_not_protocol_error(client):
    for args in [{}, {"filepath": 3}, {"filepath": "x.txt", "bogus": 1}, {"filepath": "x.txt", "max_chars": 0}]:
        r = client.request("tools/call", {"name": "read_file", "arguments": args})
        assert "result" in r, args
        assert r["result"]["isError"] is True
        assert r["result"]["structuredContent"]["error"]["type"] == "invalid_arguments"


def test_unknown_tool_is_protocol_error(client):
    r = client.request("tools/call", {"name": "rm_rf", "arguments": {}})
    assert r["error"]["code"] == fsm.INVALID_PARAMS
    assert "read_file" in r["error"]["data"]["available"]


def test_write_then_read(client, sandbox):
    res = client.call("write_file", filepath="output/notes.md", content="# hi\n")
    assert res["isError"] is False
    assert (sandbox / "output" / "notes.md").read_text() == "# hi\n"
    again = client.call("write_file", filepath="output/notes.md", content="x")
    assert again["isError"] and again["structuredContent"]["error"]["type"] == "file_exists"
    assert client.call("write_file", filepath="output/notes.md", content="x", overwrite=True)["isError"] is False


def test_read_only_mode_hides_write_file(client_factory):
    c = client_factory(allow_write=False)
    assert "write_file" not in {t["name"] for t in c.request("tools/list")["result"]["tools"]}
    r = c.request("tools/call", {"name": "write_file", "arguments": {"filepath": "output/a.txt", "content": "x"}})
    assert r["error"]["code"] == fsm.INVALID_PARAMS


def test_search_in_file(client):
    sc = client.call("search_in_file", filepath="resumes/resume_alex_chen.txt", keyword="python")["structuredContent"]
    assert sc["total_matches"] >= 1 and "Python" in sc["matches"][0]["context"]


# ---------------- batch_process ---------------- #

def test_batch_read_directory(client):
    res = client.call("batch_process", operation="read", directory="resumes")
    sc = res["structuredContent"]
    assert res["isError"] is False
    assert sc["summary"]["total"] == 8 and sc["summary"]["succeeded"] == 8 and sc["summary"]["failed"] == 0
    assert all("content" in r["data"] for r in sc["results"])
    assert [r["filepath"] for r in sc["results"]] == sorted(r["filepath"] for r in sc["results"])


def test_batch_keeps_input_order_and_isolates_failures(client):
    paths = ["resumes/resume_rahul_mehta.pdf", "resumes/missing.pdf", "../outside.txt", "resumes/resume_alex_chen.txt"]
    sc = client.call("batch_process", operation="metadata", filepaths=paths)["structuredContent"]
    assert [r["filepath"] for r in sc["results"]] == paths
    assert [r["success"] for r in sc["results"]] == [True, False, False, True]
    assert sc["results"][1]["error"]["type"] == "not_found"
    assert sc["results"][2]["error"]["type"] == "outside_sandbox"
    assert sc["summary"]["failed"] == 2
    assert "content" not in sc["results"][0]["data"]                 # metadata only


def test_batch_search(client):
    sc = client.call("batch_process", operation="search", directory="resumes", keyword="Kubernetes")["structuredContent"]
    hits = {r["filepath"].split("/")[-1] for r in sc["results"] if r["data"]["total_matches"]}
    assert hits == {"resume_maria_garcia.pdf", "resume_david_okafor.docx"}
    assert sc["summary"]["files_with_matches"] == 2


def test_batch_argument_rules(client):
    both = client.call("batch_process", operation="read", directory="resumes", filepaths=["a.txt"])
    neither = client.call("batch_process", operation="read")
    no_kw = client.call("batch_process", operation="search", directory="resumes")
    bad_op = client.call("batch_process", operation="delete", directory="resumes")
    assert all(r["isError"] for r in (both, neither, no_kw, bad_op))


def test_batch_limit(client_factory):
    c = client_factory(batch_max_files=2)
    res = c.call("batch_process", operation="metadata", directory="resumes")
    assert res["isError"] and res["structuredContent"]["error"]["type"] == "limit_reached"


def test_batch_progress_notifications(client):
    r = client.request("tools/call", {"name": "batch_process", "arguments": {"operation": "metadata", "directory": "resumes"},
                                      "_meta": {"progressToken": "tok-1"}})
    assert r["result"]["isError"] is False
    prog = [n["params"] for n in client.notifications if n["method"] == "notifications/progress"]
    assert prog[0]["progress"] == 0 and prog[-1]["progress"] == prog[-1]["total"] == 8
    assert all(p["progressToken"] == "tok-1" for p in prog)
    assert [p["progress"] for p in prog] == sorted(p["progress"] for p in prog)   # monotonic
    logs = [n for n in client.notifications if n["method"] == "notifications/message"]
    assert logs and logs[-1]["params"]["data"]["event"] == "batch_complete"


def test_batch_without_progress_token_sends_no_progress(client):
    client.call("batch_process", operation="metadata", directory="resumes")
    assert not [n for n in client.notifications if n["method"] == "notifications/progress"]
