"""filesystem_mcp_server -- the Milestone 1 file-system tools as a Model Context Protocol server.

This is a from-scratch implementation of the MCP *server* side (no MCP SDK on the server),
so every protocol detail is visible in one file:

* **JSON-RPC 2.0** framing: requests, notifications, responses, batches, standard error
  codes (-32700 / -32600 / -32601 / -32602 / -32603) plus MCP / server-defined codes.
* **MCP lifecycle**: ``initialize`` with protocol-version negotiation, the
  ``notifications/initialized`` handshake, ``ping``, cancellation.
* **Discovery**: ``tools/list``, ``resources/list``, ``resources/templates/list``,
  ``prompts/list`` (all cursor-paginated), plus ``config://server`` and HTTP ``/healthz``.
* **Tools**: the four Milestone 1 tools (``list_files``, ``read_file``, ``write_file``,
  ``search_in_file``) and the MCP-specific capabilities ``watch_directory`` (with
  ``poll_watch_events`` / ``stop_watch``) and ``batch_process``.
* **Resources**: every resume is a readable resource (``fs:///resumes/x.pdf``); directory
  listings and keyword searches are resource templates; watches are subscribable resources.
* **Server -> client notifications**: ``resources/list_changed``, ``resources/updated``,
  ``progress`` (for ``batch_process``) and ``message`` (logging).
* **Two transports**: stdio (newline-delimited JSON, the default) and Streamable HTTP
  (``POST /mcp`` returning ``application/json``, session ids, Origin checks).
* **Configuration management**: defaults < JSON config file < ``MCP_FS_*`` env vars < CLI flags.

Run it::

    python filesystem_mcp_server.py                         # stdio (what MCP clients spawn)
    python filesystem_mcp_server.py --transport http --port 8765
    python filesystem_mcp_server.py --print-config          # show the effective configuration

It is compatible with the official MCP Python SDK client and any spec-compliant host
(Claude Desktop, MCP Inspector, ...); ``tests/test_sdk_interop.py`` proves this.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

import jsonschema

import fs_tools

SERVER_NAME = "filesystem-mcp"
SERVER_TITLE = "Resume File System MCP Server"
SERVER_VERSION = "2.0.0"

#: Newest first.  ``initialize`` answers with the client's version if we support it,
#: otherwise with our latest (the client then decides whether to continue).
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

log = logging.getLogger(SERVER_NAME)

# --------------------------------------------------------------------------- #
# JSON-RPC 2.0 error codes
# --------------------------------------------------------------------------- #

PARSE_ERROR = -32700          # invalid JSON
INVALID_REQUEST = -32600      # not a valid JSON-RPC request object
METHOD_NOT_FOUND = -32601     # unknown method
INVALID_PARAMS = -32602       # bad params, unknown tool, bad cursor
INTERNAL_ERROR = -32603       # unexpected server failure
# -32000..-32099 is reserved for implementation-defined server errors.
SERVER_NOT_INITIALIZED = -32000   # request sent before the initialize handshake
RESOURCE_NOT_FOUND = -32002       # MCP convention for resources/read on an unknown URI
REQUEST_TOO_LARGE = -32004        # batch / payload over the configured limit

ERROR_NAMES = {
    PARSE_ERROR: "Parse error",
    INVALID_REQUEST: "Invalid Request",
    METHOD_NOT_FOUND: "Method not found",
    INVALID_PARAMS: "Invalid params",
    INTERNAL_ERROR: "Internal error",
    SERVER_NOT_INITIALIZED: "Server not initialized",
    RESOURCE_NOT_FOUND: "Resource not found",
    REQUEST_TOO_LARGE: "Request too large",
}


class JsonRpcError(Exception):
    """A protocol-level failure, serialised into the JSON-RPC ``error`` member."""

    def __init__(self, code: int, message: str | None = None, data: Any = None) -> None:
        super().__init__(message or ERROR_NAMES.get(code, "Server error"))
        self.code = code
        self.message = message or ERROR_NAMES.get(code, "Server error")
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err


class ToolError(Exception):
    """A tool *execution* failure: returned as a result with ``isError: true`` (not a
    protocol error), so the calling model can read it and correct itself."""

    def __init__(self, message: str, error_type: str = "tool_error") -> None:
        super().__init__(message)
        self.error_type = error_type


def _response(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error_response(msg_id: Any, err: JsonRpcError) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": err.to_dict()}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# Configuration management
# --------------------------------------------------------------------------- #

_LOG_LEVELS = ["debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"]


@dataclass
class ServerConfig:
    """Effective server configuration.

    Precedence (lowest -> highest): field defaults, JSON config file (``--config`` or
    ``MCP_FS_CONFIG``), ``MCP_FS_<FIELD>`` environment variables, command-line flags.
    """

    root: str = str(Path(__file__).resolve().parent)
    resource_dirs: list[str] = field(default_factory=lambda: ["resumes"])
    max_read_chars: int = 20_000
    allow_write: bool = True
    batch_max_files: int = 200
    batch_max_workers: int = 8
    watch_poll_interval: float = 0.5
    watch_max_watchers: int = 16
    page_size: int = 50
    log_level: str = "info"
    traffic_log: str | None = None
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765
    auth_token: str | None = None      # required bearer token for HTTP (mandatory off-loopback)
    source: dict[str, str] = field(default_factory=dict, repr=False)

    _ENV_PREFIX = "MCP_FS_"

    @classmethod
    def fields(cls) -> list[dataclasses.Field]:
        return [f for f in dataclasses.fields(cls) if f.name != "source"]

    @classmethod
    def load(cls, cli: dict[str, Any] | None = None, env: dict[str, str] | None = None,
             config_path: str | None = None) -> "ServerConfig":
        env = dict(os.environ if env is None else env)
        cfg = cls()
        cfg.source = {f.name: "default" for f in cls.fields()}

        path = config_path or env.get("MCP_FS_CONFIG")
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Cannot load config file {path!r}: {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"Config file {path!r} must contain a JSON object")
            unknown = set(data) - {f.name for f in cls.fields()}
            if unknown:
                raise ValueError(f"Unknown config keys in {path!r}: {sorted(unknown)}")
            for key, value in data.items():
                cfg._set(key, value, f"file:{Path(path).name}")

        for f in cls.fields():
            raw = env.get(cls._ENV_PREFIX + f.name.upper())
            if raw is not None and raw != "":
                cfg._set(f.name, raw, "env")

        for key, value in (cli or {}).items():
            if value is not None:
                cfg._set(key, value, "cli")

        cfg.validate()
        return cfg

    def _set(self, name: str, value: Any, source: str) -> None:
        current = getattr(self, name)
        ftype = {f.name: f.type for f in self.fields()}[name]
        if isinstance(value, str) and "list" in str(ftype):
            value = [v.strip() for v in value.split(",") if v.strip()]
        elif isinstance(value, str) and "bool" in str(ftype):
            value = value.strip().lower() in {"1", "true", "yes", "on"}
        elif isinstance(value, str) and "int" in str(ftype) and "str" not in str(ftype):
            value = int(value)
        elif isinstance(value, str) and "float" in str(ftype):
            value = float(value)
        elif isinstance(current, float) and isinstance(value, int):
            value = float(value)
        setattr(self, name, value)
        self.source[name] = source

    def validate(self) -> None:
        root = Path(self.root).expanduser()
        if not root.is_dir():
            raise ValueError(f"root {self.root!r} is not a directory")
        self.root = str(root.resolve())
        if self.transport not in {"stdio", "http"}:
            raise ValueError("transport must be 'stdio' or 'http'")
        if self.log_level not in _LOG_LEVELS:
            raise ValueError(f"log_level must be one of {_LOG_LEVELS}")
        for name in ("max_read_chars", "batch_max_files", "batch_max_workers", "watch_max_watchers", "page_size"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.watch_poll_interval <= 0:
            raise ValueError("watch_poll_interval must be > 0")
        if self.transport == "http" and self.host not in ("127.0.0.1", "localhost", "::1") and not self.auth_token:
            raise ValueError("binding the HTTP transport to a non-loopback host requires auth_token "
                             "(MCP_FS_AUTH_TOKEN); refusing to expose the file system unauthenticated")

    def public_dict(self) -> dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in self.fields()}
        if data.get("auth_token"):
            data["auth_token"] = "***"     # never echo secrets
        data["sources"] = dict(self.source)
        return data


# --------------------------------------------------------------------------- #
# Directory watching (watch_directory)
# --------------------------------------------------------------------------- #

@dataclass
class WatchEvent:
    seq: int
    type: str            # created | modified | deleted
    path: str            # sandbox-relative, forward slashes
    size_bytes: int | None
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class DirectoryWatcher:
    """Polls one directory and records created / modified / deleted files.

    Polling (rather than OS notifications) works the same on Windows, macOS, Linux,
    network shares and synced folders such as OneDrive.  A new or changed file is only
    reported once its size and mtime are unchanged for one full poll ("settled"), so a
    resume that is still being copied is never handed to the agent half-written.
    """

    def __init__(self, watch_id: str, directory: str, abs_dir: Path, extensions: set[str] | None,
                 interval: float, on_events: Callable[["DirectoryWatcher", list[WatchEvent]], None],
                 max_events: int = 1000) -> None:
        self.watch_id = watch_id
        self.directory = directory
        self.abs_dir = abs_dir
        self.extensions = extensions
        self.interval = interval
        self.on_events = on_events
        self.created_at = _now_iso()
        self.events: deque[WatchEvent] = deque(maxlen=max_events)
        self._seq = 0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._known = self._snapshot()
        self._pending: dict[str, tuple[float, int]] = {}
        self._thread = threading.Thread(target=self._run, name=f"watch-{watch_id}", daemon=True)
        self._thread.start()

    @property
    def uri(self) -> str:
        return f"watch://{self.watch_id}"

    @property
    def next_seq(self) -> int:
        return self._seq + 1

    def baseline(self) -> list[str]:
        return sorted(self._known)

    def _snapshot(self) -> dict[str, tuple[float, int]]:
        snap: dict[str, tuple[float, int]] = {}
        try:
            entries = list(os.scandir(self.abs_dir))
        except OSError:
            return snap
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_file():
                continue
            if self.extensions and Path(entry.name).suffix.lower() not in self.extensions:
                continue
            try:
                # Not entry.stat(): on Windows that is cached from the directory listing and
                # does not see a file growing while another process still has it open.
                st = os.stat(entry.path)
            except OSError:
                continue
            snap[entry.name] = (st.st_mtime, st.st_size)
        return snap

    def _rel(self, name: str) -> str:
        return f"{self.directory.rstrip('/')}/{name}" if self.directory not in ("", ".") else name

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - a watcher must never die silently
                log.exception("watcher %s tick failed", self.watch_id)

    def _tick(self) -> None:
        current = self._snapshot()
        new_events: list[tuple[str, str, int | None]] = []
        for name in set(self._known) - set(current):
            new_events.append(("deleted", name, None))
            self._known.pop(name, None)
        for name, sig in current.items():
            if self._known.get(name) == sig:
                self._pending.pop(name, None)
                continue
            if self._pending.get(name) == sig:          # unchanged for a full interval: settled
                kind = "modified" if name in self._known else "created"
                new_events.append((kind, name, sig[1]))
                self._known[name] = sig
                self._pending.pop(name, None)
            else:
                self._pending[name] = sig
        for name in list(self._pending):
            if name not in current:
                self._pending.pop(name)
        if not new_events:
            return
        emitted: list[WatchEvent] = []
        with self._cond:
            for kind, name, size in sorted(new_events, key=lambda e: e[1]):
                self._seq += 1
                ev = WatchEvent(self._seq, kind, self._rel(name), size, _now_iso())
                self.events.append(ev)
                emitted.append(ev)
            self._cond.notify_all()
        self.on_events(self, emitted)

    def events_since(self, since_seq: int, timeout: float = 0.0) -> list[WatchEvent]:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                found = [e for e in self.events if e.seq > since_seq]
                remaining = deadline - time.monotonic()
                if found or remaining <= 0 or self._stop.is_set():
                    return found
                self._cond.wait(remaining)

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def describe(self) -> dict[str, Any]:
        return {
            "watch_id": self.watch_id,
            "directory": self.directory,
            "extensions": sorted(self.extensions) if self.extensions else None,
            "poll_interval_seconds": self.interval,
            "created_at": self.created_at,
            "known_files": len(self._known),
            "event_count": self._seq,
            "next_seq": self.next_seq,
            "resource_uri": self.uri,
            "active": not self._stop.is_set(),
        }


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

Notifier = Callable[[dict[str, Any]], None]


@dataclass
class Session:
    """Per-connection protocol state (one for stdio, one per Mcp-Session-Id over HTTP)."""

    notify: Notifier | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    initialized: bool = False          # initialize request answered
    ready: bool = False                # notifications/initialized received
    protocol_version: str | None = None
    client_info: dict[str, Any] = field(default_factory=dict)
    client_capabilities: dict[str, Any] = field(default_factory=dict)
    log_level: str = "info"
    subscriptions: set[str] = field(default_factory=set)
    cancelled: set[Any] = field(default_factory=set)

    def send(self, method: str, params: dict[str, Any]) -> None:
        if self.notify is not None:
            try:
                self.notify({"jsonrpc": "2.0", "method": method, "params": params})
            except Exception:  # noqa: BLE001 - a broken pipe must not break the caller
                log.debug("dropping notification %s", method, exc_info=True)

    @property
    def structured_output(self) -> bool:
        """``structuredContent`` exists from protocol 2025-06-18 onwards."""
        return (self.protocol_version or "") >= "2025-06-18"


class ToolContext:
    """Handed to tool implementations: progress reporting + logging back to the client."""

    def __init__(self, server: "FilesystemMCPServer", session: Session, progress_token: Any, request_id: Any):
        self.server = server
        self.session = session
        self.progress_token = progress_token
        self.request_id = request_id

    def progress(self, progress: float, total: float | None = None, message: str | None = None) -> None:
        if self.progress_token is None:
            return
        params: dict[str, Any] = {"progressToken": self.progress_token, "progress": progress}
        if total is not None:
            params["total"] = total
        if message:
            params["message"] = message
        self.session.send("notifications/progress", params)

    def log(self, level: str, data: Any) -> None:
        self.server.log_to_client(self.session, level, data)

    @property
    def cancelled(self) -> bool:
        return self.request_id in self.session.cancelled


# --------------------------------------------------------------------------- #
# Tool definitions
# --------------------------------------------------------------------------- #

_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "title": "List files",
        "description": (
            "List the files in a directory (non-recursive) with name, relative path, size, modified "
            "date and extension. Use it first to discover which resumes exist. Optional extension filter."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory relative to the server root, e.g. 'resumes'."},
                "extension": {"type": "string", "description": "Optional filter such as 'pdf', '.docx' or 'txt'."},
            },
            "required": ["directory"],
            "additionalProperties": False,
        },
        "annotations": {"title": "List files", **_READ_ONLY},
    },
    {
        "name": "read_file",
        "title": "Read file",
        "description": (
            "Extract the text of one resume (PDF, DOCX or TXT) plus metadata: format, size, modified "
            "date, word count, page count. Long documents are truncated (metadata.truncated)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Path relative to the server root, e.g. 'resumes/resume_john_doe.pdf'."},
                "max_chars": {"type": "integer", "minimum": 1, "description": "Cap on returned characters."},
            },
            "required": ["filepath"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Read file", **_READ_ONLY},
    },
    {
        "name": "write_file",
        "title": "Write file",
        "description": (
            "Write UTF-8 text to a .txt/.md/.json/.csv/.log file (atomic; creates parent folders). "
            "Refuses to replace an existing file unless overwrite is true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Destination relative to the server root, e.g. 'output/report.md'."},
                "content": {"type": "string", "description": "Text to write."},
                "overwrite": {"type": "boolean", "description": "Replace the file if it exists (default false)."},
            },
            "required": ["filepath", "content"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Write file", "readOnlyHint": False, "destructiveHint": True,
                        "idempotentHint": False, "openWorldHint": False},
    },
    {
        "name": "search_in_file",
        "title": "Search in file",
        "description": (
            "Case-insensitive literal keyword search in ONE file (PDF, DOCX or TXT). Returns each match "
            "with line number and context. total_matches == 0 means the keyword is absent. For many "
            "files prefer batch_process with operation 'search'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "keyword": {"type": "string", "minLength": 1},
                "context_chars": {"type": "integer", "minimum": 0, "maximum": 1000},
                "max_matches": {"type": "integer", "minimum": 1, "maximum": 200},
                "whole_word": {"type": "boolean", "description": "Match whole words only (Java != JavaScript)."},
            },
            "required": ["filepath", "keyword"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Search in file", **_READ_ONLY},
    },
    {
        "name": "watch_directory",
        "title": "Watch directory for new resumes",
        "description": (
            "Start monitoring a directory for new, modified or deleted files (e.g. resumes arriving in "
            "'resumes'). Returns a watch_id and a subscribable resource URI. Collect events with "
            "poll_watch_events (works on every transport); stdio clients also receive "
            "notifications/resources/list_changed and resources/updated pushes. Idempotent: watching the "
            "same directory and filter again returns the existing watch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory relative to the server root."},
                "extensions": {"type": "array", "items": {"type": "string"},
                               "description": "Only report these extensions, e.g. ['pdf','docx','txt']."},
                "poll_interval_seconds": {"type": "number", "minimum": 0.1, "maximum": 60},
            },
            "required": ["directory"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Watch directory", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "poll_watch_events",
        "title": "Poll watch events",
        "description": (
            "Return events from a watch with seq greater than since_seq. With timeout_seconds > 0 this "
            "long-polls: it waits up to that long for the next event. Use next_seq from the result as the "
            "next since_seq."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "watch_id": {"type": "string"},
                "since_seq": {"type": "integer", "minimum": 0},
                "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 30},
            },
            "required": ["watch_id"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Poll watch events", **_READ_ONLY},
    },
    {
        "name": "stop_watch",
        "title": "Stop watch",
        "description": "Stop a directory watch started with watch_directory.",
        "inputSchema": {
            "type": "object",
            "properties": {"watch_id": {"type": "string"}},
            "required": ["watch_id"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Stop watch", "readOnlyHint": False, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "batch_process",
        "title": "Batch process files",
        "description": (
            "Run one operation over many files concurrently in a single call: 'read' (text + metadata), "
            "'search' (keyword hits) or 'metadata' (metadata only, no text). Give explicit filepaths, or a "
            "directory (+ optional extension) to process every matching file. Each file succeeds or fails "
            "independently; results keep input order and include a summary. Sends progress notifications "
            "when the request carries a progressToken."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["read", "search", "metadata"]},
                "filepaths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "directory": {"type": "string"},
                "extension": {"type": "string"},
                "keyword": {"type": "string", "minLength": 1, "description": "Required for 'search'."},
                "whole_word": {"type": "boolean"},
                "max_chars": {"type": "integer", "minimum": 1},
                "max_workers": {"type": "integer", "minimum": 1, "maximum": 32},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
        "annotations": {"title": "Batch process", **_READ_ONLY},
    },
]

RESOURCE_TEMPLATES: list[dict[str, Any]] = [
    {
        "uriTemplate": "fs:///{+path}",
        "name": "file-text",
        "title": "File text",
        "description": "Extracted text of any readable file (PDF, DOCX, TXT) under the server root. Equivalent to read_file.",
        "mimeType": "text/plain",
    },
    {
        "uriTemplate": "dir:///{+path}{?extension}",
        "name": "directory-listing",
        "title": "Directory listing",
        "description": "JSON listing of a directory, optionally filtered by extension. Equivalent to list_files.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "search:///{+path}{?keyword,whole_word}",
        "name": "file-search",
        "title": "Keyword search",
        "description": "JSON keyword-search results for one file. Equivalent to search_in_file.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "watch://{watch_id}",
        "name": "watch-events",
        "title": "Watch events",
        "description": "Recent events of an active directory watch. Subscribe to get resources/updated pushes.",
        "mimeType": "application/json",
    },
]

PROMPTS: list[dict[str, Any]] = [
    {
        "name": "screen_resumes",
        "title": "Screen resumes against a role",
        "description": "Ask the model to screen every resume in a folder for a role, using this server's tools.",
        "arguments": [
            {"name": "role", "description": "Job title, e.g. 'Senior Python Backend Engineer'", "required": True},
            {"name": "skills", "description": "Comma-separated must-have skills", "required": True},
            {"name": "directory", "description": "Folder with resumes (default 'resumes')", "required": False},
        ],
    },
]

_MIME = {".pdf": "application/pdf", ".txt": "text/plain",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


# --------------------------------------------------------------------------- #
# The server
# --------------------------------------------------------------------------- #

class FilesystemMCPServer:
    """Transport-independent MCP server: feed it decoded JSON, get decoded JSON back."""

    def __init__(self, config: ServerConfig | None = None) -> None:
        self.config = config or ServerConfig.load(cli={}, env={})
        os.environ["FS_ROOT"] = self.config.root   # fs_tools reads its sandbox root from here
        self.started_at = _now_iso()
        self.stats: dict[str, int] = {"requests": 0, "notifications": 0, "errors": 0, "tool_calls": 0}
        self._stats_lock = threading.Lock()
        self._watchers: dict[str, DirectoryWatcher] = {}
        self._watch_owner: dict[str, Session] = {}
        self._watch_lock = threading.Lock()
        self._methods: dict[str, Callable[[Session, dict[str, Any], Any], Any]] = {
            "initialize": self._initialize,
            "ping": lambda s, p, i: {},
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
            "resources/list": self._resources_list,
            "resources/templates/list": self._resource_templates_list,
            "resources/read": self._resources_read,
            "resources/subscribe": self._resources_subscribe,
            "resources/unsubscribe": self._resources_unsubscribe,
            "prompts/list": self._prompts_list,
            "prompts/get": self._prompts_get,
            "logging/setLevel": self._logging_set_level,
        }
        self._tools: dict[str, Callable[..., dict[str, Any]]] = {
            "list_files": self._tool_list_files,
            "read_file": self._tool_read_file,
            "write_file": self._tool_write_file,
            "search_in_file": self._tool_search_in_file,
            "watch_directory": self._tool_watch_directory,
            "poll_watch_events": self._tool_poll_watch_events,
            "stop_watch": self._tool_stop_watch,
            "batch_process": self._tool_batch_process,
        }
        self._tool_defs = {t["name"]: t for t in TOOL_DEFINITIONS}
        self._validators = {t["name"]: jsonschema.Draft202012Validator(t["inputSchema"]) for t in TOOL_DEFINITIONS}

    # ---------------- JSON-RPC dispatch ---------------- #

    def handle(self, message: Any, session: Session) -> Any:
        """Process one decoded JSON-RPC message or batch. Returns the response(s), or
        ``None`` when nothing must be sent (notifications, responses, all-notification batches)."""
        if isinstance(message, list):
            if not message:
                return _error_response(None, JsonRpcError(INVALID_REQUEST, "Empty batch"))
            if len(message) > self.config.batch_max_files:
                return _error_response(None, JsonRpcError(REQUEST_TOO_LARGE, f"Batch exceeds {self.config.batch_max_files} messages"))
            replies = [r for r in (self.handle_single(m, session) for m in message) if r is not None]
            return replies or None
        return self.handle_single(message, session)

    def handle_single(self, msg: Any, session: Session) -> dict[str, Any] | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self._count("errors")
            return _error_response(msg.get("id") if isinstance(msg, dict) else None,
                                   JsonRpcError(INVALID_REQUEST, "Expected a JSON-RPC 2.0 object with \"jsonrpc\": \"2.0\""))
        if "method" not in msg:
            # A response to something we sent (we never send requests) -- nothing to do.
            return None
        method, has_id, msg_id = msg.get("method"), "id" in msg, msg.get("id")
        if not isinstance(method, str) or (has_id and not (msg_id is None or isinstance(msg_id, (str, int))) or isinstance(msg_id, bool)):
            self._count("errors")
            return _error_response(msg_id if isinstance(msg_id, (str, int)) else None,
                                   JsonRpcError(INVALID_REQUEST, "method must be a string and id a string or integer"))
        params = msg.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            if not has_id:
                return None
            self._count("errors")
            return _error_response(msg_id, JsonRpcError(INVALID_PARAMS, "MCP params must be an object"))

        if not has_id:  # ---- notification: never answered, even on failure
            self._count("notifications")
            self._handle_notification(method, params, session)
            return None

        self._count("requests")
        try:
            if method not in self._methods:
                raise JsonRpcError(METHOD_NOT_FOUND, f"Method not found: {method}", {"method": method})
            if not session.initialized and method not in ("initialize", "ping"):
                raise JsonRpcError(SERVER_NOT_INITIALIZED, "Send 'initialize' before any other request")
            result = self._methods[method](session, params, msg_id)
            return _response(msg_id, result)
        except JsonRpcError as err:
            self._count("errors")
            return _error_response(msg_id, err)
        except Exception as exc:  # noqa: BLE001 - never let a bug kill the connection
            self._count("errors")
            log.exception("internal error handling %s", method)
            return _error_response(msg_id, JsonRpcError(INTERNAL_ERROR, f"Internal error: {type(exc).__name__}"))
        finally:
            session.cancelled.discard(msg_id)

    def _handle_notification(self, method: str, params: dict[str, Any], session: Session) -> None:
        if method == "notifications/initialized":
            session.ready = True
            log.info("client ready: %s", session.client_info.get("name", "?"))
        elif method == "notifications/cancelled":
            session.cancelled.add(params.get("requestId"))
            log.info("client cancelled request %s (%s)", params.get("requestId"), params.get("reason", "no reason"))
        elif method == "notifications/roots/list_changed":
            pass
        else:
            log.debug("ignoring unknown notification %s", method)

    def _count(self, key: str) -> None:
        with self._stats_lock:
            self.stats[key] += 1

    # ---------------- lifecycle ---------------- #

    def _initialize(self, session: Session, params: dict[str, Any], _id: Any) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        if not isinstance(requested, str):
            raise JsonRpcError(INVALID_PARAMS, "initialize requires a protocolVersion string",
                               {"supported": list(SUPPORTED_PROTOCOL_VERSIONS)})
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        session.protocol_version = version
        session.client_info = params.get("clientInfo") or {}
        session.client_capabilities = params.get("capabilities") or {}
        session.initialized = True
        return {
            "protocolVersion": version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": True, "listChanged": True},
                "prompts": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE, "version": SERVER_VERSION},
            "instructions": (
                "Sandboxed access to resume files. Discover files with list_files or resources/list, read them "
                "with read_file (or batch_process for many at once), search with search_in_file, and monitor a "
                "folder for new resumes with watch_directory + poll_watch_events. Paths are relative to the server root."
            ),
        }

    # ---------------- pagination ---------------- #

    def _paginate(self, items: list[Any], params: dict[str, Any], key: str) -> dict[str, Any]:
        cursor = params.get("cursor")
        start = 0
        if cursor is not None:
            try:
                start = int(base64.urlsafe_b64decode(str(cursor).encode()).decode())
                if start < 0 or start > len(items):
                    raise ValueError
            except Exception as exc:  # noqa: BLE001
                raise JsonRpcError(INVALID_PARAMS, "Invalid cursor") from exc
        end = start + self.config.page_size
        result: dict[str, Any] = {key: items[start:end]}
        if end < len(items):
            result["nextCursor"] = base64.urlsafe_b64encode(str(end).encode()).decode()
        return result

    # ---------------- tools ---------------- #

    def _tools_list(self, session: Session, params: dict[str, Any], _id: Any) -> dict[str, Any]:
        tools = []
        for t in TOOL_DEFINITIONS:
            if t["name"] == "write_file" and not self.config.allow_write:
                continue
            tool = dict(t)
            if (session.protocol_version or "") < "2025-06-18":
                tool.pop("title", None)          # 'title' arrived in 2025-06-18
            if (session.protocol_version or "") < "2025-03-26":
                tool.pop("annotations", None)    # annotations arrived in 2025-03-26
            tools.append(tool)
        return self._paginate(tools, params, "tools")

    def _tools_call(self, session: Session, params: dict[str, Any], msg_id: Any) -> dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or name not in self._tools or (name == "write_file" and not self.config.allow_write):
            raise JsonRpcError(INVALID_PARAMS, f"Unknown tool: {name!r}", {"available": sorted(self._tool_defs)})
        if not isinstance(args, dict):
            raise JsonRpcError(INVALID_PARAMS, "arguments must be an object")
        self._count("tool_calls")
        meta = params.get("_meta") or {}
        ctx = ToolContext(self, session, meta.get("progressToken"), msg_id)

        # Input-validation failures are *tool execution* errors (isError), not protocol
        # errors, so a model can read the message and retry with corrected arguments.
        errors = sorted(self._validators[name].iter_errors(args), key=lambda e: list(e.path))
        if errors:
            details = [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors]
            return self._tool_result(session, {"success": False, "error": {
                "type": "invalid_arguments", "message": "; ".join(details)}}, is_error=True)
        try:
            payload = self._tools[name](ctx, **args)
        except ToolError as exc:
            payload = {"success": False, "error": {"type": exc.error_type, "message": str(exc)}}
        except fs_tools.FsToolError as exc:
            payload = {"success": False, "error": {"type": exc.error_type, "message": str(exc)}}
        is_error = isinstance(payload, dict) and payload.get("success") is False
        return self._tool_result(session, payload, is_error=is_error)

    @staticmethod
    def _tool_result(session: Session, payload: dict[str, Any], is_error: bool) -> dict[str, Any]:
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}],
            "isError": is_error,
        }
        if session.structured_output:
            result["structuredContent"] = payload
        return result

    # -- Milestone 1 tools, wrapped -- #

    def _tool_list_files(self, ctx: ToolContext, directory: str, extension: str | None = None) -> dict[str, Any]:
        files = fs_tools.list_files(directory, extension)
        return {"success": True, "directory": directory, "count": len(files), "files": files}

    def _tool_read_file(self, ctx: ToolContext, filepath: str, max_chars: int | None = None) -> dict[str, Any]:
        limit = min(max_chars or self.config.max_read_chars, self.config.max_read_chars)
        return fs_tools.read_file(filepath, max_chars=limit)

    def _tool_write_file(self, ctx: ToolContext, filepath: str, content: str, overwrite: bool = False) -> dict[str, Any]:
        result = fs_tools.write_file(filepath, content, overwrite=overwrite)
        if result.get("success"):
            ctx.log("info", {"event": "file_written", "filepath": result.get("filepath")})
        return result

    def _tool_search_in_file(self, ctx: ToolContext, filepath: str, keyword: str, **kw: Any) -> dict[str, Any]:
        return fs_tools.search_in_file(filepath, keyword, **kw)

    # -- watch_directory -- #

    def _tool_watch_directory(self, ctx: ToolContext, directory: str, extensions: list[str] | None = None,
                              poll_interval_seconds: float | None = None) -> dict[str, Any]:
        abs_dir = fs_tools._resolve(directory)
        if not abs_dir.is_dir():
            raise ToolError(f"Not a directory: {directory!r}. Use list_files('.') to see what exists.", "not_found")
        exts = {fs_tools._normalise_extension(e) for e in extensions} if extensions else None
        rel = fs_tools._display(abs_dir)
        interval = float(poll_interval_seconds or self.config.watch_poll_interval)
        with self._watch_lock:
            for w in self._watchers.values():
                if w.abs_dir == abs_dir and w.extensions == exts and self._watch_owner.get(w.watch_id) is ctx.session:
                    return {"success": True, "already_watching": True, **w.describe(), "baseline_files": w.baseline()}
            if len(self._watchers) >= self.config.watch_max_watchers:
                raise ToolError(f"Watch limit reached ({self.config.watch_max_watchers}); stop_watch one first.", "limit_reached")
            watch_id = "w_" + uuid.uuid4().hex[:8]
            watcher = DirectoryWatcher(watch_id, rel, abs_dir, exts, interval, self._on_watch_events)
            self._watchers[watch_id] = watcher
            self._watch_owner[watch_id] = ctx.session
        ctx.log("info", {"event": "watch_started", "watch_id": watch_id, "directory": rel})
        return {"success": True, "already_watching": False, **watcher.describe(), "baseline_files": watcher.baseline()}

    def _on_watch_events(self, watcher: DirectoryWatcher, events: list[WatchEvent]) -> None:
        session = self._watch_owner.get(watcher.watch_id)
        if session is None:
            return
        if any(e.type in ("created", "deleted") for e in events):
            session.send("notifications/resources/list_changed", {})
        if watcher.uri in session.subscriptions:
            session.send("notifications/resources/updated", {"uri": watcher.uri})
        for e in events:
            uri = "fs:///" + quote(e.path)
            if e.type == "modified" and uri in session.subscriptions:
                session.send("notifications/resources/updated", {"uri": uri})
        self.log_to_client(session, "info", {"event": "watch_events", "watch_id": watcher.watch_id,
                                             "events": [e.to_dict() for e in events]})

    def _get_watcher(self, watch_id: str) -> DirectoryWatcher:
        w = self._watchers.get(watch_id)
        if w is None:
            raise ToolError(f"Unknown watch_id {watch_id!r}. Active: {sorted(self._watchers) or 'none'}", "not_found")
        return w

    def _tool_poll_watch_events(self, ctx: ToolContext, watch_id: str, since_seq: int = 0,
                                timeout_seconds: float = 0) -> dict[str, Any]:
        w = self._get_watcher(watch_id)
        events = w.events_since(since_seq, timeout=timeout_seconds)
        next_seq = (events[-1].seq if events else max(since_seq, w.next_seq - 1)) + 1
        return {"success": True, "watch_id": watch_id, "events": [e.to_dict() for e in events],
                "count": len(events), "next_seq": next_seq}

    def _tool_stop_watch(self, ctx: ToolContext, watch_id: str) -> dict[str, Any]:
        with self._watch_lock:
            w = self._watchers.pop(watch_id, None)
            self._watch_owner.pop(watch_id, None)
        if w is None:
            raise ToolError(f"Unknown watch_id {watch_id!r}", "not_found")
        w.stop()
        ctx.session.subscriptions.discard(w.uri)
        return {"success": True, "watch_id": watch_id, "stopped": True, "events_seen": w.next_seq - 1}

    def stop_all_watchers(self, session: Session | None = None) -> None:
        with self._watch_lock:
            for wid in list(self._watchers):
                if session is None or self._watch_owner.get(wid) is session:
                    self._watchers.pop(wid).stop()
                    self._watch_owner.pop(wid, None)

    # -- batch_process -- #

    def _tool_batch_process(self, ctx: ToolContext, operation: str, filepaths: list[str] | None = None,
                            directory: str | None = None, extension: str | None = None, keyword: str | None = None,
                            whole_word: bool = False, max_chars: int | None = None,
                            max_workers: int | None = None) -> dict[str, Any]:
        if bool(filepaths) == bool(directory):
            raise ToolError("Give exactly one of 'filepaths' or 'directory'.", "invalid_argument")
        if operation == "search" and not keyword:
            raise ToolError("operation 'search' requires 'keyword'.", "invalid_argument")
        if directory:
            listing = fs_tools.list_files(directory, extension)
            paths = [f["path"] for f in listing if f["extension"] in fs_tools.READABLE_EXTENSIONS]
        else:
            paths = list(dict.fromkeys(filepaths or []))     # de-duplicate, keep order
        if len(paths) > self.config.batch_max_files:
            raise ToolError(f"{len(paths)} files exceeds batch_max_files={self.config.batch_max_files}.", "limit_reached")
        workers = max(1, min(max_workers or self.config.batch_max_workers, self.config.batch_max_workers, len(paths) or 1))
        limit = min(max_chars or self.config.max_read_chars, self.config.max_read_chars)

        def run_one(path: str) -> dict[str, Any]:
            t0 = time.perf_counter()
            if ctx.cancelled:
                res: dict[str, Any] = {"success": False, "error": {"type": "cancelled", "message": "Request cancelled"}}
            elif operation == "read":
                res = fs_tools.read_file(path, max_chars=limit)
            elif operation == "metadata":
                res = fs_tools.read_file(path, max_chars=1)
                if res.get("success"):
                    res = {"success": True, "filepath": res["filepath"], "metadata": res["metadata"]}
            else:
                res = fs_tools.search_in_file(path, keyword or "", whole_word=whole_word)
            item = {"filepath": path, "success": bool(res.get("success")),
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}
            if res.get("success"):
                item["data"] = {k: v for k, v in res.items() if k not in ("success", "filepath")}
            else:
                item["error"] = res.get("error")
            return item

        started = time.perf_counter()
        results: list[dict[str, Any] | None] = [None] * len(paths)
        done = 0
        ctx.progress(0, len(paths), f"{operation}: 0/{len(paths)}")
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="batch") as pool:
            futures = {pool.submit(run_one, p): i for i, p in enumerate(paths)}
            for fut in futures:          # iterate in submission order; each still runs concurrently
                idx = futures[fut]
                results[idx] = fut.result()
                done += 1
                ctx.progress(done, len(paths), f"{operation}: {done}/{len(paths)} ({paths[idx]})")
        ok = sum(1 for r in results if r and r["success"])
        summary = {
            "operation": operation,
            "total": len(paths),
            "succeeded": ok,
            "failed": len(paths) - ok,
            "workers": workers,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        if operation == "search":
            summary["keyword"] = keyword
            summary["files_with_matches"] = sum(1 for r in results if r and r["success"] and r["data"]["total_matches"] > 0)
        ctx.log("info", {"event": "batch_complete", **summary})
        # The batch itself succeeded even when individual files failed; per-file errors are in results.
        return {"success": True, "summary": summary, "results": results}

    # ---------------- resources ---------------- #

    def _file_resources(self) -> list[dict[str, Any]]:
        out = []
        for d in self.config.resource_dirs:
            try:
                files = fs_tools.list_files(d)
            except fs_tools.FsToolError:
                continue
            for f in files:
                if f["extension"] not in fs_tools.READABLE_EXTENSIONS:
                    continue
                out.append({
                    "uri": "fs:///" + quote(f["path"]),
                    "name": f["name"],
                    "title": Path(f["name"]).stem.replace("_", " ").title(),
                    "description": f"{f['extension'][1:].upper()} file in {d}/ (served as extracted text)",
                    "mimeType": "text/plain",
                    "size": f["size_bytes"],
                    "annotations": {"audience": ["assistant"], "lastModified": f["modified"]},
                    "_meta": {"sourceMimeType": _MIME.get(f["extension"], "application/octet-stream")},
                })
        return out

    def _resources_list(self, session: Session, params: dict[str, Any], _id: Any) -> dict[str, Any]:
        items: list[dict[str, Any]] = [
            {"uri": "config://server", "name": "server-config", "title": "Server configuration",
             "description": "Effective configuration, where each value came from, uptime and request stats.",
             "mimeType": "application/json"},
        ]
        items += [{"uri": w.uri, "name": f"watch-{w.watch_id}", "title": f"Watch on {w.directory}/",
                   "description": "Directory watch events (subscribe for pushes)", "mimeType": "application/json"}
                  for w in list(self._watchers.values())]
        items += self._file_resources()
        if (session.protocol_version or "") < "2025-06-18":
            items = [{k: v for k, v in r.items() if k not in ("title", "_meta")} for r in items]
        return self._paginate(items, params, "resources")

    def _resource_templates_list(self, session: Session, params: dict[str, Any], _id: Any) -> dict[str, Any]:
        return self._paginate(RESOURCE_TEMPLATES, params, "resourceTemplates")

    def _resources_read(self, session: Session, params: dict[str, Any], _id: Any) -> dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            raise JsonRpcError(INVALID_PARAMS, "resources/read requires a 'uri' string")
        parts = urlsplit(uri)
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        path = unquote(parts.path.lstrip("/"))

        def as_json(payload: Any) -> dict[str, Any]:
            return {"contents": [{"uri": uri, "mimeType": "application/json",
                                  "text": json.dumps(payload, ensure_ascii=False, indent=1)}]}

        def fail(payload: dict[str, Any]) -> None:
            err = payload.get("error", {})
            code = RESOURCE_NOT_FOUND if err.get("type") in ("not_found", "outside_sandbox") else INVALID_PARAMS
            raise JsonRpcError(code, err.get("message", "Cannot read resource"), {"uri": uri, "type": err.get("type")})

        if parts.scheme == "config":
            if parts.netloc != "server":
                raise JsonRpcError(RESOURCE_NOT_FOUND, f"Unknown resource {uri}", {"uri": uri})
            return as_json({"server": {"name": SERVER_NAME, "version": SERVER_VERSION, "started_at": self.started_at,
                                       "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS)},
                            "config": self.config.public_dict(), "stats": dict(self.stats),
                            "active_watches": [w.describe() for w in self._watchers.values()]})
        if parts.scheme == "watch":
            w = self._watchers.get(parts.netloc)
            if w is None:
                raise JsonRpcError(RESOURCE_NOT_FOUND, f"No active watch {parts.netloc!r}", {"uri": uri})
            return as_json({**w.describe(), "recent_events": [e.to_dict() for e in list(w.events)[-50:]]})
        if parts.scheme == "fs":
            res = fs_tools.read_file(path, max_chars=self.config.max_read_chars)
            if not res.get("success"):
                fail(res)
            return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": res["content"],
                                  "_meta": {"metadata": res["metadata"]}}]}
        if parts.scheme == "dir":
            try:
                files = fs_tools.list_files(path or ".", query.get("extension"))
            except fs_tools.FsToolError as exc:
                fail({"error": {"type": exc.error_type, "message": str(exc)}})
            return as_json({"directory": path or ".", "count": len(files), "files": files})
        if parts.scheme == "search":
            if not query.get("keyword"):
                raise JsonRpcError(INVALID_PARAMS, "search:/// URIs need a ?keyword= query", {"uri": uri})
            res = fs_tools.search_in_file(path, query["keyword"],
                                          whole_word=query.get("whole_word", "").lower() in ("1", "true"))
            if not res.get("success"):
                fail(res)
            return as_json(res)
        raise JsonRpcError(RESOURCE_NOT_FOUND, f"Unsupported resource scheme {parts.scheme!r}",
                           {"uri": uri, "supported": ["config", "fs", "dir", "search", "watch"]})

    def _resources_subscribe(self, session: Session, params: dict[str, Any], _id: Any) -> dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str):
            raise JsonRpcError(INVALID_PARAMS, "resources/subscribe requires a 'uri' string")
        scheme = urlsplit(uri).scheme
        if scheme == "watch" and urlsplit(uri).netloc not in self._watchers:
            raise JsonRpcError(RESOURCE_NOT_FOUND, f"No active watch for {uri}", {"uri": uri})
        if scheme not in ("watch", "fs"):
            raise JsonRpcError(INVALID_PARAMS, "Only watch:// and fs:/// resources support subscriptions", {"uri": uri})
        session.subscriptions.add(uri)
        return {}

    def _resources_unsubscribe(self, session: Session, params: dict[str, Any], _id: Any) -> dict[str, Any]:
        session.subscriptions.discard(params.get("uri"))
        return {}

    # ---------------- prompts & logging ---------------- #

    def _prompts_list(self, session: Session, params: dict[str, Any], _id: Any) -> dict[str, Any]:
        return self._paginate(PROMPTS, params, "prompts")

    def _prompts_get(self, session: Session, params: dict[str, Any], _id: Any) -> dict[str, Any]:
        if params.get("name") != "screen_resumes":
            raise JsonRpcError(INVALID_PARAMS, f"Unknown prompt {params.get('name')!r}")
        args = params.get("arguments") or {}
        missing = [a for a in ("role", "skills") if not args.get(a)]
        if missing:
            raise JsonRpcError(INVALID_PARAMS, f"Missing required prompt arguments: {missing}")
        directory = args.get("directory") or "resumes"
        text = (
            f"Screen every resume in '{directory}' for the role: {args['role']}.\n"
            f"Must-have skills: {args['skills']}.\n"
            "1. Call batch_process with operation 'read' and that directory.\n"
            "2. For each candidate list matched and missing must-have skills with evidence.\n"
            "3. Rank candidates and save the ranking with write_file to output/screening.md."
        )
        return {"description": f"Screen resumes for {args['role']}",
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}

    def _logging_set_level(self, session: Session, params: dict[str, Any], _id: Any) -> dict[str, Any]:
        level = params.get("level")
        if level not in _LOG_LEVELS:
            raise JsonRpcError(INVALID_PARAMS, f"level must be one of {_LOG_LEVELS}")
        session.log_level = level
        return {}

    def log_to_client(self, session: Session, level: str, data: Any) -> None:
        if _LOG_LEVELS.index(level) >= _LOG_LEVELS.index(session.log_level):
            session.send("notifications/message", {"level": level, "logger": SERVER_NAME, "data": data})


# --------------------------------------------------------------------------- #
# Traffic log (optional JSONL tap used by the dashboard and for debugging)
# --------------------------------------------------------------------------- #

class TrafficLog:
    def __init__(self, path: str | None) -> None:
        self._fh = open(path, "a", encoding="utf-8") if path else None  # noqa: SIM115
        self._lock = threading.Lock()

    def record(self, direction: str, message: Any) -> None:
        if self._fh is None:
            return
        with self._lock:
            self._fh.write(json.dumps({"ts": _now_iso(), "dir": direction, "msg": message}, ensure_ascii=False) + "\n")
            self._fh.flush()


# --------------------------------------------------------------------------- #
# stdio transport
# --------------------------------------------------------------------------- #

def serve_stdio(server: FilesystemMCPServer, stdin: Any = None, stdout: Any = None) -> None:
    """Newline-delimited JSON-RPC over stdin/stdout. Requests run on a worker pool so a
    long ``batch_process`` or ``poll_watch_events`` never blocks ``ping`` or cancellation."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    traffic = TrafficLog(server.config.traffic_log)
    write_lock = threading.Lock()

    def write(obj: Any) -> None:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        with write_lock:
            stdout.write(data)
            stdout.flush()
        traffic.record("out", obj)

    session = Session(notify=write)
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="rpc")

    def work(msg: Any) -> None:
        reply = server.handle(msg, session)
        if reply is not None:
            write(reply)

    log.info("%s %s listening on stdio (root=%s)", SERVER_NAME, SERVER_VERSION, server.config.root)
    try:
        for raw in iter(stdin.readline, b""):
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                traffic.record("in", {"unparseable": line[:500].decode("utf-8", "replace")})
                write(_error_response(None, JsonRpcError(PARSE_ERROR, f"Parse error: {exc}")))
                continue
            traffic.record("in", msg)
            is_lifecycle = isinstance(msg, dict) and msg.get("method") in ("initialize", "notifications/initialized",
                                                                           "notifications/cancelled")
            if is_lifecycle or not isinstance(msg, (dict, list)):
                work(msg)                       # keep the handshake strictly ordered
            else:
                pool.submit(work, msg)
    finally:
        server.stop_all_watchers()
        pool.shutdown(wait=True, cancel_futures=False)
        log.info("stdin closed; server stopped")


# --------------------------------------------------------------------------- #
# Streamable HTTP transport (JSON responses)
# --------------------------------------------------------------------------- #

_LOCAL_ORIGINS = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")


def make_http_server(server: FilesystemMCPServer) -> ThreadingHTTPServer:
    """``POST /mcp`` (JSON-RPC in, ``application/json`` out), ``DELETE /mcp`` (end session),
    ``GET /healthz``. Server-push notifications need stdio; over HTTP use poll_watch_events."""
    sessions: dict[str, Session] = {}
    lock = threading.Lock()
    traffic = TrafficLog(server.config.traffic_log)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"{SERVER_NAME}/{SERVER_VERSION}"

        def log_message(self, fmt: str, *args: Any) -> None:  # route to logging, not stderr spam
            log.debug("http %s", fmt % args)

        def _send(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
            data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if data:
                self.wfile.write(data)

        def _authorized(self) -> bool:
            token = server.config.auth_token
            if not token:
                return True
            given = self.headers.get("Authorization", "")
            ok = given.startswith("Bearer ") and hmac.compare_digest(given[7:].encode(), token.encode())
            if not ok:
                self._send(401, _error_response(None, JsonRpcError(INVALID_REQUEST, "Missing or invalid bearer token")),
                           {"WWW-Authenticate": 'Bearer realm="filesystem-mcp"'})
            return ok

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or bool(_LOCAL_ORIGINS.match(origin))

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._send(200, {"status": "ok", "server": SERVER_NAME, "version": SERVER_VERSION,
                                 "sessions": len(sessions), "stats": server.stats})
            elif path == "/mcp":
                self._send(405, {"error": "SSE stream not offered; use POST"}, {"Allow": "POST, DELETE"})
            elif path == "/":
                self._send(200, {"name": SERVER_NAME, "version": SERVER_VERSION, "mcp_endpoint": "/mcp",
                                 "transport": "streamable-http", "protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS)})
            else:
                self._send(404, {"error": "not found"})

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            sid = self.headers.get("Mcp-Session-Id")
            with lock:
                sess = sessions.pop(sid, None) if sid else None
            if sess is None:
                self._send(404, {"error": "unknown session"})
                return
            server.stop_all_watchers(sess)
            self._send(204)

        def do_POST(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/mcp":
                self._send(404, {"error": "not found"})
                return
            if not self._authorized():
                return
            if not self._origin_ok():
                self._send(403, _error_response(None, JsonRpcError(INVALID_REQUEST, "Origin not allowed")))
                return
            pv = self.headers.get("MCP-Protocol-Version")
            if pv and pv not in SUPPORTED_PROTOCOL_VERSIONS:
                self._send(400, _error_response(None, JsonRpcError(INVALID_REQUEST, f"Unsupported MCP-Protocol-Version {pv}")))
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > 10_000_000:
                self._send(413, _error_response(None, JsonRpcError(REQUEST_TOO_LARGE)))
                return
            raw = self.rfile.read(length)
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send(400, _error_response(None, JsonRpcError(PARSE_ERROR, f"Parse error: {exc}")))
                return
            traffic.record("in", msg)
            is_init = isinstance(msg, dict) and msg.get("method") == "initialize"
            sid = self.headers.get("Mcp-Session-Id")
            headers: dict[str, str] = {}
            if is_init:
                sess = Session(notify=None)
                with lock:
                    sessions[sess.session_id] = sess
                headers["Mcp-Session-Id"] = sess.session_id
            else:
                if not sid:
                    self._send(400, _error_response(None, JsonRpcError(INVALID_REQUEST, "Missing Mcp-Session-Id header")))
                    return
                sess = sessions.get(sid)
                if sess is None:
                    self._send(404, _error_response(None, JsonRpcError(INVALID_REQUEST, "Unknown or expired session")))
                    return
            reply = server.handle(msg, sess)
            if reply is None:
                self._send(202, None, headers)
            else:
                traffic.record("out", reply)
                self._send(200, reply, headers)

    httpd = ThreadingHTTPServer((server.config.host, server.config.port), Handler)
    httpd.daemon_threads = True
    return httpd


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="JSON config file (also MCP_FS_CONFIG)")
    p.add_argument("--root", help="Sandbox root directory")
    p.add_argument("--resource-dirs", help="Comma-separated folders exposed via resources/list")
    p.add_argument("--transport", choices=["stdio", "http"])
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--log-level", choices=_LOG_LEVELS)
    p.add_argument("--traffic-log", help="Append every JSON-RPC message to this JSONL file")
    p.add_argument("--read-only", action="store_true", help="Disable write_file")
    p.add_argument("--auth-token", help="Bearer token required on HTTP requests (prefer MCP_FS_AUTH_TOKEN)")
    p.add_argument("--print-config", action="store_true", help="Print the effective configuration and exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cli = {"root": args.root, "resource_dirs": args.resource_dirs, "transport": args.transport, "host": args.host,
           "port": args.port, "log_level": args.log_level, "traffic_log": args.traffic_log,
           "allow_write": False if args.read_only else None, "auth_token": args.auth_token}
    try:
        config = ServerConfig.load(cli=cli, config_path=args.config)
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if args.print_config:
        print(json.dumps(config.public_dict(), indent=2))
        return 0
    py_level = {"debug": logging.DEBUG, "info": logging.INFO, "notice": logging.INFO}.get(config.log_level, logging.WARNING)
    # Logs go to stderr: on stdio, stdout belongs exclusively to the protocol.
    logging.basicConfig(level=py_level, stream=sys.stderr, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    server = FilesystemMCPServer(config)
    if config.transport == "http":
        httpd = make_http_server(server)
        log.info("%s %s on http://%s:%d/mcp (root=%s)", SERVER_NAME, SERVER_VERSION, config.host, config.port, config.root)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.stop_all_watchers()
            httpd.server_close()
    else:
        serve_stdio(server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
