# Test scenarios

Run everything with `pytest` (99 tests, no network or API key). Each scenario links to the test that proves it.

## 1. JSON-RPC 2.0 compliance (`tests/test_jsonrpc_protocol.py`)

| # | Scenario | Expected |
|---|---|---|
| 1.1 | `initialize` with a supported version | same version back, capabilities `tools/resources/prompts/logging`, `serverInfo`, `instructions` |
| 1.2 | `initialize` with an unknown version | server answers with its latest version |
| 1.3 | `initialize` without `protocolVersion` | `-32602` with the supported versions in `error.data` |
| 1.4 | `tools/list` before `initialize` | `-32000`; `ping` still works |
| 1.5 | unknown method | `-32601`, `data.method` set, `id` echoed |
| 1.6 | `jsonrpc` missing or `"1.0"`, non-string method, object/bool `id`, non-object message | `-32600` |
| 1.7 | `params` is an array | `-32602` |
| 1.8 | notifications (even unknown ones, even malformed) | no response at all |
| 1.9 | batch mixing request, notification, bad method, garbage | one reply per request, in order; the notification gets none |
| 1.10 | empty batch / all-notification batch | `-32600` / no response |
| 1.11 | `id: null` | echoed as `null` |
| 1.12 | handler raises | `-32603`, message leaks no internals, connection keeps working |
| 1.13 | pagination with `page_size=3` | cursors walk through all tools exactly once; invalid / out-of-range cursor → `-32602` |
| 1.14 | `logging/setLevel error` | `info` log notifications suppressed; bad level → `-32602` |
| 1.15 | client negotiates `2024-11-05` | no `title`, `annotations` or `structuredContent` in replies |

## 2. Tools (`tests/test_mcp_tools.py`)

| # | Scenario | Expected |
|---|---|---|
| 2.1 | `tools/list` | all 4 Milestone 1 tools + 4 new tools, JSON Schemas, annotations (`read_file` read-only, `write_file` destructive) |
| 2.2 | `list_files`, `read_file` (PDF/DOCX/TXT), `search_in_file` | correct data; text content mirrors `structuredContent` |
| 2.3 | `read_file` asking for more than `max_read_chars` | server cap wins, `truncated: true` |
| 2.4 | path traversal `../../../etc/passwd` | `isError`, `outside_sandbox` |
| 2.5 | missing / extra / ill-typed / out-of-range arguments | `isError` result with `invalid_arguments` (not a protocol error) |
| 2.6 | unknown tool | protocol error `-32602` listing available tools |
| 2.7 | `write_file` twice | second call `file_exists`; `overwrite: true` succeeds |
| 2.8 | read-only mode | `write_file` hidden from `tools/list` and rejected |
| 2.9 | `batch_process read` over a directory | 8/8 succeeded, contents included, sorted |
| 2.10 | `batch_process metadata` with good, missing and escaping paths | input order kept; failures isolated with their own error types; summary counts |
| 2.11 | `batch_process search Kubernetes` | exactly Maria Garcia and David Okafor; `files_with_matches: 2` |
| 2.12 | both / neither of `filepaths` and `directory`; `search` without keyword; bad operation | `isError` |
| 2.13 | more files than `batch_max_files` | `limit_reached` |
| 2.14 | `_meta.progressToken` given | monotonic `notifications/progress` 0 → 8 with the token, plus a `batch_complete` log; none without a token |

## 3. Resources and prompts (`tests/test_resources_and_prompts.py`)

| # | Scenario | Expected |
|---|---|---|
| 3.1 | `resources/list` | `config://server` + one `fs:///` resource per resume with size, mime types, lastModified |
| 3.2 | `resources/templates/list` | `fs:///`, `dir:///`, `search:///`, `watch://` |
| 3.3 | read `fs:///…pdf`, `dir:///resumes?extension=docx`, `search:///…?keyword=terraform` | text / listing / hits |
| 3.4 | `config://server` | config values with their source (`cli`, `default`, …) and stats |
| 3.5 | missing file, sandbox escape, unknown scheme | `-32002` with `data.uri` |
| 3.6 | `search:///` without keyword, no `uri` | `-32602` |
| 3.7 | subscribe to a file / unknown watch / config | ok / `-32002` / `-32602`; unsubscribe works |
| 3.8 | `prompts/get screen_resumes` | message mentions `batch_process` and the skills; missing args / unknown prompt → `-32602` |

## 4. `watch_directory` (`tests/test_watch_directory.py`)

| # | Scenario | Expected |
|---|---|---|
| 4.1 | add, modify, delete a resume | `created` → `modified` → `deleted` events with seq cursor |
| 4.2 | extension filter and dotfiles | ignored |
| 4.3 | long-poll while a file arrives 0.4 s later | returns as soon as the event exists |
| 4.4 | empty poll | returns immediately, cursor unchanged |
| 4.5 | a file that keeps growing across polls | reported **once**, with its final size |
| 4.6 | same watch twice; stop; poll after stop | same `watch_id` (`already_watching`); stopped watch → `not_found` |
| 4.7 | missing dir, sandbox escape, watcher limit | `isError` with `not_found` / `outside_sandbox` / `limit_reached` |
| 4.8 | subscribed watch + new file | `resources/list_changed`, `resources/updated {uri: watch://…}`, log message; file now in `resources/list` |
| 4.9 | read `watch://…` | event count and recent events |

## 5. Configuration and transports (`tests/test_config_and_transports.py`)

| # | Scenario | Expected |
|---|---|---|
| 5.1 | file + env + CLI set the same keys | CLI > env > file > default; sources recorded; list/bool/float coercion |
| 5.2 | bad root, transport, log level, page size, interval; malformed or unknown-key config file | clear `ValueError`; CLI exits with code 2 |
| 5.3 | stdio: garbage line, handshake, blank line, request, batch | parse error with `id: null`, one JSON object per line, traffic log written |
| 5.4 | HTTP: initialize → session id → notification (202) → tool call → DELETE → reuse | 200 / 202 / 200 / 204 / 404 |
| 5.5 | HTTP: no session, unknown session, foreign Origin, bad protocol header, bad JSON | 400 / 404 / 403 / 400 / 400 + `-32700` |
| 5.6 | `GET /healthz`, `GET /`, `GET /mcp` | ok / server card / 405 |

## 6. Interoperability with the official SDK (`tests/test_sdk_interop.py`)

| # | Scenario | Expected |
|---|---|---|
| 6.1 | `mcp.ClientSession` over stdio | negotiates latest version, progress callback fires, `isError` surfaces, missing resource raises `McpError`, subscription + watch yields `ResourceListChanged`, `ResourceUpdated` and `Progress` notifications |
| 6.2 | `streamablehttp_client` against `--transport http` | session id assigned, tool call and templates work |
| 6.3 | candidate-db server through the same client | jobs, unknown job `isError`, save / shortlist / remove / history, slug validation, templates, `db://jobs` resource |

## 7. Agent workflow (`tests/test_agent.py`, real server subprocesses)

| # | Scenario | Expected |
|---|---|---|
| 7.1 | run once for `senior-python-backend` | node order `discover → load → list → batch_read → score → rank → persist`; John Doe #1; report written **by the server**; shortlist stored **in the DB server**; progress events; traffic tap sees JSON-RPC from both servers |
| 7.2 | watch mode: drop Lena Park, then delete Rahul Mehta | second `batch_process` reads **only** the new file; Lena #1; Rahul gone from ranking **and** DB; watch stopped at the end |
| 7.3 | fake LLM | notes on the top 2 only |
| 7.4 | LLM raises | report still produced; warning recorded |
| 7.5 | unknown job | fatal, stops before `list_resumes`, message lists known jobs |
| 7.6 | `--job-text` | skills extracted (`React, TypeScript, CSS, Testing`); Alex Chen #1; not persisted |
| 7.7 | ask mode with a scripted tool-calling model | calls `list_jobs` (db) and `list_files` (fs) through MCP; returns the final answer |
| 7.8 | hub errors | `isError` → `MCPToolError`; unknown server → `KeyError`; discovery content |

## 8. Scoring (`tests/test_scoring.py`) and architecture (`tests/test_architecture.py`)

| # | Scenario | Expected |
|---|---|---|
| 8.1 | "basic Python", "introductory Python course", "Docker (basic)" | partial; neighbouring skills unaffected |
| 8.2 | Java vs JavaScript, "go to market" vs Go | no false positives |
| 8.3 | each seeded role | obvious candidate ranks first (John / Sara / Maria / Alex) |
| 8.4 | `matching_agent.py` AST | no `fs_tools`, `sqlite3`, `pathlib`, `shutil`, `open()`, … must use `mcp_client` |
| 8.5 | `filesystem_mcp_server.py` AST | does not import the `mcp` SDK |

## Manual scenarios (shown in the demo video)

1. Dashboard: run once, open the `batch_process` request/response in the traffic inspector.
2. Watch mode: drop `resume_lena_park.txt` (TXT) and `resume_tomas_novak.docx` (DOCX); watch the toast for `list_changed`, the incremental read and the re-rank; remove Lena and watch her disappear.
3. Raw console over HTTP: send each error preset (`-32601`, `-32602`, `isError`, `-32002`, `-32600`, `-32700`) and a batch.
4. LLM notes with a local Ollama model, then "Ask the agent", which uses tools from both servers.
