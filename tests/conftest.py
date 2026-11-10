"""Shared fixtures: an isolated sandbox (copy of the sample resumes) and helpers to talk
JSON-RPC to an in-process server without any transport."""
from __future__ import annotations

import itertools
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from filesystem_mcp_server import FilesystemMCPServer, ServerConfig, Session  # noqa: E402

_ids = itertools.count(1)


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    shutil.copytree(PROJECT / "resumes", root / "resumes")
    (root / "output").mkdir()
    return root


@pytest.fixture
def make_server(sandbox: Path):
    servers: list[FilesystemMCPServer] = []

    def factory(**overrides: Any) -> FilesystemMCPServer:
        cfg = ServerConfig.load(cli={"root": str(sandbox), **overrides}, env={})
        srv = FilesystemMCPServer(cfg)
        servers.append(srv)
        return srv

    yield factory
    for s in servers:
        s.stop_all_watchers()


class Client:
    """Minimal in-process client: records notifications the server pushes."""

    def __init__(self, server: FilesystemMCPServer, protocol: str = "2025-11-25", initialize: bool = True) -> None:
        self.server = server
        self.notifications: list[dict[str, Any]] = []
        self.session = Session(notify=self.notifications.append)
        if initialize:
            r = self.request("initialize", {"protocolVersion": protocol, "capabilities": {},
                                            "clientInfo": {"name": "pytest", "version": "1"}})
            assert "result" in r, r
            self.notify("notifications/initialized")

    def request(self, method: str, params: dict[str, Any] | None = None, msg_id: Any = None) -> dict[str, Any]:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": next(_ids) if msg_id is None else msg_id, "method": method}
        if params is not None:
            msg["params"] = params
        return self.server.handle(msg, self.session)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return self.server.handle({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})}, self.session)

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        r = self.request("tools/call", {"name": tool, "arguments": arguments})
        assert "result" in r, r
        return r["result"]


@pytest.fixture
def client(make_server) -> Client:
    return Client(make_server())


@pytest.fixture
def client_factory(make_server):
    def factory(protocol: str = "2025-11-25", initialize: bool = True, **cfg: Any) -> Client:
        return Client(make_server(**cfg), protocol=protocol, initialize=initialize)
    return factory
