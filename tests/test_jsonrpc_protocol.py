"""JSON-RPC 2.0 compliance and the MCP lifecycle (initialize / version negotiation / ping)."""
from __future__ import annotations

import base64

import pytest

import filesystem_mcp_server as fsm
from tests.conftest import Client


def test_initialize_negotiates_requested_version(client_factory):
    c = client_factory(initialize=False)
    r = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, msg_id=1)
    assert r["jsonrpc"] == "2.0" and r["id"] == 1
    res = r["result"]
    assert res["protocolVersion"] == "2025-06-18"
    assert res["serverInfo"]["name"] == "filesystem-mcp"
    assert res["capabilities"]["resources"] == {"subscribe": True, "listChanged": True}
    assert set(res["capabilities"]) == {"tools", "resources", "prompts", "logging"}
    assert "instructions" in res


def test_initialize_unknown_version_falls_back_to_latest(client_factory):
    c = client_factory(initialize=False)
    r = c.request("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {}})
    assert r["result"]["protocolVersion"] == fsm.LATEST_PROTOCOL_VERSION


def test_initialize_requires_protocol_version(client_factory):
    c = client_factory(initialize=False)
    r = c.request("initialize", {"capabilities": {}})
    assert r["error"]["code"] == fsm.INVALID_PARAMS
    assert r["error"]["data"]["supported"] == list(fsm.SUPPORTED_PROTOCOL_VERSIONS)


def test_requests_before_initialize_are_rejected_but_ping_works(client_factory):
    c = client_factory(initialize=False)
    assert c.request("ping")["result"] == {}
    r = c.request("tools/list")
    assert r["error"]["code"] == fsm.SERVER_NOT_INITIALIZED


def test_initialized_notification_marks_session_ready(client_factory):
    c = client_factory(initialize=False)
    c.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "x"}})
    assert not c.session.ready
    assert c.notify("notifications/initialized") is None
    assert c.session.ready


def test_method_not_found(client):
    r = client.request("tools/explode", msg_id="abc")
    assert r == {"jsonrpc": "2.0", "id": "abc", "error": {"code": -32601, "message": "Method not found: tools/explode",
                                                          "data": {"method": "tools/explode"}}}


@pytest.mark.parametrize("bad", [
    {"id": 1, "method": "ping"},                                  # missing jsonrpc
    {"jsonrpc": "1.0", "id": 1, "method": "ping"},                # wrong version
    {"jsonrpc": "2.0", "id": 1, "method": 42},                    # method not a string
    {"jsonrpc": "2.0", "id": {"x": 1}, "method": "ping"},         # id must be str/int
    {"jsonrpc": "2.0", "id": True, "method": "ping"},             # bool is not a valid id
    "not an object",
])
def test_invalid_request(client, bad):
    r = client.server.handle(bad, client.session)
    assert r["error"]["code"] == fsm.INVALID_REQUEST


def test_params_must_be_object(client):
    r = client.server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": [1, 2]}, client.session)
    assert r["error"]["code"] == fsm.INVALID_PARAMS and r["id"] == 5


def test_notifications_never_get_a_response(client):
    assert client.notify("tools/list") is None                    # even a request-method sent without id
    assert client.notify("does/not/exist") is None
    assert client.server.handle({"jsonrpc": "2.0", "method": "x", "params": [1]}, client.session) is None


def test_responses_from_client_are_ignored(client):
    assert client.server.handle({"jsonrpc": "2.0", "id": 9, "result": {}}, client.session) is None


def test_batch_mixed(client):
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "nope"},
        {"foo": "bar"},
    ]
    replies = client.server.handle(batch, client.session)
    assert [r.get("id") for r in replies] == [1, 2, None]
    assert replies[0]["result"] == {}
    assert replies[1]["error"]["code"] == fsm.METHOD_NOT_FOUND
    assert replies[2]["error"]["code"] == fsm.INVALID_REQUEST


def test_empty_batch_and_all_notification_batch(client):
    assert client.server.handle([], client.session)["error"]["code"] == fsm.INVALID_REQUEST
    assert client.server.handle([{"jsonrpc": "2.0", "method": "notifications/initialized"}], client.session) is None


def test_null_id_is_echoed(client):
    r = client.server.handle({"jsonrpc": "2.0", "id": None, "method": "ping"}, client.session)
    assert r == {"jsonrpc": "2.0", "id": None, "result": {}}


def test_internal_errors_are_contained(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setitem(client.server._methods, "ping", boom)
    r = client.request("ping")
    assert r["error"]["code"] == fsm.INTERNAL_ERROR
    assert "kaboom" not in r["error"]["message"]                 # no internals leaked
    assert client.request("tools/list")["result"]["tools"]      # connection still usable


def test_pagination(client_factory):
    c = client_factory(page_size=3)
    first = c.request("tools/list")["result"]
    assert len(first["tools"]) == 3 and "nextCursor" in first
    names = [t["name"] for t in first["tools"]]
    cursor = first["nextCursor"]
    while cursor:
        page = c.request("tools/list", {"cursor": cursor})["result"]
        names += [t["name"] for t in page["tools"]]
        cursor = page.get("nextCursor")
    assert names == [t["name"] for t in fsm.TOOL_DEFINITIONS]


@pytest.mark.parametrize("cursor", ["%%%", base64.urlsafe_b64encode(b"999").decode(), base64.urlsafe_b64encode(b"-1").decode()])
def test_invalid_cursor(client, cursor):
    assert client.request("resources/list", {"cursor": cursor})["error"]["code"] == fsm.INVALID_PARAMS


def test_logging_set_level_filters_notifications(client):
    assert client.request("logging/setLevel", {"level": "error"})["result"] == {}
    client.call("batch_process", operation="metadata", directory="resumes")
    assert not [n for n in client.notifications if n["method"] == "notifications/message"]
    assert client.request("logging/setLevel", {"level": "loud"})["error"]["code"] == fsm.INVALID_PARAMS


def test_cancellation_notification_is_accepted(client):
    assert client.notify("notifications/cancelled", {"requestId": 77, "reason": "user"}) is None
    assert 77 in client.session.cancelled


def test_stats_are_counted(client):
    before = dict(client.server.stats)
    client.request("ping")
    client.request("nope")
    assert client.server.stats["requests"] == before["requests"] + 2
    assert client.server.stats["errors"] == before["errors"] + 1


def test_old_protocol_hides_newer_fields(client_factory):
    c: Client = client_factory(protocol="2024-11-05")
    tool = c.request("tools/list")["result"]["tools"][0]
    assert "title" not in tool and "annotations" not in tool
    result = c.call("list_files", directory="resumes")
    assert "structuredContent" not in result                    # added in 2025-06-18
