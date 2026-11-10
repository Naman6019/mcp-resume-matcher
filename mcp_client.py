"""mcp_client -- one persistent MCP client hub for several servers.

The agent never touches the file system or the database directly: everything goes through
``MCPHub``, which holds one long-lived ``mcp.ClientSession`` (official SDK) per server.

Why not ``langchain_mcp_adapters.MultiServerMCPClient`` alone?  By default it opens a *new*
session (and, for stdio, a new server process) for every tool call, which would throw away
server-side state such as an active ``watch_directory``.  The hub keeps sessions open and
can still hand LangChain-compatible tools to an LLM via ``langchain_tools()``.

Extras used by the dashboard and CLI:
* ``on_traffic(server, direction, message)`` -- a tap on the raw JSON-RPC stream.
* ``on_notification(server, method, params)`` -- server pushes (list_changed, progress, log).
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import anyio
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.message import SessionMessage

PROJECT_DIR = Path(__file__).resolve().parent

TrafficFn = Callable[[str, str, dict[str, Any]], Any]
NotificationFn = Callable[[str, str, dict[str, Any]], Any]


class MCPToolError(RuntimeError):
    """A tool returned ``isError: true`` (the server is fine; the call failed)."""

    def __init__(self, server: str, tool: str, message: str, payload: Any = None) -> None:
        super().__init__(f"{server}.{tool}: {message}")
        self.server, self.tool, self.payload = server, tool, payload


@dataclass
class ServerSpec:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None          # set for Streamable HTTP instead of stdio
    stderr_log: str | None = None   # where the child's stderr goes (stdio only)

    @property
    def transport(self) -> str:
        return "http" if self.url else "stdio"


def default_server_specs(traffic_dir: Path | None = None, fs_root: str | None = None,
                         db_path: str | None = None) -> list[ServerSpec]:
    """The two servers this project ships, launched with the current interpreter."""
    logs = traffic_dir or PROJECT_DIR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    fs_env = {"MCP_FS_ROOT": fs_root or str(PROJECT_DIR), "MCP_FS_LOG_LEVEL": "info"}
    db_env = {"CANDIDATE_DB": db_path or str(PROJECT_DIR / "data" / "candidates.db")}
    return [
        ServerSpec("filesystem", sys.executable, [str(PROJECT_DIR / "filesystem_mcp_server.py")], fs_env,
                   str(PROJECT_DIR), stderr_log=str(logs / "filesystem-mcp.stderr.log")),
        ServerSpec("candidate_db", sys.executable, [str(PROJECT_DIR / "candidate_db_mcp_server.py")], db_env,
                   str(PROJECT_DIR), stderr_log=str(logs / "candidate-db-mcp.stderr.log")),
    ]


def _dump(msg: Any) -> dict[str, Any]:
    if isinstance(msg, SessionMessage):
        return msg.message.model_dump(by_alias=True, mode="json", exclude_none=True)
    return {"transport_error": repr(msg)}


def decode_tool_result(result: types.CallToolResult) -> Any:
    """Prefer ``structuredContent``; fall back to JSON in the first text block, then raw text."""
    if result.structuredContent is not None:
        sc = result.structuredContent
        # FastMCP wraps non-object returns as {"result": ...}
        return sc["result"] if set(sc) == {"result"} else sc
    texts = [c.text for c in result.content if isinstance(c, types.TextContent)]
    if not texts:
        return None
    try:
        return json.loads(texts[0])
    except json.JSONDecodeError:
        return "\n".join(texts)


class MCPHub:
    def __init__(self, specs: list[ServerSpec], on_traffic: TrafficFn | None = None,
                 on_notification: NotificationFn | None = None, client_name: str = "resume-matching-agent") -> None:
        self.specs = {s.name: s for s in specs}
        self.on_traffic = on_traffic
        self.on_notification = on_notification
        self.client_info = types.Implementation(name=client_name, version="2.0.0")
        self.sessions: dict[str, ClientSession] = {}
        self.init_results: dict[str, types.InitializeResult] = {}
        self._stack: AsyncExitStack | None = None

    # ---------------- lifecycle ---------------- #

    async def __aenter__(self) -> "MCPHub":
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            for spec in self.specs.values():
                await self._connect(spec)
        except BaseException:
            await self._stack.__aexit__(*sys.exc_info())
            raise
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(*exc)
            self._stack = None
            self.sessions.clear()

    async def _connect(self, spec: ServerSpec) -> None:
        assert self._stack is not None
        if spec.url:
            read, write, _ = await self._stack.enter_async_context(streamablehttp_client(spec.url))
        else:
            env = {**get_default_environment(), "PYTHONIOENCODING": "utf-8", **spec.env}
            params = StdioServerParameters(command=spec.command or sys.executable, args=spec.args, env=env, cwd=spec.cwd)
            errlog = self._stack.enter_context(open(spec.stderr_log, "a", encoding="utf-8")) if spec.stderr_log \
                else open(os.devnull, "w")  # noqa: SIM115
            read, write = await self._stack.enter_async_context(stdio_client(params, errlog=errlog))
        read, write = await self._tap(spec.name, read, write)

        async def handle_message(message: Any) -> None:
            if isinstance(message, types.ServerNotification) and self.on_notification:
                root = message.root
                params = root.params.model_dump(by_alias=True, mode="json", exclude_none=True) if root.params else {}
                await _maybe_await(self.on_notification(spec.name, root.method, params))

        session = await self._stack.enter_async_context(
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120),
                          message_handler=handle_message, client_info=self.client_info))
        self.init_results[spec.name] = await session.initialize()
        self.sessions[spec.name] = session

    async def _tap(self, name: str, read: Any, write: Any) -> tuple[Any, Any]:
        """Insert pass-through streams that report every JSON-RPC message to ``on_traffic``."""
        if self.on_traffic is None:
            return read, write
        assert self._stack is not None
        in_send, in_recv = anyio.create_memory_object_stream[SessionMessage | Exception](100)
        out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](100)
        tg = await self._stack.enter_async_context(anyio.create_task_group())
        self._stack.callback(tg.cancel_scope.cancel)   # runs first on exit: stop the pumps

        async def pump_in() -> None:
            async with in_send:
                async for msg in read:
                    await _maybe_await(self.on_traffic(name, "in", _dump(msg)))
                    await in_send.send(msg)

        async def pump_out() -> None:
            async with out_recv:
                async for msg in out_recv:
                    await _maybe_await(self.on_traffic(name, "out", _dump(msg)))
                    await write.send(msg)

        tg.start_soon(pump_in)
        tg.start_soon(pump_out)
        return in_recv, out_send

    # ---------------- calls ---------------- #

    def session(self, server: str) -> ClientSession:
        try:
            return self.sessions[server]
        except KeyError:
            raise KeyError(f"No MCP server named {server!r}; connected: {sorted(self.sessions)}") from None

    async def call_tool(self, server: str, tool: str, arguments: dict[str, Any] | None = None,
                        progress: Callable[[float, float | None, str | None], Awaitable[None]] | None = None,
                        timeout: float | None = None) -> Any:
        result = await self.session(server).call_tool(
            tool, arguments or {}, read_timeout_seconds=timedelta(seconds=timeout) if timeout else None,
            progress_callback=progress)
        payload = decode_tool_result(result)
        if result.isError:
            msg = payload.get("error", {}).get("message") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) \
                else (payload if isinstance(payload, str) else json.dumps(payload))
            raise MCPToolError(server, tool, str(msg), payload)
        return payload

    async def read_resource(self, server: str, uri: str) -> str:
        res = await self.session(server).read_resource(uri)  # type: ignore[arg-type]
        return "\n".join(getattr(c, "text", "") for c in res.contents)

    async def discover(self) -> dict[str, Any]:
        """Everything each server advertises: capabilities, tools, resources, templates, prompts."""
        out: dict[str, Any] = {}
        for name, session in self.sessions.items():
            init = self.init_results[name]
            caps = init.capabilities
            entry: dict[str, Any] = {
                "server_info": init.serverInfo.model_dump(exclude_none=True),
                "protocol_version": init.protocolVersion,
                "transport": self.specs[name].transport,
                "capabilities": caps.model_dump(exclude_none=True),
                "instructions": init.instructions,
                "tools": [], "resources": [], "resource_templates": [], "prompts": [],
            }
            if caps.tools is not None:
                entry["tools"] = [{"name": t.name, "description": t.description, "annotations":
                                   t.annotations.model_dump(exclude_none=True) if t.annotations else None,
                                   "input_schema": t.inputSchema} for t in (await session.list_tools()).tools]
            if caps.resources is not None:
                entry["resources"] = [{"uri": str(r.uri), "name": r.name, "mime_type": r.mimeType}
                                      for r in (await session.list_resources()).resources]
                entry["resource_templates"] = [{"uri_template": t.uriTemplate, "name": t.name, "description": t.description}
                                               for t in (await session.list_resource_templates()).resourceTemplates]
            if caps.prompts is not None:
                entry["prompts"] = [{"name": p.name, "description": p.description}
                                    for p in (await session.list_prompts()).prompts]
            out[name] = entry
        return out

    async def langchain_tools(self, servers: list[str] | None = None, read_only: bool = True) -> list[Any]:
        """Wrap the tools of the open sessions as LangChain tools (for LLM tool calling).

        ``read_only`` (the default) keeps only tools annotated ``readOnlyHint: true``: resume text
        is untrusted input, so a prompt-injected model must not be able to write or delete."""
        from langchain_mcp_adapters.tools import load_mcp_tools

        tools: list[Any] = []
        for name in servers or list(self.sessions):
            loaded = await load_mcp_tools(self.sessions[name], server_name=name)
            if read_only:
                allowed = {t.name for t in (await self.sessions[name].list_tools()).tools
                           if t.annotations is not None and t.annotations.readOnlyHint}
                loaded = [t for t in loaded if t.name in allowed]
            tools += loaded
        return tools


async def _maybe_await(value: Any) -> None:
    if hasattr(value, "__await__"):
        await value
