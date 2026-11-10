# Voice-over script: MCP Resume Matcher demo (5:24)

Timed to `video/out/mcp-resume-matcher-demo.mp4` (30 fps, 1920×1080, no audio). Times are **absolute video time**. Each section's text fits its slot at a relaxed ~140 words/minute; *[cue]* marks a moment worth landing a sentence on. The captions on screen already carry the key facts, so the narration can be conversational.

Sped-up or slowed parts are labelled on screen (6× slow-motion replay, 8× and 2.5× fast-forwards).

---

## 0:00 – 0:08 · Intro

> This is Milestone 2 of the resume-matching project. The file-system tools from Milestone 1 are now a real Model Context Protocol server, and the matching agent talks to it only through MCP.

## 0:08 – 0:38 · Architecture

> Here's the shape of it. On the left is the host: a LangGraph agent and an MCP client hub that keeps one open session per server.
> *[0:16]* On the right are two servers. The filesystem server is written by hand, with no SDK, so every part of the protocol is in one file. It wraps the Milestone 1 tools unchanged and adds watch_directory and batch_process.
> *[0:24]* The second server, candidate-db, is built the opposite way, with the official SDK's FastMCP, on SQLite. Same client, very different servers.
> *[0:27]* The key difference from Milestone 1: the agent used to import the file tools directly. Now every read, write and query is a JSON-RPC message.

## 0:38 – 1:09 · On the wire

> Before any UI, here's the raw protocol. I pipe newline-delimited JSON straight into the server's stdin.
> *[0:39]* Initialize negotiates protocol version 2025-11-25. "Initialized" is a notification, so it never gets a reply.
> *[0:44]* Then a batch_process search with a progress token, an unknown method, a path that tries to escape the sandbox, a missing resource, and a deliberately broken line.
> *[0:51]* The answers come back in arrival order: minus 32601 for the unknown method, minus 32700 for the parse error with a null id, an isError result for the sandbox escape so a model can recover, minus 32002 for the missing resource…
> *[0:59]* …then the progress notifications, and the batch result last. Requests run concurrently and are matched by id, which JSON-RPC allows.

## 1:09 – 1:42 · Dashboard tour

> This dashboard is itself an MCP host. Everything you see was discovered over the protocol at start-up.
> *[1:15]* The filesystem server's tools: the four from Milestone 1 with read-only and destructive annotations, then the four new ones.
> *[1:24]* Every resume is also an MCP resource, and there are resource templates for files, directories, searches and watches.
> *[1:30]* Below is the candidate-database server. In the middle is the LangGraph state machine, and on the right every JSON-RPC message, live.

## 1:42 – 2:24 · One run, end to end

> I'll run the agent for the Senior Python Backend role.
> *[1:46]* That took about half a second, so here it is again in slow motion.
> *[1:47]* Discover the servers, load the job from the database server, list the resumes, then read all eight in one batch_process call on eight threads, with progress pushed back as it goes.
> *[1:52]* Score, write the report through the filesystem server, and store the results in the database.
> *[1:57]* The scores are deterministic: required skills, nice-to-haves, experience and depth. A weak mention like Alex's "basic Python" gets partial credit and is flagged.
> *[2:07]* Here's the actual tools/call request, and here's the response: structured content with per-file results and a summary.
> *[2:18]* And these are the progress notifications the server pushed during the batch.

## 2:24 – 3:04 · watch_directory

> Now watch mode. After the first ranking the agent calls watch_directory and gets back a watch id and a subscribable watch resource. Then it long-polls for events.
> *[2:30]* I drop a new resume into the folder, from outside, not through MCP. The server notices, pushes list_changed, and the agent reads only that one file. Lena Park goes straight to number one.
> *[2:37]* A Word document goes through the same path.
> *[2:45]* If I delete a resume, the deleted event triggers a re-rank, and the agent removes it from the database too.
> *[2:50]* Here's that push notification, and the long-poll call with its sequence cursor, which works on any transport.
> *[2:59]* When the window closes, the agent stops the watch and the graph ends.

## 3:04 – 4:11 · JSON-RPC and error handling

> This console talks to the same server over its second transport, Streamable HTTP, with a session id.
> *[3:08]* Tools/list works the same as over stdio. A batch_process search finds Kubernetes in two resumes in one call.
> *[3:20]* A JSON-RPC batch: two requests answered, and the notification gets nothing back.
> *[3:26]* Now the errors. Unknown method: minus 32601. Unknown tool: minus 32602.
> *[3:38]* Bad arguments aren't a protocol error. They come back as a tool result with isError true, because the newest spec says a model should be able to read that and retry. Same for the sandbox.
> *[3:49]* A missing resource is minus 32002, a wrong jsonrpc version is minus 32600, and broken JSON is minus 32700 with a null id.
> *[4:07]* And all of that HTTP traffic shows up in the same inspector.

## 4:11 – 4:51 · LLM notes and multi-MCP ask

> The LLM is optional. Here it's a local model through Ollama, writing two-sentence notes on the top three candidates. It never changes a score.
> *[4:25]* The notes cite real evidence from each resume and name the gap.
> *[4:33]* Ask mode: the model gets the read-only tools from both servers and chooses for itself. Read-only because resume text is untrusted input.
> *[4:37]* It picks list_jobs and get_shortlist on the database server, and answers from the stored scores. Every step went over MCP.

## 4:51 – 5:12 · Tests and CLI

> There are 107 tests: protocol edge cases, every error code, both transports, watch timing including half-written files, and the official MCP SDK client talking to the hand-written server.
> *[5:00]* There's also an architecture test that fails if the agent ever imports file-system code again. And the same agent runs from the command line.

## 5:12 – 5:24 · Deliverables

> That's everything: the MCP server, the refactored agent, the bonus database server, the diagrams and the tests. The code is on GitHub at Naman6019, mcp-resume-matcher. Thanks for watching.

---

### Recording tips

* Record the narration as a single take against the muted video, then add it in any editor (or as an `<Audio>` track in `video/src/DemoVideo.tsx`, then re-render with `npx remotion render DemoVideo`).
* Sections can shift by ±1 s. Each section opens on a header that fades in over 0.4 s, which is a natural place to start speaking.
