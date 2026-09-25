# aiper

Agentic document generation and compliance analysis for the space sector.

`aiper` plans a piece of documentation work, delegates it to specialist
sub-agents, retrieves evidence page by page from your own sources, and returns
a cited draft or a compliance matrix — then keeps every subsequent edit under
git-style version control.

Everything runs in Docker. Nothing is installed on the host.

---

## Table of contents

1. [Quick start](#quick-start)
2. [The experience](#the-experience)
3. [Technical solution](#technical-solution)
4. [How it works](#how-it-works)
5. [Mock backend](#mock-backend)
6. [Repository layout](#repository-layout)
7. [Configuration reference](#configuration-reference)
8. [Known constraints](#known-constraints)

---

## Quick start

### Without any credentials (mock backend)

The fastest way to see the whole product working — no Azure key, no Postgres,
no Qdrant, no `.env` needed:

```bash
docker compose -f docker-compose.mock.yml up --build
```

Open http://localhost:3000 and sign in with **demo@aiper.dev** / **demo1234**,
or register a new account. Two sample documents are already indexed, so both
agent modes work immediately. Registration process does not use top, or email sending libraries.

This runs the frontend against `mock/`, a separate service that serves the same
HTTP contract. See [Mock backend](#mock-backend).

### With Azure OpenAI

```bash
cp .env.example .env
```

Fill in at least:

```env
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_CHAT_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME=text-embedding-3-large
```

Then `docker compose up --build`.

| Service        | URL                             |
| -------------- | ------------------------------- |
| Frontend       | http://localhost:3000           |
| API            | http://localhost:8000           |
| API docs       | http://localhost:8000/docs      |
| Health         | http://localhost:8000/health    |
| Qdrant console | http://localhost:6333/dashboard |
| PostgreSQL     | localhost:5432                  |

The schema is created and the space-sector templates are seeded automatically
on the backend's first boot.

```bash
docker compose logs -f backend
```

```bash
docker compose down -v
```

`-v` also drops the Postgres, Qdrant and upload volumes — a clean slate.

---

## The experience

Four destinations, one sidebar. Nothing else to learn.

| | | |
| --- | --- | --- |
| **Chat** | generate and compare | Attach sources, pick a mode, watch the work happen |
| **Traceability Editor** | versioned documents | Write, commit, diff, share |
| **Document Vault** | indexed sources | Everything the agent can cite |
| **Settings** | workspace and theme | Account, appearance, templates |

### Chat — the workspace

```
┌────────────┬──────────────────────────────────────────────┐
│            │  Chat                                        │
│  Chat      ├──────────────────────────────────────────────┤
│  Editor    │  ▸ Agent activity          4 steps           │
│  Vault     │    ✓ Plan updated                            │
│  Settings  │    ✓ Searching indexed pages                 │
│            │    ⟳ Delegating to  document_writer          │
│            │                                              │
│  ────────  │  ## 4 Mission environment                    │
│  Sessions  │  The system shall withstand a total ionising │
│            │  dose of 30 krad(Si)  [MIS-REQ.pdf, p.2]     │
│            ├──────────────────────────────────────────────┤
│            │ [Document Generation|Feature Comparison] [▾] │
│  ────────  │ ┌──────────────────────────────────────────┐ │
│  Ada L.    │ │ Draft the mission spec from the study…   │ │
│  ada@esa   │ │ 📎 Attach                            [↑] │ │
└────────────┴─┴──────────────────────────────────────────┴─┘
```

**Pick a mode before you ask.** *Document Generation* drafts a compliant
deliverable from a template. *Feature Comparison* checks N documents against
one target and returns a compliance matrix.

**Attach by dragging anywhere** onto the composer — PDF, PPTX, DOCX, TXT. Each
file becomes a chip showing how many pages were indexed. A file that can't be
read (a scanned PDF with no text layer) turns amber and says why, rather than
failing silently.

In Feature Comparison, one chip is marked **target** with the crosshair icon;
everything else becomes a source. The target is visibly outlined so the
direction of the check is never ambiguous.

### Watching the agent work

Most tools hide this. The **Agent activity** panel fills in live as the run
proceeds:

| | |
| --- | --- |
| `Plan updated` | a checklist, items moving pending → in-progress → done |
| `Searching indexed pages` | expand to see which files and which page numbers |
| `Delegating to document_writer` | the specialist sub-agent, badged by name |
| `Building compliance matrix` | the final assembly step |

Rows spin while running and tick when finished. Everything starts collapsed, so
it reads as a tidy list of steps rather than a log dump — open any row for the
detail. The trace is stored with the answer, so reopening a conversation
replays the reasoning, not just the conclusion.

### The answer

Streams in word by word, as Markdown — headings, tables, and in comparison mode
coloured verdict chips: **COMPLIANT** · **PARTIAL** · **NON-COMPLIANT** ·
**NOT ADDRESSED**.

Every claim taken from a source carries a citation like
`[Supplier-Datasheet.pdf, p.14]`. Those page numbers are **exact, not
approximate** — the whole retrieval design exists to make them checkable. Open
the source, turn to the page, verify.

Then: **Copy**, or **Open in editor**.

### Traceability Editor — git for documents, without the vocabulary

It looks like a word processor: a white page, a formatting toolbar, headings,
lists, tables. It does not autosave into oblivion.

When you have changes, **Commit** lights up and asks what you changed. Down the
right side runs the **history** — a spine of commits, newest first, each showing
its message, author, time and `+12 −3 ~5`. A small icon separates what the
assistant drafted from what a person edited.

Click any commit for the **diff**: additions in muted green, deletions struck
through in muted red, edits shown as the old line above the new one. Long
unchanged stretches collapse to `··· 14 unchanged blocks ···`, so you read only
what moved. Restoring an old revision *adds* a commit — nothing is ever erased.

**Share** takes an email and a role. Viewers get the document read-only with no
commit button. Someone without an account yet shows as *pending* and is
connected automatically when they register. No email is actually sent — that
step is deliberately stubbed.



## Technical solution

### Stack

| Layer | Technology | Why this one |
| --- | --- | --- |
| **Agent runtime** | [`deepagents`](https://docs.langchain.com/oss/python/deepagents/overview) | Supplies the harness we would otherwise hand-roll: `SKILL.md` skills with progressive disclosure, a planner, a virtual filesystem for staging long drafts, sub-agent delegation with isolated context windows, and context compaction. Built on LangGraph, so the whole run is a streamable graph. |
| **LLM orchestration** | LangChain, LangGraph | Middleware architecture (`before_model`, `wrap_model_call`) is what makes the context strategy composable rather than bolted on. `astream_events` gives the per-tool event stream the UI feed renders. |
| **Model provider** | Azure OpenAI via `AzureChatOpenAI` | Enterprise/space customers generally require EU-region, tenant-isolated inference. Three model roles are configured separately (supervisor / writer / analyst) so temperature can differ per job. |
| **Vector store** | **Qdrant** | Payload filtering is first-class, which is what enforces per-user and per-file isolation on every query. Runs as a container with no managed dependency. |
| **Relational store** | PostgreSQL | Holds users, documents, the full revision graph, materialised diffs, chat transcripts and file metadata. |
| **API** | FastAPI + Uvicorn, async throughout | Native async matches an IO-bound workload (LLM calls, vector search, Postgres). Server-Sent Events over `StreamingResponse` carry the agent feed. |
| **ORM** | SQLAlchemy 2.0 async + asyncpg | Typed declarative models; `JSONB` columns for TipTap documents and diff blocks. |
| **Auth** | `bcrypt` + `PyJWT` | Stateless bearer tokens; no session store needed. |
| **Document parsing** | `pypdf`, `python-pptx`, `python-docx` | All page-aware, which is the whole point of the chunking strategy. |
| **Frontend** | Next.js App Router, TypeScript | Route groups separate the unauthenticated shell `(auth)` from the application shell `(app)`. |
| **UI** | Tailwind CSS, shadcn/ui, Radix primitives, Lucide | Unstyled accessible primitives we own the styling of — no fighting a theme. |
| **Editor** | TipTap (ProseMirror) | Emits a JSON document tree, which is what makes block-level diffing tractable — the diff unit is a ProseMirror node, not a character offset. |
| **Streaming client** | `fetch` + `ReadableStream` | Native SSE parsing; `EventSource` cannot send an `Authorization` header. |

### Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Next.js · TypeScript · Tailwind · shadcn/ui                      │
│  Chat · Traceability Editor · Document Vault · Settings           │
└────────────────────────────┬─────────────────────────────────────┘
                             │  REST  +  SSE (activity feed & tokens)
┌────────────────────────────▼─────────────────────────────────────┐
│  FastAPI · JWT bearer auth                                        │
│                                                                   │
│   create_deep_agent (supervisor)                                  │
│     skills:     /skills/{document_generation,feature_comparison,  │
│                          ecss_house_style}/SKILL.md               │
│     middleware: Skills → TodoList → Filesystem → SubAgent         │
│                 → Summarization → TrimMessages                    │
│     ├─ research            evidence gathering, page citations     │
│     ├─ document_writer     drafts sections (reads house style)    │
│     └─ comparison_analyst  assigns verdicts (reads the vocabulary)│
└──────────┬────────────────────────────────┬──────────────────────┘
           │                                │
┌──────────▼───────────┐        ┌───────────▼──────────────┐
│ PostgreSQL           │        │ Qdrant                   │
│ users, documents,    │        │ one point == one page    │
│ revisions, diffs,    │        │ payload-filtered by      │
│ chat, file metadata  │        │ owner_id and file_id     │
└──────────────────────┘        └──────────────────────────┘
```

---

## How it works

### 1. Authentication

`POST /api/v1/auth/register` hashes the password with bcrypt (truncated to 72
bytes explicitly, since bcrypt would do it silently) and returns a JWT. The
frontend stores it in `localStorage` and attaches it as a bearer token; a `401`
anywhere clears the session and redirects to `/login`.

Registration also does one extra thing: it claims any document share that was
addressed to that email before the account existed. See
[Sharing](#6-sharing-mocked-invitations).

### 2. Ingestion — one page is one chunk

This is the central design decision. Compliance work lives or dies on citation
precision: a reviewer must be able to open *document X, page N* and see the
claim. So `aiper` does **not** use recursive character splitting.
[`rag/loaders.py`](backend/app/rag/loaders.py) produces exactly one retrieval
chunk per page:

| Format | Page boundary |
| --- | --- |
| `.pdf` | A PDF page (`pypdf`). A page that fails to extract yields empty text rather than aborting the file. |
| `.pptx` | A slide, including tables and speaker notes. |
| `.docx` | An explicit page break, or every 40 paragraphs as a fallback (Word has no page model until it is rendered). |
| `.txt` / `.md` | A form-feed character, else a ~3500-character budget that only breaks on paragraph boundaries. |

Empty pages are dropped and the survivors renumbered, so citations stay
contiguous.

Upload flow: `POST /api/v1/files` → validate extension and size → write to the
uploads volume → create a `FileAsset` row → parse pages on a worker thread
(`anyio.to_thread`) → embed and upsert into Qdrant, one point per page. The
point ID is `uuid5(file_id + page)`, so re-indexing is idempotent.

Indexing runs **inline**, not in a background queue, so the chat turn that
follows an upload can rely on the pages being searchable. Failures are recorded
on the asset (`index_error`) and surfaced in the Vault rather than raised.

Every Qdrant point carries `owner_id`, `file_id`, `session_id`,
`comparison_role` and `page` in its payload, all indexed. Every query is built
through `_filter()`, which always pins `owner_id` — a tool physically cannot
reach another user's pages.

### 3. An agent turn, end to end

`POST /api/v1/chat/stream` returns `text/event-stream`. The lifecycle:

1. **Resolve the session** — reuse `session_id` or create one. The first user
   message becomes the conversation title.
2. **Build the `SkillContext`** — owner, mode, the attachment IDs for this turn,
   which one is the comparison target, the chosen template. Every tool is a
   closure over this object, which is how per-user isolation is enforced at the
   tool layer rather than by prompt instruction.
3. **Load history** — the last 24 messages from Postgres, mapped to
   `HumanMessage` / `AIMessage`.
4. **Build the agent** — `create_deep_agent` with the resolved tools, the
   supervisor prompt (which names the skill for this mode), `skills=["/skills/"]`
   served through a `CompositeBackend`, the sub-agent specs and the middleware
   stack.
5. **Stream** — `agent.astream_events(..., version="v2")` is translated by
   [`agents/runtime.py`](backend/app/agents/runtime.py) into compact events.

| Event | Emitted when | Rendered as |
| --- | --- | --- |
| `status` | turn start / end | feed header state |
| `plan` | `write_todos` is called | checklist with per-item status |
| `skill` | `read_file` on `/skills/<name>/SKILL.md` | "Loaded skill" + skill badge |
| `delegation` | the `task` tool fires | "Delegating to `document_writer`" + agent badge |
| `tool_start` / `tool_end` | any other tool | one collapsible row (the two are folded together client-side); a call made inside a sub-agent carries `parent` and nests under its delegation row |
| `token` | supervisor token delta | streamed answer text |
| `final` | turn complete | the persisted answer |
| `error` | anything raised | red row + toast |

Nesting comes from `astream_events`' `parent_ids`: a tool call whose ancestry
includes an open `task` run belongs to that sub-agent. Sub-agent tokens are
deliberately *not* streamed into the answer — they would interleave with the
supervisor's — and neither are the summarizer's, whose model is tagged
`summary` for exactly this purpose.

When the stream closes, the assistant message is persisted **with its activity
trace** in a `JSONB` column, so reopening a conversation replays the full
reasoning feed, not just the answer.

### 4. Context window strategy

Three layers, configured in
[`agents/context.py`](backend/app/agents/context.py) and
[`orchestrator.py`](backend/app/agents/orchestrator.py):

1. **Offloading** — deepagents' `FilesystemMiddleware` (installed by
   `create_deep_agent`) lets the agent stage long drafts under `/draft/` in
   graph state instead of in the transcript; the summarizer parks pre-summary
   history on the same backend.
2. **Summarization** — our `SummarizationMiddleware` instance *replaces* the
   built-in one; since v0.7 middleware is overridden by matching `.name` rather
   than duplicated. It triggers on
   `("tokens", AGENT_SUMMARY_TRIGGER_TOKENS)` and keeps the last
   `AGENT_SUMMARY_KEEP_MESSAGES` messages. The summary prompt is written to
   preserve requirement IDs, clause numbers, filenames and page citations
   verbatim.

   > A `("fraction", 0.85)` trigger is more idiomatic, but a fraction resolves
   > against the model profile's `max_input_tokens`, which Azure deployments do
   > not reliably expose. Tokens are the safe choice here.

3. **Trimming** — `TrimMessagesMiddleware` runs `trim_messages` as a hard
   backstop, then drops any `ToolMessage` orphaned by the trim (Azure rejects a
   tool result whose originating `tool_call` is gone).

Two implementation details worth knowing:

- The trim hooks **`wrap_model_call`**, not `before_model`. Node-style hooks
  merge their return value into state through the graph reducers, so they
  *append* rather than replace — returning a shortened list from `before_model`
  would grow the transcript. Overriding the request bounds only what is sent to
  Azure and leaves persisted history intact.
- In LangChain 1.x the system prompt travels on `ModelRequest.system_prompt`,
  not inside `.messages`, so trimming structurally cannot drop it.
  `include_system=True` is passed anyway.

Planning became opt-in in deepagents v0.7, so `TodoListMiddleware()` is added
explicitly — the activity feed depends on those plans.

### 5. Skills — SKILL.md, not Python

The procedures are **not** in the prompts. They live as deepagents skills in
[`backend/skills/`](backend/skills), one directory per skill, each with a
`SKILL.md` whose YAML frontmatter carries `name` and `description`:

| Skill | What it holds |
| --- | --- |
| [`document_generation`](backend/skills/document_generation/SKILL.md) | The generation procedure, the staging convention (`/draft/<n>.md`), the output contract |
| [`feature_comparison`](backend/skills/feature_comparison/SKILL.md) | The comparison procedure, the closed verdict vocabulary, the evidence rules |
| [`ecss_house_style`](backend/skills/ecss_house_style/SKILL.md) | Requirement wording, identifiers, verification methods, units, citation format |

deepagents applies *progressive disclosure*: at start only each skill's name
and description are injected into the system prompt; the agent `read_file`s
the full `SKILL.md` when it decides the skill applies. The supervisor prompt
names the skill for the current mode and instructs the agent to read it before
anything else, which is what the **Loaded skill** row in the feed shows.
Editing a procedure is editing a Markdown file — no Python, no redeploy of
prompts.

The skills directory is copied into the image and served through
`CompositeBackend(default=StateBackend(), routes={"/skills/": FilesystemBackend(root_dir="/app")})`,
so `/skills/…` reads from disk while everything the agent writes stays in
ephemeral graph state.

Sub-agents are declared as deepagents `SubAgent` specs: `system_prompt`, `tools`
holding resolved **tool objects** (hence the `{name: tool}` registry), and
`skills` for the two specialists so they can read the house style and the
verdict rules themselves. Filesystem tools are inherited from the default
stack. Each runs isolated, with its own context window.

| Sub-agent | Tools | Skills |
| --- | --- | --- |
| `document_writer` | `get_template`, `search_pages` | yes |
| `comparison_analyst` | `find_evidence_in_sources` | yes |
| `research` | `search_pages`, `load_target_document`, `list_attachments` | no |

**Document Generation.** Resolve the template → per section, search for
evidence using the section title and guidance → delegate drafting with the
retrieved excerpts verbatim → assemble → close with a *Source coverage* table
listing which pages of which file were actually used and what was left `[TBC]`.

Six templates ship pre-seeded: Mission System Specification (ECSS-E-ST-10C),
Software Requirements Specification (ECSS-E-ST-40C / Q-ST-80C), RFP/ITT
Response, Compliance Matrix, Interface Control Document (ECSS-E-ST-10-24C) and
Technical Note. Users can add their own via `POST /api/v1/templates`.

**Feature Comparison.** N source documents against exactly one target. Read the
target end to end → assign `TGT-001…` IDs to its checkable items → retrieve
evidence per item from the sources only → delegate verdicts in batches of
10–15 → render one matrix. Verdicts are constrained to `COMPLIANT`, `PARTIAL`,
`NON-COMPLIANT`, `NOT ADDRESSED`, and the analyst prompt forbids `COMPLIANT`
without a page citation. The UI renders each verdict as a coloured chip.

### 6. Documents are commits, not files

The editor is a git-like store, not an autosaving text box.

| Table | Role |
| --- | --- |
| `documents` | The head. `content_json` (TipTap) + `content_text` (flattened). |
| `revisions` | Immutable commits: `revision_number`, `parent_revision_id`, `commit_message`, author, `source` (`human` \| `agent`), and a snapshot of the content. |
| `revision_diffs` | The materialised block diff against the parent, plus counts. |
| `document_collaborators` | Share entries. |

Committing (`POST /documents/{id}/commits`) snapshots the editor's JSON,
flattens it to text, diffs it against the current head, and appends a revision.
Restoring an old revision **appends a new one** — history is never rewritten.

The diff engine ([`services/diff.py`](backend/app/services/diff.py)) works on
blocks, where a block is one non-empty line of the flattened document, mapping
1:1 to a top-level TipTap node. It runs `difflib.SequenceMatcher` over the block
list and post-processes the `replace` opcodes: paired lines with a similarity
ratio ≥ 0.45 are reported as a single `modify` (rendered as strike-through old
text above new text) instead of an unrelated remove/add pair. Long runs of
unchanged blocks collapse to a `··· N unchanged blocks ···` marker.

Agent output enters this system through `POST /documents/from-markdown`, which
converts Markdown to a TipTap tree and writes the first revision with
`source="agent"` — so the history shows exactly where the machine stopped and a
human took over.

### 7. Sharing (mocked invitations)

Deliberately mocked, as specified: no email is sent and no OTP is issued.
`POST /documents/{id}/collaborators` with an email either links an existing
account immediately (`invite_status="accepted"`) or stores the share as
`pending`. Registration then claims every pending share matching the new
account's address. Access resolution (`resolve_access`) returns `owner`,
`editor` or `viewer`; viewers get a read-only editor and no commit button.

---

## Mock backend

`mock/` is a **separate service**, not a mode. It serves the same HTTP contract
as `backend/` with in-memory state and a scripted agent, so the frontend can be
developed, demoed and reviewed with zero credentials.

Production code contains no mock branches and no feature flag — the two
services never import each other. You choose one by choosing a compose file:

```bash
docker compose -f docker-compose.mock.yml up --build   # frontend + mock
```

```bash
docker compose up --build                              # the real stack
```

| Real in the mock | Simulated |
| --- | --- |
| Registration, login, bearer auth, access control | LLM inference — turns are scripted |
| Upload, type/size validation, storage | Embeddings — replaced by lexical scoring |
| **Page parsing** — real `pypdf` / `python-pptx` / `python-docx`, one page = one chunk | Qdrant — replaced by an in-memory index |
| **Retrieval and page citations** — genuinely from your files | Compliance verdicts — banded off retrieval score |
| Documents, revisions, block diffs, restore, sharing | Postgres — replaced by dicts |
| The complete SSE event stream and UI feed | |

[`mock/app/agent.py`](mock/app/agent.py) emits the exact event shapes the real
orchestrator produces — plans with per-item status transitions, delegation
rows, tool start/end pairs, then a token-by-token answer — with realistic
pacing. Document Generation walks the selected template's sections; Feature
Comparison derives one checkable item per target page. Both append a footer
saying the prose is scripted, and the Settings page shows a **mock mode** badge
(driven by the `/health` response, not a build flag).

The answer text is not intelligent — but the page numbers in it are real, which
makes it a fair test of ingestion, streaming, the feed, the editor import path
and version control.

Seeded for the demo account: the six templates, plus two sample PDFs with
deliberately conflicting values (`MIS-REQ-Baseline.pdf` as target,
`Supplier-Datasheet-OptiCam.pdf` as source) so a comparison produces a mixed
verdict spread out of the box. Details in [`mock/README.md`](mock/README.md).


## Repository layout

```
aiper/
├── docker-compose.yml            the real stack: postgres · qdrant · backend · frontend
├── docker-compose.mock.yml       frontend + scripted mock backend, no credentials
├── .env.example                  every variable, documented below
│
├── backend/                      FastAPI · LangChain · deepagents
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── skills/                   deepagents skills, one SKILL.md per directory
│   │   ├── document_generation/
│   │   ├── feature_comparison/
│   │   └── ecss_house_style/
│   └── app/
│       ├── main.py               app factory, CORS, lifespan (schema + seed + collection)
│       ├── config.py             one Settings object, read once
│       ├── schemas.py            the wire contract
│       ├── api/
│       │   ├── router.py         /api/v1
│       │   ├── auth.py           register · login · me  (+ claims pending shares)
│       │   ├── files.py          upload → parse → embed → Qdrant, inline
│       │   ├── templates.py      the built-in six, plus per-workspace additions
│       │   ├── chat.py           conversations and the SSE turn
│       │   └── documents.py      commits · diffs · restore · sharing
│       ├── agents/
│       │   ├── orchestrator.py   create_deep_agent + backend + the middleware stack
│       │   ├── context.py        summarization override + TrimMessagesMiddleware
│       │   ├── skills.py         the tools, closed over one SkillContext
│       │   ├── prompts.py        the rules; procedures are in skills/
│       │   └── runtime.py        astream_events → the UI event feed
│       ├── rag/
│       │   ├── loaders.py        one page = one chunk, per format
│       │   └── store.py          Qdrant; every query pinned to owner_id
│       ├── services/
│       │   ├── diff.py           block diff with modify-pairing and collapsing
│       │   ├── documents.py      Markdown ⇄ TipTap, and the flatten the diff runs on
│       │   ├── templates.py      the six pre-seeded templates
│       │   └── seed.py           idempotent first-boot seeding
│       ├── core/
│       │   ├── security.py       bcrypt + JWT
│       │   └── deps.py           DbSession, CurrentUser
│       └── db/
│           ├── base.py           async engine, session factory, init_db
│           └── models.py         users · files · templates · chat · documents · revisions
│
├── mock/                         same contract, in-memory, scripted agent
│   ├── README.md                 what is real and what is simulated
│   └── app/
│       ├── main.py               the HTTP surface
│       ├── store.py              dicts, lexical retrieval, the revision graph
│       ├── agent.py              the scripted event stream
│       ├── seed.py               demo account + two generated sample PDFs
│       └── loaders.py · diff.py · documents.py · templates.py · schemas.py
│                                 copies of their backend counterparts
│
└── frontend/                     Next.js App Router · TypeScript · Tailwind
    ├── Dockerfile                deps → build → slim runner
    ├── app/
    │   ├── globals.css           palette, prose, TipTap surface — light and dark
    │   ├── (auth)/               login · register
    │   └── (app)/                chat · editor · vault · settings, behind the shell
    ├── components/
    │   ├── layout/               sidebar · providers · theme toggle · page header
    │   ├── chat/                 composer · activity feed · messages · empty state
    │   ├── editor/               TipTap editor · toolbar · history · diff · share
    │   ├── vault/                upload zone
    │   └── ui/                   the shadcn/ui primitives this app actually uses
    └── lib/
        ├── api.ts                typed client + SSE over fetch
        ├── auth.tsx              session state
        ├── workspace.tsx         the collections the sidebar and pages share
        ├── activity.ts           raw events → feed rows
        ├── markdown.tsx          renderer with citation and verdict chips
        └── types.ts              one set of types for the whole contract
```

---

## Configuration reference

Applies to the real stack (`docker-compose.yml`). The mock service needs none
of these — only `CORS_ORIGINS` and `STORAGE_DIR`, both defaulted.

| Variable | Purpose |
| --- | --- |
| `AZURE_OPENAI_*` | Chat and embedding deployments, endpoint, API version. |
| `DATABASE_URL` | asyncpg DSN used by SQLAlchemy. |
| `JWT_SECRET`, `JWT_ALGORITHM` | Token signing. **Change the secret before deploying.** |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime (default 7 days). |
| `QDRANT_HOST`, `QDRANT_PORT`, `QDRANT_COLLECTION` | Vector store location and collection. |
| `EMBEDDING_DIM` | Must match the embedding deployment — 3072 for `text-embedding-3-large`. |
| `AGENT_TRIM_TOKENS` | Hard ceiling enforced before every model call. |
| `AGENT_SUMMARY_TRIGGER_TOKENS` | Transcript size at which summarization fires. |
| `AGENT_SUMMARY_KEEP_MESSAGES` | Recent messages kept verbatim after a summary. |
| `AGENT_RECURSION_LIMIT` | LangGraph step budget per turn. |
| `STORAGE_DIR`, `MAX_UPLOAD_MB` | Upload volume path and per-file limit. |
| `BACKEND_CORS_ORIGINS` | Comma-separated allowed origins. |
| `NEXT_PUBLIC_API_URL` | Backend URL baked into the frontend bundle. |

---

---

## Known constraints

Stated plainly, because a v1 that pretends otherwise wastes the reviewer's time.

**Scope**

- `Base.metadata.create_all` creates the schema on boot. There are no migrations —
  a model change needs `docker compose down -v`. Alembic is the obvious next step.
- File indexing runs **inline** in the upload request. That is deliberate, so the
  next chat turn can rely on the pages being searchable, but a 300-page PDF makes
  for a slow upload. A queue with a progress channel is the scaling answer.
- Collaboration is concurrent but not real-time: two people can hold the same
  document open and both commit, and the second commit simply diffs against the
  first. There is no shared cursor and no operational transform — the Git model,
  not the Google Docs model.
- Invitations are mocked end to end, as specified. No mail is sent, no OTP is
  issued, and a pending share is claimed when that address registers.
- Tokens live in `localStorage`, which is the pragmatic choice for a bearer-token
  SPA but is readable by any script on the origin. A production deployment should
  move to an httpOnly refresh cookie.
- TypeScript build errors surface in CI (`npx tsc --noEmit`), not at `docker compose up`.

**Retrieval**

- `.docx` has no page model until it is rendered, so page boundaries fall on
  explicit breaks or every 40 paragraphs. Citations into a `.docx` are therefore
  approximate in a way that PDF and PPTX citations are not.
- A scanned PDF with no text layer cannot be indexed. It is flagged amber in the
  Vault with the reason rather than failing silently; OCR is not wired in.

**Agent**

- The context strategy is tuned by token counts (`AGENT_TRIM_TOKENS`,
  `AGENT_SUMMARY_TRIGGER_TOKENS`) rather than a fraction of the window, because
  Azure deployments do not reliably report `max_input_tokens`. Those numbers
  assume a ~128k deployment and want revisiting per model.
- Sub-agent tokens are not streamed into the answer, by design — they would
  interleave with the supervisor's. Their tool calls do stream, nested under
  the delegation row, so a long sub-agent task shows its progress call by call.
- A turn is bounded by `AGENT_RECURSION_LIMIT`. A very large comparison (hundreds
  of target items) can hit it; raise it, or split the target.
- Nothing is cached between turns. Two identical questions cost two full runs.

**Mock backend**

- The prose and the verdicts are scripted. Parsing, retrieval, page citations,
  the event stream, version control and access control are real. It is a fair
  test of everything except the model.

---

## Development

### Prerequisites

- Python 3.12 (`python3.12 -m venv venv`)
- Node 22 + npm 10
- Docker (colima or Docker Desktop) for `docker compose`

### Backend — local setup

```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

**Required environment variable for local development:**

```bash
export AIPER_DEV_MODE=1   # bypasses production secret checks at startup
```

Without `AIPER_DEV_MODE=1` the backend refuses to start unless `JWT_SECRET` and
`DATABASE_URL` are set to non-default values (fail-closed protection for production).

### Frontend — local setup

```bash
cd frontend
npm ci --no-fund --no-audit
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

### Running tests

```bash
# From the repo root
PYTHONPATH=backend AIPER_DEV_MODE=1 pytest backend/tests -v
```

The smoke test hits `/health` via httpx's ASGI transport — no database required.
CI provides a postgres:16 service so other lane tests can run DB-backed fixtures later.

### Linting

```bash
ruff check backend/app
```

### What CI runs

On every pull request and push to `main`:

| Job | Steps |
|-----|-------|
| **backend** | Python 3.12 · `pip install` reqs + dev reqs · `ruff check backend/app` · `pytest backend/tests` (postgres:16 service available) |
| **frontend** | Node 22 · `npm ci` · `npx tsc --noEmit` · `npm run build` |

### Upgrading dependencies

**Backend:** create a fresh venv, install without pins, then run `pip list --format=freeze`
and replace the versions in `backend/requirements.txt`. Run `pytest backend/tests` before committing.

**Frontend:** run `npm install` in `frontend/`, capture the resolved versions from
`package-lock.json`, update `package.json`, then verify with `npm ci` + `npm run build`.
