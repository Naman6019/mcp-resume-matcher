"""Resource discovery (resources/list, templates), resources/read, subscriptions, prompts."""
from __future__ import annotations

import json

import filesystem_mcp_server as fsm


def test_resources_list_exposes_every_resume_and_config(client):
    res = client.request("resources/list")["result"]["resources"]
    uris = [r["uri"] for r in res]
    assert uris[0] == "config://server"
    files = [r for r in res if r["uri"].startswith("fs:///")]
    assert len(files) == 8
    john = next(r for r in files if r["name"] == "resume_john_doe.pdf")
    assert john["uri"] == "fs:///resumes/resume_john_doe.pdf"
    assert john["mimeType"] == "text/plain" and john["_meta"]["sourceMimeType"] == "application/pdf"
    assert john["size"] > 0 and "lastModified" in john["annotations"]


def test_resource_templates(client):
    tpls = client.request("resources/templates/list")["result"]["resourceTemplates"]
    assert {t["name"] for t in tpls} == {"file-text", "directory-listing", "file-search", "watch-events"}


def test_read_file_resource(client):
    c = client.request("resources/read", {"uri": "fs:///resumes/resume_john_doe.pdf"})["result"]["contents"][0]
    assert c["uri"] == "fs:///resumes/resume_john_doe.pdf" and c["mimeType"] == "text/plain"
    assert "FastAPI" in c["text"] and c["_meta"]["metadata"]["format"] == "pdf"


def test_dir_and_search_templates(client):
    d = json.loads(client.request("resources/read", {"uri": "dir:///resumes?extension=docx"})["result"]["contents"][0]["text"])
    assert d["count"] == 3
    s = json.loads(client.request("resources/read", {"uri": "search:///resumes/resume_maria_garcia.pdf?keyword=terraform"})
                   ["result"]["contents"][0]["text"])
    assert s["total_matches"] >= 2


def test_config_resource_reports_sources(client):
    data = json.loads(client.request("resources/read", {"uri": "config://server"})["result"]["contents"][0]["text"])
    assert data["server"]["name"] == "filesystem-mcp"
    assert data["config"]["sources"]["root"] == "cli"
    assert data["config"]["sources"]["page_size"] == "default"
    assert "requests" in data["stats"]


def test_resource_errors(client):
    missing = client.request("resources/read", {"uri": "fs:///resumes/ghost.pdf"})
    assert missing["error"]["code"] == fsm.RESOURCE_NOT_FOUND and missing["error"]["data"]["uri"]
    escape = client.request("resources/read", {"uri": "fs:///../../secret.txt"})
    assert escape["error"]["code"] == fsm.RESOURCE_NOT_FOUND
    scheme = client.request("resources/read", {"uri": "ftp://x/y"})
    assert scheme["error"]["code"] == fsm.RESOURCE_NOT_FOUND
    no_kw = client.request("resources/read", {"uri": "search:///resumes/resume_john_doe.pdf"})
    assert no_kw["error"]["code"] == fsm.INVALID_PARAMS
    no_uri = client.request("resources/read", {})
    assert no_uri["error"]["code"] == fsm.INVALID_PARAMS
    unsupported = client.request("resources/read", {"uri": "fs:///resumes"})           # a directory
    assert "error" in unsupported


def test_subscribe_rules(client):
    assert client.request("resources/subscribe", {"uri": "fs:///resumes/resume_john_doe.pdf"})["result"] == {}
    assert client.request("resources/subscribe", {"uri": "watch://w_nope"})["error"]["code"] == fsm.RESOURCE_NOT_FOUND
    assert client.request("resources/subscribe", {"uri": "config://server"})["error"]["code"] == fsm.INVALID_PARAMS
    assert client.request("resources/unsubscribe", {"uri": "fs:///resumes/resume_john_doe.pdf"})["result"] == {}
    assert not client.session.subscriptions


def test_prompts(client):
    prompts = client.request("prompts/list")["result"]["prompts"]
    assert prompts[0]["name"] == "screen_resumes"
    got = client.request("prompts/get", {"name": "screen_resumes", "arguments": {"role": "SRE", "skills": "Kubernetes"}})
    text = got["result"]["messages"][0]["content"]["text"]
    assert "batch_process" in text and "Kubernetes" in text
    assert client.request("prompts/get", {"name": "screen_resumes", "arguments": {}})["error"]["code"] == fsm.INVALID_PARAMS
    assert client.request("prompts/get", {"name": "x"})["error"]["code"] == fsm.INVALID_PARAMS
