"""Configuration management, the stdio framing and the Streamable HTTP transport."""
from __future__ import annotations

import io
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

import filesystem_mcp_server as fsm
from filesystem_mcp_server import FilesystemMCPServer, ServerConfig


# ---------------- configuration ---------------- #

def test_config_precedence(tmp_path: Path, sandbox: Path):
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({"root": str(sandbox), "page_size": 7, "batch_max_workers": 3, "log_level": "debug"}))
    env = {"MCP_FS_PAGE_SIZE": "9", "MCP_FS_RESOURCE_DIRS": "resumes, output", "MCP_FS_ALLOW_WRITE": "false"}
    cfg = ServerConfig.load(cli={"page_size": 11, "port": None}, env=env, config_path=str(cfg_file))
    assert cfg.page_size == 11 and cfg.source["page_size"] == "cli"
    assert cfg.batch_max_workers == 3 and cfg.source["batch_max_workers"] == "file:cfg.json"
    assert cfg.resource_dirs == ["resumes", "output"] and cfg.source["resource_dirs"] == "env"
    assert cfg.allow_write is False
    assert cfg.port == 8765 and cfg.source["port"] == "default"


def test_config_file_from_env_var(tmp_path: Path, sandbox: Path):
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({"root": str(sandbox), "watch_poll_interval": 2}))
    cfg = ServerConfig.load(env={"MCP_FS_CONFIG": str(cfg_file)})
    assert cfg.watch_poll_interval == 2.0 and isinstance(cfg.watch_poll_interval, float)


@pytest.mark.parametrize("cli, msg", [
    ({"root": "/definitely/not/here"}, "not a directory"),
    ({"transport": "carrier-pigeon"}, "transport"),
    ({"log_level": "loud"}, "log_level"),
    ({"page_size": 0}, "page_size"),
    ({"watch_poll_interval": 0}, "watch_poll_interval"),
])
def test_config_validation(cli, msg, sandbox):
    with pytest.raises(ValueError, match=msg):
        ServerConfig.load(cli={"root": str(sandbox), **cli}, env={})


def test_config_file_errors(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    with pytest.raises(ValueError, match="Cannot load"):
        ServerConfig.load(env={}, config_path=str(bad))
    unknown = tmp_path / "unknown.json"
    unknown.write_text(json.dumps({"colour": "blue"}))
    with pytest.raises(ValueError, match="Unknown config keys"):
        ServerConfig.load(env={}, config_path=str(unknown))


def test_print_config_cli(capsys, sandbox):
    assert fsm.main(["--root", str(sandbox), "--read-only", "--print-config"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["allow_write"] is False and out["sources"]["allow_write"] == "cli"


def test_bad_config_exits_2(capsys):
    assert fsm.main(["--root", "/nope/nope", "--print-config"]) == 2


# ---------------- stdio ---------------- #

def test_stdio_framing(sandbox, tmp_path):
    traffic = tmp_path / "traffic.jsonl"
    server = FilesystemMCPServer(ServerConfig.load(cli={"root": str(sandbox), "traffic_log": str(traffic)}, env={}))
    lines = [
        b"this is not json",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}}}).encode(),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
        b"",
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode(),
        json.dumps([{"jsonrpc": "2.0", "id": 3, "method": "ping"}, {"jsonrpc": "2.0", "id": 4, "method": "x"}]).encode(),
    ]
    stdin = io.BytesIO(b"\n".join(lines) + b"\n")
    stdout = io.BytesIO()
    fsm.serve_stdio(server, stdin=stdin, stdout=stdout)
    out = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert all(b"\n" not in json.dumps(o).encode() for o in out)
    assert out[0]["error"]["code"] == fsm.PARSE_ERROR and out[0]["id"] is None
    assert out[1]["id"] == 1 and out[1]["result"]["protocolVersion"] == "2025-11-25"
    rest = {json.dumps(o, sort_keys=True) for o in out[2:]}
    by_id = {}
    for o in out[2:]:
        if isinstance(o, list):
            for x in o:
                by_id[x["id"]] = x
        else:
            by_id[o["id"]] = o
    assert len(by_id[2]["result"]["tools"]) == 8
    assert by_id[3]["result"] == {} and by_id[4]["error"]["code"] == fsm.METHOD_NOT_FOUND
    assert rest
    logged = [json.loads(line) for line in traffic.read_text().splitlines()]
    assert {e["dir"] for e in logged} == {"in", "out"}


# ---------------- Streamable HTTP ---------------- #

@pytest.fixture
def http_server(sandbox):
    server = FilesystemMCPServer(ServerConfig.load(cli={"root": str(sandbox), "port": 0, "transport": "http"}, env={}))
    httpd = fsm.make_http_server(server)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    server.stop_all_watchers()


def _post(url, body, headers=None):
    req = urllib.request.Request(url + "/mcp", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            data = r.read()
            return r.status, dict(r.headers), json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        data = e.read()
        return e.code, dict(e.headers), json.loads(data) if data else None


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "http-test"}}}


def test_http_session_lifecycle(http_server):
    status, headers, body = _post(http_server, INIT)
    assert status == 200 and body["result"]["protocolVersion"] == "2025-06-18"
    sid = headers["Mcp-Session-Id"]
    h = {"Mcp-Session-Id": sid, "MCP-Protocol-Version": "2025-06-18"}
    assert _post(http_server, {"jsonrpc": "2.0", "method": "notifications/initialized"}, h)[0] == 202
    status, _, body = _post(http_server, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                          "params": {"name": "list_files", "arguments": {"directory": "resumes"}}}, h)
    assert status == 200 and body["result"]["structuredContent"]["count"] == 8
    req = urllib.request.Request(http_server + "/mcp", method="DELETE", headers={"Mcp-Session-Id": sid})
    with urllib.request.urlopen(req) as r:
        assert r.status == 204
    assert _post(http_server, {"jsonrpc": "2.0", "id": 3, "method": "ping"}, h)[0] == 404


def test_http_errors(http_server):
    assert _post(http_server, {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 400                    # no session
    assert _post(http_server, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"Mcp-Session-Id": "x"})[0] == 404
    assert _post(http_server, INIT, {"Origin": "https://evil.example"})[0] == 403                          # DNS rebinding guard
    assert _post(http_server, INIT, {"Origin": "http://localhost:3000"})[0] == 200
    assert _post(http_server, INIT, {"MCP-Protocol-Version": "1999-01-01"})[0] == 400
    req = urllib.request.Request(http_server + "/mcp", data=b"{oops", method="POST", headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400 and json.loads(exc.value.read())["error"]["code"] == fsm.PARSE_ERROR


def test_http_discovery_endpoints(http_server):
    with urllib.request.urlopen(http_server + "/healthz") as r:
        assert json.loads(r.read())["status"] == "ok"
    with urllib.request.urlopen(http_server + "/") as r:
        assert json.loads(r.read())["mcp_endpoint"] == "/mcp"
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(http_server + "/mcp")
    assert exc.value.code == 405


# ---------------- security ---------------- #

def test_non_loopback_http_requires_token(sandbox):
    with pytest.raises(ValueError, match="auth_token"):
        ServerConfig.load(cli={"root": str(sandbox), "transport": "http", "host": "0.0.0.0"}, env={})
    cfg = ServerConfig.load(cli={"root": str(sandbox), "transport": "http", "host": "0.0.0.0", "auth_token": "s3cret"}, env={})
    assert cfg.public_dict()["auth_token"] == "***"


def test_http_bearer_token(sandbox):
    server = FilesystemMCPServer(ServerConfig.load(cli={"root": str(sandbox), "port": 0, "transport": "http",
                                                        "auth_token": "s3cret"}, env={}))
    httpd = fsm.make_http_server(server)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, headers, body = _post(url, INIT)
        assert status == 401 and "Bearer" in headers.get("WWW-Authenticate", "")
        assert _post(url, INIT, {"Authorization": "Bearer wrong"})[0] == 401
        assert _post(url, INIT, {"Authorization": "Bearer s3cret"})[0] == 200
        with urllib.request.urlopen(url + "/healthz") as r:          # health stays open for probes
            assert r.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize("path", [r"\\evil\share\x.txt", "//evil/share/x.txt", r"\\?\C:\x", "~/x", "a\x00b",
    # a drive letter only means "another drive" on Windows; elsewhere "Z:" is an ordinary folder name
    pytest.param("Z:/x", marks=pytest.mark.skipif(sys.platform != "win32", reason="drive letters are Windows-only"))])
def test_unc_and_device_paths_rejected_without_touching_fs(client, path, monkeypatch):
    import pathlib

    import fs_tools
    root = fs_tools.get_root()
    monkeypatch.setattr(fs_tools, "get_root", lambda: root)     # the root itself may be resolved once

    def boom(*a, **k):
        raise AssertionError("resolve() must not be reached for " + path)
    monkeypatch.setattr(pathlib.Path, "resolve", boom)
    res = client.call("read_file", filepath=path)
    assert res["isError"] and res["structuredContent"]["error"]["type"] in ("outside_sandbox", "invalid_arguments")
